"""PHP analyzer."""

from __future__ import annotations

from typing import Any

from parkview_codeparse.analyzers._base import empty_result, span, text


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
        self.namespace = ""
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
            if t == "namespace_definition":
                ident = next((c for c in child.children if c.type == "namespace_name"), None)
                if ident is not None:
                    self.namespace = text(self.src, ident).strip()
            elif t == "namespace_use_declaration":
                self._collect_use(child)
            elif t == "class_declaration":
                self._collect_class(child, kind="class")
            elif t == "interface_declaration":
                self._collect_class(child, kind="interface")
            elif t == "trait_declaration":
                self._collect_class(child, kind="class")
            elif t == "enum_declaration":
                self._collect_class(child, kind="enum")
            elif t == "function_definition":
                self._collect_function(child, parent_qname=self.namespace)
            elif t == "const_declaration":
                self._collect_const(child)

    def _collect_use(self, node: Any) -> None:
        for clause in node.children:
            if clause.type != "namespace_use_clause":
                continue
            qname_node = next((c for c in clause.children if c.type == "qualified_name"), None)
            alias_node = next((c for c in clause.children if c.type == "name"), None)
            if qname_node is None:
                continue
            module = text(self.src, qname_node).strip()
            alias = text(self.src, alias_node).strip() if alias_node is not None else None
            self.imports.append(
                {
                    "module": module,
                    "alias": alias,
                    "kind": "import",
                    "span": span(clause),
                }
            )

    def _collect_class(self, node: Any, *, kind: str) -> None:
        name_node = next((c for c in node.children if c.type == "name"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{self.namespace}.{name}" if self.namespace else name
        bases: list[str] = []
        base = next((c for c in node.children if c.type == "base_clause"), None)
        if base is not None:
            for b in base.children:
                if b.is_named:
                    base_text = text(self.src, b).strip()
                    bases.append(base_text)
                    self.references.append(
                        {
                            "name": base_text.split("\\")[-1],
                            "qualifier": base_text,
                            "kind": "inherit",
                            "span": span(b),
                            "in_symbol": qname,
                        }
                    )
        impls = next((c for c in node.children if c.type == "class_interface_clause"), None)
        if impls is not None:
            for b in impls.children:
                if b.is_named:
                    base_text = text(self.src, b).strip()
                    bases.append(base_text)
                    self.references.append(
                        {
                            "name": base_text.split("\\")[-1],
                            "qualifier": base_text,
                            "kind": "inherit",
                            "span": span(b),
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
                "modifiers": [],
                "parent_qname": self.namespace,
            }
        )
        if kind == "class":
            self.metrics["n_classes"] += 1
        body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body is not None:
            for member in body.children:
                self._handle_member(member, parent_qname=qname)

    def _handle_member(self, node: Any, *, parent_qname: str) -> None:
        t = node.type
        if t == "method_declaration":
            self._collect_function(node, parent_qname=parent_qname, is_method=True)
        elif t == "property_declaration":
            self._collect_property(node, parent_qname=parent_qname)
        elif t == "const_declaration":
            self._collect_const(node, parent_qname=parent_qname)

    def _collect_function(self, node: Any, *, parent_qname: str, is_method: bool = False) -> None:
        name_node = next((c for c in node.children if c.type == "name"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        kind = "constructor" if name == "__construct" else ("method" if is_method else "function")
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"function {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        body = next((c for c in node.children if c.type == "compound_statement"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_property(self, node: Any, *, parent_qname: str) -> None:
        elem = next((c for c in node.children if c.type == "property_element"), None)
        if elem is None:
            return
        var = next((c for c in elem.children if c.type == "variable_name"), None)
        if var is None:
            return
        name = text(self.src, var).lstrip("$")
        self.symbols.append(
            {
                "kind": "field",
                "name": name,
                "qualified_name": f"{parent_qname}.{name}",
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )

    def _collect_const(self, node: Any, *, parent_qname: str = "") -> None:
        for elem in node.children:
            if elem.type != "const_element":
                continue
            name_node = next((c for c in elem.children if c.type == "name"), None)
            if name_node is None:
                continue
            name = text(self.src, name_node)
            scope = parent_qname or self.namespace
            qname = f"{scope}.{name}" if scope else name
            self.symbols.append(
                {
                    "kind": "constant",
                    "name": name,
                    "qualified_name": qname,
                    "signature": text(self.src, node).strip(),
                    "span": span(node),
                    "doc": "",
                    "modifiers": [],
                    "parent_qname": scope,
                }
            )

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("function_call_expression", "scoped_call_expression", "member_call_expression"):
                fn = next((c for c in n.children if c.is_named and c.type != "arguments"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip().lstrip("$")
                    name = qualifier.split("\\")[-1].split("::")[-1].split("->")[-1]
                    if name:
                        self.references.append(
                            {
                                "name": name,
                                "qualifier": qualifier,
                                "kind": "call",
                                "span": span(fn),
                                "in_symbol": in_symbol,
                            }
                        )
            if n.type in (
                "class_declaration",
                "interface_declaration",
                "function_definition",
                "method_declaration",
            ):
                continue
            stack.extend(n.children)
