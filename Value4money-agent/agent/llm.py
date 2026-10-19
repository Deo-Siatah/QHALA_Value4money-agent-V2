# The one place that talks to a model. Hasibu is a persona, not a person: see prompts/persona.md.
# Every model job in the agent is one ask_json call with a named prompt and a JSON payload.
# The provider is a registry entry, so Groq today and a local model later is a settings change.
# Nothing here decides anything: it sends the prompt, parses the answer and refuses a bad one.

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from agent.tracing import annotate_llm, log_model_call, traced

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_model = None


class LlmError(RuntimeError):
    """Raised when no model is configured or the answer is not usable; never swallowed."""


def _build_groq() -> Any:
    """Build a Groq chat model, passing the key only when settings holds one.

    max_tokens is set rather than left to the provider. Without it the reply is cut off part way
    through a long answer, and the cut arrives as text that is not JSON, so a judgement covering
    every candidate award lost the awards the model had not reached yet.
    """
    from langchain_groq import ChatGroq

    keys = {"groq_api_key": settings.groq_api_key} if settings.groq_api_key else {}
    return ChatGroq(model=settings.llm_model, temperature=settings.llm_temperature,
                    timeout=settings.llm_timeout_s, max_tokens=settings.llm_max_tokens, **keys)


def _build_ollama() -> Any:
    """Build a local Ollama chat model."""
    from langchain_ollama import ChatOllama

    return ChatOllama(model=settings.llm_model, temperature=settings.llm_temperature,
                      base_url=settings.ollama_base_url)


PROVIDERS: Dict[str, Callable[[], Any]] = {"groq": _build_groq, "ollama": _build_ollama}


def get_model() -> Any:
    """Build the chat model once and reuse it, refusing a missing model or an unknown provider."""
    global _model
    if _model is None:
        if not settings.llm_model:
            raise LlmError("LLM_MODEL is not set: name the model to use, for example in .env")
        build = PROVIDERS.get(settings.llm_provider)
        if build is None:
            raise LlmError(f"llm_provider must be one of {sorted(PROVIDERS)}, "
                           f"got {settings.llm_provider!r}")
        _model = build()
    return _model


def _parse_json(reply: str, prompt_name: str) -> dict:
    """Read one JSON object out of a model reply, naming the prompt when it is not there."""
    text = (reply or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmError(f"prompt '{prompt_name}' did not answer with JSON: {exc}. "
                       f"Reply began: {text[:200]!r}") from exc
    if not isinstance(answer, dict):
        raise LlmError(f"prompt '{prompt_name}' must answer with a JSON object, "
                       f"got {type(answer).__name__}: {text[:200]!r}")
    return answer


def ask_json(prompt_name: str, payload: Dict[str, Any]) -> dict:
    """Send one prompt, with the persona and the payload, and return the parsed JSON answer."""
    prompt_path = PROMPTS_DIR / f"{prompt_name}.md"
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LlmError(f"prompt '{prompt_name}' could not be read from {prompt_path}: {exc}") from exc

    persona = (PROMPTS_DIR / "persona.md").read_text(encoding="utf-8")
    system = f"{persona}\n\n{prompt}"
    reply = _ask_model(prompt_name, [
        SystemMessage(content=system),
        HumanMessage(content=json.dumps(payload, indent=2, default=str)),
    ])
    answer = _parse_json(reply, prompt_name)
    log_model_call(prompt_name, system, payload, answer=answer)
    return answer


@traced("llm")
def _ask_model(prompt_name: str, messages: list) -> str:
    """Send the messages to the model and record what was sent, so Opik can cost and compare it.

    The model is streamed rather than asked in one go, so the words arrive as they are written and
    the terminal can show them being written. What comes back is still one string, which is all the
    nodes have ever seen. A model that cannot stream is called in one go instead.
    """
    model = get_model()
    text, usage = "", {}
    try:
        for chunk in model.stream(messages):
            piece = getattr(chunk, "content", None)
            if isinstance(piece, str):
                text += piece
            chunk_usage = getattr(chunk, "usage_metadata", None)
            if chunk_usage:
                usage = chunk_usage
    except (AttributeError, NotImplementedError, TypeError):
        reply = model.invoke(messages)
        return _finish(reply, prompt_name)

    annotate_llm(prompt_name, model=settings.llm_model, provider=settings.llm_provider,
                 usage=_token_usage_from(usage), temperature=settings.llm_temperature)
    return text


def _finish(reply: Any, prompt_name: str) -> str:
    """Read a whole reply from a non-streaming model, recording it the same way."""
    content = getattr(reply, "content", None)
    if not isinstance(content, str):
        content = str(content)
    annotate_llm(prompt_name, model=settings.llm_model, provider=settings.llm_provider,
                 usage=_token_usage(getattr(reply, "usage_metadata", None)),
                 temperature=settings.llm_temperature)
    return content


def _token_usage_from(counts: Optional[dict]) -> dict:
    """Read streamed token counts into the names Opik expects."""
    counts = counts or {}
    prompt_tokens = counts.get("prompt_tokens", counts.get("input_tokens", 0))
    completion_tokens = counts.get("completion_tokens", counts.get("output_tokens", 0))
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": counts.get("total_tokens", prompt_tokens + completion_tokens)}


def _token_usage(reply: Any) -> dict:
    """Read the token counts off a reply in the names Opik expects."""
    return _token_usage_from(getattr(reply, "usage_metadata", None) or {})
