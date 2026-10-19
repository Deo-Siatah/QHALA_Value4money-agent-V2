# Wire the eight nodes into one run and decide where each step leads next.
#
# The run is a fixed line with two loops in it:
#   START -> intake -> research -> quality_check -> score -> draft -> verify_sources -> approval -> file -> END
#
# intake reads the bid PDFs. research finds public past awards for the same kind of work.
# quality_check drops the unusable candidates and asks the model about the suspicious ones.
# score places the bid totals against the cleaned pool. draft asks the model for short wording
# and renders the committee file. verify_sources checks that every finding cites a real award.
# approval waits for a named human. file writes the report and is the only step that changes anything.
#
# Two loops, both capped in config.py so a run can never search or redraft forever:
#   quality_check -> research   while the pool is too thin to benchmark and fewer than max_research_attempts searches are done
#   verify_sources -> draft     while the draft fails its checks and fewer than max_draft_attempts redrafts are done
# When a cap is reached the run moves on with the evidence it has, and thin evidence stays thin.
#
# The research loop is not a retry counter, it is a measure of the pool, because searching twice for a
# pool that is already good wastes time and drags in worse matches. Three bands, all settings:
#   0 to min_comparables-1   too thin to say anything, search again while tries remain
#   min_comparables to good_pool_n-1   usable but thin, score now and let the draft report medium confidence
#   good_pool_n and above    good enough, score immediately
# The size measured here is pool_size, which quality_check publishes after the model has judged which
# awards are the same kind of work. The size the rules tool reported is larger and would stop the loop
# on awards that are about to be thrown away. default good_pool_n is 10: below high_confidence_n of 15,
# so a pool that stops the loop early is labelled medium in the report and the committee can see how
# much evidence there was.
# The model never decides the route and never does the arithmetic: it only writes words and
# judges similarity. Each router below is a plain function of state, so a rule can be read and tested on its own.
#
# approval is an ordinary node that calls interrupt() inside itself, so the pause and the resume
# payload live in agent/nodes/approval.py rather than here. Nothing else in this file stops the run.
#
# Nodes are registered when build_graph is called, not at import, so a test can hand it fakes.

from typing import Literal
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from config import settings
from agent.state import AgentState
from agent.tracing import track_graph
from agent.nodes.intake import intake_node
from agent.nodes.research import research_node
from agent.nodes.quality_check import quality_check_node
from agent.nodes.score import score_node
from agent.nodes.draft import draft_node
from agent.nodes.verify_sources import verify_sources_node
from agent.nodes.approval import approval_node
from agent.nodes.file import file_node


def route_after_intake(state: AgentState) -> Literal["research"] | Literal["__end__"]:
    """Go to research, unless no bid could be parsed and there is nothing to compare."""
    return "research" if state.get("bids") else END


def route_after_quality_check(state: AgentState) -> Literal["research", "score"]:
    """Score on a usable pool, search again only on a pool too thin to benchmark and while tries remain."""
    pool_size = state.get("pool_size", 0)
    if pool_size >= settings.good_pool_n:
        return "score"
    if pool_size < settings.min_comparables and state.get("search_attempts", 0) < settings.max_research_attempts:
        return "research"
    return "score"


def route_after_verify_sources(state: AgentState) -> Literal["draft", "approval"]:
    """Redraft while the last draft failed its checks and the draft cap is not reached, else ask a human."""
    if not state.get("verify_ok", False) and state.get("verify_rounds", 0) < settings.max_draft_attempts:
        return "draft"
    return "approval"


def route_after_approval(state: AgentState) -> Literal["file"] | Literal["__end__"]:
    """File only when a named human approved; a pending or rejected run stops here."""
    return "file" if state.get("approval_status") == "approved" else END


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Build and compile the run, with a checkpointer so the approval interrupt can resume."""
    builder = StateGraph(AgentState)

    # Nodes are registered as plain functions, not traced ones. The LangGraph tracer records every
    # node itself and is what carries the graph structure to Opik, so wrapping them here as well
    # would log each node twice.
    for name, node in [("intake", intake_node), ("research", research_node),
                       ("quality_check", quality_check_node), ("score", score_node),
                       ("draft", draft_node), ("verify_sources", verify_sources_node),
                       ("approval", approval_node), ("file", file_node)]:
        builder.add_node(name, node)

    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route_after_intake, {"research": "research", END: END})
    builder.add_edge("research", "quality_check")
    builder.add_conditional_edges("quality_check", route_after_quality_check,
                                  {"research": "research", "score": "score"})
    builder.add_edge("score", "draft")
    builder.add_edge("draft", "verify_sources")
    builder.add_conditional_edges("verify_sources", route_after_verify_sources,
                                  {"draft": "draft", "approval": "approval"})
    builder.add_conditional_edges("approval", route_after_approval, {"file": "file", END: END})
    builder.add_edge("file", END)

    return track_graph(builder.compile(checkpointer=checkpointer))
