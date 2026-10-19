# The runner is where a run actually happens: a thread, a checkpoint id and the approval interrupt.
# These tests drive a fake graph instead of the real one, so they can make a run pause, fail or end
# on demand and assert on what the browser would have been sent.
#
# The fake graph calls the callbacks it is handed, exactly as LangGraph does around a node. That is
# what makes the timing testable here: without it the step events would be the only thing missing.

import time

import pytest
from langgraph.types import Command

from api import models
from api.events import RunEventHandler
from api.runner import Runner
from config import settings


class Interrupt:
    """What LangGraph puts in the __interrupt__ tuple."""

    def __init__(self, value):
        self.value = value


class State:
    """The little of graph.get_state the runner reads."""

    def __init__(self, values):
        self.values = values


class FakeGraph:
    """A graph that yields the chunks it was given, calling the callbacks around each node."""

    def __init__(self, chunks, values=None, raises=None):
        self.chunks = chunks
        self.values = values or {}
        self.raises = raises
        self.seen = []
        self.configs = []

    def stream(self, payload, config, stream_mode=None):
        self.seen.append(payload)
        self.configs.append(config)
        for index, chunk in enumerate(self.chunks):
            callbacks = config.get("callbacks") or []
            for name, update in chunk.items():
                if name == "__interrupt__":
                    continue
                for callback in callbacks:
                    callback.on_chain_start({}, {}, run_id=f"node-{index}", name=name)
                for callback in callbacks:
                    callback.on_chain_end(update, run_id=f"node-{index}")
            if self.raises is not None:
                raise self.raises
            yield chunk

    def get_state(self, config):
        return State(self.values)


APPROVAL = {"tender_ref": "T/1", "summary": "Twelve awards were compared.",
            "benchmark": {"n": 12}, "evidence_status": "has_evidence"}

PAUSED = [
    {"intake": {"bids": [{"bidder": "Apex", "stated_total": 100.0}]}},
    {"research": {"candidates": [{"award_id": "a01"}], "search_attempts": 1}},
    {"__interrupt__": (Interrupt(APPROVAL),)},
]

ENDED = [{"approval": {"approval_status": "approved"}}]

SETTLED = (models.RunStatus.awaiting_approval, models.RunStatus.filed,
           models.RunStatus.rejected, models.RunStatus.failed)


def runner(chunks=PAUSED, values=None, raises=None):
    """A runner whose graph will do exactly this."""
    return Runner(FakeGraph(chunks, values, raises))


def drive(r, run_id="run-1", paths=("a.pdf",), chunks=None, values=None, raises=None):
    """Start a run and wait for it to reach its next pause or its end. Returns the run it created."""
    if chunks is not None or values is not None or raises is not None:
        r.graph = FakeGraph(PAUSED if chunks is None else chunks, values, raises)
    run = r.start(run_id, list(paths))
    wait(lambda: run.status in SETTLED)
    return run


