import pytest

from agent.nodes import research
from agent.nodes.research import research_node
from agent.llm import LlmError
from agent.state import initial_state
from config import settings

MOVE = {"query": "classroom block construction", "match": "loose", "min_year": 2020,
        "reason": "loose matching should catch classroom works and school blocks"}


def candidate(award_id, value=4_000_000.0, title="Classroom block construction"):
    return {"award_id": award_id, "ocid": f"ocid-{award_id}", "data_year": 2024, "title": title,
            "buyer_name": "Ministry of Education", "category": "works", "value_kes": value,
            "currency": "KES", "supplier_name": "Builder Ltd", "quality_flags": []}


def search_result(count, candidates):
    return {"filters": {"query": ["classroom"], "match": "loose"}, "count": count,
            "candidates": candidates}


@pytest.fixture(autouse=True)
def no_community_server(monkeypatch):
    """No test here talks to the community server unless it says so.

    The fallback is quiet by default in this module, so a test that reaches attempt three with a
    thin pool cannot spawn a subprocess or reach the network. The fallback tests patch it back on.
    """
    monkeypatch.setattr(research, "describe_schema", lambda run_id: [])


@pytest.fixture
def asked(monkeypatch):
    """Capture the research_next_move payload and reply with a canned move."""
    calls = []

    def install(answer=MOVE, raises=None):
        def fake_ask(prompt_name, payload):
            calls.append((prompt_name, payload))
            if raises is not None:
                raise raises
            return answer

        monkeypatch.setattr(research, "ask_json", fake_ask)
        return calls

    return install


@pytest.fixture
def searched(monkeypatch):
    """Capture the find_comparable_awards call and reply with a canned search result."""
    calls = []

    def install(result=None):
        def fake_search(run_id, query, category, buyer_id, match, min_year, exclude_ocid):
            calls.append({"run_id": run_id, "query": query, "category": category,
                          "buyer_id": buyer_id, "match": match, "min_year": min_year,
                          "exclude_ocid": exclude_ocid})
            return result if result is not None else search_result(2, [candidate("a01"), candidate("a02")])

        monkeypatch.setattr(research, "find_comparable_awards", fake_search)
        return calls

    return install


def run(**overrides):
    state = initial_state("run-1", ["apex.pdf"], "2026-03-01T09:00:00Z")
    state.update({"description": "construction of classroom blocks", "category": "works"})
    # Most tests here are about the model choosing how to widen, which only happens once a search
    # has already run. Pass last_search=None to exercise the opening search, which code builds.
    overrides.setdefault("last_search", {"query": "classroom block construction", "match": "strict",
                                         "min_year": 2020, "count": 0})
    state.update(overrides)
    return state, research_node(state)


def test_the_first_attempt_has_no_previous_search(asked, searched):
    asked()
    calls = searched()

    result = run(search_attempts=0)[1]

    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["query"] == MOVE["query"]
    assert calls[0]["match"] == "loose"
    assert calls[0]["min_year"] == 2020
    assert calls[0]["category"] == "works"
    assert calls[0]["exclude_ocid"] is None
    assert result["search_attempts"] == 1


def test_the_first_search_is_built_in_code_and_the_model_is_never_asked_for_it(asked, searched):
    """The opening query decides the pool, and the pool decides the median, so code writes it."""
    calls = asked()
    searched()

    run(search_attempts=0, last_search=None)

    assert calls == []


def test_the_first_search_uses_the_tender_description_and_begins_at_loose(asked, searched):
    asked()
    calls = searched()

    result = run(search_attempts=0, last_search=None)[1]

    assert calls[0]["query"] == "construction of classroom blocks"
    assert calls[0]["match"] == "loose"


def test_a_retry_tells_the_model_what_fell_short(asked, searched):
    calls = asked()
    searched(search_result(2, [candidate("a01"), candidate("a02")]))

    run(query="classroom construction", match="strict", min_year=2023, search_attempts=1,
        last_search=search_result(2, [candidate("a01"), candidate("a02")]),
        candidates=[candidate("a01"), candidate("a02")])

    previous = calls[0][1]["previous"]
    assert calls[0][1]["attempt"] == 2
    assert previous["query"] == "classroom construction"
    assert previous["match"] == "strict"
    assert previous["min_year"] == 2023
    assert previous["returned"] == 2
    assert previous["target"] == settings.good_pool_n
    assert str(settings.min_comparables) in previous["problem"]


