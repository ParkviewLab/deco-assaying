"""C# analyzer."""

from __future__ import annotations

from typing import Any

from parkview_codeparse.analyzers._base import empty_result, span, text


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
            t = child.type
            if t == "using_directive":
                self._collect_using(child)
            elif t in ("namespace_declaration", "file_scoped_namespace_declaration"):
                self._collect_namespace(child, parent_qname=parent_qname, depth=depth)
            elif t in (
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
                "enum_declaration",
            ):
                kind_map = {
                    "class_declaration": "class",
                    "interface_declaration": "interface",
                    "struct_declaration": "class",
                    "record_declaration": "class",
                    "enum_declaration": "enum",
                }
                self._collect_type(child, kind=kind_map[t], parent_qname=parent_qname, depth=depth)
            elif t == "declaration_list":
                self.walk(child, parent_qname=parent_qname, depth=depth)

    def _collect_using(self, node: Any) -> None:
        ident = next((c for c in node.children if c.type in ("qualified_name", "identifier")), None)
        if ident is None:
            return
        is_static = any(c.type == "static" for c in node.children)
        self.imports.append(
            {
                "module": text(self.src, ident).strip(),
                "alias": None,
                "kind": "static" if is_static else "import",
                "span": span(node),
            }
        )

    def _collect_namespace(self, node: Any, *, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type in ("identifier", "qualified_name")), None)
        if name_node is None:
            return
        name = text(self.src, name_node).strip()
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "module",
                "name": name,
                "qualified_name": qname,
                "signature": f"namespace {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body is not None:
            self.walk(body, parent_qname=qname, depth=depth + 1)

    def _collect_type(self, node: Any, *, kind: str, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = self._modifiers(node)
        is_public = "public" in modifiers
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"{kind} {name}",
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
        body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body is not None:
            for member in body.children:
                self._handle_member(member, parent_qname=qname, depth=depth + 1)

    def _handle_member(self, node: Any, *, parent_qname: str, depth: int) -> None:
        t = node.type
        if t == "method_declaration":
            self._collect_method(node, parent_qname=parent_qname, kind="method")
        elif t == "constructor_declaration":
            self._collect_method(node, parent_qname=parent_qname, kind="constructor")
        elif t == "property_declaration":
            self._collect_property(node, parent_qname=parent_qname)
        elif t == "field_declaration":
            self._collect_field(node, parent_qname=parent_qname)
        elif t in (
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
            "enum_declaration",
        ):
            kind_map = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "struct_declaration": "class",
                "record_declaration": "class",
                "enum_declaration": "enum",
            }
            self._collect_type(node, kind=kind_map[t], parent_qname=parent_qname, depth=depth)

    def _collect_method(self, node: Any, *, parent_qname: str, kind: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}"
        modifiers = self._modifiers(node)
        is_async = "async" in modifiers
        if name == "Main" and "static" in modifiers and not parent_qname.count(".") > 0:
            self.metrics["has_main_guard"] = True
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"{name}",
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        if is_async:
            self.metrics["async_count"] += 1
        body = next((c for c in node.children if c.type in ("block", "arrow_expression_clause")), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_property(self, node: Any, *, parent_qname: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        self.symbols.append(
            {
                "kind": "property",
                "name": name,
                "qualified_name": f"{parent_qname}.{name}",
                "signature": text(self.src, node).strip().split("\n")[0],
                "span": span(node),
                "doc": "",
                "modifiers": self._modifiers(node),
                "parent_qname": parent_qname,
            }
        )

    def _collect_field(self, node: Any, *, parent_qname: str) -> None:
        decl = next((c for c in node.children if c.type == "variable_declaration"), None)
        if decl is None:
            return
        ident = next((c for c in decl.children if c.type == "variable_declarator"), None)
        if ident is None:
            return
        name_node = next((c for c in ident.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        self.symbols.append(
            {
                "kind": "field",
                "name": name,
                "qualified_name": f"{parent_qname}.{name}",
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": self._modifiers(node),
                "parent_qname": parent_qname,
            }
        )

    def _modifiers(self, node: Any) -> list[str]:
        return [text(self.src, c).strip() for c in node.children if c.type == "modifier"]

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "invocation_expression":
                fn = next((c for c in n.children if c.is_named and c.type != "argument_list"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip()
                    name = qualifier.split(".")[-1].split("(")[0].strip()
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
                "method_declaration",
                "constructor_declaration",
            ):
                continue
            stack.extend(n.children)
