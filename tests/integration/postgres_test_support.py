"""An owned empty schema for tests of database-wide observation cursors."""

from contextlib import contextmanager
from urllib.parse import urlsplit
from uuid import uuid4


@contextmanager
def isolated_observation_schema(database_url):
    import psycopg
    from psycopg import conninfo, sql

    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.path != "/kiwoom_monitor_diagnostic_test":
        raise RuntimeError("observation fixture requires the dedicated diagnostic database")
    schema = "observation_test_" + uuid4().hex
    parameters = conninfo.conninfo_to_dict(database_url)
    options = parameters.get("options", "") + f" -c search_path={schema}"
    scoped_url = conninfo.make_conninfo(database_url, options=options.strip())
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as admin:
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != "kiwoom_monitor_diagnostic_test":
                raise RuntimeError("observation fixture connected to a non-diagnostic database")
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield scoped_url
        finally:
            # Only this fixture's generated schema; never public or another suite's rows.
            with admin.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
