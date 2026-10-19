import pytest

from agent.llm import LlmError
from agent.nodes import quality_check
from agent.nodes.quality_check import quality_check_node


def candidate(award_id, value=4_000_000.0, title="Classroom block construction", currency="KES"):
    return {"award_id": award_id, "ocid": f"ocid-{award_id}", "data_year": 2024, "title": title,
            "buyer_name": "Ministry of Education", "category": "works", "value_kes": value,
            "currency": currency, "supplier_name": "Builder Ltd", "supplier_norm": "builder ltd",
            "lot_group": None, "quality_flags": []}


def same_work(*award_ids):
    return {aid: {"verdict": "same_work", "reason": "classroom block construction"} for aid in award_ids}


def keep_suspect(*award_ids):
    return {aid: {"verdict": "keep", "reason": "multi-site programme, not a stray figure"}
            for aid in award_ids}


def answered(monkeypatch, similarity=None, suspects=None, raises=None):
    """Answer both prompts, or make them fail, and return the captured calls."""
    calls = {"quality_similarity_judgement": [], "quality_suspect_verdicts": []}

    def fake_ask(prompt_name, payload):
        calls[prompt_name].append(payload)
        if raises is not None:
            raise raises
        if prompt_name == "quality_similarity_judgement":
            return {"judgements": similarity(payload) if callable(similarity) else (similarity or {})}
        return {"verdicts": suspects(payload) if callable(suspects) else (suspects or {})}

    monkeypatch.setattr(quality_check, "ask_json", fake_ask)
    return calls


@pytest.fixture(autouse=True)
def logged(audit_conn, monkeypatch):
    """Keep the audit wrapper off the real audit_log for every test in this module."""
    from mcp_server import audit
    from conftest import SharedConnection

    monkeypatch.setattr(audit, "get_conn", lambda: SharedConnection(audit_conn))


def run(candidates, **overrides):
    state = {"run_id": "run-1", "description": "construction of classroom blocks",
             "candidates": candidates, "quality_attempts": 0}
    state.update(overrides)
    return quality_check_node(state)


def test_rule_exclusions_are_counted_by_their_own_codes(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a02", value=0.0), candidate("a03", currency="USD"),
                  candidate("a04")]

    result = run(candidates)

    assert result["excluded"]["value_not_positive"] == 1
    assert result["excluded"]["non_kes"] == 1
    assert [c["award_id"] for c in result["pool"]] == ["a01", "a04"]


def test_the_similarity_prompt_sees_only_the_usable_rows(monkeypatch):
    calls = answered(monkeypatch, similarity=lambda p: same_work("a01"))
    candidates = [candidate("a01"), candidate("a02", value=0.0)]

    run(candidates)

    payload = calls["quality_similarity_judgement"][0]
    assert [a["award_id"] for a in payload["awards"]] == ["a01"]
    assert payload["description"] == "construction of classroom blocks"
    assert payload["awards"][0]["title"] == "Classroom block construction"
    assert payload["awards"][0]["buyer_name"] == "Ministry of Education"


def test_an_award_the_model_calls_different_work_stays_out(monkeypatch):
    answered(monkeypatch, similarity={"a01": {"verdict": "same_work", "reason": "classroom blocks"},
                                      "a02": {"verdict": "different_work", "reason": "office block"}})

    result = run([candidate("a01"), candidate("a02", title="Office block construction")])

    assert [c["award_id"] for c in result["pool"]] == ["a01"]
    assert result["excluded"] == {"different_work": 1}


def test_the_suspect_prompt_carries_the_median_and_the_ratio(monkeypatch):
    calls = answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]),
                     suspects=keep_suspect("a09"))
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates.append(candidate("a09", value=400_000_000.0))

    run(candidates)

    payload = calls["quality_suspect_verdicts"][0]
    assert payload["median_kes"] == 3_500_000.0
    assert payload["awards"][0]["award_id"] == "a09"
    assert payload["awards"][0]["suspect_reason"] == "extreme_high"
    assert payload["awards"][0]["ratio_to_median"] > 10


