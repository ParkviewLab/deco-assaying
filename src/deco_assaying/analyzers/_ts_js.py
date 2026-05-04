"""Shared analyzer for TypeScript, TSX, and JavaScript.

The three languages have nearly identical tree-sitter grammars for the
constructs we extract; the only differences are TS-only declarations
(`interface_declaration`, `type_alias_declaration`, `enum_declaration`).
The `is_typescript` flag enables those.

Notes:

- `export_statement` wraps an inner declaration; we unwrap it and record
  the inner symbol's name in `exports`.
- `default` exports show up as `export default <expr>`. If the expr is
  named (class/function declaration with an identifier) we use that name;
  otherwise we record the export as `default`.
- `lexical_declaration`/`variable_declaration` containing `arrow_function`
  or `function_expression` becomes a function symbol named after the
  declarator (`const foo = () => ...` -> symbol `foo`).
"""

from __future__ import annotations

from typing import Any

from deco_assaying.analyzers._base import empty_result, span, text


def make_analyzer(*, is_typescript: bool):
    def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
        out = empty_result()
        state = _State(source_bytes, is_typescript=is_typescript)
        state.walk_program(root)
        out["module_doc"] = _leading_block_comment(source_bytes, root)
        out["symbols"] = state.symbols
        out["imports"] = state.imports
        out["exports"] = state.exports
        out["references"] = state.references
        out["metrics"] = state.metrics
        return out

    return analyze


