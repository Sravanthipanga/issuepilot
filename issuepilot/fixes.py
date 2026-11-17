"""The Code Writer's deterministic fix library (demo mode).

Each fix is a real AST transformation with a name, the issue keywords
that select it, the transform itself, and a test template that verifies
the behaviour the issue asked for. With an LLM key configured, the
writer asks the model for a patch instead — the graph, the sandbox,
and the PR payload are identical either way; only the patch source
changes.
"""

from __future__ import annotations

import ast


def _find_function(tree: ast.Module, name: str | None):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if name is None or node.name == name:
                return node
    return None


def fix_percent_rate(source: str, function: str | None, attempt: int = 1) -> str | None:
    """`amount + rate` -> `amount * (1 + rate)` in the target function.

    Retry variant (attempt 2): `amount + amount * rate` — mathematically
    equal, textually different; the fix loop tries it when the first
    form somehow fails verification.
    """
    tree = ast.parse(source)
    fn = _find_function(tree, function)
    if fn is None:
        return None
    changed = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.BinOp) \
                and isinstance(node.value.op, ast.Add):
            left, right = node.value.left, node.value.right
            if attempt == 1:
                node.value = ast.BinOp(
                    left=left, op=ast.Mult(),
                    right=ast.BinOp(left=ast.Constant(value=1), op=ast.Add(),
                                    right=right))
            else:
                node.value = ast.BinOp(
                    left=left, op=ast.Add(),
                    right=ast.BinOp(left=left, op=ast.Mult(), right=right))
            changed = True
    if not changed:
        return None
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def fix_inclusive_range(source: str, function: str | None, attempt: int = 1) -> str | None:
    """`range(len(x) - 1)` -> `range(len(x))` (off-by-one)."""
    tree = ast.parse(source)
    fn = _find_function(tree, function)
    if fn is None:
        return None
    changed = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "range" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Sub) \
                    and isinstance(arg.right, ast.Constant) \
                    and arg.right.value == 1:
                node.args[0] = arg.left
                changed = True
    if not changed:
        return None
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def fix_none_guard(source: str, function: str | None, attempt: int = 1,
                   guard_return: str = '"Unknown"') -> str | None:
    """Insert `if <first param> is None: return <guard_return>` at the
    top of the target function (after any docstring)."""
    tree = ast.parse(source)
    fn = _find_function(tree, function)
    if fn is None or not fn.args.args:
        return None
    param = fn.args.args[0].arg
    guard = ast.parse(f"if {param} is None:\n    return {guard_return}\n").body[0]
    insert_at = 0
    if fn.body and isinstance(fn.body[0], ast.Expr) \
            and isinstance(fn.body[0].value, ast.Constant) \
            and isinstance(fn.body[0].value.value, str):
        insert_at = 1  # keep the docstring first
    fn.body.insert(insert_at, guard)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


FIXES = {
    "percent_rate": {
        "keywords": ["tax", "percent", "percentage", "%"],
        "apply": fix_percent_rate,
        "summary": "Apply the rate to the amount instead of adding it flat",
    },
    "inclusive_range": {
        "keywords": ["off-by-one", "last item", "skipped", "undercount"],
        "apply": fix_inclusive_range,
        "summary": "Include the final element in the loop range",
    },
    "none_guard": {
        "keywords": ["none", "null", "crashes when", "typeerror"],
        "apply": fix_none_guard,
        "summary": "Guard against a None argument before dereferencing it",
    },
}


def select_fix(issue_text: str) -> str | None:
    text = issue_text.lower()
    best, best_hits = None, 0
    for rule_id, rule in FIXES.items():
        hits = sum(1 for kw in rule["keywords"] if kw in text)
        if hits > best_hits:
            best, best_hits = rule_id, hits
    return best


# ------------------------------------------------------------ test templates
def test_template(rule_id: str, module: str, function: str) -> str:
    """A pytest file verifying the behaviour the issue described."""
    if rule_id == "percent_rate":
        return f'''from {module} import {function}


def test_rate_applies_to_amount():
    assert {function}(100, 0.2) == 120.0


def test_zero_rate_is_identity():
    assert {function}(59.99, 0.0) == 59.99
'''
    if rule_id == "inclusive_range":
        return f'''from {module} import Item, {function}


def test_counts_the_last_item():
    items = [Item("a", True), Item("b", True), Item("c", True)]
    assert {function}(items) == 3


def test_empty_list():
    assert {function}([]) == 0
'''
    if rule_id == "none_guard":
        return f'''from {module} import {function}


def test_none_user_returns_unknown():
    assert {function}(None) == "Unknown"


def test_normal_user_still_works():
    assert {function}({{"first": " Ada ", "last": " Lovelace "}}) == "Ada Lovelace"
'''
    return f'''from {module} import {function}


def test_function_exists():
    assert callable({function})
'''