def test_a_kept_suspect_joins_the_pool_and_an_excluded_one_is_counted(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]),
             suspects={"a09": {"verdict": "keep", "reason": "a real multi-site programme"},
                       "a10": {"verdict": "exclude", "reason": "one classroom, county-wide value"}})
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates += [candidate("a09", value=400_000_000.0), candidate("a10", value=1.0)]

    result = run(candidates)

    assert "a09" in [c["award_id"] for c in result["pool"]]
    assert "a10" not in [c["award_id"] for c in result["pool"]]
    assert result["excluded"]["suspect_excluded"] == 1


def test_a_suspect_the_model_never_answers_for_is_excluded(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]),
             suspects={})
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates.append(candidate("a09", value=400_000_000.0))

    result = run(candidates)

    assert "a09" not in [c["award_id"] for c in result["pool"]]
    assert result["excluded"]["suspect_excluded"] == 1


def test_an_unjudged_usable_row_is_excluded_and_the_error_recorded(monkeypatch):
    answered(monkeypatch, raises=LlmError("groq timed out"))

    result = run([candidate("a01"), candidate("a02")])

    assert result["pool"] == []
    assert "groq timed out" in result["errors"][0]
    assert result["pool_size"] == 0


def test_the_similarity_prompt_is_asked_in_batches_so_one_answer_stays_short(monkeypatch):
    """One answer covering 28 awards came back cut off mid-object, finish_reason 'length'.

    Every award the model never reached was then counted as excluded work and the pool came out
    empty, which is what an empty tender review is not allowed to be. Each batch now carries at
    most judgement_batch_size awards.
    """
    monkeypatch.setattr(quality_check.settings, "judgement_batch_size", 4)
    calls = answered(monkeypatch,
                     similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))

    result = run([candidate(f"a{i:02d}") for i in range(1, 11)])

    assert [len(batch["awards"]) for batch in calls["quality_similarity_judgement"]] == [4, 4, 2]
    assert result["pool_size"] == 10


def test_every_award_is_offered_to_the_model_exactly_once_across_the_batches(monkeypatch):
    monkeypatch.setattr(quality_check.settings, "judgement_batch_size", 3)
    calls = answered(monkeypatch,
                     similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    rows = [candidate(f"a{i:02d}") for i in range(1, 9)]

    run(rows)

    offered = [a["award_id"] for batch in calls["quality_similarity_judgement"]
               for a in batch["awards"]]
    assert sorted(offered) == [row["award_id"] for row in rows]


def test_one_failed_batch_costs_only_the_awards_it_carried(monkeypatch):
    """A batch the model could not answer is recorded, and the batches either side still count."""
    monkeypatch.setattr(quality_check.settings, "judgement_batch_size", 2)
    seen = []

    def fake_ask(prompt_name, payload):
        if prompt_name != "quality_similarity_judgement":
            return {"verdicts": {}}
        ids = [a["award_id"] for a in payload["awards"]]
        seen.append(ids)
        if ids == ["a03", "a04"]:
            raise LlmError("groq timed out")
        return {"judgements": same_work(*ids)}

    monkeypatch.setattr(quality_check, "ask_json", fake_ask)

    result = run([candidate(f"a{i:02d}") for i in range(1, 7)])

    assert seen == [["a01", "a02"], ["a03", "a04"], ["a05", "a06"]]
    assert result["pool_size"] == 4
    assert any("groq timed out" in error for error in result["errors"])


def test_pool_verdicts_use_one_vocabulary(monkeypatch):
    answered(monkeypatch,
             similarity=lambda p: {a["award_id"]: {"verdict": "different_work" if a["award_id"] == "a04"
                                                    else "same_work",
                                                    "reason": "office block" if a["award_id"] == "a04"
                                                    else "classroom blocks"}
                                   for a in p["awards"]},
             suspects=keep_suspect("a09"))
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates[3]["title"] = "Office block construction"
    candidates.append(candidate("a09", value=400_000_000.0))

    result = run(candidates)

    assert result["pool_verdicts"]["a04"] == {"verdict": "exclude", "reason": "office block"}
    assert result["pool_verdicts"]["a01"] == {"verdict": "keep", "reason": "classroom blocks"}
    assert result["pool_verdicts"]["a09"]["verdict"] == "keep"
    assert result["excluded"] == {"different_work": 1}


def test_pool_size_counts_what_actually_survived(monkeypatch):
    answered(monkeypatch, similarity={"a01": {"verdict": "same_work", "reason": "blocks"},
                                      "a02": {"verdict": "different_work", "reason": "office"},
                                      "a03": {"verdict": "same_work", "reason": "blocks"}})

    result = run([candidate("a01"), candidate("a02", title="Office block"),
                  candidate("a03", title="Classroom works")])

    assert result["pool_size"] == 2 == len(result["pool"])
    assert result["quality"]["pool_size"] == 3
    assert result["pool_size"] != result["quality"]["pool_size"]


def test_the_pool_keeps_what_the_report_will_need_to_cite(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))

    result = run([candidate("a01")])

    row = result["pool"][0]
    assert row["award_id"] == "a01"
    assert row["value_kes"] == 4_000_000.0
    assert row["title"] == "Classroom block construction"
    assert row["buyer_name"] == "Ministry of Education"
    assert row["ocid"] == "ocid-a01"
    assert row["data_year"] == 2024


