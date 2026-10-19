# Ask the model for the wording of the committee file, then render the file with the tool.
# The model writes sentences. This file writes the structure: headings, tables, money, the fixed
# notice and the limitations all come from draft_review_file, so none of them can drift.
#
# What this node does, in order:
#  1. It builds the payload for draft_review_wording out of state alone: the tender reference, the
#     description of the work, the benchmark exactly as the statistics tool returned it, one row per
#     bid joining the parsed document to its total, percentage and position, the cleaned pool as the
#     awards it may cite, and the exclusion counts. The model sees every number it is allowed to use
#     and nothing else, which is what lets verify_sources hold it to them afterwards.
#  2. It asks the model once. The answer is wording only: a summary, one comment per bidder and the
#     findings with the award ids each rests on. The prompt tells it not to write Markdown, because
#     a report it formatted itself would bypass the rendering rules entirely.
#  3. It assembles the review for draft_review_file. Bid comments are joined onto the arithmetic
#     issues the tool already reported, so the committee sees what was wrong with the document and
#     what was noticed about the total in the same cell. The search echo and the exclusion counts
#     come from research and quality_check, never from the model.
#  4. It builds the sources list itself, from the pool rows for the award ids the findings actually
#     cite, in the order they are first cited. Not the model's own sources_used list and not the
#     whole pool: the table then holds exactly what is cited, and the tool's rule that a cited id
#     must exist cannot be satisfied by a list the model wrote itself.
#  5. It calls draft_review_file under the run's own run_id and stores the markdown, so the report
#     and the wording behind it both land in audit_log.
#  6. If the tool refuses the wording, because a finding cited an award that was not in the sources
#     or because the model used language the house rules ban, the message is recorded and no report is
#     written. verify_sources then fails on the missing markdown and the graph hands the model up to
#     two more attempts. A run that never gets a clean report says so rather than shipping one that
#     breaks the rules.
#  7. It counts this attempt in verify_rounds, which is the counter route_after_verify_sources reads.
#
# What it returns: the markdown, the summary, the comments, the findings, the sources and the count.

from typing import Any, Dict, List

from mcp_server.tools.draft_review_file import ReviewError
from agent.llm import ask_json
from agent.state import AgentState
from agent.tools import draft_review_file

SOURCE_FIELDS = ("award_id", "ocid", "data_year", "buyer_name", "title", "value_kes")


def _award_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Describe one pooled award for the wording prompt."""
    return {"award_id": row["award_id"], "title": row.get("title"),
            "buyer_name": row.get("buyer_name"), "value_kes": row.get("value_kes"),
            "data_year": row.get("data_year")}


def _bid_payload(parsed: Dict[str, Any], scored: Dict[str, Any]) -> Dict[str, Any]:
    """Join one parsed bid to its benchmarked figures and its reported arithmetic issues."""
    return {"bidder": parsed["bidder"], "total": scored["total"],
            "pct_vs_median": scored["pct_vs_median"], "position": scored["position"],
            "arithmetic_issues": parsed.get("issues", [])}


def _wording_payload(state: AgentState) -> Dict[str, Any]:
    """Build everything the model is shown when it writes the wording."""
    scored = {row["bidder"]: row for row in state["benchmark"]["bids"]}
    return {
        "tender_ref": state["tender_ref"],
        "description": state.get("description"),
        "benchmark": state["benchmark"],
        "bids": [_bid_payload(bid, scored[bid["bidder"]]) for bid in state["bids"]],
        "awards": [_award_payload(row) for row in state.get("pool", [])],
        "excluded": state.get("excluded", {}),
    }


def _cited_sources(findings: List[Dict[str, Any]], pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """List the pooled awards the findings cite, once each, in the order they are first cited.

    An id that is not in the pool is left out here on purpose: the finding still names it, so
    draft_review_file is the one that refuses the review and says which id does not exist.
    """
    rows = {row["award_id"]: row for row in pool}
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for finding in findings:
        for award_id in finding.get("sources", []):
            if award_id in seen or award_id not in rows:
                continue
            seen.add(award_id)
            ordered.append({field: rows[award_id].get(field) for field in SOURCE_FIELDS})
    return ordered


def build_review(state: AgentState, answer: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble what draft_review_file needs: the model's wording inside our structure."""
    comments = answer.get("bid_comments") or {}
    return {
        "run_id": state["run_id"],
        "tender_ref": state["tender_ref"],
        "summary": answer.get("summary") or "",
        "benchmark": state["benchmark"],
        "search": state.get("search", {}),
        "excluded": state.get("excluded", {}),
        "bids": [{"bidder": bid["bidder"], "arithmetic_issues": bid.get("issues", []),
                  "comment": comments.get(bid["bidder"]) or None} for bid in state["bids"]],
        "findings": [{"text": finding.get("text", ""), "sources": finding.get("sources", [])}
                     for finding in answer.get("findings", [])],
        "sources": sources,
    }


def draft_node(state: AgentState) -> dict:
    """Ask the model for the wording, then render the committee file with the tool."""
    answer = ask_json("draft_review_wording", _wording_payload(state))
    findings = [{"text": finding.get("text", ""), "sources": finding.get("sources", [])}
                for finding in answer.get("findings", [])]
    sources = _cited_sources(findings, state.get("pool", []))

    result: Dict[str, Any] = {
        "summary": answer.get("summary") or "",
        "bid_comments": answer.get("bid_comments") or {},
        "findings": findings,
        "sources": sources,
        "verify_rounds": state.get("verify_rounds", 0) + 1,
    }

    try:
        rendered = draft_review_file(state["run_id"], build_review(state, answer, sources))
    except ReviewError as exc:
        result["review_markdown"] = ""
        result["errors"] = [f"draft_review_file refused the wording: {exc}"]
        return result

    result["review_markdown"] = rendered["markdown"]
    return result
