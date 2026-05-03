"""Bash analyzer.

Bash has very few of the constructs we model elsewhere — there are no
classes, types, or modules — so the extractor focuses on:

- `source` / `.` calls -> imports.
- `function_definition` -> function (Bash supports two syntaxes;
  tree-sitter normalizes both into this node type).
- Top-level `variable_assignment` whose name is SCREAMING_SNAKE_CASE ->
  constant.
- `command` -> reference (we only record the program name).
"""

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
            if t == "function_definition":
                self._collect_function(child)
            elif t == "variable_assignment":
                self._collect_assignment(child)
            elif t == "command":
                self._collect_command(child, in_symbol="<module>")

    def _collect_function(self, node: Any) -> None:
        name_node = next((c for c in node.children if c.type == "word"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        self.symbols.append(
            {
                "kind": "function",
                "name": name,
                "qualified_name": name,
                "signature": f"function {name}",
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": "",
            }
        )
        self.metrics["n_functions"] += 1
        body = next((c for c in node.children if c.type == "compound_statement"), None)
        if body is not None:
            stack = [body]
            while stack:
                n = stack.pop()
                if n.type == "command":
                    self._collect_command(n, in_symbol=name)
                if n.type == "function_definition":
                    continue
                stack.extend(n.children)

    def _collect_assignment(self, node: Any) -> None:
        name_node = next((c for c in node.children if c.type == "variable_name"), None)
        if name_node is None:
            return
        name = text(self.src, name_node)
        if not (name.isupper() or (len(name) > 1 and name[0].isupper() and "_" in name)):
            return
        self.symbols.append(
            {
                "kind": "constant",
                "name": name,
                "qualified_name": name,
                "signature": text(self.src, node).strip(),
                "span": span(node),
                "doc": "",
                "modifiers": [],
                "parent_qname": "",
            }
        )

    def _collect_command(self, node: Any, *, in_symbol: str) -> None:
        cmd_node = next((c for c in node.children if c.type == "command_name"), None)
        if cmd_node is None:
            return
        cmd = text(self.src, cmd_node).strip()
        if cmd in ("source", "."):
            arg = next((c for c in node.children if c is not cmd_node and c.is_named), None)
            if arg is not None:
                module = text(self.src, arg).strip()
                # Strip surrounding quotes if any.
                if len(module) >= 2 and module[0] in ('"', "'") and module[-1] == module[0]:
                    module = module[1:-1]
                self.imports.append(
                    {
                        "module": module,
                        "alias": None,
                        "kind": "import",
                        "span": span(node),
                    }
                )
            return
        # Otherwise record as a call reference.
        self.references.append(
            {
                "name": cmd.split("/")[-1],
                "qualifier": cmd,
                "kind": "call",
                "span": span(cmd_node),
                "in_symbol": in_symbol,
            }
        )
