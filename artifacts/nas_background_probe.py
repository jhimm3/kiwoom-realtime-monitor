"""Bounded read-only NAS PostgreSQL background activity probe.

Run via SSH stdin on the NAS host. Credentials are read locally and never printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


env_path = Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env")
values = {}
for raw in env_path.read_text().splitlines():
    raw = raw.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    values[key.strip()] = value.strip().strip('"\'')

process_env = os.environ.copy()
process_env["PGPASSWORD"] = values["POSTGRES_PASSWORD"]
process_env["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=2000"
query = (
    "SELECT backend_type,COALESCE(state,''),COALESCE(wait_event_type,''),"
    "COALESCE(wait_event,''),count(*) FROM pg_stat_activity "
    "WHERE backend_type IN ('walwriter','checkpointer','background writer',"
    "'autovacuum worker','client backend') "
    "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"
)
command = ["psql", "-X", "-A", "-t", "-F", "|", "-h", "172.18.0.2", "-p", "5432",
           "-U", values["POSTGRES_USER"], "-d", values["POSTGRES_DB"], "-c", query]
def snapshot() -> dict:
    result = subprocess.run(command, env=process_env, capture_output=True, text=True, timeout=8)
    if result.returncode:
        return {"available": False, "error_type": "psql_connection_failed",
                "exit_code": result.returncode}
    rows = []
    for line in result.stdout.splitlines():
        cells = line.split("|")
        if len(cells) == 5:
            rows.append({"backend_type": cells[0], "state": cells[1],
                         "wait_type": cells[2], "wait_event": cells[3],
                         "count": int(cells[4])})
    return {"available": True, "rows": rows}


if len(sys.argv) == 1:
    print(json.dumps(snapshot()))
else:
    seconds = int(sys.argv[1])
    if not 1 <= seconds <= 60:
        raise ValueError("sample duration must be 1..60 seconds")
    deadline = time.monotonic() + seconds
    samples = []
    while time.monotonic() < deadline:
        started = time.time()
        result = snapshot()
        if not result["available"]:
            print(json.dumps(result))
            break
        background = [row for row in result["rows"] if row["backend_type"] != "client backend"]
        active = [row for row in result["rows"] if row["backend_type"] == "client backend"
                  and row["state"] == "active"]
        samples.append({"at": round(started, 3),
                        "background": {f"{row['backend_type']}:{row['wait_type']}:{row['wait_event']}": row["count"]
                                       for row in background},
                        "active_client_count": sum(row["count"] for row in active),
                        "active_client_waits": dict(Counter({
                            f"{row['wait_type'] or 'CPU'}:{row['wait_event'] or '-'}": row["count"]
                            for row in active}))})
        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
    print(json.dumps({"available": True, "samples": samples}))