def test_a_retry_says_when_nothing_matched_at_all(asked, searched):
    calls = asked()
    searched(search_result(0, []))

    run(query="classroom construction", match="strict", min_year=2023, search_attempts=1,
        last_search=search_result(0, []), candidates=[])

    assert calls[0][1]["previous"]["returned"] == 0
    assert calls[0][1]["previous"]["problem"] == "no awards matched"


def test_a_usable_but_small_pool_says_what_is_wanted(asked, searched):
    calls = asked()
    searched(search_result(6, [candidate(f"a{i:02d}") for i in range(6)]))

    run(query="classroom construction", match="loose", min_year=2023, search_attempts=1,
        last_search=search_result(6, [candidate(f"a{i:02d}") for i in range(6)]),
        candidates=[candidate(f"a{i:02d}") for i in range(6)])

    previous = calls[0][1]["previous"]
    assert "6 awards returned" in previous["problem"]
    assert str(settings.good_pool_n) in previous["problem"]


def test_candidates_from_every_attempt_are_kept_without_duplicates(asked, searched):
    asked()
    searched(search_result(2, [candidate("a02"), candidate("a03")]))

    result = run(candidates=[candidate("a01"), candidate("a02")])[1]

    assert [c["award_id"] for c in result["candidates"]] == ["a01", "a02", "a03"]


def test_an_award_seen_twice_keeps_its_first_row(asked, searched):
    asked()
    searched(search_result(1, [candidate("a01", value=9_000_000.0, title="Later title")]))

    result = run(candidates=[candidate("a01", value=4_000_000.0, title="First title")])[1]

    assert result["candidates"] == [candidate("a01", value=4_000_000.0, title="First title")]


def test_last_search_records_only_the_latest_attempt(asked, searched):
    asked()
    searched(search_result(2, [candidate("a09")]))

    result = run()[1]

    assert result["last_search"]["count"] == 2
    assert [c["award_id"] for c in result["last_search"]["candidates"]] == ["a09"]


def test_the_move_is_written_back_to_state(asked, searched):
    asked()
    searched()

    result = run()[1]

    assert result["query"] == MOVE["query"]
    assert result["match"] == "loose"
    assert result["min_year"] == 2020


def test_the_fallback_is_not_used_while_the_pool_is_healthy(asked, searched, monkeypatch):
    asked()
    searched(search_result(12, [candidate(f"a{i:02d}") for i in range(12)]))
    called = []
    monkeypatch.setattr(research, "describe_schema", lambda run_id: called.append(run_id))

    result = run(search_attempts=1, candidates=[candidate(f"a{i:02d}") for i in range(12)])[1]

    assert called == []
    assert "fallback" not in result["search"]


def test_the_fallback_is_not_used_on_the_first_attempt(asked, searched, monkeypatch):
    asked()
    searched()
    called = []
    monkeypatch.setattr(research, "describe_schema", lambda run_id: called.append(run_id))

    run(search_attempts=0, candidates=[])

    assert called == []


def test_the_fallback_asks_the_schema_then_runs_one_query(asked, searched, monkeypatch):
    asked()
    searched()
    asked_calls = []
    answer = {"sql": "SELECT award_id, value_kes FROM awards WHERE value_kes > 0 LIMIT 30",
              "reason": "fence work is titled differently"}

    def fake_ask(prompt_name, payload):
        asked_calls.append((prompt_name, payload))
        return answer if prompt_name == "research_fallback_query" else MOVE

    monkeypatch.setattr(research, "ask_json", fake_ask)
    monkeypatch.setattr(research, "describe_schema",
                        lambda run_id: [{"name": "awards", "columns": ["award_id", "value_kes"]}])
    queries = []
    monkeypatch.setattr(research, "fallback_candidates",
                        lambda run_id, sql: queries.append((run_id, sql)) or [candidate("a09")])

    result = run(search_attempts=2, candidates=[candidate("a01")])[1]

    assert asked_calls[0][0] == "research_next_move"
    fallback_prompt = [call for call in asked_calls if call[0] == "research_fallback_query"][0]
    assert fallback_prompt[1]["have_so_far"] == 1
    assert fallback_prompt[1]["need_at_least"] == settings.min_comparables
    assert queries[0][0] == "run-1"
    assert "SELECT" in queries[0][1]
    assert "a09" in [c["award_id"] for c in result["candidates"]]
    assert result["search"]["fallback"] == "community_mcp"


