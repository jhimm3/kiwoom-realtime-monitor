"""Read-only source inventory for Kiwoom ingress and database write boundaries.

This reports evidence and unresolved dynamic calls. It does not infer that a
declared broker API is actually called or that a SQL write is a separate commit.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

if __package__:
    from .query_store_source import method_sources
else:
    from query_store_source import method_sources


API_ID = re.compile(r"^(?:ka|kt)\d{5}$")
SQL_WRITE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+(central_[a-z_]+)", re.I)
SQL_TABLE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z_0-9]*)", re.I)
REQUEST_NAMES = {"request", "request_unrecorded", "_request_with_retries", "request_with_continuation"}


def _call_name(node: ast.expr) -> str:
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def inventory(root: Path) -> dict[str, object]:
    source = root / "src" / "kiwoom_monitor"
    literal_calls: list[dict[str, object]] = []
    dynamic_calls: list[dict[str, object]] = []
    mentioned_api_ids: dict[str, set[str]] = {}
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (SyntaxError, UnicodeError) as error:
            dynamic_calls.append({"file": relative, "line": 0, "callee": "parse_error", "detail": str(error)})
            continue
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and API_ID.fullmatch(node.value):
                mentioned_api_ids.setdefault(node.value, set()).add(relative)
            if not isinstance(node, ast.Call) or _call_name(node.func) not in REQUEST_NAMES:
                continue
            name = _call_name(node.func)
            first = _literal(node.args[0]) if node.args else None
            owner_parts: list[str] = []
            ancestor = parents.get(node)
            while ancestor is not None:
                if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    owner_parts.append(ancestor.name)
                ancestor = parents.get(ancestor)
            evidence = {"file": relative, "line": node.lineno, "callee": name,
                        "owner": ".".join(reversed(owner_parts))}
            if first and API_ID.fullmatch(first):
                literal_calls.append({**evidence, "api_id": first})
            elif node.args:
                dynamic_calls.append({**evidence, "expression": ast.unparse(node.args[0])[:120]})

    broker_file = source / "central_server" / "rest_broker.py"
    broker_tree = ast.parse(broker_file.read_text(encoding="utf-8"))
    declared: set[str] = set()
    for node in ast.walk(broker_tree):
        if isinstance(node, ast.Dict):
            declared.update(value for key in node.keys if (value := _literal(key)) and API_ID.fullmatch(value))

    realtime_file = source / "central_server" / "realtime_collector.py"
    realtime_tree = ast.parse(realtime_file.read_text(encoding="utf-8"))
    ws_types: set[str] = set()
    for node in ast.walk(realtime_tree):
        if isinstance(node, ast.Dict) and any(_literal(key) == "trnm" and _literal(value) == "REG"
                                              for key, value in zip(node.keys, node.values)):
            for child in ast.walk(node):
                if isinstance(child, ast.Dict):
                    for key, value in zip(child.keys, child.values):
                        if _literal(key) == "type" and isinstance(value, (ast.List, ast.Tuple)):
                            ws_types.update(item for element in value.elts if (item := _literal(element)))
    # Dynamic REG groups are constructed before the REG payload, so collect
    # type lists from the registration method body as well.
    for node in ast.walk(realtime_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_send_subscription":
            for child in ast.walk(node):
                if isinstance(child, ast.Dict):
                    for key, value in zip(child.keys, child.values):
                        if _literal(key) == "type" and isinstance(value, (ast.List, ast.Tuple)):
                            ws_types.update(item for element in value.elts if (item := _literal(element)))

    postgres_methods: list[dict[str, object]] = []
    for method, implementation in method_sources(root)["PostgresQueryStore"].items():
        node = implementation.node
        tables = sorted({table.lower() for child in ast.walk(node)
                         if isinstance(child, ast.Constant) and isinstance(child.value, str)
                         for table in SQL_WRITE.findall(child.value)})
        if tables:
            postgres_methods.append({"method": method, "file": implementation.file,
                                     "line": node.lineno, "tables_in_literal_sql": tables,
                                     "note": "helper SQL and transaction count require manual review"})

    sqlite_tables: set[str] = set()
    for path in (source / "infrastructure" / "persistence").rglob("*.py"):
        sqlite_tables.update(match.lower() for match in SQL_TABLE.findall(path.read_text(encoding="utf-8")))
    return {
        "scope": "static_source_evidence_only", "source_root": source.relative_to(root).as_posix(),
        "literal_rest_calls": sorted(literal_calls, key=lambda row: (row["api_id"], row["file"], row["line"])),
        "all_api_id_literals_in_source_not_proof_of_active_use": [
            {"api_id": api_id, "files": sorted(files)} for api_id, files in sorted(mentioned_api_ids.items())
        ],
        "dynamic_request_calls_to_review": sorted(dynamic_calls, key=lambda row: (row["file"], row["line"])),
        "declared_broker_api_ids_not_proof_of_active_use": sorted(declared),
        "registered_realtime_types_from_literal_lists": sorted(ws_types),
        "postgres_methods_with_literal_write_sql": postgres_methods,
        "pc_sqlite_declared_tables": sorted(sqlite_tables),
        "limits": ["dynamic API IDs, helper SQL, direct HTTP, generated SQL and transaction counts need manual review",
                   "no operational database is queried or modified"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(inventory(args.root.resolve()), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)


if __name__ == "__main__":
    main()
