"""Read-only observation revision queries for the store backends."""
from __future__ import annotations

from typing import Any

from kiwoom_monitor.central_server.database_codec import (
    _observation_revision_columns,
    bounded_limit,
    observation_revision_result_rows,
)


def _observation_revision_select(placeholder: str, *, postgres: bool = False) -> str:
    return (
        f"SELECT {_observation_revision_columns(postgres=postgres)} "
        f"FROM central_observation_revisions WHERE kind={placeholder}"
    )


class SQLiteObservationReaderStoreMixin:
    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = _observation_revision_select("?")
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return observation_revision_result_rows(rows)


    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        normalized = tuple(dict.fromkeys(str(value) for value in kinds if str(value)))
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns()} FROM central_observation_revisions "
            f"WHERE accepted_sequence>? AND kind IN ({placeholders}) "
            "ORDER BY accepted_sequence LIMIT ?"
        )
        parameters = [max(0, int(after_sequence)), *normalized, bounded_limit(limit, 5000)]
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return observation_revision_result_rows(rows)





class PostgresObservationReaderStoreMixin:
    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = _observation_revision_select("%s", postgres=True)
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        context = DBWriterContext(
            writer_family="read.observation_revisions",
            writer_kind="observation_revision",
            operation="load_observation_revisions",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return observation_revision_result_rows(rows)


    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]:
        normalized = tuple(dict.fromkeys(str(value) for value in kinds if str(value)))
        if not normalized:
            return []
        from .postgres_access import DBWriterContext, open_observed_connection

        placeholders = ",".join("%s" for _ in normalized)
        sql = (
            f"SELECT {_observation_revision_columns(postgres=True)} "
            "FROM central_observation_revisions WHERE accepted_sequence>%s "
            f"AND kind IN ({placeholders}) ORDER BY accepted_sequence LIMIT %s"
        )
        parameters = [max(0, int(after_sequence)), *normalized, bounded_limit(limit, 5000)]
        context = DBWriterContext(
            writer_family="read.observation_revisions",
            writer_kind="observation_revisions_after",
            operation="load_observation_revisions_after",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return observation_revision_result_rows(rows)
