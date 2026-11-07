"""macOS user keychain protection for local desktop credential files."""
from __future__ import annotations

import ctypes
import hashlib
import os
from functools import lru_cache
from pathlib import Path


MAC_KEYCHAIN_ENVELOPE = b"KRM-MAC-KEYCHAIN\x01"
_SERVICE = b"KiwoomRealtimeMonitor.LocalSecrets.v1"
_ITEM_NOT_FOUND = -25300
_DUPLICATE_ITEM = -25299
_UNLOCKED = 1
_KEY_SIZE = 32
_NONCE_SIZE = 12


class LocalSecretUnavailable(OSError):
    """Protected credentials need their original, accessible local store."""


@lru_cache(maxsize=1)
def _frameworks():
    try:
        security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ref, size, string = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
        ref_pointer, size_pointer = ctypes.POINTER(ref), ctypes.POINTER(size)
        for name, arguments in (
            ('SecKeychainCopyDefault', [ref_pointer]),
            ('SecKeychainGetStatus', [ref, size_pointer]),
            ('SecKeychainFindGenericPassword', [ref, size, string, size, string, size_pointer, ref_pointer, ref_pointer]),
            ('SecKeychainAddGenericPassword', [ref, size, string, size, string, size, ref, ref_pointer]),
            ('SecKeychainItemFreeContent', [ref, ref]),
        ):
            function = getattr(security, name)
            function.argtypes = arguments
            function.restype = ctypes.c_int32
        core.CFRelease.argtypes = [ref]
        core.CFRelease.restype = None
    except (AttributeError, OSError):
        raise LocalSecretUnavailable('macOS Keychain API unavailable') from None
    return security, core


def _read_key(security, identity) -> bytes | None:
    size = ctypes.c_uint32()
    data = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(*identity, ctypes.byref(size), ctypes.byref(data), None)
    try:
        if status == _ITEM_NOT_FOUND:
            return None
        if status != 0 or size.value != _KEY_SIZE or not data.value:
            raise LocalSecretUnavailable('macOS Keychain credential key unavailable')
        return ctypes.string_at(data, size.value)
    finally:
        if data.value:
            security.SecKeychainItemFreeContent(None, data)


def _keychain_key(account: bytes, *, create: bool) -> bytes:
    security, core = _frameworks()
    keychain = ctypes.c_void_p()
    status = security.SecKeychainCopyDefault(ctypes.byref(keychain))
    try:
        if status != 0 or not keychain.value:
            raise LocalSecretUnavailable('macOS default Keychain unavailable')
        flags = ctypes.c_uint32()
        if security.SecKeychainGetStatus(keychain, ctypes.byref(flags)) != 0 or not flags.value & _UNLOCKED:
            raise LocalSecretUnavailable('macOS default Keychain unavailable')
        identity = (keychain, len(_SERVICE), _SERVICE, len(account), account)
        key = _read_key(security, identity)
        if key is not None:
            return key
        if create:
            key = os.urandom(_KEY_SIZE)
            buffer = ctypes.create_string_buffer(key, _KEY_SIZE)
            status = security.SecKeychainAddGenericPassword(*identity, _KEY_SIZE, buffer, None)
            if status == 0:
                return key
            if status == _DUPLICATE_ITEM:
                # A concurrent first writer owns the committed key.
                key = _read_key(security, identity)
                if key is not None:
                    return key
        raise LocalSecretUnavailable('macOS Keychain credential key unavailable')
    finally:
        if keychain.value:
            core.CFRelease(keychain)


def _cipher(path: Path, *, create: bool):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise LocalSecretUnavailable('local credential encryption unavailable') from None
    try:
        account = hashlib.sha256(os.fsencode(path.resolve())).hexdigest().encode('ascii')
    except (OSError, RuntimeError):
        raise LocalSecretUnavailable('local credential path unavailable') from None
    return AESGCM(_keychain_key(account, create=create)), MAC_KEYCHAIN_ENVELOPE + account


def protect(data: bytes, path: Path) -> bytes:
    try:
        path.lstat()
    except FileNotFoundError:
        create = True
    except OSError:
        raise LocalSecretUnavailable('local credential path unavailable') from None
    else:
        create = False
    cipher, aad = _cipher(path, create=create)
    nonce = os.urandom(_NONCE_SIZE)
    return MAC_KEYCHAIN_ENVELOPE + nonce + cipher.encrypt(nonce, data, aad)


def unprotect(data: bytes, path: Path) -> bytes:
    if not data.startswith(MAC_KEYCHAIN_ENVELOPE):
        raise LocalSecretUnavailable('local credential belongs to another protection format')
    offset = len(MAC_KEYCHAIN_ENVELOPE)
    nonce, ciphertext = data[offset:offset + _NONCE_SIZE], data[offset + _NONCE_SIZE:]
    if len(nonce) != _NONCE_SIZE or len(ciphertext) < 16:
        raise LocalSecretUnavailable('local credential envelope is damaged')
    cipher, aad = _cipher(path, create=False)
    from cryptography.exceptions import InvalidTag
    try:
        return cipher.decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        raise LocalSecretUnavailable('local credential envelope is damaged or belongs to another local store') from None
