# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Ruby analyzer."""

from __future__ import annotations

from typing import Any

from deco_assaying.analyzers._base import empty_result, span, text


def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
    out = empty_result()
    state = _State(source_bytes)
    state.walk(root, parent_qname="", depth=0)
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

    def walk(self, node: Any, *, parent_qname: str, depth: int) -> None:
        for child in node.children:
            self._handle(child, parent_qname=parent_qname, depth=depth)

    def _handle(self, node: Any, *, parent_qname: str, depth: int) -> None:
        t = node.type
        if t == "call":
            self._maybe_require(node)
            self._collect_call_refs(node, in_symbol=parent_qname or "<module>")
        elif t == "module":
            self._collect_module(node, parent_qname=parent_qname, depth=depth)
        elif t == "class":
            self._collect_class(node, parent_qname=parent_qname, depth=depth)
        elif t == "method":
            self._collect_method(node, parent_qname=parent_qname, kind="method")
        elif t == "singleton_method":
            self._collect_method(node, parent_qname=parent_qname, kind="method", is_singleton=True)
        elif t == "assignment":
            self._collect_assignment(node, parent_qname=parent_qname)
        elif t == "body_statement":
            self.walk(node, parent_qname=parent_qname, depth=depth)

    def _maybe_require(self, node: Any) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        if name not in ("require", "require_relative", "load", "autoload"):
            return
        args = next((c for c in node.children if c.type == "argument_list"), None)
        if args is None:
            return
        for a in args.children:
            if a.type == "string":
                module = _string_content(self.src, a)
                if module:
                    self.imports.append(
                        {
                            "module": module,
                            "alias": None,
                            "kind": "from" if name == "require_relative" else "import",
                            "span": span(a),
                        }
                    )

    def _collect_module(self, node: Any, *, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "constant"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "module",
                "name": name,
                "qualified_name": qname,
                "signature": f"module {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        body = next((c for c in node.children if c.type == "body_statement"), None)
        if body is not None:
            self.walk(body, parent_qname=qname, depth=depth + 1)

    def _collect_class(self, node: Any, *, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "constant"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        bases: list[str] = []
        sup = next((c for c in node.children if c.type == "superclass"), None)
        if sup is not None:
            for s in sup.children:
                if s.is_named:
                    base = text(self.src, s).strip()
                    bases.append(base)
                    self.references.append(
                        {
                            "name": base.split("::")[-1],
                            "qualifier": base,
                            "kind": "inherit",
                            "span": span(s),
                            "in_symbol": qname,
                        }
                    )
        self.symbols.append(
            {
                "kind": "class",
                "name": name,
                "qualified_name": qname,
                "signature": f"class {name}" + (f" < {bases[0]}" if bases else ""),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_classes"] += 1
        body = next((c for c in node.children if c.type == "body_statement"), None)
        if body is not None:
            self.walk(body, parent_qname=qname, depth=depth + 1)

    def _collect_method(self, node: Any, *, parent_qname: str, kind: str, is_singleton: bool = False) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        symbol_kind = "constructor" if name == "initialize" else kind
        self.symbols.append(
            {
                "kind": symbol_kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"def {name}",
                "span": span(node),
                "doc": "",
                "modifiers": ["singleton"] if is_singleton else [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        if name.startswith("test_"):
            self.metrics["test_count"] += 1
        body = next((c for c in node.children if c.type == "body_statement"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_assignment(self, node: Any, *, parent_qname: str) -> None:
        target = node.children[0] if node.children else None
        if target is None or target.type != "constant":
            return
        name = text(self.src, target)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "constant",
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call":
                ident = next((c for c in n.children if c.type == "identifier"), None)
                if ident is not None:
                    name = text(self.src, ident)
                    self.references.append(
                        {
                            "name": name,
                            "qualifier": name,
                            "kind": "call",
                            "span": span(ident),
                            "in_symbol": in_symbol,
                        }
                    )
            if n.type in ("class", "module", "method", "singleton_method"):
                continue
            stack.extend(n.children)


def _string_content(source_bytes: bytes, string_node: Any) -> str:
    parts: list[str] = []
    for c in string_node.children:
        if c.type == "string_content":
            parts.append(source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace"))
    if parts:
        return "".join(parts)
    raw = source_bytes[string_node.start_byte : string_node.end_byte].decode("utf-8", errors="replace")
    if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw
