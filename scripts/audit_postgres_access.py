"""Static DB access inventory for design review; never imports or connects to DBs.

SQL literals and statically reachable local helpers are evidence, not a SQL parser or a
runtime transaction count. Shared PostgreSQL/SQLite helpers require manual review.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

if __package__:
    from .query_store_source import method_sources
else:
    from query_store_source import method_sources


METHODS = {"connect", "_connect", "_connection", "cursor", "execute", "executemany",
           "commit", "rollback", "close", "transaction", "pipeline", "copy"}
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|SET|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b", re.I)
TABLE = re.compile(r"\bcentral_[a-z_0-9]+\b", re.I)
DB_FILE = "src/kiwoom_monitor/central_server/database.py"
ACCESS_FILE = "src/kiwoom_monitor/central_server/postgres_access.py"
CONNECTION_APPROVALS_FILE = "docs/postgres_access_direct_connection_approvals.json"
DRIVER_CONNECTS = {"psycopg.connect", "psycopg2.connect"}


def name(node: ast.AST) -> str:
    return ast.unparse(node)


def proposal(method: str) -> tuple[str, str]:
    specific = {
        "save_query": ("rest.query_cache", "query_cache"),
        "save_dataset_snapshots": ("dataset.snapshot", "dataset:<kind>|dataset:mixed"),
        "save_dataset_snapshot": ("dataset.snapshot", "delegates_to_save_dataset_snapshots"),
        "load_top20_statistics": ("dataset.statistics_cache", "dataset:top20_statistics_day"),
        "upsert_documents": ("document.collection", "document:<collection>"),
        "replace_documents": ("document.collection", "document:<collection>"),
        "save_realtime_snapshots": ("realtime.latest", "realtime_latest"),
        "save_minute_bars": ("realtime.minute", "realtime_minute"),
        "finalize_minute_bars": ("realtime.minute_finalize", "realtime_minute_finalize"),
        "save_second_trade_bars": ("realtime.second_bar", "realtime_second_bar"),
        "replace_minute_bars": ("rest.market_bars.minute", "query_minute"),
        "replace_daily_bars": ("rest.market_bars.daily", "query_daily"),
        "claim_news_jobs": ("news.job_claim", "news_job_claim"),
        "finish_news_job": ("news.job_finish", "news_job_finish"),
        "save_news_body_revision": ("news.body", "news_body"),
        "initialize": ("schema.migration", "central_schema"),
        "_connect": ("infrastructure.connection", "connection_factory"),
        "close": ("infrastructure.connection", "no_op"),
    }
    if method in specific:
        return specific[method]
    if method.startswith(("load_", "find_", "list_", "storage_", "resolve_", "news_request_count")):
        return "repository.read_candidate", method
    if any(word in method for word in ("account", "credential", "execution", "automation")):
        return "protected.repository", method
    if "news" in method:
        return "news.repository", method
    return "repository.other", method


def audit_direct_connections(connects: list[dict], approvals: dict) -> dict:
    """Compare direct driver call counts to reviewed function-scoped exceptions."""
    current = Counter((site["file"], site["owner"], site["call"]) for site in connects)
    approved = Counter()
    for item in approvals.get("approved_sites", []):
        key = (item["file"], item["owner"], item["call"])
        approved[key] += int(item["count"])
    unapproved = []
    stale = []
    for key in sorted(current.keys() | approved.keys()):
        current_count = current[key]
        approved_count = approved[key]
        file, owner, call = key
        if current_count > approved_count:
            unapproved.append({"file": file, "owner": owner, "call": call,
                               "current_count": current_count,
                               "approved_count": approved_count})
        if approved_count > current_count:
            stale.append({"file": file, "owner": owner, "call": call,
                          "current_count": current_count,
                          "approved_count": approved_count})
    return {
        "status": "pass" if not unapproved and not stale else "review_required",
        "approved_callsite_count": sum(approved.values()),
        "current_callsite_count": sum(current.values()),
        "unapproved_sites": unapproved,
        "stale_approvals": stale,
        "scope_note": ("Matches reviewed file/function/call counts only; does not prove runtime use, "
                       "PostgreSQL ownership, or absence of dynamic/aliased access."),
    }


def inventory(root: Path, approvals_path: Path | None = None) -> dict:
    calls = []
    hashes = {}
    parse_errors = []
    sources = method_sources(root)
    physical_scopes = {
        (source.file, f"{source.class_name}.{source.node.name}"): (
            "postgres_store" if owner == "PostgresQueryStore" else "sqlite_store"
        )
        for owner in ("PostgresQueryStore", "SQLiteQueryStore")
        for source in sources[owner].values()
    }
    for subtree in ("src", "scripts", "tests"):
        for path in sorted((root / subtree).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            try:
                tree = ast.parse(data.decode("utf-8-sig"), filename=relative)
            except (SyntaxError, UnicodeError) as error:
                parse_errors.append({"file": relative, "error_type": type(error).__name__})
                continue
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            found = False
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in METHODS:
                    continue
                owner = []
                ancestor = parents.get(node)
                while ancestor:
                    if isinstance(ancestor, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner.append(ancestor.name)
                    ancestor = parents.get(ancestor)
                owner_text = ".".join(reversed(owner))
                scope = physical_scopes.get((relative, owner_text))
                if scope is None:
                    scope = ("shared_or_diagnostic_helper" if relative == DB_FILE else
                             "unclassified_test" if subtree == "tests" else
                             "unclassified_candidate")
                literal = (node.args[0].value if node.args and isinstance(node.args[0], ast.Constant)
                           and isinstance(node.args[0].value, str) else "")
                calls.append({"file": relative, "owner": owner_text, "line": node.lineno,
                              "call": name(node.func), "scope": scope,
                              "literal_sql_tokens": sorted(set(SQL.findall(literal.upper()))),
                              "literal_tables": sorted(set(TABLE.findall(literal)))})
                found = True
            if found:
                hashes[relative] = hashlib.sha256(data).hexdigest()

    methods = sources["PostgresQueryStore"]
    central_root = root / "src/kiwoom_monitor/central_server"
    local_helper_paths = [*central_root.glob("database_*.py"), central_root / "postgres_access.py"]
    trees_by_file = {source.file: source.tree for source in methods.values()}
    for path in local_helper_paths:
        relative = path.relative_to(root).as_posix()
        trees_by_file.setdefault(
            relative, ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        )
    functions_by_file = {
        file: {n.name: n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for file, tree in trees_by_file.items()
    }
    imported_helpers_by_file: dict[str, dict[str, tuple[str, str]]] = {}
    for file, tree in trees_by_file.items():
        imports: dict[str, tuple[str, str]] = {}
        for statement in tree.body:
            if not isinstance(statement, ast.ImportFrom) or not statement.module:
                continue
            module = statement.module
            if statement.level == 1:
                module_name = module
            elif statement.level == 0 and module.startswith("kiwoom_monitor.central_server."):
                module_name = module.rsplit(".", 1)[-1]
            else:
                continue
            target_file = f"src/kiwoom_monitor/central_server/{module_name}.py"
            if target_file not in functions_by_file:
                continue
            for alias in statement.names:
                if alias.name not in functions_by_file[target_file]:
                    continue
                local_name = alias.asname or alias.name
                if local_name in imports or local_name in functions_by_file[file]:
                    raise ValueError(f"ambiguous local helper {local_name} in {file}")
                imports[local_name] = (target_file, alias.name)
        imported_helpers_by_file[file] = imports
    rows = []
    reachable_helper_files: set[str] = set()
    for method, source in methods.items():
        node = source.node
        reachable: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
        pending = [(source.file, node)]
        while pending:
            file, current = pending.pop()
            if (file, current.name) in reachable:
                continue
            reachable[(file, current.name)] = current
            functions = functions_by_file[file]
            for item in ast.walk(current):
                if not isinstance(item, ast.Call):
                    continue
                if isinstance(item.func, ast.Name) and item.func.id in functions:
                    pending.append((file, functions[item.func.id]))
                elif isinstance(item.func, ast.Name) and item.func.id in imported_helpers_by_file[file]:
                    target_file, target_name = imported_helpers_by_file[file][item.func.id]
                    reachable_helper_files.add(target_file)
                    pending.append((target_file, functions_by_file[target_file][target_name]))
                elif (isinstance(item.func, ast.Attribute) and
                      isinstance(item.func.value, ast.Name) and item.func.value.id == "self" and
                      item.func.attr in methods and item.func.attr != "_connect"):
                    target = methods[item.func.attr]
                    pending.append((target.file, target.node))
        literals = [child.value for current in reachable.values() for child in ast.walk(current)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)]
        direct = [c for c in calls if c["file"] == source.file and
                  c["owner"] == f"{source.class_name}.{method}"]
        family, kind = proposal(method)
        context_expressions = [name(item.context_expr) for child in ast.walk(node)
                               if isinstance(child, ast.With) for item in child.items
                               if "connect" in name(item.context_expr) or "transaction" in name(item.context_expr)]
        rows.append({"file": source.file, "function": f"PostgresQueryStore.{method}",
                     "implementation_owner": source.class_name, "line": node.lineno,
                     "purpose": method, "proposed_family": family, "proposed_kind": kind,
                     "proposal_status": "design_label_not_runtime_registration",
                     "direct_callsite_counts_not_transaction_counts": dict(Counter(c["call"] for c in direct)),
                     "connection_context_expressions": context_expressions,
                     "connection_ownership": "per-call new connection if _connect is used; delegated helpers borrow cursor; inspect explicit lifecycle separately",
                     "reachable_local_helpers": sorted({name for (_, name) in reachable} - {method}),
                     "reachable_literal_sql_tokens": sorted({v for s in literals for v in SQL.findall(s.upper())}),
                     "reachable_literal_tables_candidates": sorted({v for s in literals for v in TABLE.findall(s)}),
                     "boundary_status": "static evidence; context exit, branches, helpers and SQL commands need manual interpretation"})
    direct_connects = [c for c in calls if c["call"] in DRIVER_CONNECTS]
    approvals_path = approvals_path or root / CONNECTION_APPROVALS_FILE
    try:
        approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        approvals = {"approved_sites": []}
    connection_guard = audit_direct_connections(direct_connects, approvals)
    pg_files = {c["file"] for c in direct_connects}
    pg_files.update(source.file for source in methods.values())
    pg_files.update(reachable_helper_files)
    pg_files.add("src/kiwoom_monitor/central_server/schema_migrations.py")
    if (root / ACCESS_FILE).exists():
        pg_files.add(ACCESS_FILE)
    selected = [c for c in calls if c["file"] in pg_files and c["scope"] != "sqlite_store"]
    remainder = Counter(c["file"] for c in calls if c not in selected)
    return {"scope": "static_only_no_database_access", "database_file": DB_FILE,
            "counts": {"postgres_store_methods": len(rows), "literal_driver_connect_sites": len(direct_connects),
                       "candidate_db_api_calls_all_backends": len(calls), "parse_errors": len(parse_errors)},
            "direct_driver_connections": direct_connects,
            "direct_connection_guard": connection_guard,
            "postgres_store_methods": rows,
            "selected_pg_and_shared_helper_calls": sorted(selected, key=lambda c: (c["file"], c["line"])),
            "other_candidate_file_counts_not_proven_postgres": dict(sorted(remainder.items())),
            "source_sha256": hashes, "parse_errors": parse_errors,
            "limits": ["Follows explicit named imports among store implementations and local database_*.py/postgres_access.py helpers; other imported aliases, reflective calls, external libraries and dynamic SQL are not resolved.",
                       "Generic connect/execute/close names include SQLite and non-DB APIs; unclassified is not PostgreSQL.",
                       "Reachable helper literals can include SQLite branches and SQL fragments; not an active table inventory.",
                       "Callsite count does not establish transaction, commit, network round trip or active writer counts.",
                       "Inventory contains no SQL parameters, connection strings or source SQL bodies."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approvals", type=Path,
                        help="reviewed direct-connection exception manifest")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when connection sites need review")
    args = parser.parse_args()
    root = args.root.resolve()
    approvals_path = args.approvals.resolve() if args.approvals else None
    result = inventory(root, approvals_path)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))
    print(json.dumps({"direct_connection_guard": result["direct_connection_guard"]},
                     ensure_ascii=False))
    if args.check and result["direct_connection_guard"]["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
