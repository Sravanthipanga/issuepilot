"""GitHub API: fetch an issue, and (when asked) open the PR for real.

The PR Opener uses the git data API end to end — read the base tree,
create blobs for the patched file and the test file, commit, point a
new branch at it, open the pull request. Auth is GITHUB_TOKEN.
"""

from __future__ import annotations

import base64
import os

import httpx

API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def fetch_issue(repo_slug: str, number: int) -> dict:
    resp = httpx.get(f"{API}/repos/{repo_slug}/issues/{number}",
                     headers=_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return {"number": data["number"], "title": data["title"],
            "body": data.get("body") or "",
            "labels": [lbl["name"] for lbl in data.get("labels", [])]}


def open_pull_request(repo_slug: str, branch: str, base: str,
                      title: str, body: str, patch: dict,
                      tests: list[dict]) -> str:
    """Create branch + commit (patch + tests) + PR. Returns the PR URL."""
    h = _headers()
    ref = httpx.get(f"{API}/repos/{repo_slug}/git/ref/heads/{base}",
                    headers=h, timeout=60)
    ref.raise_for_status()
    base_sha = ref.json()["object"]["sha"]
    base_commit = httpx.get(
        f"{API}/repos/{repo_slug}/git/commits/{base_sha}",
        headers=h, timeout=60)
    base_commit.raise_for_status()
    base_tree = base_commit.json()["tree"]["sha"]

    tree_items = []
    for path, content in [(patch["file"], patch["after"])] + [
            (t["path"], t["content"]) for t in tests]:
        blob = httpx.post(f"{API}/repos/{repo_slug}/git/blobs", headers=h,
                          json={"content": base64.b64encode(
                              content.encode()).decode(),
                                "encoding": "base64"}, timeout=60)
        blob.raise_for_status()
        tree_items.append({"path": path, "mode": "100644", "type": "blob",
                           "sha": blob.json()["sha"]})

    tree = httpx.post(f"{API}/repos/{repo_slug}/git/trees", headers=h,
                      json={"base_tree": base_tree, "tree": tree_items},
                      timeout=60)
    tree.raise_for_status()
    commit = httpx.post(f"{API}/repos/{repo_slug}/git/commits", headers=h,
                        json={"message": title, "tree": tree.json()["sha"],
                              "parents": [base_sha]}, timeout=60)
    commit.raise_for_status()
    new_ref = httpx.post(f"{API}/repos/{repo_slug}/git/refs", headers=h,
                         json={"ref": f"refs/heads/{branch}",
                               "sha": commit.json()["sha"]}, timeout=60)
    new_ref.raise_for_status()
    pr = httpx.post(f"{API}/repos/{repo_slug}/pulls", headers=h,
                    json={"title": title, "body": body, "head": branch,
                          "base": base}, timeout=60)
    pr.raise_for_status()
    return pr.json()["html_url"]
