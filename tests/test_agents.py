import json
from pathlib import Path

from issuepilot import fixes
from issuepilot.agents import classify_complexity
from issuepilot.graph import route_by_complexity, route_by_results, run_issue
from issuepilot.reader import rank_files, scan_repo

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_REPO = str(ROOT / "sample_repo")


def _issue(name: str) -> dict:
    return json.loads((ROOT / "sample_issues" / name).read_text())


def test_complexity_routing_matches_the_brief():
    tax = _issue("issue_tax.json")
    profile = _issue("issue_profile_none.json")
    assert classify_complexity(tax)[0] == "simple"
    assert classify_complexity(profile)[0] == "complex"
    assert route_by_complexity({"plan": {"complexity": "complex"}}) == "complex"
    assert route_by_complexity({"plan": {"complexity": "simple"}}) == "simple"


def test_code_reader_finds_the_right_file():
    files = scan_repo(SAMPLE_REPO)
    ranked = rank_files(_issue("issue_tax.json"), files)
    assert ranked[0]["path"] == "shopcalc/pricing.py"
    ranked = rank_files(_issue("issue_inventory.json"), files)
    assert ranked[0]["path"] == "shopcalc/inventory.py"


def test_fix_transforms_are_real():
    src = (Path(SAMPLE_REPO) / "shopcalc" / "pricing.py").read_text()
    out = fixes.fix_percent_rate(src, "total_with_tax")
    assert "amount * (1 + tax_rate)" in out
    src = (Path(SAMPLE_REPO) / "shopcalc" / "inventory.py").read_text()
    out = fixes.fix_inclusive_range(src, "count_available")
    assert "range(len(items))" in out and "- 1" not in out
    src = (Path(SAMPLE_REPO) / "shopcalc" / "profiles.py").read_text()
    out = fixes.fix_none_guard(src, "display_name")
    assert "if user is None:" in out and "return 'Unknown'" in out


def test_route_by_results_bounds_the_fix_loop():
    assert route_by_results({"test_results": {"ok": True}}) == "pass"
    assert route_by_results({"test_results": {"ok": False}, "attempts": 1,
                             "patch": {"file": "x"}}) == "retry"
    assert route_by_results({"test_results": {"ok": False}, "attempts": 2,
                             "patch": {"file": "x"}}) == "give_up"


def test_end_to_end_simple_issue():
    final = run_issue(_issue("issue_tax.json"), SAMPLE_REPO,
                      thread_id="test-tax")
    assert final["status"] == "success"
    assert final["plan"]["complexity"] == "simple"
    assert "research_notes" not in final  # simple route skips research
    assert final["test_results"]["ok"] is True
    assert final["test_results"]["passed"] >= 3  # 2 generated + 1 existing
    assert final["pr"]["dry_run"] is True
    assert "amount * (1 + tax_rate)" in final["patch"]["after"]


def test_end_to_end_complex_issue_goes_through_research():
    final = run_issue(_issue("issue_profile_none.json"), SAMPLE_REPO,
                      thread_id="test-profile")
    assert final["status"] == "success"
    assert final["plan"]["complexity"] == "complex"
    assert final["research_notes"]  # research agent ran
    assert final["test_results"]["ok"] is True
    assert 'if user is None:' in final["patch"]["after"]


def test_end_to_end_inventory_issue():
    final = run_issue(_issue("issue_inventory.json"), SAMPLE_REPO,
                      thread_id="test-inventory")
    assert final["status"] == "success"
    assert final["test_results"]["passed"] >= 3
