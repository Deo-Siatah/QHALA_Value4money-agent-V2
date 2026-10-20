import sys
import threading
import types

import pytest

from agent import tracing
from agent.tracing import (annotate_llm, flush, init_tracing, log_model_call, record_tender,
                         reset_trace_tags, start_trace, traced, trace_run)


def fake_opik():
    """A stand-in for the opik module, recording what tracing asked it to do."""
    calls = []

    module = types.ModuleType("opik")

    def configure(**kwargs):
        calls.append(("configure", kwargs))

    def track(name=None, **kwargs):
        def decorate(fn):
            calls.append(("track", getattr(name, "__name__", name), kwargs.get("type")))
            return fn

        return decorate

    def flush_tracker():
        calls.append(("flush",))

    context = types.SimpleNamespace(
        update_current_span=lambda **kwargs: calls.append(("span", kwargs)),
        update_current_trace=lambda **kwargs: calls.append(("trace", kwargs)),
    )

    module.configure = configure
    module.track = track
    module.flush_tracker = flush_tracker
    module.opik_context = context
    module.Opik = lambda **kwargs: calls.append(("client", kwargs))
    return module, calls


@pytest.fixture(autouse=True)
def tracing_starts_off(monkeypatch):
    """Tracing is a module flag: start every test with it off, and put it back afterwards.

    Without this, a test that switches tracing on would leave it on for every later test in the
    session, and those tests would try to send real spans to Opik.
    """
    monkeypatch.setattr(tracing, "_enabled", False)


@pytest.fixture
def disabled(monkeypatch):
    """Tracing off, with the opik module absent from the process."""
    monkeypatch.setattr(tracing, "opik", None)
    monkeypatch.setattr(tracing.settings, "opik_enabled", False)
    return monkeypatch


def test_tracing_off_leaves_functions_alone(disabled):
    @traced("tool", "benchmark_price")
    def tool(value):
        return value * 2

    assert tool(21) == 42


def test_tracing_off_reports_itself_as_off(disabled):
    assert init_tracing("run-1") is False
    flush()
    trace_run("run-1")


def test_tracing_off_still_annotates_without_raising(disabled):
    annotate_llm("quality_similarity_judgement", model="qwen", provider="groq",
                 usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})


