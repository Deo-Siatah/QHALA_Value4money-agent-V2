import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import graph
from agent.graph import build_graph
from agent.nodes import approval
from agent.nodes.approval import approval_node, _read_decision, _request


def _resume(decision, approved_by="Grace Wanjiku", note=""):
    return {"decision": decision, "approved_by": approved_by, "note": note}


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_a_named_human_may_approve_or_reject(decision):
    assert _read_decision(_resume(decision)) == {"decision": decision,
                                                 "approved_by": "Grace Wanjiku", "note": ""}


@pytest.mark.parametrize("payload, expected", [
    (_resume("approved", ""), "approved_by"),
    (_resume("approved", "  A  "), "approved_by"),
    (_resume("approved", "agent"), "placeholder"),
    (_resume("approved", "N/A"), "placeholder"),
    (_resume("maybe"), "decision"),
    ("not a dict", "dict"),
    (None, "dict"),
])
def test_a_bad_resume_is_refused_by_name(payload, expected):
    with pytest.raises(ValueError) as err:
        _read_decision(payload)

    assert expected in str(err.value)


def test_a_report_is_needed_before_a_human_is_asked():
    result = approval_node({"run_id": "run-1", "review_markdown": "  "})

    assert result["approval_status"] == "no_report"
    assert result["approved_by"] is None


def _faked(monkeypatch):
    """Fake every node except approval, up to a clean verified report."""
    monkeypatch.setattr(graph, "intake_node", lambda s: {"bids": [{"bidder": "Apex"}]})
    monkeypatch.setattr(graph, "research_node", lambda s: {"search_attempts": 1, "search": {}})
    monkeypatch.setattr(graph, "quality_check_node", lambda s: {"pool_size": 12})
    monkeypatch.setattr(graph, "score_node", lambda s: {"benchmark": {"n": 12, "confidence": "medium"}})
    monkeypatch.setattr(graph, "draft_node",
                        lambda s: {"review_markdown": "# report\n\nbody\n",
                                   "verify_rounds": s.get("verify_rounds", 0) + 1,
                                   "summary": "Eighteen awards were compared."})
    monkeypatch.setattr(graph, "verify_sources_node", lambda s: {"verify_ok": True})
    monkeypatch.setattr(graph, "file_node", lambda s: {"submission": {"report_id": 7}})


def test_the_run_pauses_and_resumes_on_a_human_decision(monkeypatch):
    _faked(monkeypatch)
    run = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "run-1"}}

    paused = run.invoke({"run_id": "run-1", "review_markdown": "# report\n"}, config)

    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["review_markdown"] == "# report\n\nbody\n"
    assert "submission" not in paused

    result = run.invoke(Command(resume=_resume("approved", note="checked the figures")), config)

    assert result["approval_status"] == "approved"
    assert result["approved_by"] == "Grace Wanjiku"
    assert result["approval_note"] == "checked the figures"
    assert result["submission"] == {"report_id": 7}


def test_a_rejection_ends_the_run_without_filing(monkeypatch):
    _faked(monkeypatch)
    run = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "run-2"}}

    assert "__interrupt__" in run.invoke({"run_id": "run-2", "review_markdown": "# report\n"}, config)

    result = run.invoke(Command(resume=_resume("rejected", note="scope is unclear")), config)

    assert result["approval_status"] == "rejected"
    assert "submission" not in result


def test_the_request_carries_the_report_the_evidence_and_the_failures():
    state = {"run_id": "run-3", "tender_ref": "TENDER/NC/2026/014",
             "review_markdown": "# report\n\nbody\n",
             "summary": "Eighteen awards were compared.",
             "benchmark": {"n": 18, "confidence": "medium", "median_kes": 4_800_000.0,
                           "q1_kes": 4_100_000.0, "q3_kes": 5_600_000.0,
                           "bids": [{"bidder": "Apex", "total": 5_000_000.0, "pct_vs_median": 4.2,
                                     "position": "within_range"}]},
             "verify_ok": False, "verify_failures": ["finding 1 quotes 999.0"]}

    request = _request(state)

    assert request["run_id"] == "run-3"
    assert request["tender_ref"] == "TENDER/NC/2026/014"
    assert request["review_markdown"] == "# report\n\nbody\n"
    assert request["benchmark"]["median_kes"] == 4_800_000.0
    assert request["bids"][0]["bidder"] == "Apex"
    assert request["verify_failures"] == ["finding 1 quotes 999.0"]


