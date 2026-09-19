"""Register a verified Kiwoom real/mock account without persisting its number."""

from __future__ import annotations

import argparse
import asyncio
import json

from kiwoom_monitor.application.account_identity import verify_and_bind_account
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import create_query_store
from kiwoom_monitor.central_server.market_observations import as_kst
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from kiwoom_monitor.domain.order_contract import AccountEnvironment
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient, KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    ACCOUNT_ID_API,
    ACCOUNT_PATH,
    KiwoomAccountIdentityReader,
)


async def register(environment: AccountEnvironment, credential_profile_id: str) -> dict:
    settings = CentralServerSettings.from_environment()
    if environment is AccountEnvironment.MOCK:
        app_key, secret_key = settings.kiwoom_mock_app_key, settings.kiwoom_mock_secret_key
    else:
        app_key, secret_key = settings.kiwoom_app_key, settings.kiwoom_secret_key
    if not app_key or not secret_key:
        raise ValueError(f"{environment.value} Kiwoom credentials are not configured")
    store = create_query_store(
        settings.database_url,
        observation_history_enabled=settings.research_observation_history_enabled,
    )
    store.initialize()
    client = KiwoomRestClient(
        KiwoomSettings(app_key, secret_key, environment.value),
        request_interval_seconds=1.0 if environment is AccountEnvironment.MOCK else 0.2,
    )
    broker = CentralRestBroker(
        client, allowed_endpoints={ACCOUNT_ID_API: ACCOUNT_PATH},
        namespace=f"account-identity:{environment.value}",
    )
    try:
        binding = await verify_and_bind_account(
            KiwoomAccountIdentityReader(
                broker, environment=environment,
                hmac_key=settings.account_identity_key(),
                now_provider=lambda: as_kst(client.server_now()),
            ),
            store,
            credential_profile_id=credential_profile_id,
        )
        return {
            "credential_profile_id": binding.credential_profile_id,
            "scope": binding.scope.to_dict(),
            "binding_revision": binding.binding_revision,
            "verified_at": binding.verified_at.isoformat(),
            "verification_method": binding.verification_method,
        }
    finally:
        await broker.close()
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검증된 키움 계좌 UUID를 등록합니다.")
    parser.add_argument("--environment", choices=("real", "mock"), required=True)
    parser.add_argument("--profile", required=True)
    arguments = parser.parse_args(argv)
    document = asyncio.run(register(
        AccountEnvironment(arguments.environment), arguments.profile,
    ))
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
