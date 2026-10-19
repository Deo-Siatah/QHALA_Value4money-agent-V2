import pytest

from agent.nodes.verify_sources import verify_sources_node

NOT_ENOUGH = "Not enough comparable awards to benchmark these bids."


@pytest.fixture(autouse=True)
def logged(audit_conn, monkeypatch):
    """Keep the audit wrapper off the real audit_log for every test in this module."""
    from mcp_server import audit
    from conftest import SharedConnection

    monkeypatch.setattr(audit, "get_conn", lambda: SharedConnection(audit_conn))


def award(award_id, value=4_000_000.0, year=2024):
    return {"award_id": award_id, "ocid": f"ocid-{award_id}", "data_year": year,
            "title": "Classroom block construction", "buyer_name": "Ministry of Education",
            "value_kes": value, "category": "works", "currency": "KES", "quality_flags": []}


def state(**overrides):
    base = {
        "run_id": "run-1", "tender_ref": "TENDER/NC/2026/014",
        "description": "construction of classroom blocks",
        "benchmark": {"n": 18, "confidence": "high", "median_kes": 4_800_000.0,
                      "q1_kes": 4_100_000.0, "q3_kes": 5_600_000.0,
                      "sources": ["a01", "a02", "a03"],
                      "bids": [{"bidder": "Apex", "total": 5_000_000.0, "pct_vs_median": 4.2,
                                "position": "within_range"},
                               {"bidder": "Bora", "total": 9_000_000.0, "pct_vs_median": 87.5,
                                "position": "far_above"},
                               {"bidder": "Zed", "total": 2_750_000.0, "pct_vs_median": -3.2,
                                "position": "within_range"}]},
        "pool": [award("a01"), award("a02", 5_000_000.0), award("a03", 3_000_000.0, 2023)],
        "bids": [{"bidder": "Apex", "tender_ref": "TENDER/NC/2026/014", "currency": "KES", "items": [],
                  "stated_total": 5_000_000.0, "computed_total": 5_000_000.0, "arithmetic_ok": True,
                  "issues": []},
                 {"bidder": "Bora", "tender_ref": "TENDER/NC/2026/014", "currency": "KES", "items": [],
                  "stated_total": 9_000_000.0, "computed_total": 9_000_000.0, "arithmetic_ok": True,
                  "issues": []}],
        "summary": "Eighteen awards of classroom construction were compared, and one bid total sits 87.5% above the median of that pool.",
        "bid_comments": {"Apex": "4.2% above the median, inside the normal range.",
                         "Bora": "87.5% above the median of eighteen similar awards."},
        "findings": [{"text": "One bid total is 87.5% above the median of eighteen similar awards.",
                      "sources": ["a01", "a02"]}],
        "sources": [{"award_id": "a01", "ocid": "ocid-a01", "data_year": 2024,
                     "buyer_name": "Ministry of Education", "title": "Classroom block construction",
                     "value_kes": 4_000_000.0},
                    {"award_id": "a02", "ocid": "ocid-a02", "data_year": 2024,
                     "buyer_name": "Ministry of Education", "title": "Classroom works",
                     "value_kes": 5_000_000.0}],
        "review_markdown": "# Price review evidence: TENDER/NC/2026/014\n\n## Findings\n",
        "excluded": {"different_work": 1},
    }
    base.update(overrides)
    return base


def failures(**overrides):
    return verify_sources_node(state(**overrides))["verify_failures"]


def test_a_clean_draft_verifies():
    result = verify_sources_node(state())

    assert result["verify_ok"] is True
    assert result["verify_failures"] == []


def test_a_missing_report_fails():
    result = verify_sources_node(state(review_markdown=""))

    assert result["verify_ok"] is False
    assert "no review to verify" in result["verify_failures"][0]


def test_a_finding_without_a_source_fails():
    result = verify_sources_node(state(findings=[{"text": "Something is off.", "sources": []}]))

    assert result["verify_ok"] is False
    assert "finding 1" in result["verify_failures"][0]
    assert "no source" in result["verify_failures"][0]


