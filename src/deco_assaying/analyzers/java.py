# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Java analyzer."""

from __future__ import annotations

from typing import Any

from deco_assaying.analyzers._base import empty_result, span, text


def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
    out = empty_result()
    state = _State(source_bytes)
    state.walk(root)
    out["module_doc"] = ""
    out["symbols"] = state.symbols
    out["imports"] = state.imports
    out["exports"] = state.exports
    out["references"] = state.references
    out["metrics"] = state.metrics
    return out


class _State:
    def __init__(self, source_bytes: bytes) -> None:
        self.src = source_bytes
        self.package = ""
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self.references: list[dict[str, Any]] = []
        self.metrics = {
            "n_functions": 0,
            "n_classes": 0,
            "max_nest_depth": 0,
            "has_main_guard": False,
            "async_count": 0,
            "generator_count": 0,
            "test_count": 0,
        }

    def walk(self, root: Any) -> None:
        for child in root.children:
            t = child.type
            if t == "package_declaration":
                ident = next((c for c in child.children if c.is_named), None)
                if ident is not None:
                    self.package = text(self.src, ident).strip()
            elif t == "import_declaration":
                self._collect_import(child)
            elif t == "class_declaration":
                self._collect_class(child, kind="class", parent_qname=self.package, depth=0)
            elif t in ("interface_declaration", "annotation_type_declaration"):
                self._collect_class(child, kind="interface", parent_qname=self.package, depth=0)
            elif t == "enum_declaration":
                self._collect_class(child, kind="enum", parent_qname=self.package, depth=0)
            elif t == "record_declaration":
                self._collect_class(child, kind="class", parent_qname=self.package, depth=0)

    def _collect_import(self, node: Any) -> None:
        is_static = any(c.type == "static" for c in node.children)
        ident = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
        if ident is None:
            return
        self.imports.append(
            {
                "module": text(self.src, ident).strip(),
                "alias": None,
                "kind": "static" if is_static else "import",
                "span": span(node),
            }
        )

    def _collect_class(self, node: Any, *, kind: str, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = self._modifiers(node)
        is_public = "public" in modifiers
        bases: list[str] = []

        sup = next((c for c in node.children if c.type == "superclass"), None)
        if sup is not None:
            for s in sup.children:
                if s.is_named:
                    base = text(self.src, s).strip()
                    bases.append(base)
                    self.references.append(
                        {
                            "name": base.split(".")[-1],
                            "qualifier": base,
                            "kind": "inherit",
                            "span": span(s),
                            "in_symbol": qname,
                        }
                    )
        impl = next((c for c in node.children if c.type == "super_interfaces"), None)
        if impl is not None:
            for s in impl.children:
                if s.type == "type_list":
                    for ti in s.children:
                        if ti.is_named:
                            base = text(self.src, ti).strip()
                            bases.append(base)
                            self.references.append(
                                {
                                    "name": base.split(".")[-1],
                                    "qualifier": base,
                                    "kind": "inherit",
                                    "span": span(ti),
                                    "in_symbol": qname,
                                }
                            )

        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"{kind} {name}" + (f" extends {bases[0]}" if bases else ""),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        if kind == "class":
            self.metrics["n_classes"] += 1
        if is_public:
            self.exports.append({"name": name, "qualified_name": qname})

        body = next(
            (c for c in node.children if c.type in ("class_body", "interface_body", "enum_body")), None
        )
        if body is not None:
            for member in body.children:
                self._handle_member(member, parent_qname=qname, depth=depth + 1)

    def _handle_member(self, node: Any, *, parent_qname: str, depth: int) -> None:
        t = node.type
        if t == "constructor_declaration":
            self._collect_method(node, parent_qname=parent_qname, kind="constructor")
        elif t == "method_declaration":
            self._collect_method(node, parent_qname=parent_qname, kind="method")
        elif t == "field_declaration":
            self._collect_field(node, parent_qname=parent_qname)
        elif t in ("class_declaration", "interface_declaration", "enum_declaration"):
            inner_kind = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "enum_declaration": "enum",
            }[t]
            self._collect_class(node, kind=inner_kind, parent_qname=parent_qname, depth=depth)

    def _collect_method(self, node: Any, *, parent_qname: str, kind: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}"
        modifiers = self._modifiers(node)
        params = next((c for c in node.children if c.type == "formal_parameters"), None)
        is_static_main = name == "main" and "static" in modifiers
        if is_static_main:
            self.metrics["has_main_guard"] = True
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": (
                    f"{' '.join(modifiers) + ' ' if modifiers else ''}{name}{text(self.src, params) if params is not None else '()'}"
                ).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        body = next((c for c in node.children if c.type in ("block", "constructor_body")), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_field(self, node: Any, *, parent_qname: str) -> None:
        decl = next((c for c in node.children if c.type == "variable_declarator"), None)
        if decl is None:
            return
        ident = next((c for c in decl.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        modifiers = self._modifiers(node)
        is_const = "static" in modifiers and "final" in modifiers
        self.symbols.append(
            {
                "kind": "constant" if is_const else "field",
                "name": name,
                "qualified_name": f"{parent_qname}.{name}",
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )

    def _modifiers(self, node: Any) -> list[str]:
        mods = next((c for c in node.children if c.type == "modifiers"), None)
        if mods is None:
            return []
        return [
            text(self.src, m).strip()
            for m in mods.children
            if m.is_named
            or m.type in ("public", "private", "protected", "static", "final", "abstract", "synchronized")
        ]

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "method_invocation":
                ident = next((c for c in n.children if c.type == "identifier"), None)
                if ident is not None:
                    name = text(self.src, ident)
                    obj = next(
                        (
                            c
                            for c in n.children
                            if c.is_named and c.type != "argument_list" and c is not ident
                        ),
                        None,
                    )
                    qualifier = f"{text(self.src, obj)}.{name}" if obj is not None else name
                    self.references.append(
                        {
                            "name": name,
                            "qualifier": qualifier,
                            "kind": "call",
                            "span": span(ident),
                            "in_symbol": in_symbol,
                        }
                    )
            if n.type in (
                "class_declaration",
                "interface_declaration",
                "method_declaration",
                "constructor_declaration",
            ):
                continue
            stack.extend(n.children)
