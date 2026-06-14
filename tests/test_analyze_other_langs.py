# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Smoke tests for the non-Python language analyzers.

These are intentionally light — they verify that each analyzer produces
something coherent for a representative input, not that every edge case
is covered. The Python analyzer has the deeper end-to-end test suite.
"""

from deco_assaying import analyze


def _by_qname(symbols):
    return {s["qualified_name"]: s for s in symbols}


# --- TypeScript / JavaScript ---------------------------------------------


def test_typescript_basic():
    src = (
        'import { Foo, Bar as B } from "./util";\n'
        "export const X = 1;\n"
        "export interface I { x: number }\n"
        "export type Alias = string | number;\n"
        "export enum Color { Red, Green }\n"
        "export class C extends B {\n"
        "    field: number = 0;\n"
        "    static s = 'x';\n"
        "    async m(n: number): Promise<string> { return ''; }\n"
        "}\n"
        "export function top(a: number): number { return a + 1; }\n"
        "const handler = (req: Request) => req.url;\n"
    )
    r = analyze.analyze_inline(content=src, filename="x.ts")
    assert r["file"]["language"] == "typescript"
    by = _by_qname(r["symbols"])
    assert by["X"]["kind"] == "constant"
    assert by["I"]["kind"] == "interface"
    assert by["Alias"]["kind"] == "type_alias"
    assert by["Color"]["kind"] == "enum"
    assert by["C"]["kind"] == "class"
    assert by["C.field"]["kind"] == "field"
    assert by["C.m"]["kind"] == "method"
    assert by["top"]["kind"] == "function"
    assert by["handler"]["kind"] == "function"  # arrow assigned to const
    assert {e["name"] for e in r["exports"]} >= {"X", "I", "Alias", "Color", "C", "top"}
    assert r["metrics"]["async_count"] == 1
    assert any(ref["kind"] == "inherit" and ref["qualifier"] == "B" for ref in r["references"])


def test_javascript_basic():
    src = (
        'import Foo from "./util";\n'
        "export const x = 1;\n"
        "export class C { m() { return 1; } }\n"
        "export function top() { return 1; }\n"
    )
    r = analyze.analyze_inline(content=src, filename="x.js")
    assert r["file"]["language"] == "javascript"
    by = _by_qname(r["symbols"])
    assert "x" in by and by["x"]["kind"] == "constant"
    assert "C" in by and by["C"]["kind"] == "class"
    assert "C.m" in by
    assert "top" in by


# --- Go -------------------------------------------------------------------


def test_go_basic():
    src = (
        "// Package main is the entry.\n"
        "package main\n\n"
        'import (\n    "fmt"\n    f "fmt"\n)\n\n'
        "const Pi = 3.14\n"
        'var Name = "a"\n\n'
        "type S struct {\n    X int\n    y int\n}\n"
        "type I interface { M() string }\n\n"
        'func top(a int) string { return "" }\n'
        "func TestFoo(t *testing.T) {}\n"
        'func (s *S) Method(n int) string {\n    fmt.Println(n)\n    return ""\n}\n'
        "func main() { top(1) }\n"
    )
    r = analyze.analyze_inline(content=src, filename="main.go")
    assert r["file"]["language"] == "go"
    assert r["module_doc"] == "Package main is the entry."
    by = _by_qname(r["symbols"])
    assert by["main"]["kind"] == "module"
    assert by["main.S"]["kind"] == "class"
    assert by["main.S.X"]["kind"] == "field"
    assert by["main.I"]["kind"] == "interface"
    assert by["main.top"]["kind"] == "function"
    assert by["main.S.Method"]["kind"] == "method"
    assert by["main.main"]["kind"] == "function"
    assert r["metrics"]["has_main_guard"] is True
    assert r["metrics"]["test_count"] == 1
    modules = [imp["module"] for imp in r["imports"]]
    assert modules.count("fmt") == 2  # one with alias, one without
    refs = {(rr["kind"], rr["qualifier"]) for rr in r["references"]}
    assert ("call", "fmt.Println") in refs


# --- Rust -----------------------------------------------------------------


def test_rust_basic():
    src = (
        "//! Crate doc.\n"
        "use std::collections::HashMap;\n"
        "use crate::util::{Foo, Bar as B};\n\n"
        "pub const X: u32 = 1;\n"
        "pub type Alias = String;\n\n"
        "pub struct S { pub field: u32 }\n"
        "pub enum E { A, B(u32) }\n"
        "pub trait T { fn m(&self) -> String; }\n\n"
        "impl S {\n"
        "    pub fn new() -> Self { S { field: 0 } }\n"
        "    pub fn method(&self, n: u32) -> u32 { n + 1 }\n"
        "}\n\n"
        "pub fn top(a: u32) -> u32 { a + 1 }\n"
        "pub async fn fetch() -> String { String::new() }\n\n"
        "fn main() { top(1); }\n\n"
        "#[test]\n"
        "fn it_works() { assert_eq!(1, 1); }\n"
    )
    r = analyze.analyze_inline(content=src, filename="lib.rs")
    assert r["file"]["language"] == "rust"
    assert r["module_doc"] == "Crate doc."
    by = _by_qname(r["symbols"])
    assert by["X"]["kind"] == "constant"
    assert by["S"]["kind"] == "class"
    assert by["S.field"]["kind"] == "field"
    assert by["E"]["kind"] == "enum"
    assert by["T"]["kind"] == "interface"
    assert by["S.new"]["kind"] == "constructor"
    assert by["S.method"]["kind"] == "method"
    assert by["fetch"]["kind"] == "function"
    assert "async" in by["fetch"]["modifiers"]
    assert by["main"]["kind"] == "function"
    assert by["it_works"]["kind"] == "function"
    assert "test" in by["it_works"]["modifiers"]
    modules = [imp["module"] for imp in r["imports"]]
    assert "std::collections::HashMap" in modules
    assert "crate::util::Foo" in modules
    assert any(imp["alias"] == "B" for imp in r["imports"])
    assert r["metrics"]["has_main_guard"] is True
    assert r["metrics"]["async_count"] == 1
    assert r["metrics"]["test_count"] == 1


# --- Java -----------------------------------------------------------------


def test_java_basic():
    src = (
        "package com.example;\n\n"
        "import java.util.List;\n"
        "import static java.util.Collections.emptyList;\n\n"
        "public class Foo extends Bar implements I {\n"
        "    private int x = 1;\n"
        '    public static final String K = "k";\n\n'
        "    public Foo() {}\n"
        "    public List<String> doIt(int n) { return emptyList(); }\n"
        "}\n\n"
        "interface I { void m(); }\n"
    )
    r = analyze.analyze_inline(content=src, filename="Foo.java")
    assert r["file"]["language"] == "java"
    by = _by_qname(r["symbols"])
    assert by["com.example.Foo"]["kind"] == "class"
    assert by["com.example.Foo.x"]["kind"] == "field"
    assert by["com.example.Foo.K"]["kind"] == "constant"  # static final
    assert by["com.example.Foo.Foo"]["kind"] == "constructor"
    assert by["com.example.Foo.doIt"]["kind"] == "method"
    assert by["com.example.I"]["kind"] == "interface"
    assert any(imp["module"] == "java.util.List" for imp in r["imports"])
    assert any(imp["kind"] == "static" for imp in r["imports"])
    assert any(ref["qualifier"] == "Bar" and ref["kind"] == "inherit" for ref in r["references"])


# --- Ruby -----------------------------------------------------------------


def test_ruby_basic():
    src = (
        'require "json"\n'
        'require_relative "./util"\n\n'
        "module Mod\n"
        "  CONST = 42\n\n"
        "  class Foo < Bar\n"
        "    def initialize\n      @name = 'x'\n    end\n\n"
        "    def greet(other)\n      puts 'hi'\n    end\n"
        "  end\n"
        "end\n"
    )
    r = analyze.analyze_inline(content=src, filename="x.rb")
    assert r["file"]["language"] == "ruby"
    by = _by_qname(r["symbols"])
    assert by["Mod"]["kind"] == "module"
    assert by["Mod.Foo"]["kind"] == "class"
    assert by["Mod.CONST"]["kind"] == "constant"
    assert by["Mod.Foo.initialize"]["kind"] == "constructor"
    assert by["Mod.Foo.greet"]["kind"] == "method"
    modules = [imp["module"] for imp in r["imports"]]
    assert "json" in modules
    assert "./util" in modules
    assert any(ref["qualifier"] == "Bar" and ref["kind"] == "inherit" for ref in r["references"])


# --- C / C++ --------------------------------------------------------------


def test_c_basic():
    src = (
        "#include <stdio.h>\n"
        '#include "foo.h"\n\n'
        "#define PI 3.14\n"
        "typedef struct { int x; int y; } Point;\n\n"
        "static int helper(int n) { return n + 1; }\n\n"
        "int main(int argc, char *argv[]) {\n"
        '    printf("%d", helper(2));\n'
        "    return 0;\n"
        "}\n"
    )
    r = analyze.analyze_inline(content=src, filename="main.c")
    assert r["file"]["language"] == "c"
    by = _by_qname(r["symbols"])
    assert by["PI"]["kind"] == "constant"
    assert "Point" in by
    assert by["helper"]["kind"] == "function"
    assert by["main"]["kind"] == "function"
    assert r["metrics"]["has_main_guard"] is True
    modules = [imp["module"] for imp in r["imports"]]
    assert "stdio.h" in modules
    assert "foo.h" in modules


def test_cpp_basic():
    src = "#include <vector>\nnamespace ns {\n    int top(int x) { return x + 1; }\n}\n"
    r = analyze.analyze_inline(content=src, filename="x.cpp")
    assert r["file"]["language"] == "cpp"
    by = _by_qname(r["symbols"])
    assert "ns" in by
    assert "ns.top" in by


# --- C# -------------------------------------------------------------------


def test_csharp_basic():
    src = (
        "using System;\n"
        "namespace App {\n"
        "    public class C {\n"
        "        public int X { get; set; }\n"
        "        public C() {}\n"
        "        public void M() {}\n"
        "    }\n"
        "    public interface I { void M(); }\n"
        "}\n"
    )
    r = analyze.analyze_inline(content=src, filename="C.cs")
    assert r["file"]["language"] == "csharp"
    by = _by_qname(r["symbols"])
    assert "App" in by
    assert by["App.C"]["kind"] == "class"
    assert by["App.C.X"]["kind"] == "property"
    assert by["App.C.C"]["kind"] == "constructor"
    assert by["App.C.M"]["kind"] == "method"
    assert by["App.I"]["kind"] == "interface"
    modules = [imp["module"] for imp in r["imports"]]
    assert "System" in modules


# --- PHP ------------------------------------------------------------------


def test_php_basic():
    src = (
        "<?php\n"
        "namespace App;\n"
        "use App\\Util\\Helper;\n\n"
        "const X = 1;\n\n"
        "interface I { public function m(): string; }\n\n"
        "class Foo extends Bar implements I {\n"
        "    public function __construct() {}\n"
        "    public function m(): string { return ''; }\n"
        "}\n\n"
        "function top(int $a): int { return $a + 1; }\n"
    )
    r = analyze.analyze_inline(content=src, filename="x.php")
    assert r["file"]["language"] == "php"
    by = _by_qname(r["symbols"])
    assert by["App.X"]["kind"] == "constant"
    assert by["App.I"]["kind"] == "interface"
    assert by["App.Foo"]["kind"] == "class"
    assert by["App.Foo.__construct"]["kind"] == "constructor"
    assert by["App.Foo.m"]["kind"] == "method"
    assert by["App.top"]["kind"] == "function"
    assert any(imp["module"].endswith("Helper") for imp in r["imports"])
    assert any(ref["qualifier"] == "Bar" and ref["kind"] == "inherit" for ref in r["references"])


# --- Bash -----------------------------------------------------------------


def test_bash_basic():
    src = (
        "#!/usr/bin/env bash\n"
        "source ./util.sh\n"
        'NAME="value"\n\n'
        'greet() {\n    echo "hello $1"\n}\n\n'
        "function compute {\n    return 0\n}\n\n"
        'greet "world"\n'
    )
    r = analyze.analyze_inline(content=src, filename="run.sh")
    assert r["file"]["language"] == "bash"
    by = _by_qname(r["symbols"])
    assert "greet" in by and by["greet"]["kind"] == "function"
    assert "compute" in by and by["compute"]["kind"] == "function"
    assert "NAME" in by and by["NAME"]["kind"] == "constant"
    modules = [imp["module"] for imp in r["imports"]]
    assert "./util.sh" in modules
    refs = [(rr["qualifier"], rr["in_symbol"]) for rr in r["references"]]
    assert any(q == "echo" for q, _ in refs)
