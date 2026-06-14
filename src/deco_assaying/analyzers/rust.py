# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Rust analyzer.

Extracts:

- `use_declaration` -> imports. Single `use a::b::c;` becomes one import;
  `use a::{X, Y as Z};` becomes one import per item, with the alias on
  `Y as Z` recorded.
- `function_item`, `function_signature_item` (trait method signatures) ->
  function/method symbols. Methods on an `impl Type` body are qualified
  as `Type.method`.
- `struct_item` -> class. Public fields become field symbols.
- `enum_item` -> enum.
- `trait_item` -> interface.
- `impl_item` -> not a symbol itself; we walk its children and qualify
  the methods inside by the impl target.
- `mod_item` -> module symbol; nested items inside an inline `mod foo { ... }`
  body are recursed into with `foo` as the qualifier.
- `const_item`, `static_item` -> constant.
- `type_item` -> type_alias.
- `macro_definition` -> macro.

`async_count` counts `async fn`. `has_main_guard` flips for a top-level
`fn main`. `test_count` counts items immediately preceded by a `#[test]`
attribute.
"""

from __future__ import annotations

from typing import Any

from deco_assaying.analyzers._base import empty_result, span, text


def analyze(source_bytes: bytes, root: Any) -> dict[str, Any]:
    out = empty_result()
    state = _State(source_bytes)
    state.walk(root, parent_qname="", depth=0)
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
        next_test = False
        for child in node.children:
            t = child.type
            if t == "attribute_item":
                if "test" in text(self.src, child):
                    next_test = True
                continue
            if t == "use_declaration":
                self._collect_use(child)
            elif t == "function_item":
                self._collect_function(child, parent_qname=parent_qname, is_method=False, is_test=next_test)
            elif t == "function_signature_item":
                self._collect_function_signature(child, parent_qname=parent_qname)
            elif t == "struct_item":
                self._collect_struct(child, parent_qname=parent_qname)
            elif t == "enum_item":
                self._collect_enum(child, parent_qname=parent_qname)
            elif t == "trait_item":
                self._collect_trait(child, parent_qname=parent_qname, depth=depth)
            elif t == "impl_item":
                self._collect_impl(child, parent_qname=parent_qname, depth=depth)
            elif t == "mod_item":
                self._collect_mod(child, parent_qname=parent_qname, depth=depth)
            elif t in ("const_item", "static_item"):
                self._collect_const_static(child, kind="constant", parent_qname=parent_qname)
            elif t == "type_item":
                self._collect_type_alias(child, parent_qname=parent_qname)
            elif t == "macro_definition":
                self._collect_macro(child, parent_qname=parent_qname)
            else:
                self._collect_call_refs(child, in_symbol=parent_qname or "<module>")

            if t != "attribute_item":
                next_test = False

    # --- imports ---------------------------------------------------------

    def _collect_use(self, node: Any) -> None:
        # Walk the path tree and emit one import per leaf.
        path_node = next((c for c in node.children if c.is_named), None)
        if path_node is None:
            return
        self._emit_use(path_node, prefix="", span_node=node)

    def _emit_use(self, node: Any, *, prefix: str, span_node: Any) -> None:
        t = node.type
        if t == "scoped_identifier":
            module = self._scoped_identifier_text(node)
            full = _join_rust_path(prefix, module)
            self.imports.append({"module": full, "alias": None, "kind": "import", "span": span(span_node)})
        elif t == "identifier":
            name = text(self.src, node)
            full = _join_rust_path(prefix, name)
            self.imports.append({"module": full, "alias": None, "kind": "import", "span": span(span_node)})
        elif t == "use_as_clause":
            inner = next((c for c in node.children if c.is_named and c.type != "identifier"), None)
            alias_node = next((c for c in node.children if c.type == "identifier"), None)
            if inner is None:
                # Path is the first identifier; alias is the second.
                idents = [c for c in node.children if c.type == "identifier"]
                if not idents:
                    return
                base = text(self.src, idents[0])
                alias = text(self.src, idents[1]) if len(idents) > 1 else None
            else:
                base = (
                    self._scoped_identifier_text(inner)
                    if inner.type == "scoped_identifier"
                    else text(self.src, inner)
                )
                alias = text(self.src, alias_node) if alias_node is not None else None
            full = _join_rust_path(prefix, base)
            self.imports.append({"module": full, "alias": alias, "kind": "import", "span": span(span_node)})
        elif t == "scoped_use_list":
            scope = next(
                (c for c in node.children if c.type in ("scoped_identifier", "identifier", "crate", "self")),
                None,
            )
            list_node = next((c for c in node.children if c.type == "use_list"), None)
            scope_text = ""
            if scope is not None:
                scope_text = (
                    self._scoped_identifier_text(scope)
                    if scope.type == "scoped_identifier"
                    else text(self.src, scope)
                )
            new_prefix = _join_rust_path(prefix, scope_text)
            if list_node is not None:
                for item in list_node.children:
                    if item.is_named:
                        self._emit_use(item, prefix=new_prefix, span_node=span_node)
        elif t == "use_list":
            for item in node.children:
                if item.is_named:
                    self._emit_use(item, prefix=prefix, span_node=span_node)

    def _scoped_identifier_text(self, node: Any) -> str:
        # Lazy: just grab the source text and trust it's already `a::b::c`.
        return text(self.src, node).strip()

    # --- functions / signatures -----------------------------------------

    def _collect_function(
        self, node: Any, *, parent_qname: str, is_method: bool, is_test: bool = False
    ) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        params = next((c for c in node.children if c.type == "parameters"), None)
        body = next((c for c in node.children if c.type == "block"), None)
        modifiers = self._function_modifiers(node)
        is_async = "async" in modifiers
        kind = "method" if is_method else "function"
        if is_method and name == "new":
            kind = "constructor"

        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": _rust_function_signature(node, self.src, name),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers + (["test"] if is_test else []),
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1
        if is_async:
            self.metrics["async_count"] += 1
        if is_test:
            self.metrics["test_count"] += 1
        if not parent_qname and name == "main":
            self.metrics["has_main_guard"] = True
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})
        if body is not None:
            self._collect_call_refs(body, in_symbol=qname)
        # Don't double-count params node.
        del params

    def _collect_function_signature(self, node: Any, *, parent_qname: str) -> None:
        # Function declared in a trait body without a body.
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        self.symbols.append(
            {
                "kind": "method",
                "name": name,
                "qualified_name": qname,
                "signature": _rust_function_signature(node, self.src, name),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_functions"] += 1

    def _function_modifiers(self, node: Any) -> list[str]:
        out: list[str] = []
        for c in node.children:
            if c.type == "visibility_modifier":
                out.append("pub")
            elif c.type == "function_modifiers":
                for m in c.children:
                    if m.type in ("async", "unsafe", "const", "extern"):
                        out.append(m.type)
        return out

    # --- struct / enum / trait / impl -----------------------------------

    def _collect_struct(self, node: Any, *, parent_qname: str) -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": "class",
                "name": name,
                "qualified_name": qname,
                "signature": f"struct {name}",
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        self.metrics["n_classes"] += 1
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})

        field_list = next((c for c in node.children if c.type == "field_declaration_list"), None)
        if field_list is not None:
            for f in field_list.children:
                if f.type == "field_declaration":
                    fid = next((c for c in f.children if c.type == "field_identifier"), None)
                    if fid is None:
                        continue
                    fname = text(self.src, fid)
                    self.symbols.append(
                        {
                            "kind": "field",
                            "name": fname,
                            "qualified_name": f"{qname}.{fname}",
                            "signature": text(self.src, f).strip(),
                            "span": span(f),
                            "doc": "",
                            "modifiers": ["pub"]
                            if any(c.type == "visibility_modifier" for c in f.children)
                            else [],
                            "parent_qname": qname,
                        }
                    )

    def _collect_enum(self, node: Any, *, parent_qname: str) -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": "enum",
                "name": name,
                "qualified_name": qname,
                "signature": f"enum {name}",
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})

    def _collect_trait(self, node: Any, *, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": "interface",
                "name": name,
                "qualified_name": qname,
                "signature": f"trait {name}",
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})
        decls = next((c for c in node.children if c.type == "declaration_list"), None)
        if decls is not None:
            self.walk(decls, parent_qname=qname, depth=depth + 1)

    def _collect_impl(self, node: Any, *, parent_qname: str, depth: int) -> None:
        target = next((c for c in node.children if c.type == "type_identifier"), None)
        if target is None:
            # impl Trait for Type — fall back to walking with "<impl>"
            target_qname = "<impl>"
        else:
            target_name = text(self.src, target)
            target_qname = f"{parent_qname}.{target_name}" if parent_qname else target_name
        decls = next((c for c in node.children if c.type == "declaration_list"), None)
        if decls is None:
            return
        # Inside impl bodies, function_items become methods qualified by target.
        next_test = False
        for child in decls.children:
            t = child.type
            if t == "attribute_item":
                if "test" in text(self.src, child):
                    next_test = True
                continue
            if t == "function_item":
                self._collect_function(child, parent_qname=target_qname, is_method=True, is_test=next_test)
            elif t == "const_item":
                self._collect_const_static(child, kind="constant", parent_qname=target_qname)
            elif t == "type_item":
                self._collect_type_alias(child, parent_qname=target_qname)
            else:
                self._collect_call_refs(child, in_symbol=target_qname)
            if t != "attribute_item":
                next_test = False

    def _collect_mod(self, node: Any, *, parent_qname: str, depth: int) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": "module",
                "name": name,
                "qualified_name": qname,
                "signature": f"mod {name}",
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        body = next((c for c in node.children if c.type == "declaration_list"), None)
        if body is not None:
            self.walk(body, parent_qname=qname, depth=depth + 1)

    # --- consts / type aliases / macros ---------------------------------

    def _collect_const_static(self, node: Any, *, kind: str, parent_qname: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})

    def _collect_type_alias(self, node: Any, *, parent_qname: str) -> None:
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        qname = f"{parent_qname}.{name}" if parent_qname else name
        modifiers = ["pub"] if any(c.type == "visibility_modifier" for c in node.children) else []
        self.symbols.append(
            {
                "kind": "type_alias",
                "name": name,
                "qualified_name": qname,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": modifiers,
                "parent_qname": parent_qname,
            }
        )
        if "pub" in modifiers:
            self.exports.append({"name": name, "qualified_name": qname})

    def _collect_macro(self, node: Any, *, parent_qname: str) -> None:
        ident = next((c for c in node.children if c.type == "identifier"), None)
        if ident is None:
            return
        name = text(self.src, ident)
        qname = f"{parent_qname}.{name}!" if parent_qname else f"{name}!"
        self.symbols.append(
            {
                "kind": "macro",
                "name": name,
                "qualified_name": qname,
                "signature": f"macro_rules! {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": parent_qname,
            }
        )

    # --- references ------------------------------------------------------

    def _collect_call_refs(self, node: Any, *, in_symbol: str) -> None:
        stack: list[Any] = [node]
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = next((c for c in n.children if c.is_named and c.type != "arguments"), None)
                if fn is not None:
                    qualifier = text(self.src, fn).strip()
                    name = qualifier.split("::")[-1].split(".")[-1].split("(")[0].strip()
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
                "function_item",
                "function_signature_item",
                "struct_item",
                "enum_item",
                "trait_item",
                "impl_item",
                "mod_item",
                "closure_expression",
            ):
                continue
            stack.extend(n.children)


# ---------------------------------------------------------------------------
# Free helpers


def _join_rust_path(prefix: str, suffix: str) -> str:
    if not prefix:
        return suffix
    if not suffix:
        return prefix
    return f"{prefix}::{suffix}"


def _rust_function_signature(node: Any, source_bytes: bytes, name: str) -> str:
    """Approximate signature: take everything up to the body `{` if present."""
    end = node.end_byte
    body = next((c for c in node.children if c.type == "block"), None)
    if body is not None:
        end = body.start_byte
    raw = source_bytes[node.start_byte : end].decode("utf-8", errors="replace").strip()
    if raw.endswith(";"):
        raw = raw[:-1].rstrip()
    return raw


def _leading_doc_comment(source_bytes: bytes, root: Any) -> str:
    """Rust convention: `//!` inner-doc and `///` outer-doc lines at the top."""
    lines: list[str] = []
    for c in root.children:
        if c.type in ("line_comment", "block_comment", "comment"):
            txt = source_bytes[c.start_byte : c.end_byte].decode("utf-8", errors="replace")
            if txt.startswith("//!") or txt.startswith("///"):
                lines.append(txt[3:].strip())
            elif txt.startswith("/*"):
                inner = txt[2:]
                if inner.endswith("*/"):
                    inner = inner[:-2]
                lines.append(inner.strip())
        elif c.is_named:
            break
    return "\n".join(lines).strip()
