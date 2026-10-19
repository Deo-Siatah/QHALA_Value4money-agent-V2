# The bridge between the agent and the browser.
#
# The agent is synchronous: graph.stream, psycopg and every tool block the thread they are called on.
# So a run executes in a worker thread while the SSE connection lives on the event loop, and every
# event has to cross that gap. RunEventChannel is the gap, and RunEventHandler is what turns
# LangChain's callbacks into the events on it.
#
# Both are tested here without a graph, a database or a server, because the crossing is the whole
# point: a callback fires in the worker thread and has to arrive on the loop, in order, without ever
# blocking the agent that produced it.

import asyncio
import threading

from api import models
from api.events import RunEventChannel, RunEventHandler


def test_an_event_emitted_from_a_worker_thread_arrives_on_the_loop():
    """The whole design in one test. The agent thread must never touch the queue directly."""

    async def scenario():
        channel = RunEventChannel()

        def worker():
            channel.emit(models.StepFinished(step="intake", index=0, elapsed_ms=9400))

        thread = threading.Thread(target=worker)
        thread.start()

        event = await channel.get()
        thread.join()

        assert event.step == "intake"
        assert event.elapsed_ms == 9400

    asyncio.run(scenario())


def test_events_arrive_in_the_order_they_were_emitted():
    async def scenario():
        channel = RunEventChannel()
        for index in range(5):
            channel.emit(models.StepFinished(step=f"step{index}", index=index, elapsed_ms=index))

        seen = [(await channel.get()).step for _ in range(5)]

        assert seen == ["step0", "step1", "step2", "step3", "step4"]

    asyncio.run(scenario())


def test_a_slow_browser_never_blocks_the_agent():
    """Nobody is reading the queue while a 30 second node runs. A full queue drops its oldest event
    and keeps going, because stalling the run to satisfy a browser is the wrong trade."""

    async def scenario():
        channel = RunEventChannel(maxsize=2)
        for index in range(10):
            channel.emit(models.StepFinished(step=f"step{index}", index=index, elapsed_ms=index))

        kept = [(await channel.get()).step for _ in range(2)]

        assert channel.dropped == 8
        assert kept == ["step8", "step9"]

    asyncio.run(scenario())


def test_the_event_stream_ends_when_the_run_is_over():
    async def scenario():
        channel = RunEventChannel()
        channel.emit(models.RunFinished(status="filed", submission={"report_id": 1}))
        channel.close()

        seen = [event.type async for event in channel.events()]

        assert seen == ["run.finished"]

    asyncio.run(scenario())


def test_events_sent_after_the_run_ended_are_dropped():
    async def scenario():
        channel = RunEventChannel()
        channel.close()

        channel.emit(models.Heartbeat())

        assert await channel.get() is None

    asyncio.run(scenario())


def test_a_reader_can_ask_how_long_it_will_wait():
    """The SSE connection uses this to send a heartbeat while a long node says nothing."""
    import pytest

    async def scenario():
        channel = RunEventChannel()
        channel.emit(models.StepStarted(step="quality_check", index=2, at="now"))

        assert (await channel.get()).step == "quality_check"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(channel.get(0.05), timeout=1)

    asyncio.run(scenario())


def test_a_step_reports_when_it_starts_and_how_long_it_took():
    handler = RunEventHandler(RunEventChannel())

    handler.on_chain_start({}, {"run_id": "run-1"}, run_id="node-1", name="intake")
    handler.on_chain_end({"bids": []}, run_id="node-1")

    assert handler.step_started == ("intake", 0)
    assert handler.steps[0].step == "intake"
    assert handler.steps[0].index == 0
    assert handler.steps[0].elapsed_ms >= 0


def test_a_chain_that_is_not_a_step_of_the_run_is_left_alone():
    """LangGraph opens chains of its own around the nodes. Only the eight named steps are events."""
    handler = RunEventHandler(RunEventChannel())

    handler.on_chain_start({}, {}, run_id="graph-1", name="LangGraph")
    handler.on_chain_end({"ok": True}, run_id="graph-1")

    assert handler.steps == []