def test_values_arriving_from_postgres_become_plain_floats(monkeypatch):
    from decimal import Decimal

    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [dict(candidate("a01"), value_kes=Decimal("2069300.00"))]

    result = run(candidates)

    value = result["pool"][0]["value_kes"]
    assert isinstance(value, float)
    assert value == 2069300.0
    assert benchmark_price_works_with(result["pool"])


def benchmark_price_works_with(pool):
    """The pool the node builds is accepted by the benchmarking tool as it stands."""
    from mcp_server.tools.benchmark_price import benchmark_price

    return benchmark_price([{"bidder": "Apex", "total": 2069300.0}], pool)["n"] == 1


def test_the_award_payload_shown_to_the_model_is_a_number(monkeypatch):
    from decimal import Decimal

    calls = answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))

    run([dict(candidate("a01"), value_kes=Decimal("2069300.00"))])

    shown = calls["quality_similarity_judgement"][0]["awards"][0]["value_kes"]
    assert isinstance(shown, float)
    assert shown == 2069300.0


def test_quality_attempts_count_up(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work("a01"))

    result = run([candidate("a01")], quality_attempts=2)

    assert result["quality_attempts"] == 3


def test_no_candidates_means_no_model_call_and_an_empty_pool(monkeypatch):
    calls = answered(monkeypatch)

    result = run([])

    assert result["pool"] == []
    assert result["pool_size"] == 0
    assert result["excluded"] == {}
    assert calls == {"quality_similarity_judgement": [], "quality_suspect_verdicts": []}


def test_the_quality_tool_result_is_stored_untouched(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work("a01"))

    result = run([candidate("a01"), candidate("a02", value=0.0)])

    assert result["quality"]["counts"] == {"ok": 1, "suspect": 0, "excluded": 1}
    assert result["quality"]["median_kes"] == 4_000_000.0
    assert result["quality"]["results"][0]["award_id"] == "a01"


@pytest.mark.parametrize("answer", [
    {"a99": {"verdict": "same_work", "reason": "invented"}},
    {"a01": {"verdict": "maybe", "reason": "unsure"}},
    {"a01": {"reason": "no verdict"}},
])
def test_an_unusable_answer_stops_the_run(monkeypatch, answer):
    answered(monkeypatch, similarity=answer)

    with pytest.raises(ValueError) as err:
        run([candidate("a01")])

    assert "quality_similarity_judgement" in str(err.value)


def test_an_unusable_suspect_answer_stops_the_run(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]),
             suspects={"a09": {"verdict": "looks big", "reason": "?"}})
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates.append(candidate("a09", value=400_000_000.0))

    with pytest.raises(ValueError) as err:
        run(candidates)

    assert "quality_suspect_verdicts" in str(err.value)


def test_every_tool_call_is_logged_under_the_run(monkeypatch, audit_conn):
    answered(monkeypatch, similarity=lambda p: same_work("a01"))
    candidates = [candidate(f"a{i:02d}", value=float(i * 1_000_000)) for i in range(1, 6)]
    candidates.append(candidate("a09", value=400_000_000.0))

    run(candidates)

    rows = audit_conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    assert [r["tool"] for r in rows] == ["flag_data_quality"]
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["input"]["candidates"] == candidates


