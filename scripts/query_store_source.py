"""Resolve QueryStore implementations from local source without importing the app.

Only direct, statically imported database_* mixins are supported. Unknown bases,
overrides and extra inheritance depth fail closed so an audit cannot silently
lose a moved method.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


DATABASE_FILE = "src/kiwoom_monitor/central_server/database.py"
_STORE_CLASSES = ("QueryStore", "SQLiteQueryStore", "PostgresQueryStore")
_METHOD_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class MethodSource:
    logical_owner: str
    file: str
    class_name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    tree: ast.Module


def _single_class(tree: ast.Module, class_name: str, file: str) -> ast.ClassDef:
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == class_name]
    if len(matches) != 1:
        raise ValueError(f"expected one {class_name} class in {file}")
    return matches[0]


def _direct_methods(cls: ast.ClassDef, owner: str, file: str,
                    tree: ast.Module) -> dict[str, MethodSource]:
    methods: dict[str, MethodSource] = {}
    for node in cls.body:
        if not isinstance(node, _METHOD_NODES):
            continue
        if node.name in methods:
            raise ValueError(f"duplicate {owner}.{node.name} in {file}")
        methods[node.name] = MethodSource(owner, file, cls.name, node, tree)
    return methods


def _local_mixin_file(root: Path, tree: ast.Module, base_name: str) -> tuple[str, str]:
    imports: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        module = node.module
        if node.level == 1:
            module_name = module
        elif node.level == 0 and module.startswith("kiwoom_monitor.central_server."):
            module_name = module.rsplit(".", 1)[-1]
        else:
            continue
        if not module_name.startswith("database_") or "." in module_name:
            continue
        for item in node.names:
            if (item.asname or item.name) == base_name:
                imports.append((module_name, item.name))
    if len(imports) != 1:
        raise ValueError(f"unresolved or ambiguous local database mixin {base_name}")
    module_name, imported_name = imports[0]
    relative = f"src/kiwoom_monitor/central_server/{module_name}.py"
    if not (root / relative).is_file():
        raise ValueError(f"missing local database mixin module {relative}")
    return relative, imported_name


def _query_store_methods(root: Path, tree: ast.Module,
                         query_store: ast.ClassDef) -> dict[str, MethodSource]:
    """Collect direct aggregate methods and imported, direct Protocol leaves."""
    methods = _direct_methods(query_store, "QueryStore", DATABASE_FILE, tree)
    for base in query_store.bases:
        if not isinstance(base, ast.Name):
            raise ValueError("dynamic base for QueryStore")
        if base.id == "Protocol":
            continue
        relative, imported_name = _local_mixin_file(root, tree, base.id)
        leaf_tree = ast.parse((root / relative).read_text(encoding="utf-8-sig"),
                              filename=relative)
        leaf = _single_class(leaf_tree, imported_name, relative)
        if (len(leaf.bases) != 1 or not isinstance(leaf.bases[0], ast.Name)
                or leaf.bases[0].id != "Protocol"):
            raise ValueError(f"nested Protocol inheritance is not audited: {relative}:{imported_name}")
        for name, method in _direct_methods(leaf, "QueryStore", relative, leaf_tree).items():
            if name in methods:
                raise ValueError(f"duplicate QueryStore protocol method {name}")
            methods[name] = method
    return methods


def method_sources(root: Path) -> dict[str, dict[str, MethodSource]]:
    """Map logical store methods to the physical AST nodes that implement them."""
    root = root.resolve()
    source = root / DATABASE_FILE
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=DATABASE_FILE)
    result: dict[str, dict[str, MethodSource]] = {}
    for owner in _STORE_CLASSES:
        cls = _single_class(tree, owner, DATABASE_FILE)
        if owner == "QueryStore":
            result[owner] = _query_store_methods(root, tree, cls)
            continue
        methods = _direct_methods(cls, owner, DATABASE_FILE, tree)
        for base in cls.bases:
            if not isinstance(base, ast.Name):
                raise ValueError(f"dynamic base for {owner}")
            if base.id in {"QueryStore", "object"}:
                continue  # Protocol stubs do not implement backend methods.
            relative, imported_name = _local_mixin_file(root, tree, base.id)
            mixin_tree = ast.parse((root / relative).read_text(encoding="utf-8-sig"),
                                   filename=relative)
            mixin = _single_class(mixin_tree, imported_name, relative)
            if mixin.bases and not all(isinstance(value, ast.Name) and value.id == "object"
                                       for value in mixin.bases):
                raise ValueError(f"nested inheritance is not audited: {relative}:{imported_name}")
            for name, implementation in _direct_methods(mixin, owner, relative,
                                                         mixin_tree).items():
                if name == "__init__" or name in methods:
                    raise ValueError(f"mixin constructor or override is not audited: {owner}.{name}")
                methods[name] = implementation
        result[owner] = methods
    return result