def test_a_fallback_that_finds_nothing_is_recorded_and_the_run_goes_on(asked, searched, monkeypatch):
    asked()
    searched()
    monkeypatch.setattr(research, "describe_schema", lambda run_id: [])
    monkeypatch.setattr(research, "fallback_candidates", lambda run_id, sql: [candidate("a09")])

    result = run(search_attempts=2, candidates=[])[1]

    assert "returned nothing" in result["errors"][0]
    assert "fallback" not in result["search"]


def test_a_model_that_cannot_write_the_query_is_recorded(asked, searched, monkeypatch):
    asked()
    searched()
    monkeypatch.setattr(research, "describe_schema", lambda run_id: [{"name": "awards"}])
    real_ask = research.ask_json

    def boom(prompt_name, payload):
        if prompt_name == "research_fallback_query":
            raise LlmError("groq timed out")
        return real_ask(prompt_name, payload)

    monkeypatch.setattr(research, "ask_json", boom)

    result = run(search_attempts=2, candidates=[])[1]

    assert "could not write the fallback query" in result["errors"][0]
    assert result["candidates"] == [candidate("a01"), candidate("a02")]


def test_a_query_without_sql_is_refused(asked, searched, monkeypatch):
    asked()
    searched()
    monkeypatch.setattr(research, "describe_schema", lambda run_id: [{"name": "awards"}])
    real_ask = research.ask_json

    def forgetful(prompt_name, payload):
        if prompt_name == "research_fallback_query":
            return {"reason": "I forgot the query"}
        return real_ask(prompt_name, payload)

    monkeypatch.setattr(research, "ask_json", forgetful)

    result = run(search_attempts=2, candidates=[])[1]

    assert "returned no sql" in result["errors"][0]


def test_the_search_echo_records_what_was_actually_run(asked, searched):
    asked()
    searched()

    result = run()[1]

    assert result["search"] == {"match": "loose", "category": "works", "min_year": 2020,
                                "attempts": 1}


def test_the_search_echo_follows_the_fallback_when_the_model_cannot_answer(asked, searched):
    asked(raises=LlmError("groq timed out"))
    searched()

    result = run(match="strict", min_year=2023, search_attempts=1)[1]

    assert result["search"] == {"match": "loose", "category": "works", "min_year": 2023,
                                "attempts": 2}


@pytest.mark.parametrize("answer, expected", [
    ({"query": "classroom", "match": "wildcard", "min_year": 2020}, "match"),
    ({"query": "  ", "match": "loose", "min_year": 2020}, "query"),
    ({"query": "classroom", "match": "loose", "min_year": "2020"}, "min_year"),
])
def test_a_bad_move_from_the_model_is_refused_before_searching(asked, searched, answer, expected):
    asked(answer)
    calls = searched()

    with pytest.raises(ValueError) as err:
        run()[1]

    assert expected in str(err.value)
    assert "research_next_move" in str(err.value)
    assert calls == []


def test_an_unreachable_model_still_searches_and_records_the_error(asked, searched):
    asked(raises=LlmError("groq timed out"))
    calls = searched()

    result = run(match="strict", min_year=2023, query="classroom construction", search_attempts=0)[1]

    assert "groq timed out" in result["errors"][0]
    assert result["match"] == "loose"
    assert result["min_year"] == 2023
    assert result["query"] == "classroom construction"
    assert calls[0]["match"] == "loose"


def test_the_fallback_widens_one_step_at_a_time(asked, searched):
    asked(raises=LlmError("groq timed out"))
    calls = searched()

    first = run(match="strict", min_year=2023, query="q1", search_attempts=0)[1]
    second = run(match="loose", min_year=2023, query="q2", search_attempts=1)[1]
    third = run(match="fuzzy", min_year=2023, query="q3", search_attempts=2)[1]
    fourth = run(match="fuzzy", min_year=2023, query="q4", search_attempts=3)[1]

    assert (first["match"], first["min_year"]) == ("loose", 2023)
    assert (second["match"], second["min_year"]) == ("fuzzy", 2023)
    assert (third["match"], third["min_year"]) == ("fuzzy", None)
    assert (fourth["match"], fourth["min_year"]) == ("fuzzy", None)
    assert [c["match"] for c in calls] == ["loose", "fuzzy", "fuzzy", "fuzzy"]


def test_the_model_answers_again_after_a_failed_attempt(asked, searched):
    asked()
    searched()

    result = run(match="strict", min_year=2023, search_attempts=1)[1]

    assert result["match"] == "loose"
    assert "errors" not in result