def test_a_tokens_chain_is_not_mistaken_for_a_step():
    handler = RunEventHandler(RunEventChannel())

    handler.on_chain_start({}, {}, run_id="llm-1", name="ChatGroq")
    handler.on_chain_end({}, run_id="llm-1")

    assert handler.steps == []


def test_the_models_words_are_recorded_as_they_are_written():
    handler = RunEventHandler(RunEventChannel())
    handler.on_chain_start({}, {}, run_id="node-1", name="draft")
    handler.on_chain_start({}, {}, run_id="llm-1", name="ChatGroq")

    handler.on_llm_new_token("Bora", run_id="llm-1")
    handler.on_llm_new_token(" sits", run_id="llm-1")

    assert handler.tokens == ["Bora", " sits"]
    assert handler.current_step == "draft"


def test_a_model_call_reports_which_prompt_it_was():
    handler = RunEventHandler(RunEventChannel())
    handler.on_chain_start({}, {}, run_id="node-1", name="draft")
    handler.on_llm_start({}, run_id="llm-1", name="ChatGroq")

    handler.on_llm_end({}, run_id="llm-1")

    assert handler.model_prompts == ["ChatGroq"]


def test_a_step_that_ends_without_having_started_is_not_reported():
    """LangGraph can replay a chain on resume. A step with no start time has no elapsed time to
    claim, so it is dropped rather than reported as having taken no time at all."""
    handler = RunEventHandler(RunEventChannel())

    handler.on_chain_end({"bids": []}, run_id="never-started")

    assert handler.steps == []


def test_a_step_data_payload_is_kept_only_when_it_is_a_mapping():
    handler = RunEventHandler(RunEventChannel())
    handler.on_chain_start({}, {}, run_id="node-1", name="intake")
    handler.on_chain_end("not a mapping", run_id="node-1")

    assert handler.step_data == [None]


def test_a_callback_that_fails_does_not_break_the_run():
    """A trace that cannot be written is not worth failing a tender review over, the same rule
    agent/tracing.py applies to Opik. The timings still have to be recorded, so a dead channel
    costs the UI its lines and nothing else."""

    class Broken(RunEventChannel):
        def emit(self, event):
            raise RuntimeError("the channel is gone")

    handler = RunEventHandler(Broken())
    handler.raise_error = False

    handler.on_chain_start({}, {}, run_id="node-1", name="intake")
    handler.on_chain_end({"bids": []}, run_id="node-1")

    assert [record.step for record in handler.steps] == ["intake"]
    assert handler.step_data == [{"bids": []}]


def test_a_steps_elapsed_time_is_the_difference_between_its_start_and_its_end():
    """Measured from a clock the test controls, because Windows ticks time.monotonic about every
    15.6ms and a ten millisecond sleep measures as zero on some machines and not on others."""
    ticks = iter([100.0, 112.5])
    handler = RunEventHandler(RunEventChannel(), clock=lambda: next(ticks))

    handler.on_chain_start({}, {}, run_id="node-1", name="intake")
    handler.on_chain_end({}, run_id="node-1")

    assert handler.steps[0].elapsed_ms == 12500.0


def test_a_model_calls_elapsed_time_is_measured_the_same_way():
    ticks = iter([10.0, 10.0, 10.75])
    handler = RunEventHandler(RunEventChannel(), clock=lambda: next(ticks))
    handler.on_chain_start({}, {}, run_id="node-1", name="draft")

    handler.on_llm_start({}, prompts=["write it"], run_id="llm-1", name="ChatGroq")
    handler.on_llm_end({}, run_id="llm-1")

    assert handler.model_prompts == ["ChatGroq"]
    assert handler.model_times == [("ChatGroq", 750.0)]


def test_a_real_clock_never_reports_a_negative_time():
    handler = RunEventHandler(RunEventChannel())
    handler.on_chain_start({}, {}, run_id="node-1", name="intake")
    handler.on_chain_end({}, run_id="node-1")

    assert handler.steps[0].elapsed_ms >= 0
