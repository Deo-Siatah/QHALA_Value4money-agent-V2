# Drop the unusable candidate awards, then ask the model only the two questions rules cannot answer.
#
# What this node does, in order:
#  1. It hands the whole candidate list to flag_data_quality under the run's own run_id. The rules in
#     that tool are fixed and need no model: no value, a zero or negative value, a currency that is
#     not KES, a duplicate row and a missing award_id are all out, and a value ten times the median
#     is marked suspect. Its result is stored untouched in quality.
#  2. It asks Hasibu, through quality_similarity_judgement, whether each usable award is the same
#     kind of work as the tender. Only the rows the rules kept are offered. An award judged
#     different_work leaves the pool and is counted as different_work.
#  3. It asks, through quality_suspect_verdicts, one separate question about the suspect rows only:
#     is this a real award of this kind of work whose size is genuine, or is the record wrong. The
#     two prompts never see the same award, so nothing is judged twice. A suspect kept joins the
#     pool; a suspect excluded is counted as suspect_excluded.
#  4. An award the model did not answer for stays out of the pool, whichever prompt it came from,
#     and a model that could not be reached at all is recorded in errors. The same rule either way:
#     an award nobody judged is not evidence, and it never reaches the median. A run that loses its
#     pool to an unreachable model says there was not enough evidence rather than guessing.
#  5. An answer that names an award_id the tool never gave, or a verdict outside the vocabulary the
#     prompt documents, stops the run. Silently folding a broken answer into the exclusions would
#     hide a model that has stopped following the format.
#  6. pool_verdicts is stored in one vocabulary, keep or exclude, whatever prompt produced it, so
#     later steps and the report read a single shape. excluded counts every dropped candidate under
#     the reason it was dropped, which is what the report prints.
#  7. pool keeps the surviving candidate rows whole, so the draft can still cite the award title,
#     buyer and year, and so benchmark_price finds the award_id and value_kes it needs.
#  8. pool_size is published as len(pool). The size the rules tool reported is larger and says
#     nothing about the model's judgement; route_after_quality_check reads this one number to decide
#     whether to search again.
#
# What it returns: the rules result, the normalised verdicts, the pool, its size, the exclusion
# counts and the attempt count.

from typing import Any, Dict, List, Optional, Tuple

from config import settings

from agent.llm import LlmError, ask_json
from agent.state import AgentState
from agent.tools import flag_data_quality

SAME_WORK = {"same_work": "keep", "different_work": "exclude"}
KEEP_OR_EXCLUDE = ("keep", "exclude")


def _award_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """Describe one candidate award for the similarity prompt."""
    return {"award_id": row["award_id"], "title": row.get("title"),
            "value_kes": row.get("value_kes"), "buyer_name": row.get("buyer_name"),
            "data_year": row.get("data_year")}


