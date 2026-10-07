from __future__ import annotations

import logging
import ssl
import sys
from functools import lru_cache


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def system_ssl_context() -> ssl.SSLContext:
    """인증서 검증을 유지하면서 현재 운영체제의 신뢰 인증서를 사용한다."""
    context = ssl.create_default_context()
    if sys.platform != "win32" or not hasattr(ssl, "enum_certificates"):
        return context

    loaded: set[bytes] = set()
    for store_name in ("ROOT", "CA"):
        try:
            certificates = ssl.enum_certificates(store_name)
        except OSError:
            logger.warning("Windows %s 인증서 저장소를 읽지 못했습니다.", store_name, exc_info=True)
            continue
        for certificate, encoding, _trust in certificates:
            if encoding != "x509_asn" or certificate in loaded:
                continue
            try:
                context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
                loaded.add(certificate)
            except ssl.SSLError:
                logger.debug("Windows 인증서를 SSL 컨텍스트에 추가하지 못했습니다.", exc_info=True)
    return context