def bid(bidder, stated_total):
    """One bid as intake leaves it, for the scale guard to read a total from."""
    return {"bidder": bidder, "stated_total": stated_total, "computed_total": stated_total,
            "arithmetic_ok": True, "currency": "KES", "items": []}


def pool_ids(result):
    """The award_ids that survived into the pool."""
    return [row["award_id"] for row in result["pool"]]


def test_an_award_fifty_times_below_the_bid_median_is_dropped(monkeypatch):
    """A 20,000 award beside 4,000,000 bids is a different size of job, not a cheaper one."""
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a02"), candidate("a03", value=20_000.0)]

    result = run(candidates, bids=[bid("Bora Contractors (K) Ltd", 4_000_000.0)])

    assert pool_ids(result) == ["a01", "a02"]
    assert result["excluded"]["scale_mismatch"] == 1


def test_an_award_fifty_times_above_the_bid_median_is_dropped(monkeypatch):
    """The guard is symmetric: a huge award beside small bids is the same mistake in reverse."""
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a03", value=200_000_000.0)]

    result = run(candidates, bids=[bid("Koromo Road Works Ltd", 4_000_000.0)])

    assert pool_ids(result) == ["a01"]
    assert result["excluded"]["scale_mismatch"] == 1


def test_an_award_at_exactly_ten_times_the_bid_median_stays(monkeypatch):
    """The line is drawn at more than ten times, so a bid of exactly ten times is kept."""
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a03", value=40_000_000.0)]

    result = run(candidates, bids=[bid("Kapenguria Works Agency Ltd", 4_000_000.0)])

    assert pool_ids(result) == ["a01", "a03"]
    assert "scale_mismatch" not in result["excluded"]


def test_an_award_at_nine_times_the_bid_median_stays(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a03", value=36_000_000.0)]

    result = run(candidates, bids=[bid("Kapenguria Works Agency Ltd", 4_000_000.0)])

    assert pool_ids(result) == ["a01", "a03"]


def test_the_bid_median_is_taken_across_all_the_bids_not_the_first(monkeypatch):
    """One large bid must not drag the scale the whole pool is judged against."""
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a05", value=45_000_000.0)]
    bids = [bid("Uplands Infrastructure Ltd", 100_000_000.0), bid("Koromo Road Works Ltd", 4_000_000.0),
            bid("Rift Valley Roads Agency Ltd", 4_000_000.0)]

    result = run(candidates, bids=bids)

    assert pool_ids(result) == ["a01"]


def test_a_scale_mismatch_is_never_offered_to_the_model(monkeypatch):
    """The model judges whether the work is the same kind, not whether the figure is the same size."""
    calls = answered(monkeypatch,
                     similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a03", value=15_000.0)]

    run(candidates, bids=[bid("Bora Contractors (K) Ltd", 4_000_000.0)])

    offered = [a["award_id"] for a in calls["quality_similarity_judgement"][0]["awards"]]
    assert offered == ["a01"]


def test_scale_mismatches_are_counted_under_their_own_code(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a02", value=25_000.0), candidate("a03", value=30_000.0),
                  candidate("a04", currency="USD")]

    result = run(candidates, bids=[bid("Bora Contractors (K) Ltd", 4_000_000.0)])

    assert result["excluded"]["scale_mismatch"] == 2
    assert result["excluded"]["non_kes"] == 1


def test_the_guard_switches_off_when_the_bids_carry_no_usable_total(monkeypatch):
    """No figure to judge against means no exclusions: a rule with no input does not fire."""
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))
    candidates = [candidate("a01"), candidate("a02", value=40_000_000.0)]
    bids = [bid("Broken Ltd", 0.0), bid("Also Broken Ltd", None)]

    result = run(candidates, bids=bids)

    assert pool_ids(result) == ["a01", "a02"]
    assert "scale_mismatch" not in result["excluded"]


def test_a_run_with_no_bids_at_all_keeps_its_whole_pool(monkeypatch):
    answered(monkeypatch, similarity=lambda p: same_work(*[a["award_id"] for a in p["awards"]]))

    result = run([candidate("a01"), candidate("a02", value=40_000_000.0)])

    assert pool_ids(result) == ["a01", "a02"]
    assert "scale_mismatch" not in result["excluded"]