def _suspect_payload(result: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Describe one suspect award for the suspect prompt, with why it was flagged and by how much."""
    return {"award_id": result["award_id"], "title": row.get("title"),
            "value_kes": row.get("value_kes"),
            "ratio_to_median": result.get("ratio_to_median"),
            "suspect_reason": ", ".join(result.get("reasons", []))}


def _verdicts(prompt_name: str, asked: Dict[str, Any], expected: List[str],
              vocabulary: Tuple[str, ...]) -> Dict[str, Dict[str, str]]:
    """Read one prompt's verdicts, refusing an unknown award_id or a verdict outside the vocabulary."""
    answered = asked.get("judgements") or asked.get("verdicts") or {}
    verdicts: Dict[str, Dict[str, str]] = {}
    for award_id, entry in answered.items():
        if award_id not in expected:
            raise ValueError(f"{prompt_name} returned award_id {award_id!r}, which was not offered")
        verdict = (entry or {}).get("verdict")
        if verdict not in vocabulary:
            raise ValueError(f"{prompt_name} returned verdict {verdict!r} for {award_id}, "
                             f"expected one of {list(vocabulary)}")
        verdicts[award_id] = {"verdict": verdict, "reason": (entry or {}).get("reason") or ""}
    return verdicts


def _ask(prompt_name: str, build_payload: Any, expected: List[str],
         vocabulary: Tuple[str, ...]) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """Ask one prompt in batches and return its verdicts, or record why there are none.

    The awards are judged a batch at a time because one answer covering every candidate was cut off
    part way through: the model ran out of tokens mid-object, the reply stopped being JSON, and every
    award it had not reached was then counted as excluded work, leaving an empty pool. A batch that
    cannot be answered costs only the awards it carried; the batches either side still count.
    """
    verdicts: Dict[str, Dict[str, str]] = {}
    errors: List[str] = []
    for batch in _batches(expected):
        try:
            asked = ask_json(prompt_name, build_payload(batch))
        except LlmError as exc:
            errors.append(f"model could not answer {prompt_name}: {exc}")
            continue
        verdicts.update(_verdicts(prompt_name, asked, batch, vocabulary))
    return verdicts, errors


def _batches(ids: List[str]) -> List[List[str]]:
    """Split the awards into groups no larger than judgement_batch_size."""
    size = max(1, settings.judgement_batch_size)
    return [ids[start:start + size] for start in range(0, len(ids), size)]


def _as_money(row: Dict[str, Any]) -> Dict[str, Any]:
    """Give one pooled award its value as a float.

    Postgres returns a NUMERIC column as a Decimal. The tools take floats, and the model is shown
    figures, so the conversion happens here, once, where the database rows meet the rest of the run.
    """
    if row.get("value_kes") is None:
        return dict(row)
    return {**row, "value_kes": float(row["value_kes"])}


def _bid_median_kes(bids: List[Dict[str, Any]]) -> Optional[float]:
    """The middle stated total across the bids, or None when no bid carries a usable figure."""
    totals = [float(bid["stated_total"]) for bid in bids
              if bid.get("stated_total") is not None and float(bid["stated_total"]) > 0]
    if not totals:
        return None
    totals.sort()
    middle = len(totals) // 2
    if len(totals) % 2:
        return totals[middle]
    return (totals[middle - 1] + totals[middle]) / 2


def _off_scale_ids(candidates: List[Dict[str, Any]], bids: List[Dict[str, Any]],
                   ratio: float) -> set:
    """Award ids whose value is more than ratio times, or less than 1/ratio times, the bid median.

    Words can match while size does not: a 20,000 award is not a cheap version of a 4,000,000 job
    beside a 4,000,000 bid, it is a different size of job. Both directions are dropped, because a
    huge award beside small bids is the same category error in reverse. With no figure to judge
    against the rule does not fire: a rule with no input must not invent an exclusion.
    """
    median = _bid_median_kes(bids)
    if median is None or ratio <= 0:
        return set()
    dropped = set()
    for row in candidates:
        value = row.get("value_kes")
        if value is None:
            continue
        value = float(value)
        if value > median * ratio or value < median / ratio:
            dropped.add(row["award_id"])
    return dropped


def quality_check_node(state: AgentState) -> dict:
    """Rule-check the candidates, judge the ones rules cannot settle, and build the pool."""
    run_id = state["run_id"]
    candidates = state.get("candidates", [])
    description = state.get("description")
    quality = flag_data_quality(run_id, candidates)
    rows = {row["award_id"]: row for row in candidates}

    results = quality.get("results", [])
    off_scale = _off_scale_ids(candidates, state.get("bids", []), settings.max_scale_ratio)
    usable = [r["award_id"] for r in results if r["verdict"] == "ok" and r["award_id"] not in off_scale]
    suspects = [r["award_id"] for r in results
                if r["verdict"] == "suspect" and r["award_id"] not in off_scale]

    similarity, errors = _ask(
        "quality_similarity_judgement",
        lambda batch: {"description": description,
                       "awards": [_award_payload(_as_money(rows[award_id])) for award_id in batch]},
        usable, tuple(SAME_WORK))
    suspect_answers, suspect_errors = _ask(
        "quality_suspect_verdicts",
        lambda batch: {"description": description, "median_kes": quality.get("median_kes"),
                       "awards": [_suspect_payload(flagged, _as_money(rows[flagged["award_id"]]))
                                  for flagged in results if flagged["award_id"] in batch]},
        suspects, KEEP_OR_EXCLUDE)

    pool_verdicts = {award_id: {"verdict": SAME_WORK[entry["verdict"]], "reason": entry["reason"]}
                     for award_id, entry in similarity.items()}
    pool_verdicts.update(suspect_answers)

    pool: List[Dict[str, Any]] = []
    excluded: Dict[str, int] = {}
    for result in results:
        award_id = result["award_id"]
        if award_id in off_scale:
            excluded["scale_mismatch"] = excluded.get("scale_mismatch", 0) + 1
        elif result["verdict"] == "excluded":
            reason = result["reasons"][0] if result["reasons"] else "excluded"
            excluded[reason] = excluded.get(reason, 0) + 1
        elif result["verdict"] == "suspect":
            if suspect_answers.get(award_id, {}).get("verdict") == "keep":
                pool.append(_as_money(rows[award_id]))
            else:
                excluded["suspect_excluded"] = excluded.get("suspect_excluded", 0) + 1
        elif similarity.get(award_id, {}).get("verdict") == "same_work":
            pool.append(_as_money(rows[award_id]))
        else:
            excluded["different_work"] = excluded.get("different_work", 0) + 1

    result: Dict[str, Any] = {
        "quality": quality,
        "pool": pool,
        "pool_size": len(pool),
        "pool_verdicts": pool_verdicts,
        "excluded": excluded,
        "quality_attempts": state.get("quality_attempts", 0) + 1,
    }
    if errors + suspect_errors:
        result["errors"] = errors + suspect_errors
    return result
