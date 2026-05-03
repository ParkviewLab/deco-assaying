"""Go analyzer.

Extracts:

- The package clause as a `module` symbol.
- `import_declaration` blocks; each `import_spec` becomes one import. We
  unquote the path and record an alias when present.
- `function_declaration` -> function symbol; in `package main`, a function
  literally named `main` flips `metrics.has_main_guard`.
- `method_declaration` -> method symbol qualified by its receiver type
  (pointer or value).
- `type_declaration > type_spec` -> class (struct), interface, or
  type_alias depending on the inner type.
- `const_declaration` / `var_declaration` -> constant / field-level
  symbols (constants pinned at module scope; vars only when they are
  exported, i.e. start with an uppercase letter).
- Outgoing call references inside function/method bodies.

`generator_count` and `async_count` stay zero (Go has neither concept).
`test_count` counts top-level functions whose name matches `Test*` per
Go's testing convention.
"""

from __future__ import annotations

from typing import Any

from parkview_codeparse.analyzers._base import empty_result, span, text


def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
    out = empty_result()
    state = _State(source_bytes)
    state.walk(root)
    out["module_doc"] = _leading_doc_comment(source_bytes, root)
    out["symbols"] = state.symbols
    out["imports"] = state.imports
    out["exports"] = state.exports
    out["references"] = state.references
    out["metrics"] = state.metrics
    return out


