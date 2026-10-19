# Search public past awards for the same kind of work, and widen the search when the pool is thin.
#
# What this node does, in order:
#  1. It counts the attempt, so the first search is attempt 1 and each retry is the next number.
#     The counter is what route_after_quality_check checks against max_research_attempts.
#  2. It asks Hasibu, through research_next_move, what the next search should be. On the first
#     attempt there is no previous search and the payload says so with null. On a retry the payload
#     carries the move that was just used, how many awards it returned, how many would make a good
#     pool, and one plain sentence saying what was short. The model is told to widen one step at a
#     time, because a broad jump wastes the attempt it spent.
#  3. If the model cannot be reached at all, the node does not stop the run and does not guess a
#     search. It records the error and widens one step in code: strict becomes loose, loose becomes
#     fuzzy, and once matching is already fuzzy the year floor is dropped so every year is searched.
#     The query is left as it was, because matching is what widens a search, not the wording.
#  4. It refuses a move that is not usable before spending a search on it: the match mode must be
#     strict, loose or fuzzy, the query must be text, and the year floor must be a whole year or
#     null. A search run on a bad move looks like evidence that was never there.
#  5. It calls find_comparable_awards under the run's own run_id, so every search is in audit_log.
#     No tender ocid is excluded: these bids are synthetic and have no public record of their own.
#  6. Candidates accumulate across attempts, deduplicated by award_id and keeping the first row
#     seen for each award. A widened search is the same question asked more loosely, so the awards
#     found on the first attempt stay in the pool instead of being thrown away by a fuzzy retry.
#     last_search records only the most recent attempt, because that is the one the model is
#     reasoning about.
#
# What it returns: the move, the attempt count, the search echo the report will quote later, the
# latest search result and the accumulated candidates. The node does not judge any award;
# quality_check does that next.

from typing import Any, Dict, List, Optional, Tuple

from config import settings
from agent.llm import LlmError, ask_json
from agent.state import AgentState
from agent.tools import find_comparable_awards
from agent.community_mcp import describe_schema, fallback_candidates

MATCHES = ("strict", "loose", "fuzzy")

WIDER_MATCH = {"strict": "loose", "loose": "fuzzy", "fuzzy": "fuzzy"}

FIRST_MATCH = "loose"

FALLBACK_AFTER_ATTEMPTS = 2


def _first_search(state: AgentState) -> Dict[str, Any]:
    """The opening search, built in code from the tender's own description.

    The first query decides the pool, and the pool decides the median. Asking the model to phrase it
    meant two runs over the same three bids could benchmark against different middles, and a bid
    could cross the 50% line  between them. The tender description is already written once, by intake,
    so the search starts from that same sentence every time. The model is asked only how to widen a
    search that has already been run and found too thin.
    """
    return {"query": (state.get("description") or "").strip(),
            "match": FIRST_MATCH,
            "min_year": None}


def _wider_search(state: AgentState) -> Dict[str, Any]:
    """Step the search one notch wider in code, for when the model cannot be reached."""
    previous_match = state.get("match", FIRST_MATCH)
    match = WIDER_MATCH.get(previous_match, "fuzzy")
    min_year = state.get("min_year")
    if previous_match == "fuzzy":
        min_year = None
    return {"query": state.get("query", ""), "match": match, "min_year": min_year}


MATCH_ORDER = {"strict": 0, "loose": 1, "fuzzy": 2}


def _not_wider(previous: Dict[str, Any], proposed: Dict[str, Any]) -> bool:
    """Whether a proposed search is no wider than the one already tried.

    A model asked to widen can answer with the same strict search forever, spending every attempt on
    a query already known to return nothing. Fuzzy with no year floor is the widest there is.
    """
    was = MATCH_ORDER.get(previous.get("match"), 0)
    now = MATCH_ORDER.get(proposed.get("match"), 0)
    if now < was:
        return True
    if now == was and not (now == 2 and proposed.get("min_year") is None
                           and previous.get("min_year") is not None):
        return True
    return False


