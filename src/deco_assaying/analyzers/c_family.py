"""Shared C / C++ analyzer.

Both grammars share `translation_unit` as the root and very similar leaf
shapes for `preproc_include`, `preproc_def`, `function_definition`,
`type_definition`, and `struct_specifier`. C++ adds `namespace_definition`,
`class_specifier`, and `template_declaration`; the `is_cpp` flag enables
those.
"""

from __future__ import annotations

from typing import Any

from deco_assaying.analyzers._base import empty_result, span, text


def make_analyzer(*, is_cpp: bool):
    def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
        out = empty_result()
        state = _State(source_bytes, is_cpp=is_cpp)
        state.walk(root, parent_qname="", depth=0)
        out["module_doc"] = ""
        out["symbols"] = state.symbols
        out["imports"] = state.imports
        out["exports"] = state.exports
        out["references"] = state.references
        out["metrics"] = state.metrics
        return out

    return analyze


class _State:
    def __init__(self, source_bytes: bytes, *, is_cpp: bool) -> None:
        self.src = source_bytes
        self.is_cpp = is_cpp
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
            if t == "preproc_include":
                self._collect_include(child)
            elif t == "preproc_def":
                self._collect_define(child, parent_qname=parent_qname)
            elif t == "type_definition":
                self._collect_typedef(child, parent_qname=parent_qname)
            elif t == "struct_specifier":
                self._collect_struct(child, parent_qname=parent_qname, kind="class")
            elif t == "function_definition":
                self._collect_function(child, parent_qname=parent_qname)
            elif self.is_cpp and t == "namespace_definition":
                self._collect_namespace(child, parent_qname=parent_qname, depth=depth)
            elif self.is_cpp and t == "class_specifier":
                self._collect_struct(child, parent_qname=parent_qname, kind="class")
            elif self.is_cpp and t == "template_declaration":
                # Recurse into the template's body declarations.
                self.walk(child, parent_qname=parent_qname, depth=depth)
            elif t == "declaration_list":
                self.walk(child, parent_qname=parent_qname, depth=depth)

    def _collect_include(self, node: Any) -> None:
        path_node = next(
            (c for c in node.children if c.type in ("system_lib_string", "string_literal")), None
        )
        if path_node is None:
            return
        raw = text(self.src, path_node).strip()
        if (raw.startswith("<") and raw.endswith(">")) or (len(raw) >= 2 and raw[0] == raw[-1] == '"'):
            module = raw[1:-1]
        else:
            module = raw
        self.imports.append(
            {
                "module": module,
                "alias": None,
                "kind": "import",
                "span": span(node),
            }
        )

    def _collect_define(self, node: Any, *, parent_qname: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "constant",
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": ["macro"],
                "parent_qname": parent_qname,
            }
        )

    def _collect_typedef(self, node: Any, *, parent_qname: str) -> None:
        # Often `typedef struct { ... } Name;` — extract the trailing type_identifier.
        name_node = next((c for c in reversed(list(node.children)) if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        # If it wraps a struct, also emit a class symbol for the struct fields.
        struct = next((c for c in node.children if c.type == "struct_specifier"), None)
        if struct is not None:
            self._collect_struct(struct, parent_qname=parent_qname, kind="class", forced_name=name)
            return
        self.symbols.append(
            {
                "kind": "type_alias",
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )

    def _collect_struct(self, node: Any, *, parent_qname: str, kind: str, forced_name: str = "") -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        name = forced_name or (text(self.src, name_node) if name_node is not None else "")
        if not name:
            return
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": f"{node.type.split('_')[0]} {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_classes"] += 1
        field_list = next((c for c in node.children if c.type == "field_declaration_list"), None)
        if field_list is not None:
            for f in field_list.children:
                if f.type == "field_declaration":
                    decl = next((c for c in f.children if c.type == "field_identifier"), None)
                    if decl is None:
                        # try variable_declarator (C++) or descend
                        for c in f.children:
                            if c.type == "field_identifier":
                                decl = c
                                break
                    if decl is None:
                        continue
                    fname = text(self.src, decl)
                    self.symbols.append(
                        {
                            "kind": "field",
                            "name": fname,
                            "qualified_name": f"{qname}.{fname}",
                            "signature": text(self.src, f).strip(),
                            "span": span(f),
                            "doc": "",
                            "modifiers": [],
                            "parent_qname": qname,
                        }
                    )
                elif f.type == "function_definition":
                    self._collect_function(f, parent_qname=qname, is_method=True)

    def _collect_function(self, node: Any, *, parent_qname: str, is_method: bool = False) -> None:
        # function_definition -> declarator (function_declarator) -> identifier + parameter_list
        decl = next((c for c in node.children if c.type == "function_declarator"), None)
        if decl is None:
            # Could be wrapped in pointer_declarator etc; descend one level
            for c in node.children:
                if c.is_named:
                    inner = next((d for d in c.children if d.type == "function_declarator"), None)
                    if inner is not None:
                        decl = inner
                        break
        if decl is None:
            return
        ident = next(
            (
                c
                for c in decl.children
                if c.type
                in (
                    "identifier",
                    "field_identifier",
                    "qualified_identifier",
                    "destructor_name",
                    "operator_name",
                )
            ),
            None,
        )
        if ident is None:
            return
        name = text(self.src, ident).strip()
        qname = f"{parent_qname}.{name}" if parent_qname else name
        params = next((c for c in decl.children if c.type == "parameter_list"), None)
        kind = "method" if is_method else "function"
        if name == "main" and not parent_qname:
            self.metrics["has_main_guard"] = True

        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, decl).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        del params
        body = next((c for c in node.children if c.type == "compound_statement"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_namespace(self, node: Any, *, parent_qname: str, depth: int) -> None:
        ident = next((c for c in node.children if c.type in ("namespace_identifier", "identifier")), None)
        if ident is None:
            return
        name = text(self.src, ident)
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

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = next((c for c in n.children if c.is_named and c.type != "argument_list"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip()
                    name = qualifier.split("::")[-1].split(".")[-1].split("->")[-1].split("(")[0].strip()
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
                "function_definition",
                "class_specifier",
                "struct_specifier",
                "namespace_definition",
            ):
                continue
            stack.extend(n.children)
