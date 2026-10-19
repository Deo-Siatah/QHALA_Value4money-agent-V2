# The shapes the API sends and receives. These are the spec for the wire format the React UI reads,
# so they are pinned here rather than discovered in the browser.
#
# Two things are worth noticing. STEPS is checked against the terminal CLI's own list, because a run
# has one order and two front ends, and the SSE events are a discriminated union: the UI switches on
# a single "type" field and every event carries its own fields, with nothing optional-but-required.

from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent.cli import STEP_NAMES
from api import models


def test_the_step_order_is_the_one_the_terminal_runs():
    """One run has one order. The CLI already prints it; the web UI must show the same eight."""
    assert list(models.STEPS) == STEP_NAMES


def test_an_approval_names_a_person_and_not_a_placeholder():
    """submit_for_committee refuses these names too, but refusing here answers 422 instead of
    starting a run that can only fail later."""
    for name in ["agent", "AI", "auto", "n/a", "a"]:
        with pytest.raises(ValidationError) as err:
            models.ApprovalBody(decision="approved", approved_by=name)
        assert "person" in str(err.value).lower()


@pytest.mark.parametrize("decision", ["approved", "rejected", "publish_anyway"])
def test_every_decision_the_approval_node_accepts_reaches_it(decision):
    body = models.ApprovalBody(decision=decision, approved_by="Grace Wanjiku")

    assert body.decision == decision


def test_a_decision_the_approval_node_would_refuse_is_refused_here():
    with pytest.raises(ValidationError) as err:
        models.ApprovalBody(decision="maybe", approved_by="Grace Wanjiku")

    assert "maybe" in str(err.value)


def test_publish_anyway_is_offered_because_a_thin_pool_still_reaches_the_gate():
    """With fewer comparables than min_comparables the run holds no evidence, and the approval node
    only accepts a deliberate override for it."""
    assert "publish_anyway" in models.DECISIONS


def test_a_nearly_empty_state_still_makes_a_snapshot():
    """intake can return with no bids, and a run that failed early still has to render."""
    snapshot = models.snapshot_from_state({"run_id": "run-1"}, status="failed")

    assert snapshot.run_id == "run-1"
    assert snapshot.bids == []
    assert snapshot.benchmark is None
    assert snapshot.pool_values == []


def test_a_snapshot_carries_the_figures_the_run_page_shows():
    state = {
        "run_id": "run-1", "tender_ref": "MDE/EDU/2026/CLS/022", "category": "works",
        "description": "construction of classroom blocks",
        "bids": [{"bidder": "Taveta", "stated_total": 2478800.0, "items": [],
                  "computed_total": 2478800.0, "arithmetic_ok": True,
                  "issues": ["Stated total does not match"], "currency": "KES"}],
        "bid_comments": {"Taveta": "4.2% above the median."},
        "benchmark": {"n": 17, "confidence": "high", "median_kes": 2839845.72,
                      "q1_kes": 1898942.0, "q3_kes": 4021089.0,
                      "bids": [{"bidder": "Taveta", "total": 2478800.0,
                                "pct_vs_median": -12.7, "position": "below_range"}]},
        "pool": [{"award_id": "a01", "value_kes": 2839845.72},
                 {"award_id": "a02", "value_kes": Decimal("1898942.00")}],
        "pool_size": 17, "excluded": {"different_work": 10, "scale_mismatch": 1},
        "findings": [{"text": "One bid is 12.7% below the median.", "sources": ["a01"]}],
        "sources": [{"award_id": "a01", "title": "Classroom block"}],
        "summary": "Seventeen awards of classroom construction were compared.",
        "verify_ok": True, "verify_failures": [], "errors": [],
    }

    snapshot = models.snapshot_from_state(state, status="awaiting_approval",
                                          current_step="approval", elapsed_ms=61200)

    assert snapshot.tender_ref == "MDE/EDU/2026/CLS/022"
    assert snapshot.current_step == "approval"
    assert snapshot.benchmark.median_kes == 2839845.72
    assert snapshot.bids[0].pct_vs_median == -12.7
    assert snapshot.bids[0].position == "below_range"
    assert snapshot.bids[0].comment == "4.2% above the median."
    assert snapshot.bid_comments["Taveta"] == "4.2% above the median."
    assert snapshot.bids[0].arithmetic_issues == ["Stated total does not match"]
    assert snapshot.pool_size == 17
    assert snapshot.excluded == {"different_work": 10, "scale_mismatch": 1}