def test_the_real_tool_returns_candidates_for_what_the_node_searched(audit_conn, db_conn,
                                                                   monkeypatch):
    from mcp_server import audit
    from mcp_server.tools import find_comparable_awards as search_tool
    from agent.tools import find_comparable_awards
    from conftest import SharedConnection

    monkeypatch.setattr(audit, "get_conn", lambda: SharedConnection(audit_conn))
    monkeypatch.setattr(search_tool, "get_conn", lambda: SharedConnection(db_conn))
    monkeypatch.setattr(research, "ask_json", lambda name, payload: dict(MOVE, query="classroom construction",
                                                                         match="strict", min_year=None))
    monkeypatch.setattr(research, "find_comparable_awards", find_comparable_awards)

    result = research_node({
        "run_id": "run-int", "description": "construction of classroom blocks", "category": "works",
        "query": "classroom construction", "match": "strict", "min_year": None, "search_attempts": 0,
        "last_search": {"query": "classroom construction", "match": "strict", "min_year": None,
                        "count": 0},
    })

    assert result["last_search"]["count"] > 0
    assert all("award_id" in c and c["value_kes"] for c in result["candidates"])
    logged = audit_conn.execute(
        "SELECT * FROM audit_log WHERE run_id = %s ORDER BY id", ("run-int",)).fetchall()
    assert len(logged) == 1
    assert logged[0]["tool"] == "find_comparable_awards"
    assert logged[0]["input"]["match"] == "loose"


def test_the_first_search_query_is_built_in_code_and_not_asked_of_the_model(monkeypatch):
    """The first query decides the pool, so a model rephrasing it moved the median between runs."""
    asked = []

    def fake_ask(prompt_name, payload):
        asked.append(prompt_name)
        return {"query": "chainlink fence construction", "match": "strict", "min_year": 2023}

    monkeypatch.setattr(research, "ask_json", fake_ask)

    state = {"run_id": "run-1", "description": "construction of chainlink fence with a sliding gate",
             "search_attempts": 0, "tender_ref": "T/1", "category": "works"}
    result = research.research_node(state)

    assert asked == []
    assert result["query"] == "construction of chainlink fence with a sliding gate"


def test_the_first_search_begins_at_loose_not_strict(monkeypatch):
    """Strict matched almost nothing on this corpus and cost a full attempt every run."""
    state = {"run_id": "run-1", "description": "construction of chainlink fence",
             "search_attempts": 0, "tender_ref": "T/1", "category": "works"}
    monkeypatch.setattr(research, "find_comparable_awards",
                        lambda *args, **kwargs: {"awards": [], "count": 0})

    result = research.research_node(state)

    assert result["match"] == "loose"


def test_the_model_still_chooses_how_to_widen_the_second_search(monkeypatch):
    asked = []

    def fake_ask(prompt_name, payload):
        asked.append((prompt_name, payload))
        return {"query": "fencing", "match": "fuzzy", "min_year": None}

    monkeypatch.setattr(research, "ask_json", fake_ask)
    monkeypatch.setattr(research, "find_comparable_awards",
                        lambda *args, **kwargs: {"awards": [], "count": 0})

    state = {"run_id": "run-1", "description": "chainlink fence", "search_attempts": 1,
             "query": "chainlink fence", "match": "loose", "min_year": 2023,
             "last_search": {"query": "chainlink fence", "match": "loose", "min_year": 2023,
                             "count": 0},
             "tender_ref": "T/1", "category": "works"}
    research.research_node(state)

    assert [name for name, _ in asked] == ["research_next_move"]


def test_the_widening_after_a_model_answer_is_still_widened_by_a_rule(monkeypatch):
    """A model that keeps proposing the same narrow search must not spend every attempt on it."""
    calls = []

    def fake_ask(prompt_name, payload):
        calls.append(payload)
        return {"query": "fence", "match": "strict", "min_year": 2024}

    monkeypatch.setattr(research, "ask_json", fake_ask)
    monkeypatch.setattr(research, "find_comparable_awards",
                        lambda *args, **kwargs: {"awards": [], "count": 0})

    state = {"run_id": "run-1", "description": "chainlink fence", "search_attempts": 1,
             "query": "chainlink fence", "match": "loose", "min_year": 2023,
             "last_search": {"query": "chainlink fence", "match": "loose", "min_year": 2023,
                             "count": 0},
             "tender_ref": "T/1", "category": "works"}
    research.research_node(state)

    assert state["match"] != "strict"