class _State:
    def __init__(self, source_bytes: bytes) -> None:
        self.src = source_bytes
        self.package: str = ""
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
            if t == "package_clause":
                self._collect_package(child)
            elif t == "import_declaration":
                self._collect_imports(child)
            elif t == "function_declaration":
                self._collect_function(child)
            elif t == "method_declaration":
                self._collect_method(child)
            elif t == "type_declaration":
                for spec in child.children:
                    if spec.type == "type_spec":
                        self._collect_type(spec)
            elif t == "const_declaration":
                for spec in child.children:
                    if spec.type == "const_spec":
                        self._collect_const(spec)
            elif t == "var_declaration":
                for spec in child.children:
                    if spec.type == "var_spec":
                        self._collect_var(spec)

    # --- package ---------------------------------------------------------

    def _collect_package(self, node: Any) -> None:
        ident = _first_named_of_type(node, ("package_identifier",))
        if ident is None:
            return
        self.package = text(self.src, ident)
        self.symbols.append(
            {
                "kind": "module",
                "name": self.package,
                "qualified_name": self.package,
                "signature": f"package {self.package}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": "",
            }
        )

    # --- imports ---------------------------------------------------------

    def _collect_imports(self, node: Any) -> None:
        for c in node.children:
            if c.type == "import_spec":
                self._collect_one_import(c)
            elif c.type == "import_spec_list":
                for spec in c.children:
                    if spec.type == "import_spec":
                        self._collect_one_import(spec)

    def _collect_one_import(self, node: Any) -> None:
        path_node = next((n for n in node.children if n.type == "interpreted_string_literal"), None)
        alias_node = next(
            (n for n in node.children if n.type in ("package_identifier", "blank_identifier", "dot")), None
        )
        if path_node is None:
            return
        module = _unquote_go_string(text(self.src, path_node))
        alias = text(self.src, alias_node) if alias_node is not None else None
        self.imports.append({"module": module, "alias": alias, "kind": "import", "span": span(node)})

    # --- functions / methods --------------------------------------------

    def _collect_function(self, node: Any) -> None:
        ident = next((n for n in node.children if n.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{self.package}.{name}" if self.package else name
        is_exported = name[:1].isupper()
        params, ret_text = self._params_and_ret(node)

        self.symbols.append(
            {
                "kind": "function",
                "name": name,
                "qualified_name": qname,
                "signature": _go_function_signature(name, params, ret_text),
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if is_exported else [],
                "parent_qname": self.package,
            }
        )
        self.metrics["n_functions"] += 1
        if is_exported:
            self.exports.append({"name": name, "qualified_name": qname})
        if self.package == "main" and name == "main":
            self.metrics["has_main_guard"] = True
        if name.startswith("Test") and name[4:5].isupper():
            self.metrics["test_count"] += 1

        body = next((n for n in node.children if n.type == "block"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_method(self, node: Any) -> None:
        # Children: func, parameter_list (receiver), field_identifier (name),
        # parameter_list (params), optional return type, block.
        receiver_type = ""
        seen_first_params = False
        method_name_node: Any | None = None
        for c in node.children:
            if c.type == "parameter_list":
                if not seen_first_params:
                    receiver_type = _extract_receiver_type(self.src, c)
                    seen_first_params = True
            elif c.type == "field_identifier":
                method_name_node = c
        if method_name_node is None:
            return
        name = text(self.src, method_name_node)
        recv = receiver_type or "<receiver>"
        qname = f"{self.package}.{recv}.{name}" if self.package else f"{recv}.{name}"
        params, ret_text = self._params_and_ret(node, skip_first_param_list=True)

        self.symbols.append(
            {
                "kind": "method",
                "name": name,
                "qualified_name": qname,
                "signature": f"func ({receiver_type}) {name}{params}{(' ' + ret_text) if ret_text else ''}",
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if name[:1].isupper() else [],
                "parent_qname": f"{self.package}.{recv}" if self.package else recv,
            }
        )
        self.metrics["n_functions"] += 1
        body = next((n for n in node.children if n.type == "block"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _params_and_ret(self, node: Any, *, skip_first_param_list: bool = False) -> tuple[str, str]:
        params_text = "()"
        ret_text = ""
        skipped = False
        seen_params = False
        for c in node.children:
            if c.type == "parameter_list":
                if skip_first_param_list and not skipped:
                    skipped = True
                    continue
                if not seen_params:
                    params_text = text(self.src, c)
                    seen_params = True
            elif c.is_named and seen_params and c.type not in ("block", "comment"):
                ret_text = text(self.src, c)
        return params_text, ret_text

    # --- types -----------------------------------------------------------

    def _collect_type(self, type_spec: Any) -> None:
        ident = next((n for n in type_spec.children if n.type == "type_identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{self.package}.{name}" if self.package else name
        # The inner type is the next named child after the identifier.
        inner = None
        for c in type_spec.children:
            if c.is_named and c is not ident:
                inner = c
                break
        kind = "type_alias"
        sig = text(self.src, type_spec).strip()
        if inner is not None:
            if inner.type == "struct_type":
                kind = "class"
                self.metrics["n_classes"] += 1
            elif inner.type == "interface_type":
                kind = "interface"
        is_exported = name[:1].isupper()
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": sig,
                "span": span(type_spec),
                "doc": "",
                "modifiers": ["exported"] if is_exported else [],
                "parent_qname": self.package,
            }
        )
        if is_exported:
            self.exports.append({"name": name, "qualified_name": qname})

        # Struct fields
        if inner is not None and inner.type == "struct_type":
            field_list = next((n for n in inner.children if n.type == "field_declaration_list"), None)
            if field_list is not None:
                for f in field_list.children:
                    if f.type == "field_declaration":
                        for fid in f.children:
                            if fid.type == "field_identifier":
                                fname = text(self.src, fid)
                                self.symbols.append(
                                    {
                                        "kind": "field",
                                        "name": fname,
                                        "qualified_name": f"{qname}.{fname}",
                                        "signature": text(self.src, f).strip(),
                                        "span": span(f),
                                        "doc": "",
                                        "modifiers": ["exported"] if fname[:1].isupper() else [],
                                        "parent_qname": qname,
                                    }
                                )

    # --- constants / vars -----------------------------------------------

    def _collect_const(self, spec: Any) -> None:
        for c in spec.children:
            if c.type == "identifier":
                name = text(self.src, c)
                qname = f"{self.package}.{name}" if self.package else name
                is_exported = name[:1].isupper()
                self.symbols.append(
                    {
                        "kind": "constant",
                        "name": name,
                        "qualified_name": qname,
                        "signature": text(self.src, spec).strip(),
                        "span": span(spec),
                        "doc": "",
                        "modifiers": ["exported"] if is_exported else [],
                        "parent_qname": self.package,
                    }
                )
                if is_exported:
                    self.exports.append({"name": name, "qualified_name": qname})

    def _collect_var(self, spec: Any) -> None:
        for c in spec.children:
            if c.type == "identifier":
                name = text(self.src, c)
                if not name[:1].isupper():
                    continue  # only record exported package-level vars
                qname = f"{self.package}.{name}" if self.package else name
                self.symbols.append(
                    {
                        "kind": "constant",
                        "name": name,
                        "qualified_name": qname,
                        "signature": text(self.src, spec).strip(),
                        "span": span(spec),
                        "doc": "",
                        "modifiers": ["exported", "var"],
                        "parent_qname": self.package,
                    }
                )
                self.exports.append({"name": name, "qualified_name": qname})

    # --- references ------------------------------------------------------

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack: list[Any] = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = next((c for c in n.children if c.is_named and c.type != "argument_list"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip()
                    name = _last_segment_dot_or_dot(qualifier)
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
            if n.type in ("function_declaration", "method_declaration", "func_literal"):
                continue
            stack.extend(n.children)


# ---------------------------------------------------------------------------
# Free helpers


def _first_named_of_type(node: Any, types: tuple[str, ...]) -> Any | None:
    for c in node.children:
        if c.is_named and c.type in types:
            return c
    return None


def _unquote_go_string(raw: str) -> str:
    if len(raw) >= 2 and raw[0] in ("'", '"', "`") and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _extract_receiver_type(source_bytes: bytes, param_list: Any) -> str:
    """Pull the type out of `(s *S)` or `(s S)` receiver lists."""
    for child in param_list.children:
        if child.type != "parameter_declaration":
            continue
        for c in child.children:
            if c.type == "type_identifier":
                return source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
            if c.type == "pointer_type":
                inner = next((n for n in c.children if n.type == "type_identifier"), None)
                if inner is not None:
                    return source_bytes[inner.start_byte : inner.end_byte].decode("utf-8", errors="replace")
    return ""


def _go_function_signature(name: str, params: str, ret: str) -> str:
    base = f"func {name}{params}"
    return f"{base} {ret}" if ret else base


def _last_segment_dot_or_dot(qualifier: str) -> str:
    if not qualifier:
        return ""
    return qualifier.split(".")[-1].split("[")[0].strip()


def _leading_doc_comment(source_bytes: bytes, root: Any) -> str:
    """Go convention: a contiguous `// ...` block immediately above package."""
    lines: list[str] = []
    for c in root.children:
        if c.type == "comment":
            txt = source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
            if txt.startswith("//"):
                lines.append(txt[2:].strip())
            elif txt.startswith("/*"):
                inner = txt[2:]
                if inner.endswith("*/"):
                    inner = inner[:-2]
                lines.append(inner.strip())
        elif c.is_named:
            break
    return "\n".join(lines).strip()
