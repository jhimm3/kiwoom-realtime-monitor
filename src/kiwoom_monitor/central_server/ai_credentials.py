"""AI credential publication without executing paid validation requests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .credential_runtime import CredentialRuntimeHooks, ValidatedCredential


@dataclass
class _AICandidate:
    api_key: str = field(repr=False)
    committed: bool = False
    published: bool = False


class AICredentialOwner:
    def __init__(self, service, vault, provider: str):
        self.service, self.provider = service, provider
        self.profile = f"nas-{provider}-default"
        service._store.register_credential_profile(provider, self.profile, datetime.now(UTC).isoformat())
        try:
            record = vault.load(provider, self.profile)
        except Exception:
            record = None
        service._credential_revisions[provider] = (
            record.revision if record and (record.disabled or service._keys[provider]) else None
        )
        service._credential_validation[provider] = (
            "DISABLED" if record and record.disabled else
            record.payload.get("validation", "UNVERIFIED") if record else "UNVERIFIED"
        )

    def hooks(self):
        return CredentialRuntimeHooks(
            self.prepare, self.drain, self.publish, self.resume, self.active_revision,
            release_candidate=self.release, begin_commit=self.begin_commit,
            supports_profile=lambda profile: profile == self.profile,
            validation_status=lambda profile: self.service._credential_validation[self.provider]
                if profile == self.profile else None,
        )

    def active_revision(self, profile):
        return self.service._credential_revisions[self.provider] if profile == self.profile else None

    async def prepare(self, profile, current, credentials, disabled):
        return ValidatedCredential("VERIFIED" if disabled else "UNVERIFIED",
            prepared=_AICandidate("" if disabled else credentials["api_key"]))

    async def drain(self, profile):
        await self.service.pause_credentials(self.provider)

    def begin_commit(self, profile, candidate):
        candidate.prepared.committed = True
        self.service.replace_credentials(self.provider, "", None, "UNVERIFIED")

    async def publish(self, profile, record, candidate):
        self.service.replace_credentials(self.provider, candidate.prepared.api_key, record.revision,
            "DISABLED" if record.disabled else candidate.validation)
        candidate.prepared.published = True

    async def resume(self, profile):
        self.service.resume_credentials(self.provider)

    def release(self, profile, candidate):
        prepared = candidate.prepared
        if prepared.committed and not prepared.published:
            self.service.replace_credentials(self.provider, "", None, "UNVERIFIED")
            self.service.resume_credentials(self.provider)
        prepared.api_key = ""
