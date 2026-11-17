"""Shared state — the contract every agent reads and writes.

Mirrors the brief's ``AgentState { issue, code_context, plan, patch,
tests, pr_url }``, extended with the bookkeeping a real system needs:
complexity routing, research notes, test results, retry attempts, and
an error log. Agent errors are captured *into* state instead of
crashing the graph — partial failure is a normal outcome, not an
exception path.
"""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # inputs
    issue: dict            # {number, title, body, labels}
    repo_path: str
    repo_slug: str         # owner/name, for the GitHub API
    open_pr: bool          # actually open the PR (needs GITHUB_TOKEN)
    # agent outputs
    code_context: list[dict]   # [{path, score, functions, excerpt}]
    plan: dict                 # {complexity, score, fix_rule, target_file,
                               #  target_function, steps, rationale}
    research_notes: list[str]
    patch: dict                # {file, before, after, diff}
    tests: list[dict]          # [{path, content}]
    test_results: dict         # {passed, failed, output, runner}
    pr: dict                   # {title, body, branch, url, dry_run}
    # bookkeeping
    attempts: int
    errors: list[dict]         # [{node, error, attempt}]
    trace: list[str]           # human-readable run log
    status: str                # running | success | tests-failing | failed