def test_a_verification_failure_is_shown_to_the_approver(monkeypatch):
    _faked(monkeypatch)
    monkeypatch.setattr(graph, "verify_sources_node",
                        lambda s: {"verify_ok": False, "verify_failures": ["finding 1 cites a99"]})
    run = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "run-4"}}

    paused = run.invoke({"run_id": "run-4", "review_markdown": "# report\n", "verify_ok": False,
                         "verify_failures": ["finding 1 cites a99"]}, config)

    assert "finding 1 cites a99" in paused["__interrupt__"][0].value["verify_failures"]
    assert paused["__interrupt__"][0].value["verify_ok"] is False

    result = run.invoke(Command(resume=_resume("approved")), config)

    assert result["submission"] == {"report_id": 7}

def answering(monkeypatch, **decision):
    """Answer the interrupt and hand back every payload the approver was shown."""
    seen = []

    def fake_interrupt(payload):
        seen.append(payload)
        return decision

    monkeypatch.setattr(approval, "interrupt", fake_interrupt)
    return seen


def insufficient_state(**overrides):
    """A state whose pool was too thin to benchmark anything."""
    state = {"run_id": "run-1", "tender_ref": "T/1", "summary": "No comparables found.",
             "review_markdown": "# Review\n\nNot enough comparable awards.",
             "benchmark": {"n": 0, "confidence": "insufficient", "median_kes": None,
                           "q1_kes": None, "q3_kes": None, "bids": []},
             "verify_ok": True, "verify_failures": []}
    state.update(overrides)
    return state


def test_an_insufficient_benchmark_is_flagged_in_what_the_approver_is_shown(monkeypatch):
    """The person has to be told the report holds no evidence before they approve it."""
    seen = answering(monkeypatch, decision="publish_anyway", approved_by="Deo Siatah")

    approval_node(insufficient_state())

    assert seen[-1]["evidence_status"] == "no_evidence"


def test_a_real_benchmark_is_flagged_as_having_evidence(monkeypatch):
    seen = answering(monkeypatch, decision="approved", approved_by="Deo Siatah")
    state = insufficient_state(benchmark={"n": 17, "confidence": "high", "median_kes": 2_670_667.86,
                                          "q1_kes": 1.0, "q3_kes": 3.0, "bids": []})

    approval_node(state)

    assert seen[-1]["evidence_status"] == "has_evidence"


def test_approving_a_report_with_no_evidence_is_refused(monkeypatch):
    """Filing an evidence-free report needs a deliberate second step, not a plain approval."""
    monkeypatch.setattr(approval, "interrupt",
                        lambda payload: {"decision": "approved", "approved_by": "Deo Siatah"})

    with pytest.raises(ValueError) as err:
        approval_node(insufficient_state())

    assert "no comparable awards" in str(err.value).lower()


def test_publishing_anyway_is_a_different_decision_the_person_has_to_choose(monkeypatch):
    monkeypatch.setattr(approval, "interrupt",
                        lambda payload: {"decision": "publish_anyway", "approved_by": "Deo Siatah",
                                         "note": "proceeding without a benchmark"})

    result = approval_node(insufficient_state())

    assert result["approval_status"] == "approved"
    assert result["approval_note"] == "proceeding without a benchmark"


def test_publishing_anyway_still_needs_a_named_person(monkeypatch):
    monkeypatch.setattr(approval, "interrupt",
                        lambda payload: {"decision": "publish_anyway", "approved_by": "agent"})

    with pytest.raises(ValueError):
        approval_node(insufficient_state())


def test_a_report_with_evidence_is_approved_the_usual_way(monkeypatch):
    monkeypatch.setattr(approval, "interrupt", lambda payload: {"decision": "approved",
                                                               "approved_by": "Deo Siatah"})
    state = insufficient_state(benchmark={"n": 17, "confidence": "high", "median_kes": 2_670_667.86,
                                          "q1_kes": 1.0, "q3_kes": 3.0, "bids": []})

    result = approval_node(state)

    assert result["approval_status"] == "approved"


def test_declining_is_always_allowed_even_without_evidence(monkeypatch):
    """Refusing to file is always the easy answer; only filing is gated."""
    monkeypatch.setattr(approval, "interrupt", lambda payload: {"decision": "rejected",
                                                               "approved_by": "Deo Siatah"})

    result = approval_node(insufficient_state())

    assert result["approval_status"] == "rejected"
