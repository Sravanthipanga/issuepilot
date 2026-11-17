"""Codebase research: rank a repository's files against an issue.

Deterministic relevance scoring (no embeddings, no API key): explicit
backticked identifiers and file paths in the issue score highest, then
filename matches, then content keyword hits. The import graph is built
alongside so the Research agent can expand from a hot file to its
neighbours on the complex path.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

STOPWORDS = {
    "the", "a", "an", "is", "it", "in", "on", "of", "to", "and", "or",
    "when", "with", "returns", "return", "should", "always", "instead",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
            if t not in STOPWORDS}


def _functions(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def scan_repo(repo_path: str) -> list[dict]:
    """All Python files with their functions and import edges."""
    root = Path(repo_path)
    files = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        imports = set()
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
                elif isinstance(node, ast.Import):
                    imports.update(a.name for a in node.names)
        except SyntaxError:
            pass
        files.append({
            "path": str(path.relative_to(root)),
            "source": source,
            "functions": _functions(source),
            "imports": sorted(imports),
        })
    return files


def rank_files(issue: dict, files: list[dict], top_n: int = 5) -> list[dict]:
    text = f"{issue.get('title', '')} {issue.get('body', '')}"
    backticked = set(re.findall(r"`([^`]+)`", text))
    tokens = _tokens(text)
    ranked = []
    for f in files:
        score = 0
        for mention in backticked:
            if mention == f["path"] or mention.endswith("/" + f["path"]):
                score += 10
            if mention in f["functions"]:
                score += 8
            if mention.split(".")[-1] == Path(f["path"]).stem:
                score += 4
        stem = Path(f["path"]).stem
        if stem in tokens:
            score += 3
        content_tokens = _tokens(f["source"])
        score += min(6, len(tokens & content_tokens) // 4)
        for fn in f["functions"]:
            if fn.lower() in tokens:
                score += 2
        if score > 0:
            ranked.append({**f, "score": score})
    ranked.sort(key=lambda f: (-f["score"], f["path"]))
    return ranked[:top_n]


def neighbours(files: list[dict], target_path: str) -> list[dict]:
    """Files that import the target's module, plus files it imports."""
    stem = Path(target_path).stem
    out = []
    for f in files:
        if f["path"] == target_path:
            continue
        imports_stem = any(imp.split(".")[-1] == stem for imp in f["imports"])
        target = next((x for x in files if x["path"] == target_path), None)
        imported_by_target = target and any(
            imp.split(".")[-1] == Path(f["path"]).stem
            for imp in target["imports"])
        if imports_stem or imported_by_target:
            out.append(f)
    return out
