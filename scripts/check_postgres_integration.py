"""NAS 서버 컨테이너 안에서 PostgreSQL 저장 경계를 실제로 검증한다."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from time import time

from kiwoom_monitor.central_server.central_schema import CENTRAL_SCHEMA_VERSION
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import (
    PostgresQueryStore,
    StoredQuery,
    _append_postgres_news_articles,
    _append_postgres_observation_revision,
    _append_postgres_theme_snapshot,
    _load_postgres_confirmed_news_articles,
    _save_postgres_news_ai,
    _save_postgres_news_body,
    _save_postgres_news_event,
    _save_postgres_news_source_page,
    _load_market_profile_settings,
    _save_market_profile_settings,
    _load_account_settings,
    _save_real_account_recovery,
    _save_real_account_event,
)
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.application.news_rules import classify_supply_contract, rule_input_hash
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)


_A4B_COLLECTIONS = (
    "journal_v2_fills",
    "journal_v2_group_overrides",
    "journal_v2_news_links",
    "journal_v2_sync_states",
)


def _journal_news_link_key(document: dict[str, object]) -> str:
    identity = {
        "origin_scope": {
            "broker": document["origin_broker"],
            "environment": document["origin_environment"],
            "account_ref": document["origin_account_ref"],
        },
        "group_id": str(document["group_id"]),
        "stock_code": str(document["stock_code"]),
        "identity": str(document["identity"]),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "journal-news-link:v2:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _a4b_content_documents(
    marker: str, account_ref: str, observed_at: datetime,
) -> dict[str, dict[str, object]]:
    revision = observed_at.isoformat()
    scope = {
        "origin_broker": "kiwoom", "origin_environment": "mock",
        "origin_account_ref": account_ref, "canonical_account_ref": account_ref,
    }
    fill_key = f"fill:v2:{marker}"
    group_key = f"group:{marker}"
    provenance_hash = hashlib.sha256(marker.encode("utf-8")).hexdigest()
    fill = {
        **scope, "fill_key": fill_key, "order_no": marker, "stock_code": "005930",
        "stock_name": "통합검사", "side": "매수", "filled_at": revision,
        "quantity": 1, "price": 70000, "updated_at": revision,
    }
    group = {
        **scope, "fill_key": fill_key, "group_id": group_key, "updated_at": revision,
    }
    news = {
        **scope, "group_id": group_key, "stock_code": "005930", "identity": marker,
        "linked_at": revision, "updated_at": revision, "is_deleted": True,
    }
    news_key = _journal_news_link_key(news)
    values = {
        "journal_v2_fills": {"owner": account_ref, "key": fill_key, "document": fill},
        "journal_v2_group_overrides": {
            "owner": account_ref, "key": fill_key, "document": group,
        },
        "journal_v2_news_links": {
            "owner": account_ref, "key": news_key, "document": news,
        },
        "journal_v2_sync_states": {
            "owner": account_ref, "key": fill_key,
            "document": {
                **scope, "collection": "journal_v2_group_overrides", "owner": account_ref,
                "document_key": fill_key, "is_deleted": True, "updated_at": revision,
            },
        },
    }
    for collection, value in values.items():
        document = value["document"]
        assert isinstance(document, dict)
        document.update({
            "source_collection": collection, "source_owner": account_ref,
            "source_key": value["key"], "source_content_hash": provenance_hash,
        })
    return values


async def _asgi_json_request(
    app: object, method: str, path: str, token: str, payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent: list[dict[str, object]] = []
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode("ascii"), "query_string": b"",
        "headers": [
            (b"authorization", f"Bearer {token}".encode("ascii")),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("postgres-integration", 1), "server": ("localhost", 8787),
    }
    await app(scope, receive, send)  # type: ignore[operator]
    status = next(int(message["status"]) for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        bytes(message.get("body", b"")) for message in sent
        if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body or b"{}")


async def _exercise_a4b_content_api(
    database_url: str, values: dict[str, dict[str, object]],
) -> tuple[dict[str, int], int]:
    token = f"postgres-integration-{uuid.uuid4().hex}"
    settings = CentralServerSettings(
        database_url=database_url, access_token=token,
        autonomous_top20_enabled=False, market_event_collection_enabled=False,
        news_query_set_enabled=False, external_market_enabled=False,
        shadow_candidate_enabled=False, mock_account_monitor_enabled=False,
    )
    app = create_app(settings)
    statuses: dict[str, int] = {}
    async with app.router.lifespan_context(app):
        for collection, value in values.items():
            status, _ = await _asgi_json_request(
                app, "POST", f"/api/v1/content/{collection}", token,
                {"documents": [value]},
            )
            statuses[collection] = status
        invalid = json.loads(json.dumps(values["journal_v2_group_overrides"]))
        invalid["owner"] = str(uuid.uuid4())
        mismatch_status, _ = await _asgi_json_request(
            app, "POST", "/api/v1/content/journal_v2_group_overrides", token,
            {"documents": [invalid]},
        )
    return statuses, mismatch_status


def _exercise_market_profile_transaction(store, profile_id, binding_revision):
    """Check role CAS inside rollback so an operational NAS role is never replaced."""
    before = store.load_market_profile_settings()
    checks = {}
    with store._connect() as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
                current = _load_market_profile_settings(cursor, "%s")
                value = {"market_profile_id": profile_id, "expected_binding_revision": binding_revision}
                saved = _save_market_profile_settings(cursor, value, current["revision"], "%s")
                checks["market_profile_verified_role_cas"] = (
                    saved["market_profile_id"] == profile_id and saved["legacy_real_profile_id"] == current["legacy_real_profile_id"]
                    and saved["revision"] == current["revision"] + 1)
                checks["market_profile_unchanged_revision"] = (
                    saved == _save_market_profile_settings(cursor, value, saved["revision"], "%s"))
                try:
                    _save_market_profile_settings(cursor, value, current["revision"], "%s")
                except ValueError as error:
                    checks["market_profile_stale_revision"] = str(error) == "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"
                else:
                    checks["market_profile_stale_revision"] = False
        finally:
            connection.rollback()
    checks["market_profile_operational_role_preserved"] = before == store.load_market_profile_settings()
    return checks


def _exercise_real_recovery_transaction(store, profile_id):
    """Exercise real evidence storage in rollback; leave operational data intact."""
    from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
    from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution
    stored = [value for value in store.load_account_bindings() if value["credential_profile_id"] == profile_id][-1]
    scope = AccountScope("kiwoom", AccountEnvironment.REAL, stored["account_ref"])
    binding = AccountBinding(profile_id, scope, stored["binding_revision"],
                             datetime.fromisoformat(stored["verified_at"]), "ka00001")
    now = datetime.now(UTC)
    recovery = AccountRecovery(AccountSnapshot(scope.account_ref, 500000, 0, {"005930": 10}, now), ())
    owner = "kiwoom:real:" + scope.account_ref
    before = store.load_documents("real_account_recovery", owner)
    before_events = store.load_documents("real_account_event", owner)
    checks = {}
    with store._connect() as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
                settings = _load_account_settings(cursor, scope.to_dict(), "%s")
                first = _save_real_account_recovery(cursor, binding, recovery, now, settings["revision"], "%s")
                second = _save_real_account_recovery(cursor, binding, recovery, now, settings["revision"], "%s")
                checks["real_account_recovery_scoped_idempotent"] = first == second and first["scope"] == scope.to_dict()
                try:
                    _save_real_account_recovery(cursor, binding, recovery, now, settings["revision"] + 1, "%s")
                except ValueError as error:
                    checks["real_account_recovery_stale_policy"] = str(error) == "ACCOUNT_CONTEXT_MISMATCH"
                else:
                    checks["real_account_recovery_stale_policy"] = False
                event = OrderExecution("integration-order", "integration-fill", "005930", "test", "매수", 1000, 1,
                                       "090000", origin_scope=scope)
                first_event = _save_real_account_event(cursor, binding, "order_execution", event, now, settings["revision"], "%s")
                second_event = _save_real_account_event(cursor, binding, "order_execution", event, now, settings["revision"], "%s")
                checks["real_account_event_scoped_idempotent"] = first_event == second_event and first_event["scope"] == scope.to_dict()
                try:
                    _save_real_account_event(cursor, binding, "order_execution", event, now, settings["revision"] + 1, "%s")
                except ValueError as error:
                    checks["real_account_event_stale_policy"] = str(error) == "ACCOUNT_CONTEXT_MISMATCH"
                else:
                    checks["real_account_event_stale_policy"] = False
        finally:
            connection.rollback()
    checks["real_account_recovery_rollback_preserved"] = before == store.load_documents("real_account_recovery", owner)
    checks["real_account_event_rollback_preserved"] = before_events == store.load_documents("real_account_event", owner)
    return checks


def main() -> int:
    database_url = os.environ.get("KIWOOM_SERVER_DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        print(json.dumps({"status": "error", "reason": "postgres_url_required"}))
        return 2
    marker = f"integration-{uuid.uuid4().hex}"
    rollback_marker = f"rollback-{uuid.uuid4().hex}"
    export_dataset_id = ""
    account_ref = ""
    real_account_ref = ""
    real_profiles = []
    draft_profile_id = ""
    duplicate_profile = ""
    profile_request_id = str(uuid.uuid4())
    local_account_ref = str(uuid.uuid4())
    now = time()
    observed_at = datetime.now(UTC).replace(microsecond=0)
    store = PostgresQueryStore(database_url)
    try:
        store.initialize()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT version,name FROM central_schema_migrations ORDER BY version")
            migrations = [(int(row[0]), str(row[1])) for row in cursor.fetchall()]
        if not migrations or migrations[-1][0] != CENTRAL_SCHEMA_VERSION:
            raise RuntimeError(f"schema_version={migrations[-1][0] if migrations else 0}")

        checks: dict[str, bool] = {}
        store.save_query(marker, "integration", now + 60, StoredQuery({"marker": marker}, False, ""))
        checks["query_cache"] = store.load_query(marker) == StoredQuery({"marker": marker}, False, "")

        store.save_realtime_snapshots([{
            "event_type": "integration_check", "item_key": marker,
            "received_at": now, "event": {"type": "integration_check", "code": marker},
        }])
        checks["realtime"] = any(
            value.get("code") == marker for value in store.load_realtime_snapshots([marker])
        )

        minute_bar = {
            "trading_date": observed_at.date().isoformat(), "minute": "09:01",
            "code": marker, "market": "KRX", "open": 100, "high": 110,
            "low": 90, "close": 105, "volume": 10,
            "trade_value_million_won": 1, "updated_at": now,
        }
        minute_observation_key = f"{minute_bar['trading_date']}T{minute_bar['minute']}"
        metadata = MarketDataObservation(
            MarketDatasetKind.MINUTE_BAR, marker, minute_bar,
            MarketDataMetadata(
                observed_at, observed_at, TradingVenue.KRX, DataUnit.MILLION_WON,
                DataValueKind.ACTUAL, DataCompleteness.COMPLETE,
                ObservationOrigin.REALTIME, "postgres-integration",
                CandidateUniverse.RANKING_TOP20,
            ),
        )
        store.replace_minute_bars(
            [minute_bar], observations=[(minute_observation_key, metadata)],
        )
        checks["minute_bars"] = store.load_minute_bars(
            marker, observed_at.date().isoformat(), "KRX",
        )[0]["close"] == 105
        delta = {
            **minute_bar, "open": 106, "high": 106, "low": 106, "close": 106,
            "volume": 2, "trade_value_million_won": 1,
            "operation_id": f"{marker}-minute-delta",
        }
        delta_metadata = MarketDataObservation(
            MarketDatasetKind.MINUTE_BAR, marker, delta,
            MarketDataMetadata(
                observed_at, observed_at, TradingVenue.KRX, DataUnit.UNKNOWN,
                DataValueKind.ACTUAL, DataCompleteness.IN_PROGRESS,
                ObservationOrigin.REALTIME, "kiwoom-websocket-0B",
                CandidateUniverse.UNKNOWN,
            ),
        )
        store.save_minute_bars(
            [delta], observations=[(minute_observation_key, delta_metadata)],
        )
        store.save_minute_bars(
            [delta], observations=[(minute_observation_key, delta_metadata)],
        )
        checks["minute_delta_exactly_once"] = store.load_minute_bars(
            marker, observed_at.date().isoformat(), "KRX",
        )[0]["volume"] == 12
        second_bar = {
            "trading_date": observed_at.date().isoformat(),
            "trade_second": "09:01:01", "code": marker, "market": "KRX",
            "open": 100, "high": 110, "low": 90, "close": 105,
            "volume": 10, "trade_value_won": 1_020, "trade_count": 3,
            "available_at": now,
        }
        store.save_second_trade_bars([second_bar])
        store.save_second_trade_bars([second_bar])
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT volume,trade_value_won,trade_count "
                "FROM central_second_trade_bars WHERE code=%s",
                (marker,),
            )
            second_row = cursor.fetchone()
        checks["second_trade_bars"] = second_row == (10, 1_020, 3)
        loaded_metadata = store.load_market_data_metadata(
            MarketDatasetKind.MINUTE_BAR, marker, minute_observation_key,
        )
        checks["metadata"] = bool(
            loaded_metadata and loaded_metadata.source == "kiwoom-websocket-0B"
            and loaded_metadata.completeness == DataCompleteness.IN_PROGRESS
        )

        daily_bar = {
            "trading_date": observed_at.date().isoformat(), "code": marker, "market": "KRX",
            "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10,
            "trade_value_million_won": 1, "updated_at": now,
        }
        store.replace_daily_bars([daily_bar])
        checks["daily_bars"] = store.load_daily_bars(marker, "KRX", 1)[0]["close"] == 105

        store.save_dataset_snapshot(
            "integration_check", marker, marker, {"marker": marker},
        )
        checks["dataset_snapshots"] = store.load_dataset_snapshots(
            "integration_check", marker, 1,
        )[0]["payload"]["marker"] == marker

        ranking_payload_a = {"query_type": marker, "items": [{"stk_cd": marker, "rank": 1}]}
        ranking_payload_b = {"query_type": marker, "items": [{"stk_cd": marker, "rank": 2}]}
        for payload in (ranking_payload_a, ranking_payload_b, ranking_payload_a):
            store.save_dataset_snapshot(
                "ranking", marker, marker, payload,
                observation=ranking_observation(
                    marker, marker, payload, observed_at, source="postgres-integration",
                ),
            )
        observation_revisions = store.load_observation_revisions("ranking", marker, 10)
        checks["observation_history"] = (
            len(observation_revisions) == 3
            and observation_revisions[0]["payload"] == ranking_payload_a
            and observation_revisions[0]["revision_of"] == observation_revisions[1]["revision_id"]
            and observation_revisions[0]["payload_hash"] == observation_revisions[2]["payload_hash"]
        )
        export_manifest = store.create_observation_export(
            observed_at.replace(hour=0, minute=0, second=0),
            observed_at.replace(hour=0, minute=0, second=0) + timedelta(days=1),
            ("ranking",), marker,
        )
        export_dataset_id = str(export_manifest["fixed_watermark"])
        export_page = store.load_observation_export_page(export_dataset_id, 0, 2)
        final_export_page = store.load_observation_export_page(
            export_dataset_id, int(export_page["next_cursor"]), 2,
        )
        checks["research_export"] = (
            export_manifest["revision_count"] == 3
            and len(export_page["observations"]) == 2
            and len(final_export_page["observations"]) == 1
            and final_export_page["next_cursor"] is None
        )
        connection = store._connect()
        try:
            cursor = connection.cursor()
            rollback_payload = {"query_type": rollback_marker, "items": []}
            _append_postgres_observation_revision(
                cursor, "ranking", rollback_marker, rollback_marker, rollback_payload,
                ranking_observation(
                    rollback_marker, rollback_marker, rollback_payload, observed_at,
                    source="postgres-integration",
                ),
            )
            connection.rollback()
        finally:
            connection.close()
        checks["observation_history_rollback"] = not store.load_observation_revisions(
            "ranking", rollback_marker, 1,
        )

        store.upsert_documents("integration_check", [{
            "owner": "postgres", "key": marker,
            "document": {"value": 1, "marker": marker},
        }])
        loaded = store.load_documents("integration_check", "postgres", 10)
        checks["documents"] = any(value.get("key") == marker for value in loaded)

        theme_document = {
            "format": "kiwoom-realtime-monitor-theme-db", "version": 1,
            "created_at": observed_at.isoformat(), "active_profile": "integration",
            "profiles": [], "aliases": [], "stock_catalog": [],
        }
        theme_value = [{
            "owner": "default", "key": "full", "document": theme_document,
            "effective_at": observed_at.isoformat(), "origin_device": "postgres-integration",
        }]
        connection = store._connect()
        try:
            cursor = connection.cursor()
            _append_postgres_theme_snapshot(cursor, theme_value, received_at=now)
            cursor.execute(
                "SELECT count(*) FROM central_theme_snapshots "
                "WHERE origin_device=%s AND profile_id=%s",
                ("postgres-integration", "integration"),
            )
            checks["theme_history"] = int(cursor.fetchone()[0]) == 1
            connection.rollback()
        finally:
            connection.close()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM central_theme_snapshots "
                "WHERE origin_device=%s AND profile_id=%s",
                ("postgres-integration", "integration"),
            )
            checks["theme_history_rollback"] = int(cursor.fetchone()[0]) == 0

        connection = store._connect()
        try:
            cursor = connection.cursor()
            _append_postgres_news_articles(cursor, [{
                "owner": marker, "key": marker, "collector_id": "postgres-integration",
                "collection_scope": "integration",
                "document": {
                    "stock_code": marker, "stock_name": "통합기업", "identity": marker,
                    "title": "통합기업, 고객사와 500억원 공급계약 체결",
                    "description": "공급계약 공시", "published_at": observed_at.isoformat(),
                },
            }], received_at=now)
            cursor.execute(
                "SELECT article_revision_id FROM central_news_article_revisions "
                "WHERE stock_code=%s AND identity=%s", (marker, marker),
            )
            article_revision_id = str(cursor.fetchone()[0])
            body_revision_id = _save_postgres_news_body(cursor, {
                "article_revision_id": article_revision_id, "extractor_version": "integration-v1",
                "fetched_at": now, "status": "summary_only", "body_text": "공급계약 체결",
            })
            rule_result = classify_supply_contract({
                "stock_code": marker, "stock_name": "통합기업",
                "title": "통합기업, 고객사와 500억원 공급계약 체결",
                "description": "공급계약 공시",
            }, "공급계약 체결")
            event_revision_id = _save_postgres_news_event(cursor, {
                "article_revision_id": article_revision_id, "body_revision_id": body_revision_id,
                "rule_version": rule_result.rule_version,
                "input_hash": rule_input_hash(article_revision_id, body_revision_id, rule_result),
                "candidate_identities": (), "result": rule_result.as_document(),
            })
            _save_postgres_news_ai(cursor, [{
                "analysis_revision_id": marker, "target_id": marker,
                "article_revision_id": article_revision_id, "body_revision_id": body_revision_id,
                "provider": "integration", "model": "none", "prompt_version": "integration-v1",
                "schema_version": "integration-v1", "input_hash": marker, "computed_at": now,
                "output": {"marker": marker}, "usage": {"total_tokens": 0},
            }])
            cursor.execute(
                "SELECT (SELECT count(*) FROM central_news_jobs WHERE article_revision_id=%s),"
                "(SELECT count(*) FROM central_news_body_revisions WHERE article_revision_id=%s),"
                "(SELECT count(*) FROM central_news_ai_revisions WHERE analysis_revision_id=%s),"
                "(SELECT count(*) FROM central_news_event_revisions WHERE event_revision_id=%s),"
                "(SELECT count(*) FROM central_news_event_membership_revisions WHERE event_revision_id=%s)",
                (article_revision_id, article_revision_id, marker, event_revision_id, event_revision_id),
            )
            checks["news_history_jobs"] = tuple(int(value) for value in cursor.fetchone()) == (2, 1, 1, 1, 1)
            connection.rollback()
        finally:
            connection.close()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT count(*) FROM central_news_article_revisions WHERE identity=%s),"
                "(SELECT count(*) FROM central_news_ai_revisions WHERE analysis_revision_id=%s),"
                "(SELECT count(*) FROM central_news_event_revisions WHERE stock_code=%s)",
                (marker, marker, marker),
            )
            checks["news_history_rollback"] = tuple(int(value) for value in cursor.fetchone()) == (0, 0, 0)

        budget_date = f"integration-{marker}"
        checks["news_source_budget"] = (
            store.claim_news_request("query_set", scope_limit=1, hard_limit=2, budget_date=budget_date)
            and not store.claim_news_request("query_set", scope_limit=1, hard_limit=2, budget_date=budget_date)
            and store.claim_news_request("watchlist", scope_limit=1, hard_limit=2, budget_date=budget_date)
        )
        connection = store._connect()
        try:
            cursor = connection.cursor()
            source_id = f"naver-query:{marker}"
            saved = _save_postgres_news_source_page(cursor, {
                "source_id": source_id, "scope": "query_set", "query_text": "증권", "run_id": marker,
                "page_start": 1, "checked_at": now, "request_count": 1, "budget_remaining": 0,
                "coverage": "query_set", "truncated": False, "error": "", "next_start": 1,
                "next_schedule_at": now + 300, "last_success": now,
                "cursor_published_at": observed_at.isoformat(), "cursor_identity": marker,
                "pending_published_at": None, "pending_identity": "", "document": {"total": 1},
                "items": [{"identity": marker, "document": {
                    "title": "통합기업 공급계약 체결", "description": "", "link": marker,
                    "original_link": marker, "published_at": observed_at.isoformat(),
                }, "targets": [{"stock_code": None, "stock_name": None,
                                  "relation_status": "unresolved", "evidence_text": "",
                                  "rule_version": "exact-krx-company-name-v1"}]}],
            })
            cursor.execute(
                "SELECT article_revision_id FROM central_news_article_revisions "
                "WHERE stock_code='GLOBAL' AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
                (marker,),
            )
            source_article_revision_id = str(cursor.fetchone()[0])
            _save_postgres_news_body(cursor, {
                "article_revision_id": source_article_revision_id,
                "extractor_version": "integration-v1", "fetched_at": now,
                "status": "summary_only", "body_text": "통합기업 공급계약 체결",
            })
            repeated = _save_postgres_news_source_page(cursor, {
                "source_id": source_id, "scope": "query_set", "query_text": "증권",
                "run_id": marker + "-repeat", "page_start": 1, "checked_at": now,
                "request_count": 1, "budget_remaining": 0, "coverage": "query_set",
                "truncated": False, "error": "", "next_start": 1,
                "next_schedule_at": now + 300, "last_success": now,
                "cursor_published_at": observed_at.isoformat(), "cursor_identity": marker,
                "pending_published_at": None, "pending_identity": "", "document": {"total": 1},
                "items": [{"identity": marker, "document": {
                    "title": "통합기업 공급계약 체결", "description": "", "link": marker,
                    "original_link": marker, "published_at": observed_at.isoformat(),
                }, "targets": [{"stock_code": marker, "stock_name": "통합기업",
                                  "relation_status": "confirmed", "evidence_text": "통합기업",
                                  "rule_version": "exact-krx-company-name-v1"}]}],
            })
            cursor.execute(
                "SELECT (SELECT count(*) FROM central_news_source_runs WHERE source_id=%s),"
                "(SELECT count(*) FROM central_news_source_observations WHERE source_id=%s),"
                "(SELECT count(*) FROM central_news_article_revisions WHERE stock_code='GLOBAL' AND identity=%s),"
                "(SELECT count(*) FROM central_news_jobs WHERE article_revision_id=%s AND stage='RULE' AND target_id=%s)",
                (source_id, source_id, marker, source_article_revision_id, marker),
            )
            checks["news_source_history"] = (
                saved["raw_count"] == 1 and repeated["unique_count"] == 0
                and repeated["duplicate_count"] == 1 and cursor.fetchone() == (2, 2, 1, 1)
            )
            confirmed_news = _load_postgres_confirmed_news_articles(cursor, marker, 10)
            checks["confirmed_stock_news"] = (
                len(confirmed_news) == 1
                and confirmed_news[0].get("title") == "통합기업 공급계약 체결"
            )
            connection.rollback()
        finally:
            connection.close()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM central_news_source_runs WHERE source_id=%s", (source_id,))
            checks["news_source_rollback"] = int(cursor.fetchone()[0]) == 0

        vi_key = f"vi-{marker}"
        checks["market_event_vi"] = store.append_vi_events([{
            "event_id": vi_key, "event_key": vi_key, "stock_code": marker,
            "event_kind": "ACTIVATED", "vi_type": "STATIC", "effective_at": observed_at.isoformat(),
            "received_at": now, "available_at": now, "price": 100, "direction": "+",
            "trigger_count": 1, "exchange": "KRX", "source": "integration", "document": {"marker": marker},
        }]) == 1
        cohort_key = f"cohort-{marker}"
        current = {"stock_code": marker, "stock_name": "통합", "condition_name": "15%", "first_seen_at": now,
                   "entry_session": observed_at.date().isoformat(), "last_signal": "I", "last_signal_at": now,
                   "active": True, "nxt_eligible": False, "expired_at": None, "marker": marker}
        cohort_inserted = store.record_hot_cohort_revision({
            "revision_id": cohort_key, "revision_key": cohort_key, "stock_code": marker,
            "event_type": "ENTERED", "condition_name": "15%", "condition_seq": "1",
            "session_id": observed_at.date().isoformat(), "effective_at": now, "available_at": now,
            "document": {"marker": marker},
        }, current)
        checks["market_event_cohort"] = cohort_inserted and any(
            value.get("stock_code") == marker for value in store.load_hot_cohort(active_only=True)
        )
        limit_key = f"limit-{marker}"
        checks["market_event_upper_limit"] = store.append_upper_limit_facts([{
            "fact_id": limit_key, "fact_key": limit_key, "stock_code": marker,
            "session_id": observed_at.date().isoformat(), "status": "TOUCHED", "upper_limit_price": 130,
            "current_price": 120, "high_price": 130, "effective_at": now, "available_at": now,
            "source": "integration", "evidence": "fixture", "document": {"marker": marker},
        }]) == 1 and bool(store.load_market_event_history("upper_limit", code=marker))

        store.save_external_bars([{
            "provider": "integration", "instrument": marker, "contract": marker,
            "timeframe": "5m", "bar_time": observed_at.isoformat(), "open": 1.0,
            "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0,
            "updated_at": now,
        }])
        checks["external_bars"] = store.load_external_bars(marker, "5m", 1)[0]["close"] == 1.5
        shadow_decision = {
            "decision_id": f"decision-{marker}", "decided_at": observed_at.isoformat(),
            "final_action": "ENTER",
        }
        shadow_candidate = {
            "event_id": f"candidate-{marker}", "symbol": marker,
            "available_at": observed_at.isoformat(),
        }
        store.save_shadow_monitor_state(marker, {"cursor": 1, "quality": {"status": "READY"}})
        store.save_shadow_evaluation(
            marker, shadow_decision, shadow_candidate,
            (observed_at + timedelta(minutes=1)).isoformat(),
        )
        shadow_page = store.load_shadow_candidates(0, 1000)
        checks["shadow_candidates"] = (
            store.load_shadow_monitor_state(marker)["cursor"] == 1
            and any(value.get("event_id") == shadow_candidate["event_id"] for value in shadow_page["events"])
        )
        execution_intent_id = f"intent-{marker}"
        execution_intent = {
            "intent_id": execution_intent_id, "run_id": marker, "decision_id": marker,
            "environment": "mock", "account_ref": marker, "symbol": "005930", "venue": "KRX",
            "side": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 1,
            "created_at": observed_at.isoformat(), "expires_at": (observed_at + timedelta(minutes=1)).isoformat(),
            "policy_version": "integration-v1", "state": "QUEUED", "broker_order_id": "",
            "filled_quantity": 0, "fill_ids": [], "last_broker_as_of": None,
            "updated_at": observed_at.isoformat(),
        }
        execution_event = {
            "event_id": f"event-{marker}", "intent_id": execution_intent_id,
            "event_type": "ORDER_ACCEPTED", "state": "ACCEPTED",
            "occurred_at": observed_at.isoformat(), "received_at": observed_at.isoformat(),
            "broker_order_id": marker, "broker_execution_id": "", "quantity": 0, "price": 0,
            "reason": "", "broker_as_of": None,
        }
        accepted_intent = {**execution_intent, "state": "ACCEPTED", "broker_order_id": marker}
        execution_snapshot = {
            "snapshot_id": f"account-{marker}", "environment": "mock", "account_ref": marker,
            "as_of": observed_at.isoformat(), "received_at": observed_at.isoformat(),
            "available_cash_won": 1, "reserved_open_buy_won": 0, "positions": {},
        }
        checks["mock_execution_ledger"] = (
            store.create_execution_intent(execution_intent)
            and store.append_execution_event(accepted_intent, execution_event)
            and store.load_execution_intent(execution_intent_id)["state"] == "ACCEPTED"
            and store.find_execution_intent_by_broker_order_id(
                "mock", marker, marker, marker,
            )["intent_id"] == execution_intent_id
            and len(store.load_execution_events(execution_intent_id)) == 1
            and store.save_execution_account_snapshot(execution_snapshot)
            and store.acquire_execution_runtime(
                f"mock:{marker}", f"{marker}:{marker}", observed_at.isoformat(),
                (observed_at + timedelta(minutes=1)).isoformat(),
            )
        )
        checks["mock_execution_lease_release_cas"] = (
            not store.release_execution_runtime(f"mock:{marker}", f"other:{marker}")
            and store.release_execution_runtime(f"mock:{marker}", f"{marker}:{marker}")
            and store.acquire_execution_runtime(
                f"mock:{marker}", f"{marker}:new-{marker}", observed_at.isoformat(),
                (observed_at + timedelta(minutes=1)).isoformat(),
            )
            and not store.release_execution_runtime(f"mock:{marker}", f"{marker}:{marker}")
        )
        ownership = {"owner_key": f"mock:{marker}", "owner_token": f"{marker}:new-{marker}", "run_id": marker}
        checks["mock_execution_owned_snapshot"] = store.save_execution_account_snapshot(
            {**execution_snapshot, "snapshot_id": f"owned-account-{marker}"}, ownership=ownership,
        )
        previous_owner = {**ownership, "owner_token": f"{marker}:{marker}"}
        rejected = 0
        for write in (
            lambda: store.create_execution_intent(execution_intent, ownership=previous_owner),
            lambda: store.append_execution_event(accepted_intent, execution_event, ownership=previous_owner),
            lambda: store.save_execution_account_snapshot(execution_snapshot, ownership=previous_owner),
        ):
            try: write()
            except RuntimeError as error:
                if str(error) != "EXECUTION_OWNERSHIP_LOST": raise
                rejected += 1
        checks["mock_execution_late_owner_write_rejected"] = rejected == 3
        account_ref = store.register_account_identity({
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": uuid.uuid4().hex * 2,
            "created_at": observed_at.isoformat(),
        })
        binding = store.append_account_binding({
            "credential_profile_id": marker, "broker": "kiwoom", "environment": "mock",
            "account_ref": account_ref, "verified_at": observed_at.isoformat(),
            "verification_method": "ka00001",
        })
        store.register_account_scope_alias({
            "origin_account_ref": local_account_ref, "canonical_account_ref": account_ref,
            "broker": "kiwoom", "environment": "mock", "credential_profile_id": marker,
            "binding_revision": binding["binding_revision"],
            "verified_at": observed_at.isoformat(), "verification_method": "ka00001",
        })
        resolution = store.resolve_account_scope("kiwoom", "mock", local_account_ref)
        checks["account_scope_alias"] = (
            resolution["verified"] and resolution["origin_account_ref"] == local_account_ref
            and resolution["canonical_account_ref"] == account_ref
        )
        from kiwoom_monitor.central_server.credential_store import CredentialStore
        with tempfile.TemporaryDirectory(prefix="credential-integration-") as directory:
            vault = CredentialStore(directory, store)
            try:
                # Generate ephemeral credentials; output contains only boolean check results.
                credentials = {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}
                encrypted = vault.save("kiwoom_mock", marker, credentials, expected_revision=0)
                activation = {
                    "operation_id": marker, "provider": "kiwoom_mock", "profile_id": marker,
                    "credential_revision": encrypted.revision, "request_id": marker,
                    "request_digest": vault.request_digest(credentials), "environment": "mock",
                    "account_ref": account_ref, "run_id": marker, "committed_at": observed_at.isoformat(),
                }
                activated = store.finalize_credential_activation(activation)
                repeated = store.finalize_credential_activation(activation)
                checks["credential_activation_idempotency"] = (
                    activated == repeated and activated["binding_revision"] == binding["binding_revision"] + 1
                    and len(store.load_credential_activations(marker)) == 1
                )
                account_settings_scope = {"broker": "kiwoom", "environment": "mock", "account_ref": account_ref}
                account_settings = store.load_account_settings(account_settings_scope)
                checks["account_settings_activation_orders_off"] = (
                    account_settings["active_profile_id"] == marker and account_settings["monitor_enabled"]
                    and not account_settings["mock_order_enabled"] and account_settings["revision"] == 1
                )
                account_settings_value = {key: value for key, value in account_settings.items() if key != "revision"}
                changed_settings = store.save_account_settings(
                    {**account_settings_value, "monitor_enabled": False}, expected_revision=1,
                )
                checks["account_settings_cas"] = changed_settings["revision"] == 2
                try:
                    store.save_account_settings(account_settings_value, expected_revision=1)
                except ValueError as error:
                    checks["account_settings_stale_revision"] = str(error) == "ACCOUNT_SETTINGS_REVISION_CONFLICT"
                else:
                    checks["account_settings_stale_revision"] = False
                store.finalize_credential_activation(activation)
                checks["account_settings_replay_preserves_toggle"] = (
                    changed_settings == store.load_account_settings(account_settings_scope)
                )
                duplicate_profile = str(uuid.uuid4())
                try:
                    store.finalize_credential_activation({
                        **activation, "profile_id": duplicate_profile,
                        "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
                    })
                except ValueError as error:
                    checks["account_settings_duplicate_activation_rollback"] = (
                        str(error) == "ACCOUNT_PROFILE_CONFLICT"
                        and not store.load_credential_activations(duplicate_profile)
                        and not any(p["profile_id"] == duplicate_profile for p in store.list_credential_profiles())
                    )
                else:
                    checks["account_settings_duplicate_activation_rollback"] = False
                checks["credential_vault_linux_permissions"] = (
                    os.name == "posix" and os.stat(directory).st_mode & 0o777 == 0o700
                    and os.stat(os.path.join(directory, "master.key")).st_mode & 0o777 == 0o600
                )
                checks["credential_vault_roundtrip"] = (
                    vault.load("kiwoom_mock", marker).payload["credentials"] == credentials
                )
                profile_digest = vault.request_digest({"request_id": profile_request_id, "label": marker})
                draft = store.create_credential_profile("openai", profile_request_id, marker, profile_digest)
                draft_profile_id = draft["profile_id"]
                checks["credential_profile_create_idempotency"] = (
                    draft == store.create_credential_profile("openai", profile_request_id, marker, profile_digest)
                    and any(row["profile_id"] == draft_profile_id and row["lifecycle_state"] == "draft"
                            for row in store.list_credential_profiles())
                )
                checks["credential_activation_lookup"] = (
                    activated == store.find_credential_activation(operation_id=marker)
                    and activated == store.find_credential_activation(provider="kiwoom_mock", profile_id=marker, request_id=marker)
                )
                disabled_activation = {**activation, "operation_id": str(uuid.uuid4()),
                    "request_id": str(uuid.uuid4()), "credential_revision": 2, "disabled": True}
                bindings_before_disable = store.load_account_bindings()
                disabled_receipt = store.finalize_credential_activation(disabled_activation)
                disabled_settings = store.load_account_settings(account_settings_scope)
                checks["credential_disable_preserves_binding"] = (
                    bindings_before_disable == store.load_account_bindings()
                    and disabled_receipt["binding_revision"] == activated["binding_revision"])
                checks["credential_disable_settings_off"] = (
                    disabled_settings["active_profile_id"] is None
                    and not disabled_settings["monitor_enabled"] and not disabled_settings["mock_order_enabled"])
                checks["credential_disable_replay"] = (
                    disabled_receipt == store.finalize_credential_activation(disabled_activation)
                    and disabled_settings == store.load_account_settings(account_settings_scope))
                real_account_ref = store.register_account_identity({
                    "broker": "kiwoom", "environment": "real",
                    "identity_fingerprint": uuid.uuid4().hex * 2,
                    "created_at": observed_at.isoformat(),
                })
                real_profile, real_duplicate, real_replacement = [str(uuid.uuid4()) for _ in range(3)]
                real_profiles.extend((real_profile, real_duplicate, real_replacement))
                real_activation = {**activation, "provider": "kiwoom_real", "environment": "real",
                    "account_ref": real_account_ref, "profile_id": real_profile,
                    "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())}
                real_scope = {"broker": "kiwoom", "environment": "real", "account_ref": real_account_ref}
                real_receipt = store.finalize_credential_activation(real_activation)
                real_settings = store.load_account_settings(real_scope)
                checks["real_account_activation_claim"] = (
                    real_settings["active_profile_id"] == real_profile and real_settings["monitor_enabled"]
                    and not real_settings["mock_order_enabled"] and real_settings["revision"] == 1)
                real_value = {key: value for key, value in real_settings.items() if key != "revision"}
                checks.update(_exercise_real_recovery_transaction(store, real_profile))
                real_changed = store.save_account_settings(
                    {**real_value, "monitor_enabled": False}, expected_revision=1)
                real_rotated = store.finalize_credential_activation({**real_activation,
                    "credential_revision": 2, "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
                checks["real_account_rotation_preserves_preferences"] = (
                    real_rotated["binding_revision"] == real_receipt["binding_revision"] + 1
                    and real_changed == store.load_account_settings(real_scope))
                real_bindings = store.load_account_bindings()
                try:
                    store.finalize_credential_activation({**real_activation, "profile_id": real_duplicate,
                        "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
                except ValueError as error:
                    checks["real_account_duplicate_atomic_rollback"] = (
                        str(error) == "ACCOUNT_PROFILE_CONFLICT"
                        and real_bindings == store.load_account_bindings()
                        and real_changed == store.load_account_settings(real_scope)
                        and not store.load_credential_activations(real_duplicate)
                        and not any(p["profile_id"] == real_duplicate for p in store.list_credential_profiles()))
                else:
                    checks["real_account_duplicate_atomic_rollback"] = False
                real_disabled = {**real_activation, "disabled": True, "credential_revision": 3,
                    "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())}
                real_disabled_receipt = store.finalize_credential_activation(real_disabled)
                real_off = store.load_account_settings(real_scope)
                checks["real_account_disable_preserves_history"] = (
                    real_bindings == store.load_account_bindings()
                    and real_disabled_receipt["binding_revision"] == real_rotated["binding_revision"]
                    and real_off["active_profile_id"] is None and not real_off["monitor_enabled"]
                    and not real_off["mock_order_enabled"])
                store.finalize_credential_activation({**real_activation, "profile_id": real_replacement,
                    "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
                replacement_settings = store.load_account_settings(real_scope)
                store.finalize_credential_activation(real_activation)
                checks["real_account_replay_preserves_replacement"] = (
                    real_disabled_receipt == store.finalize_credential_activation(real_disabled)
                    and replacement_settings == store.load_account_settings(real_scope)
                    and replacement_settings["active_profile_id"] == real_replacement
                    and disabled_settings == store.load_account_settings(account_settings_scope))
                checks.update(_exercise_market_profile_transaction(store, real_replacement, 1))
            finally:
                vault.close()
        a4b_values = _a4b_content_documents(marker, account_ref, observed_at)
        a4b_statuses, mismatch_status = asyncio.run(
            _exercise_a4b_content_api(database_url, a4b_values)
        )
        loaded_a4b: dict[str, dict[str, object]] = {}
        for collection, expected in a4b_values.items():
            values = store.load_documents(collection, account_ref, 10)
            loaded_a4b[collection] = next(
                value for value in values if value.get("key") == expected["key"]
            )
        checks["a4b_http_round_trip"] = all(
            a4b_statuses.get(collection) == 200 for collection in _A4B_COLLECTIONS
        )
        checks["a4b_scope_mismatch_rejected"] = mismatch_status == 422
        checks["a4b_owner_key_scope"] = all(
            loaded_a4b[collection]["owner"] == account_ref
            and loaded_a4b[collection]["key"] == a4b_values[collection]["key"]
            and loaded_a4b[collection]["document"]["origin_account_ref"] == account_ref
            and loaded_a4b[collection]["document"]["canonical_account_ref"] == account_ref
            for collection in _A4B_COLLECTIONS
        )
        checks["a4b_source_provenance"] = all(
            loaded_a4b[collection]["document"]["source_collection"] == collection
            and loaded_a4b[collection]["document"]["source_owner"] == account_ref
            and loaded_a4b[collection]["document"]["source_key"]
            == loaded_a4b[collection]["key"]
            and bool(loaded_a4b[collection]["document"]["source_content_hash"])
            for collection in _A4B_COLLECTIONS
        )
        checks["a4b_tombstones"] = (
            loaded_a4b["journal_v2_news_links"]["document"]["is_deleted"] is True
            and loaded_a4b["journal_v2_sync_states"]["document"]["is_deleted"] is True
            and loaded_a4b["journal_v2_sync_states"]["document"]["collection"]
            == "journal_v2_group_overrides"
        )
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            raise RuntimeError(f"repository_round_trip_failed={','.join(failed)}")

        connection = store._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s)",
                ("integration_check", "postgres", rollback_marker, 0.0, json.dumps({"value": 2})),
            )
            connection.rollback()
        finally:
            connection.close()
        with store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM central_documents WHERE collection=%s AND owner=%s "
                "AND document_key=%s", ("integration_check", "postgres", rollback_marker),
            )
            if int(cursor.fetchone()[0]) != 0:
                raise RuntimeError("rollback_failed")
        print(json.dumps({
            "status": "ok", "schema_version": migrations[-1][0],
            "checks": checks, "rollback": True,
        }))
        return 0
    finally:
        try:
            with store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_documents WHERE collection=%s AND owner=%s "
                    "AND document_key IN (%s,%s)",
                    ("integration_check", "postgres", marker, rollback_marker),
                )
                cursor.execute("DELETE FROM central_api_query_cache WHERE cache_key=%s", (marker,))
                cursor.execute("DELETE FROM central_realtime_latest WHERE item_key=%s", (marker,))
                cursor.execute("DELETE FROM central_minute_bars WHERE code=%s", (marker,))
                cursor.execute(
                    "DELETE FROM central_minute_bar_operations WHERE operation_id=%s",
                    (f"{marker}-minute-delta",),
                )
                cursor.execute("DELETE FROM central_second_trade_bars WHERE code=%s", (marker,))
                cursor.execute("DELETE FROM central_daily_bars WHERE code=%s", (marker,))
                cursor.execute(
                    "DELETE FROM central_dataset_snapshots WHERE kind=%s AND subject=%s",
                    ("integration_check", marker),
                )
                cursor.execute(
                    "DELETE FROM central_dataset_snapshots WHERE kind=%s AND subject=%s",
                    ("ranking", marker),
                )
                cursor.execute(
                    "DELETE FROM central_observation_revisions WHERE subject IN (%s,%s)",
                    (marker, rollback_marker),
                )
                if export_dataset_id:
                    cursor.execute(
                        "DELETE FROM central_research_export_members WHERE dataset_id=%s",
                        (export_dataset_id,),
                    )
                    cursor.execute(
                        "DELETE FROM central_research_exports WHERE dataset_id=%s",
                        (export_dataset_id,),
                    )
                cursor.execute(
                    "DELETE FROM central_market_data_observation_meta WHERE subject=%s",
                    (marker,),
                )
                cursor.execute("DELETE FROM central_external_bars WHERE instrument=%s", (marker,))
                cursor.execute("DELETE FROM central_shadow_candidate_events WHERE monitor_id=%s", (marker,))
                cursor.execute("DELETE FROM central_shadow_decisions WHERE monitor_id=%s", (marker,))
                cursor.execute("DELETE FROM central_shadow_monitor_state WHERE monitor_id=%s", (marker,))
                cursor.execute("DELETE FROM central_execution_events WHERE intent_id=%s", (f"intent-{marker}",))
                cursor.execute("DELETE FROM central_execution_intents WHERE intent_id=%s", (f"intent-{marker}",))
                cursor.execute("DELETE FROM central_execution_account_snapshots WHERE account_ref=%s", (marker,))
                cursor.execute("DELETE FROM central_execution_runtime_leases WHERE owner_key=%s", (f"mock:{marker}",))
                cursor.execute(
                    "DELETE FROM central_account_scope_aliases WHERE origin_account_ref=%s",
                    (local_account_ref,),
                )
                cursor.execute(
                    "DELETE FROM central_account_binding_revisions WHERE credential_profile_id=%s",
                    (marker,),
                )
                cursor.execute("DELETE FROM central_credential_activations WHERE profile_id=%s", (marker,))
                cursor.execute("DELETE FROM central_credential_profiles WHERE profile_id=%s", (marker,))
                if duplicate_profile:
                    cursor.execute("DELETE FROM central_account_binding_revisions WHERE credential_profile_id=%s",
                                   (duplicate_profile,))
                    cursor.execute("DELETE FROM central_credential_activations WHERE profile_id=%s", (duplicate_profile,))
                    cursor.execute("DELETE FROM central_credential_profiles WHERE profile_id=%s", (duplicate_profile,))
                if draft_profile_id:
                    cursor.execute("DELETE FROM central_credential_profiles WHERE profile_id=%s", (draft_profile_id,))
                for real_profile in real_profiles:
                    cursor.execute("DELETE FROM central_account_binding_revisions WHERE credential_profile_id=%s",
                                   (real_profile,))
                    cursor.execute("DELETE FROM central_credential_activations WHERE profile_id=%s", (real_profile,))
                    cursor.execute("DELETE FROM central_credential_profiles WHERE profile_id=%s", (real_profile,))
                if real_account_ref:
                    cursor.execute("DELETE FROM central_documents WHERE collection='server_account_settings' AND owner=%s",
                                   (f"kiwoom:real:{real_account_ref}",))
                    cursor.execute("DELETE FROM central_account_registry WHERE account_ref=%s", (real_account_ref,))
                cursor.execute("DELETE FROM central_documents WHERE collection='credential_profile_requests' "
                               "AND owner='openai' AND document_key=%s", (profile_request_id,))
                cursor.execute("DELETE FROM central_documents WHERE collection='credential_vault_state' AND owner=%s",
                               (f"kiwoom_mock--{marker}",))
                if account_ref:
                    cursor.execute("DELETE FROM central_documents WHERE collection='server_account_settings' AND owner=%s",
                                   (f"kiwoom:mock:{account_ref}",))
                    cursor.execute(
                        "DELETE FROM central_documents WHERE owner=%s "
                        "AND collection IN (%s,%s,%s,%s)",
                        (account_ref, *_A4B_COLLECTIONS),
                    )
                    cursor.execute("DELETE FROM central_account_registry WHERE account_ref=%s", (account_ref,))
                cursor.execute("DELETE FROM central_upper_limit_fact_revisions WHERE stock_code=%s", (marker,))
                cursor.execute("DELETE FROM central_hot_cohort_revisions WHERE stock_code=%s", (marker,))
                cursor.execute("DELETE FROM central_hot_cohort_current WHERE stock_code=%s", (marker,))
                cursor.execute("DELETE FROM central_vi_event_revisions WHERE stock_code=%s", (marker,))
                cursor.execute("DELETE FROM central_news_request_budget WHERE budget_date=%s", (budget_date,))
        except Exception:
            pass
        store.close()


if __name__ == "__main__":
    sys.exit(main())
