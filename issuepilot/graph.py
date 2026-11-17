"""The orchestration graph — a real LangGraph StateGraph.

    START -> code_reader -> planner
                               |  route_by_complexity (live state)
                    simple ----+---- complex
                      |               |
                      v               v
                 code_writer      research -> code_writer
                      ^                            |
                      |                            v
                      +---- retry ---- test_runner <- test_writer
                                           |
                                pass / give up
                                           v
                                       pr_opener -> END

Routing is a function of state, not a fixed chain: complexity sends
complex issues through Research first, and failing tests loop back to
the Code Writer (bounded by MAX_ATTEMPTS) instead of dying. A
checkpointer is attached so runs are resumable and inspectable.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import agents
from .state import AgentState

MAX_ATTEMPTS = 2


def route_by_complexity(state: AgentState) -> str:
    plan = state.get("plan", {})
    return "complex" if plan.get("complexity") == "complex" else "simple"


def route_by_results(state: AgentState) -> str:
    results = state.get("test_results", {})
    if results.get("ok"):
        return "pass"
    if state.get("attempts", 0) < MAX_ATTEMPTS and state.get("patch"):
        return "retry"
    return "give_up"


def build_graph(checkpointer=None):
    workflow = StateGraph(AgentState)
    workflow.add_node("code_reader", agents.code_reader)
    workflow.add_node("planner", agents.planner)
    workflow.add_node("research", agents.research)
    workflow.add_node("code_writer", agents.code_writer)
    workflow.add_node("test_writer", agents.test_writer)
    workflow.add_node("test_runner", agents.test_runner)
    workflow.add_node("pr_opener", agents.pr_opener)

    workflow.add_edge(START, "code_reader")
    workflow.add_edge("code_reader", "planner")
    workflow.add_conditional_edges(
        "planner", route_by_complexity,
        {"simple": "code_writer", "complex": "research"})
    workflow.add_edge("research", "code_writer")
    workflow.add_edge("code_writer", "test_writer")
    workflow.add_edge("test_writer", "test_runner")
    workflow.add_conditional_edges(
        "test_runner", route_by_results,
        {"pass": "pr_opener", "retry": "code_writer",
         "give_up": "pr_opener"})
    workflow.add_edge("pr_opener", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


def run_issue(issue: dict, repo_path: str, repo_slug: str = "",
              open_pr: bool = False, thread_id: str = "run-1") -> dict:
    """Run one issue through the graph and return the final state."""
    graph = build_graph()
    initial: AgentState = {
        "issue": issue, "repo_path": repo_path, "repo_slug": repo_slug,
        "open_pr": open_pr, "attempts": 0, "errors": [], "trace": [],
        "status": "running",
    }
    return graph.invoke(initial, config={"configurable":
                                         {"thread_id": thread_id}})
