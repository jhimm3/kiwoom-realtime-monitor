"""Build the NAS daily-bar lookup index without changing the schema version.

Run on the NAS host with its existing psql client. The PostgreSQL container's
internal address is passed explicitly; credentials stay in the NAS .env file.
The index is built concurrently so normal daily-bar writes can continue.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic


INDEX_NAME = "idx_central_daily_bars_code_market_date"
INDEX_REGCLASS = f"public.{INDEX_NAME}"
TABLE_REGCLASS = "public.central_daily_bars"
CREATE_SQL = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
    "ON public.central_daily_bars (code, market, trading_date DESC)"
)
DEFAULT_ENV_FILE = Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env")
DEFAULT_DATA_DIR = Path("/volume1/docker/kiwoom-monitor/deploy/synology/postgres-data")


def _credentials(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        if key in {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"}:
            values[key] = value.strip().strip('"').strip("'")
    missing = {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"} - values.keys()
    if missing:
        raise RuntimeError(f"NAS .env에 PostgreSQL 설정 키가 없습니다: {sorted(missing)}")
    return values


def _psql(host: str, port: int, credentials: dict[str, str], sql: str, *,
          write: bool = False, timeout: int = 15) -> str:
    environment = dict(os.environ)
    environment.update({
        "PGPASSWORD": credentials["POSTGRES_PASSWORD"],
        "PGCONNECT_TIMEOUT": "5",
        "PGOPTIONS": (
            "-c lock_timeout=5s -c statement_timeout=15min"
            if write else
            "-c default_transaction_read_only=on -c lock_timeout=1s -c statement_timeout=3s"
        ),
    })
    result = subprocess.run(
        ["psql", "-X", "-w", "-A", "-t", "-v", "ON_ERROR_STOP=1",
         "-h", host, "-p", str(port), "-U", credentials["POSTGRES_USER"],
         "-d", credentials["POSTGRES_DB"], "-c", sql],
        env=environment, capture_output=True, text=True, timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"psql 실패 (exit={result.returncode}): {result.stderr.strip()[-500:]}")
    return result.stdout.strip()


def _catalog(host: str, port: int, credentials: dict[str, str]) -> dict[str, object]:
    result = _psql(host, port, credentials, f"""SELECT json_build_object(
        'database', current_database(),
        'table', c.oid::regclass::text,
        'estimated_rows', c.reltuples,
        'heap_bytes', pg_relation_size(c.oid),
        'index', (
            SELECT json_build_object(
                'valid', i.indisvalid, 'ready', i.indisready,
                'definition', pg_get_indexdef(i.indexrelid),
                'size_bytes', pg_relation_size(i.indexrelid))
            FROM pg_index i WHERE i.indexrelid=to_regclass('{INDEX_REGCLASS}')
        )) FROM pg_class c WHERE c.oid=to_regclass('{TABLE_REGCLASS}')""")
    if not result:
        raise RuntimeError(f"{TABLE_REGCLASS} 테이블을 찾지 못했습니다.")
    return json.loads(result)


def _expected_index(index: dict[str, object]) -> bool:
    definition = " ".join(str(index.get("definition", "")).lower().replace('"', "").split())
    return (
        f"create index {INDEX_NAME} on public.central_daily_bars using btree " in definition
        and "(code, market, trading_date desc)" in definition
        and " where " not in definition
    )


def _check_index(catalog: dict[str, object]) -> bool:
    index = catalog.get("index")
    if index is None:
        return False
    if not isinstance(index, dict) or not index.get("valid") or not index.get("ready"):
        raise RuntimeError("동일 이름의 미완성 인덱스가 있습니다. 자동으로 삭제하거나 재생성하지 않습니다.")
    if not _expected_index(index):
        raise RuntimeError("동일 이름의 다른 인덱스가 있습니다. 자동으로 변경하지 않습니다.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="NAS 호스트에서 접속할 PostgreSQL 내부 주소")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--apply", action="store_true", help="온라인 인덱스 생성을 실행")
    args = parser.parse_args(argv)
    credentials = _credentials(args.env_file)
    catalog = _catalog(args.host, args.port, credentials)
    ready = _check_index(catalog)
    if catalog["database"] != credentials["POSTGRES_DB"]:
        raise RuntimeError("예상한 PostgreSQL DB가 아닙니다.")
    data_dir = args.data_dir.resolve(strict=True)
    free_bytes = os.statvfs(data_dir).f_bavail * os.statvfs(data_dir).f_frsize
    print(json.dumps({
        "state": "ready" if ready else "missing", "database": catalog["database"],
        "table": catalog["table"], "index": INDEX_NAME,
        "estimated_rows": catalog["estimated_rows"],
        "heap_bytes": catalog["heap_bytes"], "free_bytes": free_bytes,
        "index_state": catalog["index"],
    }, ensure_ascii=False), flush=True)
    if not args.apply or ready:
        return 0
    if free_bytes < max(1_000_000_000, 2 * int(catalog["heap_bytes"])):
        raise RuntimeError("인덱스 생성 전 사용 가능한 저장 공간이 부족합니다.")

    import fcntl

    started = monotonic()
    with open("/tmp/kiwoom-daily-bar-index.lock", "a+b") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("같은 인덱스 생성 작업이 이미 실행 중입니다.") from error
        catalog = _catalog(args.host, args.port, credentials)
        if _check_index(catalog):
            print(json.dumps({"state": "already_ready", "index": INDEX_NAME}))
            return 0
        print(json.dumps({"state": "building", "index": INDEX_NAME}), flush=True)
        try:
            _psql(args.host, args.port, credentials, CREATE_SQL, write=True, timeout=930)
        except Exception:
            try:
                state = _catalog(args.host, args.port, credentials)["index"]
                print(json.dumps({"state": "build_failed", "index_state": state}), file=sys.stderr)
            except Exception:
                pass
            raise
        catalog = _catalog(args.host, args.port, credentials)
        if not _check_index(catalog):
            raise RuntimeError("생성 후 인덱스를 찾지 못했습니다.")
        print(json.dumps({
            "state": "created", "index": INDEX_NAME,
            "size_bytes": catalog["index"]["size_bytes"],
            "elapsed_seconds": round(monotonic() - started, 3),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
