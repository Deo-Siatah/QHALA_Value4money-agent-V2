# Check the draft against the house rules before a human is asked to read it. No model, no tool.
# Five plain checks, each producing one sentence for verify_failures. It decides nothing about the
# report's content and rewrites nothing: it either passes the draft on or says exactly what is wrong
# so the graph can hand the model one more attempt.
#
# What this node checks, and why each one exists:
#  1. There is a report at all. draft leaves review_markdown empty when the tool refused the wording,
#     so a missing report here is the signal that the previous attempt broke a rule.
#  2. Every finding cites at least one award. No source, no finding.
#  3. Every award a finding cites is in the sources table and in the pool the statistics were run on.
#     Citing an award that is not in the pool would put a record in the report that the median, the
#     quartiles and the confidence label say nothing about.
#  4. Every figure the model wrote already exists in the data it was shown. The model was handed the
#     benchmark, the pool and the bid totals, and was told to copy figures rather than compute them.
#     A number that appears nowhere in that payload was invented, and an invented percentage in a
#     committee evidence file is the kind of error this whole project exists to prevent. Years and
#     pool sizes count as allowed, because the payload carries them too. A minus the model writes has
#     to be a minus in the data, and a magnitude is compared on its own. Identifiers are left out:
#     the digits in an award_id like a03 are not a figure anyone may quote, so reading numbers out
#     of them would let a small invented number pass as if it were data.
#  5. When the statistics tool reported insufficient confidence, the rendered report must say there
#     were not enough comparable awards. That sentence is added by the tool, so finding it missing
#     means the file does not say what it must.
#
# It sets verify_ok and appends its failures. route_after_verify_sources reads verify_ok against
# max_draft_attempts to decide whether to redraft or to ask a human.

import re
from typing import Any, Dict, Iterable, List, Set

from agent.state import AgentState

NOT_ENOUGH = "Not enough comparable awards to benchmark these bids."

NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

IDENTIFIER_FIELDS = ("award_id", "ocid")

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*$")


def _numbers_in(value: Any, skip_identifiers: bool = True) -> Iterable[float]:
    """Yield every number that appears anywhere in a value, however deeply it is nested."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, str):
        if skip_identifiers and IDENTIFIER.match(value):
            return
        for match in NUMBER.finditer(value):
            try:
                yield float(match.group().replace(",", ""))
            except ValueError:
                continue
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in IDENTIFIER_FIELDS:
                continue
            yield from _numbers_in(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _numbers_in(item)


def _allowed_numbers(state: AgentState) -> Set[float]:
    """Every figure the model was shown, so only invented numbers can fail the check."""
    allowed: Set[float] = set()
    for value in [state.get("benchmark", {}), state.get("pool", []), state.get("bids", []),
                  state.get("excluded", {}), state.get("search", {})]:
        allowed.update(_numbers_in(value))
    allowed.update(_numbers_in(state.get("tender_ref"), skip_identifiers=False))
    return allowed


def _model_text(state: AgentState) -> List[tuple]:
    """The summary, every bid comment and every finding text, each with where it came from."""
    texts = [("summary", state.get("summary", "") or "")]
    for bidder, comment in (state.get("bid_comments") or {}).items():
        texts.append((f"comment for {bidder}", comment or ""))
    for index, finding in enumerate(state.get("findings", []), start=1):
        texts.append((f"finding {index}", finding.get("text", "") or ""))
    return texts


def _check_sources(state: AgentState) -> List[str]:
    """Every finding needs a source, and the source has to be a real pooled award."""
    listed = {source["award_id"] for source in state.get("sources", [])}
    pooled = {row["award_id"] for row in state.get("pool", [])}
    problems = []
    for index, finding in enumerate(state.get("findings", []), start=1):
        cited = finding.get("sources") or []
        if not cited:
            problems.append(f"finding {index} has no source: no source, no finding")
            continue
        for award_id in cited:
            if award_id not in listed:
                problems.append(f"finding {index} cites {award_id}, which is not in the sources table")
            elif award_id not in pooled:
                problems.append(f"finding {index} cites {award_id}, which is not in the benchmarked pool")
    return problems


def _shown(number: float) -> str:
    """Write a number the way it was written in the text, not in exponent form."""
    return f"{int(number)}" if number.is_integer() else f"{number:g}"


def _written_numbers(text: str) -> Iterable[float]:
    """Yield the numbers a piece of text writes, keeping any minus the writer put there."""
    for match in NUMBER.finditer(text):
        try:
            yield float(match.group().replace(",", ""))
        except ValueError:
            continue


def _check_figures(state: AgentState) -> List[str]:
    """Every figure in the model's own text has to exist in the data the model was shown.

    A written minus must match a minus in the data, so a model cannot turn a bid 3.2% below the
    median into 3.2%. A magnitude is compared on its own, because the same figure is often written
    as "3.2% below" rather than "-3.2%" and redrafting that would be pedantry, not safety.
    """
    allowed = _allowed_numbers(state)
    allowed_magnitudes = {abs(number) for number in allowed}
    problems = []
    for where, text in _model_text(state):
        for number in set(_written_numbers(text)):
            known = number in allowed if number < 0 else abs(number) in allowed_magnitudes
            if not known:
                problems.append(f"{where} quotes {_shown(number)}, which is not a figure in the evidence")
    return sorted(problems, key=lambda problem: problem)


def _check_positions(state: AgentState) -> List[str]:
    """A bid may only be placed against the pool when the pool was big enough to place it.

    benchmark_price returns no percentages at all when the pool is too thin, so a report carrying
    one has written a number the statistics never produced. That is how a 228,639% figure reached a
    committee: a comparison against a median of nothing.
    """
    benchmark = state.get("benchmark", {})
    if benchmark.get("confidence") != "insufficient":
        return []

    problems = []
    for bid in benchmark.get("bids") or []:
        if bid.get("position"):
            problems.append(f"{bid.get('bidder')} is labelled {bid['position']}, but with no "
                            "comparable awards a bid cannot be labelled by position at all")
        if bid.get("pct_vs_median") is not None:
            problems.append(f"{bid.get('bidder')} is given {bid['pct_vs_median']}% against a "
                            "median that was never computed")
    return problems


def verify_sources_node(state: AgentState) -> dict:
    """Check the draft's sources and figures, and say whether a human may see it."""
    problems: List[str] = []
    markdown = state.get("review_markdown") or ""

    if not markdown.strip():
        problems.append("no review to verify: the previous draft was refused")
    problems += _check_sources(state)
    problems += _check_figures(state)
    problems += _check_positions(state)
    if state.get("benchmark", {}).get("confidence") == "insufficient" and NOT_ENOUGH not in markdown:
        problems.append(f"the evidence is thin but the report does not say: {NOT_ENOUGH}")

    return {"verify_ok": not problems, "verify_failures": problems}
