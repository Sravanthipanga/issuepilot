"""The five agents, plus the resilience wrapper they all run under.

Each agent is a plain function ``state -> partial state``. The wrapper
gives every node the same failure semantics: retry transient errors
with backoff, then capture the failure into ``state['errors']`` and
let the graph route on state — a dead tool call degrades the run, it
does not crash it.
"""

from __future__ import annotations

import difflib
import functools
import os
import re
import time
from pathlib import Path

from . import fixes
from .reader import neighbours, rank_files, scan_repo
from .sandbox import get_sandbox

COMPLEXITY_KEYWORDS = ["intermittent", "sometimes", "multiple", "across",
                       "investigate", "production", "randomly"]


def with_resilience(name: str, retries: int = 2):
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(state: dict) -> dict:
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    return fn(state)
                except Exception as exc:  # captured into state, by design
                    last_exc = exc
                    errors = list(state.get("errors", []))
                    errors.append({"node": name, "error": str(exc)[:300],
                                   "attempt": attempt + 1})
                    state = {**state, "errors": errors}
                    if attempt < retries:
                        time.sleep(0.5 * (2 ** attempt))
            return {"errors": state.get("errors", []),
                    "status": "failed",
                    "trace": [f"{name}: FAILED after {retries + 1} attempts: "
                              f"{last_exc}"]}
        return wrapped
    return deco


# ------------------------------------------------------------- Code Reader
@with_resilience("code_reader")
def code_reader(state: dict) -> dict:
    files = scan_repo(state["repo_path"])
    ranked = rank_files(state["issue"], files, top_n=5)
    context = [{"path": f["path"], "score": f["score"],
                "functions": f["functions"],
                "excerpt": f["source"][:1200]} for f in ranked]
    return {"code_context": context,
            "trace": [f"code_reader: {len(ranked)} relevant file(s): "
                      + ", ".join(f["path"] for f in ranked)]}


# ----------------------------------------------------------------- Planner
def classify_complexity(issue: dict) -> tuple[str, int, list[str]]:
    body = issue.get("body", "")
    reasons = []
    score = 0
    if len(body) > 450:
        score += 1
        reasons.append("long issue body")
    paths = set(re.findall(r"`([^`]+\.py)`", body))
    if len(paths) > 1:
        score += 1
        reasons.append("multiple files referenced")
    hits = [kw for kw in COMPLEXITY_KEYWORDS if kw in body.lower()]
    if hits:
        score += min(2, len(hits))
        reasons.append(f"complexity signals: {', '.join(hits)}")
    if any(lbl in ("complex", "investigation")
           for lbl in issue.get("labels", [])):
        score += 2
        reasons.append("labelled complex/investigation")
    return ("complex" if score >= 2 else "simple"), score, reasons


@with_resilience("planner")
def planner(state: dict) -> dict:
    issue = state["issue"]
    complexity, score, reasons = classify_complexity(issue)
    context = state.get("code_context", [])
    target_file = context[0]["path"] if context else None
    text = f"{issue.get('title', '')} {issue.get('body', '')}"
    mentioned = set(re.findall(r"`([^`]+)`", text))
    target_function = None
    if context:
        for fn in context[0]["functions"]:
            if fn in mentioned:
                target_function = fn
                break
    rule_id = fixes.select_fix(text)
    steps = [
        f"Read the codebase and rank files against issue "
        f"#{issue.get('number', '?')}",
        f"Target: {target_file} :: {target_function or '(file level)'}",
        f"Apply fix pattern: {rule_id or '(none matched — LLM/manual)'}"
        + (f" — {fixes.FIXES[rule_id]['summary']}" if rule_id else ""),
        "Generate tests for the corrected behaviour",
        "Run the full suite in the sandbox; loop back on failure",
        "Open the pull request (or emit the dry-run payload)",
    ]
    plan = {"complexity": complexity, "score": score,
            "complexity_reasons": reasons, "fix_rule": rule_id,
            "target_file": target_file, "target_function": target_function,
            "steps": steps}
    return {"plan": plan,
            "trace": [f"planner: complexity={complexity} (score {score}: "
                      f"{'; '.join(reasons) or 'single focused change'}); "
                      f"fix={rule_id}"]}


# ---------------------------------------------------------------- Research
@with_resilience("research")
def research(state: dict) -> dict:
    """Complex path only: widen the context around the target file."""
    plan = state.get("plan", {})
    target = plan.get("target_file")
    files = scan_repo(state["repo_path"])
    notes = []
    context = list(state.get("code_context", []))
    if target:
        for nb in neighbours(files, target):
            notes.append(f"{nb['path']} is a graph neighbour of {target} "
                         f"(imports/imported by it); functions: "
                         f"{', '.join(nb['functions']) or 'none'}")
            if all(c["path"] != nb["path"] for c in context):
                context.append({"path": nb["path"], "score": 0,
                                "functions": nb["functions"],
                                "excerpt": nb["source"][:1200]})
    callers = [f["path"] for f in files
               if plan.get("target_function")
               and plan["target_function"] in f["source"]
               and f["path"] != target]
    if callers:
        notes.append(f"{plan['target_function']} is also referenced in: "
                     + ", ".join(callers))
    notes.append("Research pass complete — proceeding with widened context "
                 f"({len(context)} files).")
    return {"code_context": context, "research_notes": notes,
            "trace": [f"research: +{len(notes)} note(s), context widened"]}