def wait(predicate, timeout=5):
    """Wait on a condition the run's thread will satisfy, then say so plainly if it never does.

    The run is on another thread, so a test that asserted straight away would be asserting about a
    race. Polling with a deadline keeps the failure readable instead of leaving the test to time out.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("the run never got where the test expected it to be")


def read_all(channel, settle=0.15):
    """Every event the run produced, in order.

    A run paused at the approval gate is still open and will produce nothing more, so this drains
    what is there and stops once the channel has been quiet for a moment, rather than waiting for a
    close that only a finished run sends.
    """
    events = []
    deadline = time.monotonic() + 5
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        with channel._lock:
            if channel._items:
                events.append(channel._items.popleft())
                quiet_since = time.monotonic()
                continue
            closed = channel._closed
        if closed or time.monotonic() - quiet_since > settle:
            return events
        time.sleep(0.005)
    raise AssertionError("the run kept producing events and never settled")


def types_of(run):
    return [event.type for event in read_all(run.channel)]


def test_a_run_reports_that_it_started_with_the_eight_steps():
    run = drive(runner())

    events = read_all(run.channel)
    assert events[0].type == "run.started"
    assert events[0].steps == list(models.STEPS)


def test_a_run_that_stops_at_the_gate_asks_for_a_decision_and_does_not_finish():
    run = drive(runner())

    types = types_of(run)
    assert "approval.required" in types
    assert "run.finished" not in types
    assert run.status == models.RunStatus.awaiting_approval
    assert run.approval_request == APPROVAL


def test_the_request_shown_to_the_person_is_the_approval_nodes_own_payload():
    """It carries evidence_status and the benchmark, and the UI shows them without rebuilding them."""
    run = drive(runner())

    asked = next(e for e in read_all(run.channel) if e.type == "approval.required")
    assert asked.request["evidence_status"] == "has_evidence"
    assert asked.request["benchmark"]["n"] == 12


def test_a_paused_run_keeps_its_channel_open_because_a_person_has_not_answered_yet():
    run = drive(runner())

    assert not run.channel.closed


def test_each_step_reports_its_own_time():
    run = drive(runner())

    finished = {e.step: e.elapsed_ms for e in read_all(run.channel) if e.type == "step.finished"}
    assert sorted(finished) == ["intake", "research"]
    assert all(value >= 0 for value in finished.values())


def test_a_decision_resumes_the_same_run_and_files_it():
    r = runner()
    run = drive(r)
    read_all(run.channel)
    r.graph = FakeGraph([{"file": {"submission": {"report_id": 7}}}],
                        values={"submission": {"report_id": 7}})

    r.resume("run-1", {"decision": "approved", "approved_by": "Grace Wanjiku", "note": ""})

    wait(lambda: run.status == models.RunStatus.filed)
    assert isinstance(r.graph.seen[0], Command)
    finished = next(e for e in read_all(run.channel) if e.type == "run.finished")
    assert finished.submission == {"report_id": 7}
    assert run.channel.closed


def test_a_run_is_filed_only_when_it_actually_produced_a_submission():
    """A run that ended without one filed nothing. It is a failure, not a decision: nothing was
    rejected by anybody."""
    run = drive(runner(chunks=ENDED))

    wait(lambda: run.status == models.RunStatus.failed)
    finished = next(e for e in read_all(run.channel) if e.type == "run.finished")
    assert finished.status == models.RunStatus.failed
    assert finished.submission is None


def test_a_run_a_person_declined_is_rejected():
    run = drive(runner(chunks=[{"approval": {"approval_status": "rejected",
                                             "approved_by": "Grace Wanjiku"}}]))

    wait(lambda: run.status == models.RunStatus.rejected)
    finished = next(e for e in read_all(run.channel) if e.type == "run.finished")
    assert finished.status == models.RunStatus.rejected
    assert finished.submission is None


def test_a_run_that_breaks_reports_the_failure_and_closes_its_channel():
    run = drive(runner(raises=RuntimeError("the database is away")))

    wait(lambda: run.status == models.RunStatus.failed)
    failure = next(e for e in read_all(run.channel) if e.type == "run.error")
    assert "the database is away" in failure.message
    assert run.channel.closed


def test_two_runs_can_be_held_at_once_and_are_listed_newest_first():
    r = runner()

    r.start("run-1", ["a.pdf"])
    second = r.start("run-2", ["b.pdf"])

    assert [run.run_id for run in r.all_runs()] == ["run-2", "run-1"]
    assert second.sequence > r.get("run-1").sequence


def test_the_same_run_id_cannot_be_claimed_twice():
    r = runner()
    r.start("run-1", ["a.pdf"])

    with pytest.raises(Exception) as err:
        r.start("run-1", ["a.pdf"])

    assert "already exists" in str(err.value)


def test_an_unknown_run_is_refused_by_name():
    r = runner()

    with pytest.raises(Exception) as err:
        r.get("run-nope")

    assert "run-nope" in str(err.value)


def test_a_run_with_no_bids_to_read_is_refused_before_a_thread_is_taken():
    r = runner()

    with pytest.raises(Exception) as err:
        r.start("run-1", [])

    assert "no bid PDFs" in str(err.value)
    assert r.all_runs() == []


def test_a_run_that_is_not_waiting_cannot_be_decided():
    """Otherwise the same run would be driven by two threads and could file twice."""
    r = runner(chunks=ENDED)
    run = drive(r)
    wait(lambda: run.status in (models.RunStatus.rejected, models.RunStatus.failed))

    with pytest.raises(Exception) as err:
        r.resume("run-1", {"decision": "approved", "approved_by": "Grace Wanjiku", "note": ""})

    assert "not waiting" in str(err.value)


def test_the_concurrency_cap_is_the_configured_one_and_stays_small():
    """Every tool call opens a connection, so runs queue rather than all opening threads at once,
    and the number belongs in config.py with every other limit."""
    assert Runner(FakeGraph([])).max_concurrency == settings.run_max_concurrency
    assert 1 <= settings.run_max_concurrency <= 2


def test_each_run_gets_its_own_thread_id_so_two_runs_never_share_a_checkpoint():
    r = runner()

    drive(r, run_id="run-1")
    drive(r, run_id="run-2")

    ids = [config["configurable"]["thread_id"] for config in r.graph.configs]
    assert ids == ["run-1", "run-2"]


def test_the_handlers_callback_is_handed_to_the_graph_so_steps_can_be_timed():
    r = runner()

    drive(r)

    callbacks = r.graph.configs[0]["callbacks"]
    assert any(isinstance(callback, RunEventHandler) for callback in callbacks)


def test_a_snapshot_shows_what_the_checkpoint_holds_and_the_steps_that_ran():
    values = {"tender_ref": "T/1", "pool_size": 17,
              "benchmark": {"n": 17, "confidence": "high", "median_kes": 2839845.72,
                            "q1_kes": 1898942.0, "q3_kes": 4021089.0, "bids": []},
              "pool": [{"award_id": "a01", "value_kes": 2839845.72}]}
    r = runner(values=values)

    drive(r)

    snapshot = r.snapshot("run-1")
    assert snapshot.run_id == "run-1"
    assert snapshot.tender_ref == "T/1"
    assert snapshot.pool_size == 17
    assert snapshot.benchmark.median_kes == 2839845.72
    assert snapshot.pool_values == [2839845.72]
    assert snapshot.status == models.RunStatus.awaiting_approval
    done = {t.step for t in snapshot.steps if t.status == models.StepStatus.done}
    assert done == {"intake", "research"}


def test_a_step_summary_is_the_line_the_terminal_prints():
    """One run has one description. agent/cli.py:118 already knows how to say it in a line."""
    update = {"candidates": [{"award_id": "a01"}], "pool_size": 17, "excluded": {"different_work": 2}}

    assert models.summarise("research", update) == "1 candidates"
    assert models.summarise("quality_check", update) == "pool 17 · excluded 2"
