# IssuePilot — architecture notes

## Why a graph, not a chain

A chain (reader → writer → done) cannot express the two decisions this
system lives on: *is this issue complex enough to research first?* and
*the tests failed — now what?* Both are functions of state produced
mid-run, which is exactly what a StateGraph's conditional edges are
for. The compiled graph is the single source of truth — `issuepilot
graph` exports it as Mermaid, and the README diagram is drawn from the
same structure.

## State discipline

Agents return partial updates; LangGraph merges them into `AgentState`.
Nothing is passed agent-to-agent except through state, which makes
every run replayable from its checkpoint and every failure attributable
to the node and attempt that produced it (`state.errors`,
`state.trace`).

## Failure semantics

| Failure | Handling |
|---|---|
| Transient tool error in any node | Retry with backoff (resilience wrapper), error noted in state |
| Node fails permanently | Captured in `state.errors`, status `failed`, graph ends cleanly |
| Generated tests fail | Route back to Code Writer with the failure in state; attempt 2 uses a variant transform |
| Tests still fail at the bound | PR payload is emitted anyway, titled `[needs human]`, with the failing output in the body |
| Sandbox timeout | Reported as a failed run with the timeout in the output |

## The writer's two modes

Demo mode exists so the system is *verifiable* without secrets: the
fix library performs real AST transforms, the generated tests really
run, and the PR payload is real. LLM mode swaps only the patch source
(prompt → corrected file); routing, testing, sandboxing, and PR
assembly are unchanged — which is the point of the architecture: the
model is a component, not the system.

## Safety posture

- The target repo is never modified in place; patches apply to a
  disposable copy until the PR is opened through the API.
- Test execution is sandboxed (subprocess with timeout locally;
  network-less, memory-capped container under Docker).
- Opening a real PR is opt-in (`--open-pr`) and requires an explicit
  token; the default is a dry-run payload a human can inspect.