def test_the_pool_values_are_plain_numbers_for_the_chart():
    """Postgres hands NUMERIC back as Decimal, and JSON has no Decimal. The box plot needs numbers."""
    snapshot = models.snapshot_from_state(
        {"run_id": "run-1", "pool": [{"award_id": "a01", "value_kes": Decimal("1500.50")}]},
        status="running")

    assert snapshot.pool_values == [1500.5]


def test_a_pool_row_with_no_value_is_left_out_rather_than_shown_as_zero():
    snapshot = models.snapshot_from_state(
        {"run_id": "run-1", "pool": [{"award_id": "a01", "value_kes": None},
                                     {"award_id": "a02", "value_kes": 100.0}]},
        status="running")

    assert snapshot.pool_values == [100.0]


def test_a_bid_with_no_benchmark_row_still_renders_with_nothing_claimed():
    """An insufficient pool leaves pct_vs_median and position as None, and the UI must show that
    rather than a fabricated zero."""
    snapshot = models.snapshot_from_state(
        {"run_id": "run-1",
         "bids": [{"bidder": "Taveta", "stated_total": 2478800.0}],
         "benchmark": {"n": 2, "confidence": "insufficient", "median_kes": None,
                       "q1_kes": None, "q3_kes": None,
                       "bids": [{"bidder": "Taveta", "total": 2478800.0,
                                 "pct_vs_median": None, "position": None}]}},
        status="awaiting_approval")

    assert snapshot.bids[0].pct_vs_median is None
    assert snapshot.bids[0].position is None


def test_the_steps_start_pending_and_the_running_one_is_marked():
    snapshot = models.snapshot_from_state({"run_id": "run-1"}, status="running", current_step="draft")

    timings = {timing.step: timing.status for timing in snapshot.steps}

    assert timings["intake"] == "pending"
    assert timings["draft"] == "running"
    assert timings["file"] == "pending"
    assert [timing.step for timing in snapshot.steps] == list(models.STEPS)


def test_a_finished_step_carries_its_own_elapsed_time():
    steps = [models.StepTiming(step="intake", index=0, status="done", elapsed_ms=9400, summary="3 bids read"),
             models.StepTiming(step="research", index=1, status="done", elapsed_ms=7800)]

    assert steps[0].elapsed_ms == 9400
    assert steps[0].summary == "3 bids read"


@pytest.mark.parametrize("event,expected", [
    (models.RunStarted(run_id="run-1", steps=list(models.STEPS), started_at="2026-10-17T10:00:00Z"),
     "run.started"),
    (models.StepStarted(step="intake", index=0, at="2026-10-17T10:00:01Z"), "step.started"),
    (models.StepFinished(step="intake", index=0, elapsed_ms=9400), "step.finished"),
    (models.ModelToken(step="draft", delta="Bora sits"), "model.token"),
    (models.ModelDone(step="draft", prompt="draft_review_wording", elapsed_ms=27400), "model.done"),
    (models.ApprovalRequired(request={"tender_ref": "T/1"}), "approval.required"),
    (models.RunFinished(status="filed", submission={"report_id": 42}), "run.finished"),
    (models.RunError(step="research", message="database is away"), "run.error"),
    (models.Heartbeat(elapsed_ms=3000), "heartbeat"),
])
def test_every_event_carries_the_type_the_ui_switches_on(event, expected):
    assert event.type == expected


def test_events_round_trip_through_the_union_so_the_ui_can_parse_them():
    """The UI parses with the same union, so a new event type has to be addable in one place."""
    raw = models.StepFinished(step="quality_check", index=2, elapsed_ms=32410,
                              summary="pool 17 · excluded 13",
                              data={"pool_size": 17}).model_dump_json()

    assert models.RunEventAdapter.validate_json(raw).type == "step.finished"


def test_a_union_member_rejects_another_type_discriminator():
    with pytest.raises(ValidationError):
        models.StepFinished.model_validate({"type": "model.token", "step": "draft", "delta": "x"})