def _previous(state: AgentState) -> Optional[Dict[str, Any]]:
    """Describe the search that was just run, for the model to widen, or null on the first attempt."""
    last = state.get("last_search")
    if not last:
        return None
    returned = last.get("count", 0)
    if returned == 0:
        problem = "no awards matched"
    elif returned < settings.min_comparables:
        problem = (f"only {returned} awards returned, fewer than the {settings.min_comparables} "
                   f"needed to benchmark")
    else:
        problem = (f"{returned} awards returned, fewer than the {settings.good_pool_n} wanted "
                   f"for a good pool")
    return {"query": state.get("query"), "match": state.get("match"),
            "min_year": state.get("min_year"), "returned": returned,
            "target": settings.good_pool_n, "problem": problem}


def _next_move(state: AgentState, attempt: int) -> Tuple[Dict[str, Any], List[str]]:
    """Ask the model to widen the search, falling back to a wider one in code if it cannot answer."""
    previous = _previous(state)
    if previous is None:
        return _first_search(state), []

    payload = {"description": state.get("description"), "attempt": attempt,
               "previous": previous}
    try:
        answer = ask_json("research_next_move", payload)
    except LlmError as exc:
        return _wider_search(state), [f"model could not choose the next search: {exc}"]

    query, match, min_year = answer.get("query"), answer.get("match"), answer.get("min_year")
    if match not in MATCHES:
        raise ValueError(f"research_next_move gave match {match!r}, expected one of {list(MATCHES)}")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"research_next_move gave query {query!r}, expected text")
    if min_year is not None and not isinstance(min_year, int):
        raise ValueError(f"research_next_move gave min_year {min_year!r}, expected a whole year or null")

    proposed = {"query": query.strip(), "match": match, "min_year": min_year}
    if _not_wider(previous, proposed):
        return _wider_search(state), ["the model proposed a search no wider than the one already "
                                      f"tried ({previous.get('match')}); widened in code instead"]
    return proposed, []


def _accumulated(previous: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep every award seen so far, one row per award_id, first one wins."""
    seen = {row["award_id"]: row for row in previous}
    for row in found:
        seen.setdefault(row["award_id"], row)
    return list(seen.values())


def _fallback_due(state: AgentState) -> bool:
    """Whether it is time to ask the community MCP server for a different angle.

    Only once our own search has had two goes and still cannot build a pool worth benchmarking.
    Earlier than that the fixed search is the better answer, and the fallback is there for the
    case it cannot express, not to replace it.
    """
    return (state.get("search_attempts", 0) >= FALLBACK_AFTER_ATTEMPTS
            and len(state.get("candidates", [])) < settings.min_comparables)


def _fallback(state: AgentState) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Ask the community server what the schema really looks like, then run one guarded read."""
    run_id, errors = state["run_id"], []
    schema = describe_schema(run_id)
    if not schema:
        return [], ["community MCP server was asked for the schema and returned nothing"]

    try:
        answer = ask_json("research_fallback_query", {
            "description": state.get("description"), "schema": schema,
            "have_so_far": len(state.get("candidates", [])),
            "need_at_least": settings.min_comparables,
            "previous_query": state.get("query"),
        })
    except LlmError as exc:
        return [], [f"model could not write the fallback query: {exc}"]

    sql = (answer or {}).get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return [], ["research_fallback_query returned no sql"]
    return fallback_candidates(run_id, sql), []


def research_node(state: AgentState) -> dict:
    """Search for comparable past awards, widening the search when the pool is thin."""
    run_id = state["run_id"]
    attempt = state.get("search_attempts", 0) + 1

    move, errors = _next_move(state, attempt)
    search = find_comparable_awards(run_id, move["query"], state.get("category"), None,
                                    move["match"], move["min_year"], None)
    candidates = _accumulated(state.get("candidates", []), search.get("candidates", []))
    fallback_used = None

    if _fallback_due(state):
        found, fallback_errors = _fallback(state)
        errors += fallback_errors
        if found:
            before = len(candidates)
            candidates = _accumulated(candidates, found)
            fallback_used = {"rows_found": len(candidates) - before}

    echo = {"match": move["match"], "category": state.get("category"),
            "min_year": move["min_year"], "attempts": attempt}
    if fallback_used is not None:
        echo["fallback"] = "community_mcp"

    result = {
        "query": move["query"],
        "match": move["match"],
        "min_year": move["min_year"],
        "search_attempts": attempt,
        "search": echo,
        "last_search": search,
        "candidates": candidates,
    }
    if errors:
        result["errors"] = errors
    return result
