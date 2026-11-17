"""IssuePilot CLI.

    issuepilot demo                          # the bundled end-to-end demo
    issuepilot run --issue-file issue.json --repo ./some-repo
    issuepilot run --issue-number 142 --repo-slug owner/name --repo ./clone
    issuepilot graph                         # print the graph as Mermaid
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from .graph import build_graph, run_issue

app = typer.Typer(help="IssuePilot — multi-agent issue-to-PR orchestration.")
console = Console()
ROOT = Path(__file__).resolve().parents[1]


def _report(final: dict) -> None:
    issue = final.get("issue", {})
    plan = final.get("plan", {})
    results = final.get("test_results", {})
    pr = final.get("pr", {})
    lines = [
        f"[bold]Issue #{issue.get('number', '?')}:[/] {issue.get('title', '')}",
        f"Route: {plan.get('complexity', '?')} "
        f"(score {plan.get('score', '?')}) · fix: {plan.get('fix_rule')}",
        f"Target: {plan.get('target_file')} :: {plan.get('target_function')}",
        f"Tests: {results.get('passed', 0)} passed, "
        f"{results.get('failed', 0)} failed "
        f"({results.get('runner', '?')} sandbox, "
        f"{final.get('attempts', 0)} attempt(s))",
        f"Status: [bold]{final.get('status')}[/]",
    ]
    if pr:
        lines.append(f"PR: {pr.get('url') or 'dry-run payload'} "
                     f"(branch {pr.get('branch')})")
    for entry in final.get("trace", []):
        lines.append(f"[dim]· {entry}[/]")
    for err in final.get("errors", []):
        lines.append(f"[red]error in {err['node']}: {err['error']}[/]")
    console.print(Panel("\n".join(lines), title="IssuePilot run",
                        border_style="green" if final.get("status") == "success"
                        else "yellow"))


@app.command()
def demo() -> None:
    """Run the three bundled sample issues against the sample repo."""
    repo = str(ROOT / "sample_repo")
    for issue_file in sorted((ROOT / "sample_issues").glob("*.json")):
        issue = json.loads(issue_file.read_text())
        final = run_issue(issue, repo, thread_id=f"demo-{issue['number']}")
        _report(final)


@app.command()
def run(
    issue_file: Path = typer.Option(None, "--issue-file",
                                    help="JSON: {number, title, body, labels}"),
    issue_number: int = typer.Option(None, "--issue-number",
                                     help="GitHub issue number (needs --repo-slug and GITHUB_TOKEN)"),
    repo: Path = typer.Option(..., "--repo", help="Path to the repository"),
    repo_slug: str = typer.Option("", "--repo-slug", help="owner/name"),
    open_pr: bool = typer.Option(False, "--open-pr",
                                 help="Actually open the PR on GitHub"),
    out: Path = typer.Option(None, "--out",
                             help="Write the PR payload (title/body/diff) here"),
) -> None:
    if issue_number is not None:
        if not repo_slug:
            raise typer.BadParameter("--issue-number requires --repo-slug")
        from .github_api import fetch_issue

        issue = fetch_issue(repo_slug, issue_number)
    elif issue_file is not None:
        issue = json.loads(issue_file.read_text())
    else:
        raise typer.BadParameter("give --issue-file or --issue-number")
    final = run_issue(issue, str(repo), repo_slug=repo_slug,
                      open_pr=open_pr,
                      thread_id=f"issue-{issue.get('number', 'x')}")
    _report(final)
    if out is not None and final.get("pr"):
        pr = final["pr"]
        out.write_text(f"# {pr['title']}\n\n{pr['body']}\n", encoding="utf-8")
        console.print(f"[dim]Wrote PR payload to {out}[/]")
    if final.get("status") != "success":
        raise typer.Exit(1)


@app.command()
def graph() -> None:
    """Print the orchestration graph in Mermaid syntax."""
    typer.echo(build_graph().get_graph().draw_mermaid())


if __name__ == "__main__":
    app()