# -------------------------------------------------------------- Code Writer
def _llm_patch(state: dict, source: str) -> str | None:
    """LLM mode: ask the configured model for the corrected file."""
    engine = os.environ.get("ISSUEPILOT_ENGINE", "")
    issue = state["issue"]
    prompt = (f"Fix this GitHub issue in the file below. Return ONLY the "
              f"complete corrected file, no commentary.\n\nIssue: "
              f"{issue.get('title')}\n{issue.get('body')}\n\nFile:\n{source}")
    try:
        import httpx
        if engine == "openai" and os.environ.get("OPENAI_API_KEY"):
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization":
                         f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": "gpt-4o",
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        elif engine == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                         "anthropic-version": "2023-06-01"},
                json={"model": "claude-sonnet-4-5", "max_tokens": 4096,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]
        else:
            return None
    except Exception:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text


@with_resilience("code_writer")
def code_writer(state: dict) -> dict:
    plan = state.get("plan", {})
    attempts = state.get("attempts", 0) + 1
    target = plan.get("target_file")
    if not target:
        return {"attempts": attempts, "status": "failed",
                "trace": ["code_writer: no target file in plan"]}
    source = (Path(state["repo_path"]) / target).read_text(encoding="utf-8")
    after = None
    engine = os.environ.get("ISSUEPILOT_ENGINE", "demo")
    if engine != "demo":
        after = _llm_patch(state, source)
    if after is None and plan.get("fix_rule"):
        rule = fixes.FIXES[plan["fix_rule"]]
        kwargs = {}
        if plan["fix_rule"] == "none_guard":
            m = re.search(r'return\s+"([^"]+)"', state["issue"].get("body", ""))
            if m:
                kwargs["guard_return"] = f'"{m.group(1)}"'
        after = rule["apply"](source, plan.get("target_function"),
                              attempt=attempts, **kwargs)
    if after is None or after == source:
        return {"attempts": attempts, "status": "failed",
                "trace": [f"code_writer: attempt {attempts} produced no "
                          "change"]}
    diff = "\n".join(difflib.unified_diff(
        source.splitlines(), after.splitlines(),
        fromfile=f"a/{target}", tofile=f"b/{target}", lineterm=""))
    patch = {"file": target, "before": source, "after": after, "diff": diff}
    note = ""
    if attempts > 1 and state.get("test_results"):
        note = (f" (retry after test failure: "
                f"{state['test_results'].get('failed')} failing)")
    return {"patch": patch, "attempts": attempts,
            "trace": [f"code_writer: attempt {attempts} patched {target}"
                      f"{note}"]}


# -------------------------------------------------------------- Test Writer
@with_resilience("test_writer")
def test_writer(state: dict) -> dict:
    plan = state.get("plan", {})
    patch = state.get("patch")
    if not patch or not plan.get("fix_rule"):
        return {"tests": [], "trace": ["test_writer: nothing to test"]}
    module = plan["target_file"][:-3].replace("/", ".")
    fn = plan.get("target_function") or "target"
    content = fixes.test_template(plan["fix_rule"], module, fn)
    path = f"tests/test_issuepilot_{fn}.py"
    return {"tests": [{"path": path, "content": content}],
            "trace": [f"test_writer: generated {path}"]}


@with_resilience("test_runner")
def test_runner(state: dict) -> dict:
    sandbox = get_sandbox()
    results = sandbox.run(state["repo_path"], state.get("patch"),
                          state.get("tests", []))
    status = "running" if results["ok"] else "tests-failing"
    return {"test_results": results, "status": status,
            "trace": [f"test_runner ({results['runner']}): "
                      f"{results['passed']} passed, "
                      f"{results['failed']} failed"]}


# --------------------------------------------------------------- PR Opener
@with_resilience("pr_opener")
def pr_opener(state: dict) -> dict:
    issue = state["issue"]
    plan = state.get("plan", {})
    results = state.get("test_results", {})
    patch = state.get("patch", {})
    tests_ok = bool(results.get("ok"))
    branch = f"issuepilot/issue-{issue.get('number', 'x')}"
    title = f"Fix #{issue.get('number', '?')}: {issue.get('title', '')}"
    if not tests_ok:
        title = "[needs human] " + title
    body = "\n".join([
        f"Automated fix by IssuePilot for #{issue.get('number', '?')}.",
        "",
        "## Plan",
        *[f"- {s}" for s in plan.get("steps", [])],
        "",
        f"Complexity: {plan.get('complexity', '?')} "
        f"(score {plan.get('score', '?')})",
        f"Fix pattern: {plan.get('fix_rule') or 'n/a'}",
        "",
        "## Tests",
        f"Sandbox ({results.get('runner', '?')}): "
        f"{results.get('passed', 0)} passed, {results.get('failed', 0)} "
        f"failed across {state.get('attempts', 1)} attempt(s).",
        "",
        "## Diff",
        "```diff",
        patch.get("diff", "(no patch)"),
        "```",
    ])
    pr = {"title": title, "body": body, "branch": branch, "url": None,
          "dry_run": True}
    if state.get("open_pr") and state.get("repo_slug") and patch:
        from .github_api import open_pull_request
        url = open_pull_request(state["repo_slug"], branch, "main", title,
                                body, patch, state.get("tests", []))
        pr.update({"url": url, "dry_run": False})
    status = "success" if tests_ok else "tests-failing"
    outcome = f"opened {pr['url']}" if pr["url"] else "dry-run payload ready"
    return {"pr": pr, "status": status,
            "trace": [f"pr_opener: {outcome} (branch {branch})"]}
