from __future__ import annotations

import unittest
import uuid
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.trade_group_edit_service import TradeGroupEditService
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, trade_fill_key
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.central_journal_sync import CentralJournalSyncService
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class FakeRepository:
    def __init__(self) -> None:
        self.assigned: list[tuple[tuple[str, ...], str, AccountScope, AccountScope | None]] = []
        self.cleared: list[tuple[str, ...]] = []

    def assign_group(
        self, fill_keys: tuple[str, ...], group_id: str, *,
        account_scope: AccountScope, canonical_scope: AccountScope | None = None,
    ) -> None:
        self.assigned.append((fill_keys, group_id, account_scope, canonical_scope))

    def clear_group_assignments(self, fill_keys: tuple[str, ...], **kwargs) -> None:
        self.cleared.append(fill_keys)


def fill(order: str, code: str, side: str, at: datetime) -> TradeFill:
    return TradeFill(order, code, code, side, at, 1, 100)


class TradeGroupEditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = TradeGroupEditService(self.repository, lambda: "manual:fixed")
        self.at = datetime(2026, 9, 10, 9)

    def test_merge_validates_selection_and_stock_before_writing(self) -> None:
        first = group_trade_episodes((fill("1", "A", "매수", self.at),))[0]
        second = group_trade_episodes((fill("2", "A", "매수", self.at + timedelta(days=1)),))[0]
        other = group_trade_episodes((fill("3", "B", "매수", self.at),))[0]

        self.assertFalse(self.service.merge((first,)).changed)
        self.assertFalse(self.service.merge((first, other)).changed)
        result = self.service.merge((first, second))

        self.assertTrue(result.changed)
        self.assertEqual("매매 묶음 2개를 합쳤습니다.", result.message)
        self.assertEqual("manual:fixed", self.repository.assigned[0][1])
        self.assertEqual(
            (trade_fill_key(first.fills[0]), trade_fill_key(second.fills[0])),
            self.repository.assigned[0][0],
        )
        self.assertEqual((first.fills[0].origin_scope, None), self.repository.assigned[0][2:])

    def test_split_validates_selection_and_stock_before_writing(self) -> None:
        first = fill("1", "A", "매수", self.at)
        second = fill("2", "A", "매도", self.at + timedelta(minutes=1))
        other = fill("3", "B", "매수", self.at)

        self.assertFalse(self.service.split(()).changed)
        self.assertFalse(self.service.split((first, other)).changed)
        result = self.service.split((first, second))

        self.assertTrue(result.changed)
        self.assertEqual("선택 체결 2건을 새 묶음으로 분리했습니다.", result.message)
        self.assertEqual("manual:fixed", self.repository.assigned[0][1])
        self.assertEqual((first.origin_scope, None), self.repository.assigned[0][2:])

    def test_reset_only_clears_selected_episode_fill_keys(self) -> None:
        episode = group_trade_episodes((
            fill("1", "A", "매수", self.at),
            fill("2", "A", "매도", self.at + timedelta(minutes=1)),
        ))[0]

        self.assertFalse(self.service.reset(()).changed)
        result = self.service.reset((episode,))

        self.assertTrue(result.changed)
        self.assertEqual("선택 묶음을 자동분류로 되돌렸습니다.", result.message)
        self.assertEqual(tuple(trade_fill_key(value) for value in episode.fills), self.repository.cleared[0])

    def test_merge_and_split_reject_different_account_scopes(self) -> None:
        real = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        mock = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        real_fill = TradeFill("1", "A", "A", "매수", self.at, 1, 100, origin_scope=real)
        mock_fill = TradeFill(
            "2", "A", "A", "매수", self.at + timedelta(days=1), 1, 100,
            origin_scope=mock,
        )
        episodes = (
            group_trade_episodes((real_fill,))[0],
            group_trade_episodes((mock_fill,))[0],
        )

        self.assertFalse(self.service.merge(episodes).changed)
        self.assertFalse(self.service.split((real_fill, mock_fill)).changed)
        self.assertEqual([], self.repository.assigned)

    def test_split_passes_origin_and_canonical_scopes_explicitly(self) -> None:
        origin = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        canonical = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        values = (
            TradeFill("1", "A", "A", "매수", self.at, 1, 100,
                      origin_scope=origin, canonical_scope=canonical),
            TradeFill("2", "A", "A", "매도", self.at + timedelta(minutes=1), 1, 110,
                      origin_scope=origin, canonical_scope=canonical),
        )

        self.assertTrue(self.service.split(values).changed)
        self.assertEqual((origin, canonical), self.repository.assigned[0][2:])

    def test_merge_rejects_distinct_origins_even_with_same_canonical_scope(self) -> None:
        canonical = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        first = TradeFill(
            "1", "A", "A", "매수", self.at, 1, 100,
            origin_scope=AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4())),
            canonical_scope=canonical,
        )
        second = TradeFill(
            "2", "A", "A", "매수", self.at + timedelta(days=1), 1, 100,
            origin_scope=AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4())),
            canonical_scope=canonical,
        )

        self.assertFalse(self.service.merge((
            group_trade_episodes((first,))[0], group_trade_episodes((second,))[0],
        )).changed)
        self.assertEqual([], self.repository.assigned)

    def test_real_and_mock_group_edits_keep_database_and_export_scopes(self) -> None:
        class V2Client:
            def __init__(self) -> None:
                self.remote: dict[str, list[dict[str, object]]] = {}

            def capabilities(self) -> dict[str, bool]:
                return {"journal_v2_sync": True}

            def load_all(self, collection: str) -> list[dict[str, object]]:
                return list(self.remote.get(collection, ()))

            def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
                self.remote.setdefault(collection, []).extend(documents)
                return len(documents)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "journal.sqlite3"
            repository = JournalRepository(path)
            editor = TradeGroupEditService(repository, lambda: "manual:same")
            real = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
            mock = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
            real_fill = TradeFill("1", "A", "A", "매수", self.at, 1, 100, origin_scope=real)
            mock_fill = TradeFill("1", "A", "A", "매수", self.at, 1, 100, origin_scope=mock)
            repository.upsert_fills((real_fill,), account_scope=real)
            repository.upsert_fills((mock_fill,), account_scope=mock)
            # 0a 이전 UI 경로가 만든 v2 fill key/legacy scope 행도 다음 수동 편집에서 복구한다.
            repository.assign_group((trade_fill_key(real_fill),), "broken:legacy")

            self.assertTrue(editor.split((real_fill,)).changed)
            self.assertTrue(editor.split((mock_fill,)).changed)

            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute(
                    "SELECT group_id,origin_environment,origin_account_ref,canonical_account_ref "
                    "FROM trade_group_overrides ORDER BY origin_environment"
                ).fetchall()
            self.assertEqual(
                {
                    ("manual:same", "real", real.account_ref, real.account_ref),
                    ("manual:same", "mock", mock.account_ref, mock.account_ref),
                },
                set(rows),
            )

            client = V2Client()
            CentralJournalSyncService(client).sync(path)  # type: ignore[arg-type]
            exported = {
                (
                    value["document"]["origin_environment"],
                    value["document"]["origin_account_ref"],
                    value["document"]["canonical_account_ref"],
                )
                for value in client.remote["journal_v2_group_overrides"]
            }
            self.assertEqual(
                {("real", real.account_ref, real.account_ref),
                 ("mock", mock.account_ref, mock.account_ref)},
                exported,
            )


if __name__ == "__main__":
    unittest.main()