def test_a_finding_citing_an_award_that_is_not_in_the_sources_fails():
    result = verify_sources_node(state(findings=[{"text": "Eighteen awards.", "sources": ["a99"]}]))

    assert result["verify_ok"] is False
    assert "a99" in result["verify_failures"][0]


def test_a_finding_citing_an_award_outside_the_benchmarked_pool_fails():
    listed = state()["sources"] + [{"award_id": "a04", "ocid": "ocid-a04", "data_year": 2024,
                                    "buyer_name": "County Government", "title": "Classroom works",
                                    "value_kes": 4_500_000.0}]

    result = verify_sources_node(state(findings=[{"text": "Eighteen awards.", "sources": ["a04"]}],
                                       sources=listed))

    assert result["verify_ok"] is False
    assert "a04" in result["verify_failures"][0]


def test_a_figure_that_appears_nowhere_fails():
    text = "One bid total is 1250000.0% above the median of eighteen similar awards."

    result = verify_sources_node(state(findings=[{"text": text, "sources": ["a01"]}]))

    assert result["verify_ok"] is False
    assert "1250000" in result["verify_failures"][0]


def test_a_figure_in_a_bid_comment_is_checked_too():
    result = verify_sources_node(state(bid_comments={"Apex": "9999999.0% above the median.",
                                                     "Bora": ""}))

    assert result["verify_ok"] is False
    assert "9999999" in result["verify_failures"][0]


def test_a_figure_in_the_summary_is_checked_too():
    result = verify_sources_node(state(summary="The pool of 3 awards had a median of 4.1 million."))

    assert result["verify_ok"] is False
    assert "3" in result["verify_failures"][0]


@pytest.mark.parametrize("text", [
    "One bid total is 87.5% above the median of eighteen similar awards.",
    "The normal range is 4100000.0 to 5600000.0 KES.",
    "Awards from 2024 and 2023 were used.",
    "The largest was 9,000,000.00 and the median 4800000.0.",
    "Apex sits 4.2% above the median.",
    "Zed sits 3.2% below the median.",
])
def test_figures_that_exist_in_the_data_pass(text):
    result = verify_sources_node(state(findings=[{"text": text, "sources": ["a01"]}]))

    assert result["verify_ok"] is True, result["verify_failures"]


def test_a_magnitude_with_the_wrong_sign_is_tolerated_but_a_written_minus_is_not():
    below = verify_sources_node(state(bid_comments={"Apex": "3.2% below the median.",
                                                     "Bora": "", "Zed": ""}))
    assert below["verify_ok"] is True, below["verify_failures"]

    invented = verify_sources_node(state(bid_comments={"Apex": "-3.2% below the median.",
                                                        "Bora": "", "Zed": "12.5% below."}))
    assert invented["verify_ok"] is False
    assert any("12.5" in failure for failure in invented["verify_failures"])


def test_no_findings_is_not_a_failure():
    result = verify_sources_node(state(findings=[]))

    assert result["verify_ok"] is True


def test_a_thin_pool_must_say_so_in_the_report():
    benchmark = {"n": 3, "confidence": "insufficient", "median_kes": None, "q1_kes": None,
                 "q3_kes": None, "sources": ["a01"],
                 "bids": [{"bidder": "Apex", "total": 5_000_000.0, "pct_vs_median": None,
                           "position": None}]}

    result = verify_sources_node(state(
        benchmark=benchmark, summary="Three awards were found, which is not enough to compare.",
        bid_comments={"Apex": "No comparison was possible.", "Bora": ""}, findings=[]))

    assert result["verify_ok"] is False
    assert any(NOT_ENOUGH in failure for failure in result["verify_failures"])


