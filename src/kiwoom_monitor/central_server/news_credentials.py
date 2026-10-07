"""News provider authentication candidates, drains and publication fences."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.infrastructure.naver_news import NaverNewsClient, NaverNewsCredentials
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient, DartCredentialValidationError
from .credential_runtime import CredentialRuntimeHooks, ValidatedCredential, CredentialOperationError


@dataclass
class _NewsClientCandidate:
    client: object = field(repr=False)
    committed: bool = False
    published: bool = False


class NaverCredentialOwner:
    PROFILE = "nas-naver-default"

    def __init__(self, service, vault):
        self.service = service
        service._store.register_credential_profile("naver", self.PROFILE, datetime.now(UTC).isoformat())
        try:
            record = vault.load("naver", self.PROFILE)
        except Exception:
            record = None
        self._revision = (
            record.revision if record and (record.disabled or service._client is not None) else None
        )

    def hooks(self):
        return CredentialRuntimeHooks(
            self.prepare, self.drain, self.publish, self.resume, self.active_revision,
            release_candidate=self.release, begin_commit=self.begin_commit,
            supports_profile=lambda profile: profile == self.PROFILE,
        )

    def active_revision(self, profile):
        return self._revision if profile == self.PROFILE else None

    async def prepare(self, profile, current, credentials, disabled):
        client = None if disabled else NaverNewsClient(
            NaverNewsCredentials(credentials["client_id"], credentials["client_secret"])
        )
        if client is not None:
            await asyncio.to_thread(
                client.search_page, "증권", display=1, start=1,
                request_claim=self.service.claim_naver_validation_request,
            )
        return ValidatedCredential("VERIFIED", prepared=_NewsClientCandidate(client))

    async def drain(self, profile):
        await self.service.pause_naver_credentials()

    def begin_commit(self, profile, candidate):
        # A vault write can succeed before reporting failure. Never restore old keys past this fence.
        candidate.prepared.committed = True
        self._revision = None
        self.service.replace_naver_client(None)

    async def publish(self, profile, record, candidate):
        self.service.replace_naver_client(candidate.prepared.client)
        self._revision = record.revision
        candidate.prepared.published = True

    async def resume(self, profile):
        self.service.resume_naver_credentials()

    def release(self, profile, candidate):
        prepared = candidate.prepared
        if prepared.committed and not prepared.published:
            self._revision = None
            self.service.replace_naver_client(None)
            # NAVER stays absent; DART/cache/BODY/RULE/AI can still run.
            self.service.resume_naver_credentials()
        prepared.client = None


class DartCredentialOwner:
    PROFILE = "nas-dart-default"

    def __init__(self, service, vault, cache_path: Path):
        self.service = service
        self._cache_path = cache_path
        service._store.register_credential_profile("dart", self.PROFILE, datetime.now(UTC).isoformat())
        try:
            record = vault.load("dart", self.PROFILE)
        except Exception:
            record = None
        self._revision = (
            record.revision if record and (record.disabled or service._dart_client is not None) else None
        )

    def hooks(self):
        return CredentialRuntimeHooks(
            self.prepare, self.drain, self.publish, self.resume, self.active_revision,
            release_candidate=self.release, begin_commit=self.begin_commit,
            supports_profile=lambda profile: profile == self.PROFILE,
        )

    def active_revision(self, profile):
        return self._revision if profile == self.PROFILE else None

    async def prepare(self, profile, current, credentials, disabled):
        client = None if disabled else DartDisclosureClient(credentials["api_key"], self._cache_path)
        if client is not None:
            try:
                await asyncio.to_thread(client.validate_credentials)
            except DartCredentialValidationError as error:
                raise CredentialOperationError(error.code) from None
        return ValidatedCredential("VERIFIED", prepared=_NewsClientCandidate(client))

    async def drain(self, profile):
        await self.service.pause_dart_credentials()

    def begin_commit(self, profile, candidate):
        candidate.prepared.committed = True
        self._revision = None
        self.service.replace_dart_client(None)

    async def publish(self, profile, record, candidate):
        self.service.replace_dart_client(candidate.prepared.client)
        self._revision = record.revision
        candidate.prepared.published = True

    async def resume(self, profile):
        self.service.resume_dart_credentials()

    def release(self, profile, candidate):
        prepared = candidate.prepared
        if prepared.committed and not prepared.published:
            self._revision = None
            self.service.replace_dart_client(None)
            self.service.resume_dart_credentials()
        prepared.client = None