def test_a_key_configures_opik_with_the_project_and_workspace(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    monkeypatch.setattr(tracing.settings, "opik_workspace", "deo-team")
    monkeypatch.setattr(tracing.settings, "opik_project", "value4money-agent")

    assert init_tracing("run-1") is True

    kind, kwargs = calls[0]
    assert kind == "configure"
    assert kwargs["api_key"] == "opik-secret"
    assert kwargs["workspace"] == "deo-team"
    assert kwargs["project_name"] == "value4money-agent"
    assert kwargs["use_local"] is False


def test_enabled_but_keyless_is_off_and_never_configures(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "")

    assert init_tracing("run-1") is False
    assert calls == []


def test_a_traced_function_is_tracked_and_still_works(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    @traced("tool", "parse_bid_pdf")
    def parse(path):
        return {"bidder": "Apex"}

    assert parse("a.pdf") == {"bidder": "Apex"}
    assert ("track", "parse_bid_pdf", "tool") in calls


def test_a_model_call_is_annotated_with_its_model_and_usage(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    annotate_llm("draft_review_wording", model="qwen/qwen3.8-27b", provider="groq",
                 usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})

    spans = [entry[1] for entry in calls if entry[0] == "span"]
    assert spans[0]["model"] == "qwen/qwen3.8-27b"
    assert spans[0]["provider"] == "groq"
    assert spans[0]["usage"]["total_tokens"] == 30


def test_the_trace_is_threaded_by_run_id(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-9")

    trace_run("run-9")

    traces = [entry[1] for entry in calls if entry[0] == "trace"]
    assert traces[0]["thread_id"] == "run-9"


def test_flush_reaches_opik_only_when_tracing_is_on(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    flush()

    assert ("flush",) in calls


def test_a_real_opik_is_installed_so_imports_never_fail():
    import opik  # noqa: F401

    assert hasattr(opik, "track")


def test_a_function_decorated_before_tracing_starts_is_still_traced(monkeypatch):
    """The bug that hid every tool and model call: a decorator evaluated at import time.

    init_tracing() runs inside main(), long after the modules are imported, so a decorator that
    decided then returned the function untouched and the span never existed.
    """
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)

    @traced("tool", "parse_bid_pdf")
    def parse(path):
        return {"bidder": "Apex"}

    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    assert parse("a.pdf") == {"bidder": "Apex"}
    assert ("track", "parse_bid_pdf", "tool") in calls


def test_a_model_call_decorated_at_import_time_is_still_traced(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)

    @traced("llm", "ask_json")
    def ask(payload):
        return {"ok": True}

    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    ask({"a": 1})

    assert ("track", "ask_json", "llm") in calls


def test_an_approval_pause_is_traced_as_a_pause_and_not_an_error(monkeypatch):
    """A human saying no yet is the gate working. Opik must not file it as a failed run."""
    from langgraph.errors import GraphInterrupt

    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    @traced("general", "approval")
    def approval(state):
        raise GraphInterrupt("waiting for a person")

    with pytest.raises(GraphInterrupt):
        approval({"run_id": "run-1"})

    assert ("track", "approval", "general") in calls
    assert [entry[1] for entry in calls if entry[0] == "span"][0]["error_info"] is None


def test_a_real_failure_is_still_reported_to_opik_as_an_error(monkeypatch):
    """Not every exception is a pause: a broken tool must still show up as an error."""
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    @traced("tool", "benchmark_price")
    def benchmark(pool):
        raise ValueError("pool is not a list")

    with pytest.raises(ValueError):
        benchmark([])

    assert ("track", "benchmark_price", "tool") in calls


def test_a_traced_function_keeps_the_signature_it_was_written_with():
    """The audit wrapper binds arguments by signature, so decoration must not hide it."""
    import inspect

    @traced("tool", "parse_bid_pdf")
    def parse(run_id: str, path: str) -> dict:
        return {}

    assert list(inspect.signature(parse).parameters) == ["run_id", "path"]


def test_a_traced_function_keeps_its_name_and_docstring():
    @traced("tool", "x")
    def parse_bid_pdf(run_id, path):
        """Read one bid PDF."""
        return {}

    assert parse_bid_pdf.__name__ == "parse_bid_pdf"
    assert parse_bid_pdf.__doc__ == "Read one bid PDF."


def test_tracing_off_never_even_looks_at_opik(disabled):
    calls = []

    @traced("tool", "parse_bid_pdf")
    def parse(path):
        calls.append(path)
        return path

    assert parse("a.pdf") == "a.pdf"
    assert calls == ["a.pdf"]


def test_the_prompt_and_its_payload_are_recorded_on_the_model_span(monkeypatch):
    """What was sent to Hasibu, and what came back: otherwise the trace cannot explain a decision."""
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    log_model_call("research_next_move", "you are Hasibu, write a query",
                   {"attempt": 1, "description": "chainlink fence"},
                   answer={"query": "chainlink fence", "match": "strict"})

    recorded = [entry[1] for entry in calls if entry[0] == "span"][0]
    assert recorded["input"]["system"] == "you are Hasibu, write a query"
    assert recorded["input"]["payload"] == {"attempt": 1, "description": "chainlink fence"}
    assert recorded["output"] == {"query": "chainlink fence", "match": "strict"}


def test_the_full_prompt_is_recorded_for_the_search_prompt_only(monkeypatch):
    """research_next_move decides what gets searched, so its wording is the thing to audit.

    Every other prompt sees tender and bid content, which a third-party trace store should not
    receive field by field unless it is asked to.
    """
    monkeypatch.setattr(tracing, "log_full_prompts", lambda name: name == "research_next_move")
    _record_body = tracing._record_body

    assert _record_body("research_next_move", "secret wording") is not None
    assert _record_body("draft_review_wording", "secret wording") is None


def test_a_model_call_records_which_prompt_it_was(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")

    log_model_call("quality_similarity_judgement", "p", {"awards": 30}, answer={"judgements": {}})

    recorded = [entry[1] for entry in calls if entry[0] == "span"][0]
    assert recorded["metadata"]["prompt"] == "quality_similarity_judgement"


def test_logging_a_model_call_without_opik_does_not_raise(disabled):
    log_model_call("research_next_move", "p", {"a": 1}, answer={"b": 2})


def test_the_pause_keeps_the_payload_the_approver_is_shown():
    """Raising a fresh GraphInterrupt would reach the gate with nothing to ask about."""
    from langgraph.errors import GraphInterrupt

    original = GraphInterrupt([{"run_id": "run-1", "tender_ref": "T/1"}])
    module, calls = fake_opik()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing("run-1")
    try:

        @traced("general", "approval")
        def approval(state):
            raise original

        with pytest.raises(GraphInterrupt) as caught:
            approval({"run_id": "run-1"})
    finally:
        monkeypatch.undo()

    assert caught.value.args == ([{"run_id": "run-1", "tender_ref": "T/1"}],)


def test_the_thread_id_is_set_inside_the_traced_function(monkeypatch):
    """Setting a thread before the root span exists sets it on nothing.

    init_tracing() ran before opik.track(), so update_current_trace had no current trace and the
    thread id was lost. It has to be written from inside the tracked function.
    """
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    def fake_track(name=None, **kwargs):
        def decorate(fn):
            def wrapper(*args, **inner):
                calls.append(("inside", name))
                return fn(*args, **inner)
            return wrapper
        return decorate

    monkeypatch.setattr(module, "track", fake_track)

    @traced("general", "agent_run")
    def work():
        start_trace("run-20261016-120000", tender_ref="T/1")

    work()

    assert ("inside", "agent_run") in calls
    assert [entry[1] for entry in calls if entry[0] == "trace"][0]["thread_id"] == \
        "run-20261016-120000"


def test_the_tender_is_recorded_as_a_tag_so_runs_of_one_tender_can_be_compared(monkeypatch):
    """tender_ref as a thread would split a run from its own later resume, so it is a tag."""
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    start_trace("run-1", tender_ref="JKUAT/ENG/2026/FENCE/017")

    recorded = [entry[1] for entry in calls if entry[0] == "trace"][0]
    assert "JKUAT/ENG/2026/FENCE/017" in recorded["tags"]
    assert recorded["thread_id"] == "run-1"


def test_a_run_with_no_tender_yet_still_gets_its_project_tag(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    start_trace("run-1")

    recorded = [entry[1] for entry in calls if entry[0] == "trace"][0]
    assert "value4money" in recorded["tags"]
    assert recorded["thread_id"] == "run-1"


def test_a_later_run_records_a_tender_the_first_one_could_not_know(monkeypatch):
    """tender_ref is not known until intake has parsed something, so it is set when it is known."""
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    start_trace("run-1")
    record_tender("JKUAT/ENG/2026/FENCE/017")

    tags = [entry[1] for entry in calls if entry[0] == "trace"][-1]["tags"]
    assert "JKUAT/ENG/2026/FENCE/017" in tags


def test_a_tender_is_not_recorded_twice(monkeypatch):
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    start_trace("run-1", tender_ref="T/1")
    record_tender("T/1")

    tags = [entry[1] for entry in calls if entry[0] == "trace"][-1]["tags"]
    assert tags.count("T/1") == 1


def test_starting_a_trace_without_opik_does_not_raise(disabled):
    start_trace("run-1", tender_ref="T/1")
    record_tender("T/1")


def test_an_approval_pause_is_not_recorded_as_a_tracer_error():
    """OpikTracer marks any exception as an error, so a pause needs telling apart from a failure.

    _errored_chain_run sets chain_run.error from the stacktrace unconditionally, and only
    _should_skip_error, behind skip_error_callback, can stop it being sent.
    """
    assert tracing.is_approval_pause(
        "GraphInterrupt: the run paused here for a human decision") is True
    assert tracing.is_approval_pause(
        "langgraph.errors.GraphInterrupt\nargs: ()") is True


def test_a_real_failure_is_still_reported_to_the_tracer():
    assert tracing.is_approval_pause("ValueError: pool is not a list") is False
    assert tracing.is_approval_pause("psycopg.errors.UndefinedFunction: median(numeric)") is False


def test_the_tracer_is_only_built_when_tracing_is_on(disabled):
    assert tracing.langgraph_tracer() is None


def test_the_tracer_is_tagged_with_the_project_and_told_about_pauses(monkeypatch):
    from opik.integrations.langchain import OpikTracer

    module, _ = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    tracer = tracing.langgraph_tracer()
    assert isinstance(tracer, OpikTracer)
    assert tracer._trace_default_tags == [tracing.PROJECT_TAG]
    assert tracer._should_skip_error("GraphInterrupt: waiting") is True
    assert tracer._should_skip_error("ValueError: broken") is False


def test_a_graph_built_while_tracing_is_on_carries_the_tracer(monkeypatch):
    """Otherwise the graph structure is never sent and the UI has nothing to draw."""
    from opik.integrations.langchain import OpikTracer
    from agent.graph import build_graph

    module, _ = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    graph = build_graph()

    callbacks = (getattr(graph, "config", None) or {}).get("callbacks", [])
    assert any(isinstance(callback, OpikTracer) for callback in callbacks)


def test_a_graph_built_while_tracing_is_off_is_left_alone(disabled):
    """Most tests build a graph, and none of them should reach Opik."""
    from agent.graph import build_graph

    graph = build_graph()

    assert (getattr(graph, "config", None) or {}).get("callbacks") is None


def test_wrapping_a_graph_twice_does_not_inject_a_second_tracer(monkeypatch):
    """build_graph may be called again for a resume, and the tracer must not double up."""
    from opik.integrations.langchain import OpikTracer
    from agent.graph import build_graph

    module, _ = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    first = build_graph()
    second = build_graph()

    for graph in (first, second):
        tracers = [c for c in getattr(graph, "config", {}).get("callbacks", [])
                   if isinstance(c, OpikTracer)]
        assert len(tracers) == 1


def test_nodes_are_no_longer_wrapped_twice(monkeypatch):
    """The LangGraph tracer logs every node itself, so a second wrapper is a duplicate span."""
    from agent.graph import build_graph

    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    build_graph()

    assert not [entry for entry in calls if entry[0] == "track"]


def test_a_tracing_backend_that_will_not_configure_leaves_the_run_alone(monkeypatch):
    """The evidence file is filed into Postgres; a trace store must never stop the service booting."""
    module, calls = fake_opik()

    def refuse(**kwargs):
        raise RuntimeError("opik is unreachable")

    module.configure = refuse
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")

    assert init_tracing("run-1") is False
    assert tracing.enabled() is False


def test_two_runs_on_two_threads_each_tag_their_own_tender(monkeypatch):
    """A web process drives runs concurrently; one run's tender must not hide another's."""
    module, calls = fake_opik()
    monkeypatch.setattr(tracing, "opik", module)
    monkeypatch.setattr(tracing.settings, "opik_enabled", True)
    monkeypatch.setattr(tracing.settings, "opik_api_key", "opik-secret")
    init_tracing(None)

    a_started = threading.Event()
    b_rewound = threading.Event()

    def run_a():
        reset_trace_tags()
        start_trace("run-a")
        a_started.set()
        b_rewound.wait(2)
        record_tender("T/A")

    def run_b():
        a_started.wait(2)
        reset_trace_tags()
        start_trace("run-b")
        b_rewound.set()
        record_tender("T/B")

    threads = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    traces = [entry[1] for entry in calls if entry[0] == "trace"]
    assert any(t.get("thread_id") == "run-a" for t in traces)
    assert any(t.get("thread_id") == "run-b" for t in traces)
    assert any("T/A" in t.get("tags", []) for t in traces)
    assert any("T/B" in t.get("tags", []) for t in traces)