def test_a_thin_pool_that_says_so_verifies():
    benchmark = {"n": 3, "confidence": "insufficient", "median_kes": None, "q1_kes": None,
                 "q3_kes": None, "sources": ["a01"],
                 "bids": [{"bidder": "Apex", "total": 5_000_000.0, "pct_vs_median": None,
                           "position": None}]}

    result = verify_sources_node(state(
        benchmark=benchmark,
        summary="Three awards of classroom construction were found, which is not enough to compare.",
        bid_comments={"Apex": "No comparison was possible.", "Bora": ""},
        findings=[], review_markdown="# report\n\n" + NOT_ENOUGH + "\n"))

    assert result["verify_ok"] is True, result["verify_failures"]


def test_every_problem_is_reported_at_once():
    result = verify_sources_node(state(
        review_markdown="", findings=[{"text": "1250000.0% above.", "sources": ["a99"]}],
        bid_comments={"Apex": "7777777.0% above."}))

    assert result["verify_ok"] is False
    assert len(result["verify_failures"]) >= 3
    assert any("7777777" in f for f in result["verify_failures"])


def test_the_node_never_calls_a_tool_or_a_model(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("verify_sources must not call a tool or the model")

    import agent.nodes.verify_sources as node

    for name in dir(node):
        if name.startswith(("flag_data", "benchmark_price", "draft_review_file", "ask_json")):
            monkeypatch.setattr(node, name, explode)

    assert verify_sources_node(state())["verify_ok"] is True


def test_a_whole_run_from_candidates_to_a_verified_report(monkeypatch, audit_conn):
    from agent.nodes import draft, quality_check, score
    from agent.nodes.draft import draft_node
    from agent.nodes.quality_check import quality_check_node
    from agent.nodes.score import score_node
    from agent.tools import benchmark_price, draft_review_file, flag_data_quality

    monkeypatch.setattr(quality_check, "flag_data_quality", flag_data_quality)
    monkeypatch.setattr(quality_check, "ask_json",
                        lambda name, payload: {"judgements": {a["award_id"]: {"verdict": "same_work",
                                                                               "reason": "classroom blocks"}
                                                             for a in payload["awards"]}}
                        if name == "quality_similarity_judgement" else {"verdicts": {}})
    monkeypatch.setattr(score, "benchmark_price", benchmark_price)

    def fake_wording(prompt_name, payload):
        assert prompt_name == "draft_review_wording"
        bids = payload["bids"]
        return {
            "summary": f"{payload['benchmark']['n']} awards of classroom construction were compared, "
                       f"and one bid total sits {bids[1]['pct_vs_median']}% above the median of that pool.",
            "bid_comments": {b["bidder"]: f"{b['pct_vs_median']}% against the median." for b in bids},
            "findings": [{"text": f"One bid total is {bids[1]['pct_vs_median']}% above the median of "
                                 f"{payload['benchmark']['n']} similar awards.",
                          "sources": ["a01", "a02"]}],
            "sources_used": ["a01", "a02"],
        }

    monkeypatch.setattr(draft, "ask_json", fake_wording)
    monkeypatch.setattr(draft, "draft_review_file", draft_review_file)

# The awards span 1.0M to 6.5M against a bid median of 9M. The smallest has to stay inside the
    # scale band, ten times either side of the bid median, or quality_check drops it as a different
    # size of job before the benchmark is built.
    pool_rows = [dict(award(f"a{i:02d}", float(i) * 500_000.0 + 500_000.0),
                      title="Classroom block construction") for i in range(1, 13)]
    current = {"run_id": "run-e2e", "tender_ref": "TENDER/NC/2026/014",
               "description": "construction of classroom blocks", "candidates": pool_rows,
               "quality_attempts": 0,
               "bids": [{"bidder": "Apex", "tender_ref": "TENDER/NC/2026/014", "currency": "KES",
                         "items": [], "stated_total": 6_000_000.0, "computed_total": 6_000_000.0,
                         "arithmetic_ok": True, "issues": []},
                        {"bidder": "Bora", "tender_ref": "TENDER/NC/2026/014", "currency": "KES",
                         "items": [], "stated_total": 12_000_000.0, "computed_total": 12_000_000.0,
                         "arithmetic_ok": True, "issues": []}],
               "search": {"match": "loose", "category": "works", "min_year": None, "attempts": 1},
               "verify_rounds": 0}

    current.update(quality_check_node(current))
    current.update(score_node(current))
    current.update(draft_node(current))
    result = verify_sources_node(current)

    assert current["pool_size"] == 12
    assert current["benchmark"]["confidence"] == "medium"
    assert "## Findings" in current["review_markdown"]
    assert result["verify_ok"] is True, result["verify_failures"]


def test_a_figure_the_model_invents_fails_the_whole_run(monkeypatch):
    from agent.nodes import draft
    from agent.nodes.draft import draft_node
    from agent.tools import draft_review_file

    monkeypatch.setattr(draft, "draft_review_file", draft_review_file)
    monkeypatch.setattr(draft, "ask_json", lambda name, payload: {
        "summary": "One bid is 999.5% above the median of the pool.",
        "bid_comments": {}, "findings": [], "sources_used": []})

    current = state()
    current.update(draft_node(current))
    result = verify_sources_node(current)

    assert current["review_markdown"].startswith("# Price review evidence")
    assert result["verify_ok"] is False
    assert any("999.5" in failure for failure in result["verify_failures"])


def test_a_thin_pool_may_not_label_a_bid_far_above_or_far_below(monkeypatch):
    """A position computed against a handful of comparables is a guess wearing a percentage."""
    from agent.nodes.verify_sources import verify_sources_node

    state = {"run_id": "run-1", "review_markdown": "# Review\n\n" + NOT_ENOUGH + "\n",
             "findings": [], "sources": [], "summary": "",
             "bids": [{"bidder": "Bora Contractors (K) Ltd", "stated_total": 8650000.0}],
             "pool": [], "pool_size": 2,
"benchmark": {"n": 2, "confidence": "insufficient", "median_kes": None,
                           "q1_kes": None, "q3_kes": None,
                           "bids": [{"bidder": "Bora Contractors (K) Ltd", "total": 8650000.0,
                                     "pct_vs_median": 228639.5, "position": "far_above"}]}}

    result = verify_sources_node(state)

    assert result["verify_ok"] is False
    assert any("no position" in failure or "cannot be labelled" in failure
               for failure in result["verify_failures"])


def test_a_thin_pool_may_not_carry_a_percentage_at_all(monkeypatch):
    from agent.nodes.verify_sources import verify_sources_node

    state = {"run_id": "run-1",
             "review_markdown": "# Review\n\nNot enough comparable awards to benchmark these bids.\n",
             "findings": [{"text": "One bid is 228639.5% above the median of two similar awards.",
                           "sources": []}],
             "sources": [], "summary": "", "bids": [], "pool": [], "pool_size": 2,
             "benchmark": {"n": 2, "confidence": "insufficient", "median_kes": None,
                           "q1_kes": None, "q3_kes": None, "bids": []}}

    result = verify_sources_node(state)

    assert result["verify_ok"] is False


def test_a_real_pool_may_still_say_far_above():
    from agent.nodes.verify_sources import verify_sources_node

    state = {"run_id": "run-1",
             "review_markdown": "# Review\n\nOne bid is 204.6% above the median of 17 awards.\n",
             "findings": [{"text": "One bid is 204.6% above the median of 17 similar awards.",
                           "sources": ["a01"]}],
             "sources": [{"award_id": "a01", "value_kes": 100.0}], "summary": "",
             "bids": [{"bidder": "Bora", "stated_total": 8650000.0}],
             "pool": [{"award_id": "a01", "value_kes": 100.0}], "pool_size": 17,
             "benchmark": {"n": 17, "confidence": "high", "median_kes": 2860000.0,
                           "q1_kes": 1.0, "q3_kes": 3.0,
                           "bids": [{"bidder": "Bora", "total": 8650000.0,
                                     "pct_vs_median": 204.6, "position": "far_above"}]}}

    result = verify_sources_node(state)

    assert not [failure for failure in result["verify_failures"]
                if "no position" in failure or "cannot be labelled" in failure]
