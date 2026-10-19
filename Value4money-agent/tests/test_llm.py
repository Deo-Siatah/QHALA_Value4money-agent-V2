import json

import pytest

from agent import llm
from agent.llm import LlmError, ask_json, get_model

PROMPTS = ["intake_tender_profile", "research_next_move", "quality_similarity_judgement",
           "quality_suspect_verdicts", "draft_review_wording"]


class FakeReply:
    """Stands in for a chat model's reply, whole or in pieces."""

    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage


class FakeModel:
    """Records the messages it was given and replies with a fixed answer."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return FakeReply(self.reply)


class FakeStreamModel:
    """A model that streams its answer in pieces, the way a real one does."""

    def __init__(self, pieces):
        self.pieces = pieces
        self.calls = []

    def stream(self, messages):
        self.calls.append(messages)
        for index, piece in enumerate(self.pieces):
            yield FakeReply(piece, usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


@pytest.fixture
def fake_provider(monkeypatch):
    """Register a fake provider so no test ever reaches a real model."""
    built = []

    def install(reply='{"ok": true}'):
        def build():
            model = FakeModel(reply)
            built.append(model)
            return model

        monkeypatch.setitem(llm.PROVIDERS, "fake", build)
        monkeypatch.setattr(llm.settings, "llm_provider", "fake")
        monkeypatch.setattr(llm.settings, "llm_model", "fake-model")
        monkeypatch.setattr(llm, "_model", None)
        return built

    return install


@pytest.mark.parametrize("prompt", PROMPTS)
def test_every_prompt_file_exists_and_states_its_output_shape(prompt):
    text = (llm.PROMPTS_DIR / f"{prompt}.md").read_text(encoding="utf-8")

    assert "# Task:" in text
    assert "## Input" in text
    assert "## Output" in text
    assert "## Example" in text
    assert "## Rules" in text


def test_the_persona_names_hasibu_and_bans_the_words():
    text = (llm.PROMPTS_DIR / "persona.md").read_text(encoding="utf-8").lower()

    assert "hasibu" in text
    for word in ["recommend", "winner", "overpriced", "gouging", "rigging", "fraud", "corrupt"]:
        assert word in text


def test_the_draft_prompt_states_the_length_limits():
    text = (llm.PROMPTS_DIR / "draft_review_wording.md").read_text(encoding="utf-8")

    assert "60 words" in text
    assert "25 words" in text
    assert "40 words" in text
    assert "Markdown" in text


def test_an_unknown_provider_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "not-a-provider")
    monkeypatch.setattr(llm.settings, "llm_model", "some-model")
    monkeypatch.setattr(llm, "_model", None)

    with pytest.raises(LlmError) as err:
        get_model()

    assert "not-a-provider" in str(err.value)
    assert "groq" in str(err.value) and "ollama" in str(err.value)


def test_a_missing_model_name_is_refused_by_setting_name(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "fake")
    monkeypatch.setattr(llm.settings, "llm_model", "")
    monkeypatch.setattr(llm, "_model", None)

    with pytest.raises(LlmError) as err:
        get_model()

    assert "LLM_MODEL" in str(err.value)


def test_a_missing_prompt_file_is_refused_by_name(fake_provider):
    fake_provider()

    with pytest.raises(LlmError) as err:
        ask_json("no_such_prompt", {"a": 1})

    assert "no_such_prompt" in str(err.value)


def test_the_model_is_built_once_and_reused(fake_provider):
    built = fake_provider()

    first, second = get_model(), get_model()

    assert first is second
    assert len(built) == 1


def test_ask_json_sends_the_persona_the_prompt_and_the_payload(fake_provider):
    built = fake_provider('{"category": "works", "description": "construction of classroom blocks"}')

    answer = ask_json("intake_tender_profile", {"tender_ref": "TENDER/NC/2026/014",
                                                "items": [{"description": "Classroom construction"}]})

    assert answer == {"category": "works", "description": "construction of classroom blocks"}
    system, human = built[0].calls[0]
    persona = (llm.PROMPTS_DIR / "persona.md").read_text(encoding="utf-8")
    assert system.content.startswith(persona)
    assert "# Task: name the work that was tendered" in system.content
    assert json.loads(human.content) == {"tender_ref": "TENDER/NC/2026/014",
                                         "items": [{"description": "Classroom construction"}]}


def test_a_fenced_json_block_is_accepted(fake_provider):
    fake_provider('```json\n{"verdicts": {"a01": {"verdict": "keep", "reason": "same work"}}}\n```')

    answer = ask_json("quality_suspect_verdicts", {"awards": []})

    assert answer["verdicts"]["a01"]["verdict"] == "keep"


@pytest.mark.parametrize("reply", [
    "I think this tender looks fine.",
    "",
    '["a01", "a02"]',
    "{'verdicts': {}}",
])
def test_prose_or_a_non_object_is_refused_by_prompt_name(fake_provider, reply):
    fake_provider(reply)

    with pytest.raises(LlmError) as err:
        ask_json("quality_suspect_verdicts", {"awards": []})

    assert "quality_suspect_verdicts" in str(err.value)


def test_values_that_json_cannot_hold_are_written_as_text(fake_provider):
    from datetime import datetime, timezone

    fake_provider('{"when": "2026-03-01T09:00:00+00:00"}')

    answer = ask_json("intake_tender_profile",
                      {"tender_ref": "T/1", "at": datetime(2026, 3, 1, 9, tzinfo=timezone.utc)})

    assert answer["when"] == "2026-03-01T09:00:00+00:00"


def test_groq_and_ollama_are_both_registered():
    assert "groq" in llm.PROVIDERS
    assert "ollama" in llm.PROVIDERS


def test_the_groq_client_is_given_room_to_finish_its_answer(monkeypatch):
    """A similarity judgement over 28 awards came back cut off mid-object, finish_reason 'length'.

    ChatGroq leaves max_tokens unset, so the provider's own cap applies. gpt-oss-120b spends part
    of that budget reasoning before it writes anything, and what was left stopped the JSON half way
    through the verdicts. Every award the model had not reached was then counted as excluded work.
    """
    monkeypatch.setattr(llm.settings, "llm_provider", "groq")
    monkeypatch.setattr(llm.settings, "llm_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(llm.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(llm, "_model", None)

    model = get_model()

    assert model.max_tokens == llm.settings.llm_max_tokens
    assert model.max_tokens >= 8192


def test_the_real_providers_are_built_from_settings(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "ollama")
    monkeypatch.setattr(llm.settings, "llm_model", "llama3.1")
    monkeypatch.setattr(llm, "_model", None)

    model = get_model()

    assert model.model == "llama3.1"
    assert str(model.base_url).startswith("http://localhost:11434")


def test_the_model_still_answers_when_it_cannot_stream(monkeypatch):
    """A model with no stream is called in one go, so nothing breaks."""
    model = FakeModel('{"ok": true}')
    monkeypatch.setitem(llm.PROVIDERS, "fake", lambda: model)
    monkeypatch.setattr(llm.settings, "llm_provider", "fake")
    monkeypatch.setattr(llm.settings, "llm_model", "fake-model")
    monkeypatch.setattr(llm, "_model", None)

    assert ask_json("intake_tender_profile", {"tender_ref": "T/1"}) == {"ok": True}


def test_a_streamed_answer_is_joined_into_one_string(monkeypatch):
    """The model is asked in pieces, but the caller still receives one whole answer."""
    model = FakeStreamModel(['{"category": ', '"works", ', '"description": "fence"}'])
    monkeypatch.setitem(llm.PROVIDERS, "fake", lambda: model)
    monkeypatch.setattr(llm.settings, "llm_provider", "fake")
    monkeypatch.setattr(llm.settings, "llm_model", "fake-model")
    monkeypatch.setattr(llm, "_model", None)

    answer = ask_json("intake_tender_profile", {"tender_ref": "T/1"})

    assert answer == {"category": "works", "description": "fence"}
    assert len(model.calls) == 1


def test_streamed_usage_is_reported_to_opik(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "annotate_llm", lambda prompt, **kwargs: seen.append((prompt, kwargs)))
    monkeypatch.setitem(llm.PROVIDERS, "fake", lambda: FakeStreamModel(['{"a": 1}']))
    monkeypatch.setattr(llm.settings, "llm_provider", "fake")
    monkeypatch.setattr(llm.settings, "llm_model", "fake-model")
    monkeypatch.setattr(llm, "_model", None)

    ask_json("quality_similarity_judgement", {})

    assert seen[0][1]["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