class _State:
    def __init__(self, source_bytes: bytes, *, is_typescript: bool) -> None:
        self.src = source_bytes
        self.is_ts = is_typescript
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

    # --- top-level walk --------------------------------------------------

    def walk_program(self, root: Any) -> None:
        for child in root.children:
            self._handle_top_level(child, parent_qname="", depth=0, exported=False)

    def _handle_top_level(self, node: Any, *, parent_qname: str, depth: int, exported: bool) -> None:
        t = node.type
        if t == "import_statement":
            self._collect_import(node)
        elif t == "export_statement":
            self._handle_export(node, parent_qname=parent_qname, depth=depth)
        elif t == "function_declaration":
            self._collect_function(node, parent_qname=parent_qname, depth=depth, exported=exported)
        elif t == "class_declaration":
            self._collect_class(node, parent_qname=parent_qname, depth=depth, exported=exported)
        elif t == "interface_declaration" and self.is_ts:
            self._collect_interface(node, parent_qname=parent_qname, depth=depth, exported=exported)
        elif t == "type_alias_declaration" and self.is_ts:
            self._collect_type_alias(node, parent_qname=parent_qname, exported=exported)
        elif t == "enum_declaration" and self.is_ts:
            self._collect_enum(node, parent_qname=parent_qname, depth=depth, exported=exported)
        elif t in ("lexical_declaration", "variable_declaration"):
            self._collect_variable_decl(node, parent_qname=parent_qname, exported=exported)
            self._collect_call_refs(node, in_symbol=parent_qname or "<module>")
        else:
            self._collect_call_refs(node, in_symbol=parent_qname or "<module>")

    # --- export handling -------------------------------------------------

    def _handle_export(self, node: Any, *, parent_qname: str, depth: int) -> None:
        # `export default <expr>` is recorded as exporting "default".
        is_default = any(c.type == "default" for c in node.children)
        for c in node.children:
            if c.type in ("function_declaration", "class_declaration"):
                self._handle_top_level(c, parent_qname=parent_qname, depth=depth, exported=True)
                # Also tag this as the default export if applicable.
                if is_default:
                    self.exports.append({"name": "default", "qualified_name": "default"})
            elif c.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                if self.is_ts:
                    self._handle_top_level(c, parent_qname=parent_qname, depth=depth, exported=True)
            elif c.type in ("lexical_declaration", "variable_declaration"):
                self._collect_variable_decl(c, parent_qname=parent_qname, exported=True)
            elif c.type in ("function_expression", "class_expression"):
                if is_default:
                    self.exports.append({"name": "default", "qualified_name": "default"})

    # --- imports ---------------------------------------------------------

    def _collect_import(self, node: Any) -> None:
        # Find the source string and the import clause.
        source = ""
        clause: Any | None = None
        for c in node.children:
            if c.type == "string":
                source = _string_content(self.src, c)
            elif c.type == "import_clause":
                clause = c
        if clause is None:
            # `import "side-effect"` — record as side_effect.
            if source:
                self.imports.append(
                    {
                        "module": source,
                        "alias": None,
                        "kind": "side_effect",
                        "span": span(node),
                    }
                )
            return

        for c in clause.children:
            if c.type == "named_imports":
                for spec in c.children:
                    if spec.type == "import_specifier":
                        names = [n for n in spec.children if n.type == "identifier"]
                        if not names:
                            continue
                        original = text(self.src, names[0])
                        alias = text(self.src, names[1]) if len(names) > 1 else None
                        self.imports.append(
                            {
                                "module": f"{source}::{original}",
                                "alias": alias,
                                "kind": "from",
                                "span": span(spec),
                            }
                        )
            elif c.type == "namespace_import":
                # `import * as ns from "..."`
                ident = next((n for n in c.children if n.type == "identifier"), None)
                alias = text(self.src, ident) if ident is not None else None
                self.imports.append(
                    {
                        "module": source,
                        "alias": alias,
                        "kind": "import",
                        "span": span(c),
                    }
                )
            elif c.type == "identifier":
                # default import: `import Foo from "..."`
                self.imports.append(
                    {
                        "module": f"{source}::default",
                        "alias": text(self.src, c),
                        "kind": "from",
                        "span": span(c),
                    }
                )

    # --- variable / arrow-function declarations -------------------------

    def _collect_variable_decl(self, node: Any, *, parent_qname: str, exported: bool) -> None:
        for c in node.children:
            if c.type != "variable_declarator":
                continue
            ident = next((n for n in c.children if n.type == "identifier"), None)
            if ident is None:
                continue
            name = text(self.src, ident)
            qname = f"{parent_qname}.{name}" if parent_qname else name

            # If RHS is an arrow_function or function_expression, we treat
            # the declarator as a function symbol.
            value = next((n for n in c.children if n.type in ("arrow_function", "function_expression")), None)
            if value is not None:
                is_async = any(ch.type == "async" for ch in value.children)
                is_gen = any(ch.type == "*" for ch in value.children)
                params = next(
                    (n for n in value.children if n.type in ("formal_parameters", "identifier")), None
                )
                self.symbols.append(
                    {
                        "kind": "function",
                        "name": name,
                        "qualified_name": qname,
                        "signature": _function_signature(
                            name=name,
                            params=text(self.src, params) if params is not None else "()",
                            is_async=is_async,
                        ),
                        "span": span(c),
                        "doc": "",
                        "modifiers": (["async"] if is_async else [])
                        + (["generator"] if is_gen else [])
                        + (["exported"] if exported else []),
                        "parent_qname": parent_qname,
                    }
                )
                self.metrics["n_functions"] += 1
                if is_async:
                    self.metrics["async_count"] += 1
                if is_gen:
                    self.metrics["generator_count"] += 1
                if exported:
                    self.exports.append({"name": name, "qualified_name": qname})
                continue

            # Otherwise it's a constant/variable declaration.
            self.symbols.append(
                {
                    "kind": "constant",
                    "name": name,
                    "qualified_name": qname,
                    "signature": text(self.src, c).strip(),
                    "span": span(c),
                    "doc": "",
                    "modifiers": ["exported"] if exported else [],
                    "parent_qname": parent_qname,
                }
            )
            if exported:
                self.exports.append({"name": name, "qualified_name": qname})

    # --- functions / classes / interfaces / type aliases / enums --------

    def _collect_function(self, node: Any, *, parent_qname: str, depth: int, exported: bool) -> None:
        ident = next((n for n in node.children if n.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        params = next((n for n in node.children if n.type == "formal_parameters"), None)
        ret = next((n for n in node.children if n.type == "type_annotation"), None)
        is_async = any(ch.type == "async" for ch in node.children)
        is_gen = any(ch.type == "*" for ch in node.children)
        self.symbols.append(
            {
                "kind": "function",
                "name": name,
                "qualified_name": qname,
                "signature": _function_signature(
                    name=name,
                    params=text(self.src, params) if params is not None else "()",
                    ret=text(self.src, ret).lstrip(": ") if ret is not None else "",
                    is_async=is_async,
                ),
                "span": span(node),
                "doc": "",
                "modifiers": (["async"] if is_async else [])
                + (["generator"] if is_gen else [])
                + (["exported"] if exported else []),
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        if is_async:
            self.metrics["async_count"] += 1
        if is_gen:
            self.metrics["generator_count"] += 1
        if exported:
            self.exports.append({"name": name, "qualified_name": qname})
        body = next((n for n in node.children if n.type == "statement_block"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_class(self, node: Any, *, parent_qname: str, depth: int, exported: bool) -> None:
        name_node = _first_named_of_type(node, ("type_identifier", "identifier"))
        if name_node is None:
            # anonymous class
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name

        bases: list[str] = []
        heritage = next((n for n in node.children if n.type == "class_heritage"), None)
        if heritage is not None:
            for c in heritage.children:
                if c.type in ("extends_clause", "implements_clause"):
                    for base in c.children:
                        if base.is_named:
                            bt = text(self.src, base).strip()
                            if bt:
                                bases.append(bt)
                                self.references.append(
                                    {
                                        "name": _last_segment(bt),
                                        "qualifier": bt,
                                        "kind": "inherit",
                                        "span": span(base),
                                        "in_symbol": qname,
                                    }
                                )

        self.symbols.append(
            {
                "kind": "class",
                "name": name,
                "qualified_name": qname,
                "signature": _class_signature(name, bases),
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if exported else [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_classes"] += 1
        self.metrics["max_nest_depth"] = max(self.metrics["max_nest_depth"], depth + 1)
        if exported:
            self.exports.append({"name": name, "qualified_name": qname})

        body = next((n for n in node.children if n.type == "class_body"), None)
        if body is not None:
            for member in body.children:
                self._handle_class_member(member, parent_qname=qname, depth=depth + 1)

    def _handle_class_member(self, node: Any, *, parent_qname: str, depth: int) -> None:
        t = node.type
        if t == "method_definition":
            self._collect_method(node, parent_qname=parent_qname, depth=depth)
        elif t in ("public_field_definition", "field_definition"):
            ident = next((n for n in node.children if n.type == "property_identifier"), None)
            if ident is None:
                return
            name = text(self.src, ident)
            self.symbols.append(
                {
                    "kind": "field",
                    "name": name,
                    "qualified_name": f"{parent_qname}.{name}",
                    "signature": text(self.src, node).strip(),
                    "span": span(node),
                    "doc": "",
                    "modifiers": [
                        c.type
                        for c in node.children
                        if c.type in ("static", "readonly", "public", "private", "protected")
                    ],
                    "parent_qname": parent_qname,
                }
            )

    def _collect_method(self, node: Any, *, parent_qname: str, depth: int) -> None:
        ident = next((n for n in node.children if n.type == "property_identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}"
        is_async = any(c.type == "async" for c in node.children)
        is_gen = any(c.type == "*" for c in node.children)
        params = next((n for n in node.children if n.type == "formal_parameters"), None)
        ret = next((n for n in node.children if n.type == "type_annotation"), None)
        modifiers = [
            c.type
            for c in node.children
            if c.type in ("static", "get", "set", "readonly", "public", "private", "protected")
        ]
        if is_async:
            modifiers.append("async")
        if is_gen:
            modifiers.append("generator")

        kind = "constructor" if name == "constructor" else "method"
        if "get" in modifiers or "set" in modifiers:
            kind = "property"

        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": _function_signature(
                    name=name,
                    params=text(self.src, params) if params is not None else "()",
                    ret=text(self.src, ret).lstrip(": ") if ret is not None else "",
                    is_async=is_async,
                ),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        if is_async:
            self.metrics["async_count"] += 1
        if is_gen:
            self.metrics["generator_count"] += 1
        body = next((n for n in node.children if n.type == "statement_block"), None)
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)

    def _collect_interface(self, node: Any, *, parent_qname: str, depth: int, exported: bool) -> None:
        name_node = _first_named_of_type(node, ("type_identifier",))
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "interface",
                "name": name,
                "qualified_name": qname,
                "signature": f"interface {name}",
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if exported else [],
                "parent_qname": parent_qname,
            }
        )
        if exported:
            self.exports.append({"name": name, "qualified_name": qname})

    def _collect_type_alias(self, node: Any, *, parent_qname: str, exported: bool) -> None:
        name_node = _first_named_of_type(node, ("type_identifier",))
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "type_alias",
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if exported else [],
                "parent_qname": parent_qname,
            }
        )
        if exported:
            self.exports.append({"name": name, "qualified_name": qname})

    def _collect_enum(self, node: Any, *, parent_qname: str, depth: int, exported: bool) -> None:
        name_node = _first_named_of_type(node, ("identifier", "type_identifier"))
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "enum",
                "name": name,
                "qualified_name": qname,
                "signature": f"enum {name}",
                "span": span(node),
                "doc": "",
                "modifiers": ["exported"] if exported else [],
                "parent_qname": parent_qname,
            }
        )
        if exported:
            self.exports.append({"name": name, "qualified_name": qname})

    # --- references ------------------------------------------------------

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack: list[Any] = [node]
        while stack:
            n = stack.pop()
            t = n.type
            if t == "call_expression":
                fn = next((c for c in n.children if c.is_named and c.type != "arguments"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip()
                    name = _last_segment(qualifier)
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
            if t in (
                "function_declaration",
                "class_declaration",
                "method_definition",
                "arrow_function",
                "function_expression",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
            ):
                continue
            stack.extend(n.children)


# ---------------------------------------------------------------------------
# Free helpers


def _string_content(source_bytes: bytes, string_node: Any) -> str:
    parts: list[str] = []
    for c in string_node.children:
        if c.type in ("string_fragment", "string_content"):
            parts.append(source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace"))
    if parts:
        return "".join(parts)
    raw = source_bytes[string_node.start_byte : string_node.end_byte].decode("utf-8", errors="replace")
    if len(raw) >= 2 and raw[0] in ("'", '"', "`") and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _first_named_of_type(node: Any, types: tuple[str, ...]) -> Any | None:
    for c in node.children:
        if c.is_named and c.type in types:
            return c
    return None


def _leading_block_comment(source_bytes: bytes, root: Any) -> str:
    """A `/** ... */` JSDoc-style block at the very top of the file."""
    for c in root.children:
        if c.type == "comment":
            txt = source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
            if txt.startswith("/**") or txt.startswith("/*"):
                return _strip_block_comment(txt)
            return ""
        if c.is_named:
            break
    return ""


def _strip_block_comment(txt: str) -> str:
    if txt.startswith("/**"):
        txt = txt[3:]
    elif txt.startswith("/*"):
        txt = txt[2:]
    if txt.endswith("*/"):
        txt = txt[:-2]
    out_lines = []
    for ln in txt.splitlines():
        stripped = ln.lstrip().lstrip("*").strip()
        out_lines.append(stripped)
    return "\n".join(out_lines).strip()


def _last_segment(qualifier: str) -> str:
    if not qualifier:
        return ""
    return qualifier.split(".")[-1].split("[")[0].strip()


def _function_signature(*, name: str, params: str, ret: str = "", is_async: bool = False) -> str:
    prefix = "async function " if is_async else "function "
    suffix = f": {ret}" if ret else ""
    return f"{prefix}{name}{params}{suffix}"


def _class_signature(name: str, bases: list[str]) -> str:
    if bases:
        return f"class {name} extends {bases[0]}"
    return f"class {name}"
