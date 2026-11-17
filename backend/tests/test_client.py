"""The client's two adaptations to providers that disagree about parameters.

Both are learned from the provider's own 400 rather than from a list of model
names, because a hard-coded list is wrong by the next model release — and the
failure it produces is not a visible error but a run that scores the model as
having refused to write the language.
"""
from __future__ import annotations

import json

import pytest

from polyglot.core.client.openai_compat import OpenAICompatClient


class FakeHTTPError(Exception):
    def __init__(self, code: int, body: str) -> None:
        self.code = code
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def transport(monkeypatch):
    """Records every request body and answers from a scripted sequence."""
    import urllib.error
    import urllib.request

    monkeypatch.setattr(urllib.error, "HTTPError", FakeHTTPError, raising=False)

    sent: list[dict] = []
    outcomes: list = []

    class Response:
        def __init__(self, data): self._data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self._data).encode()

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        outcome = outcomes.pop(0)
        if isinstance(outcome, FakeHTTPError):
            raise outcome
        return Response(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    # The module catches the real class, so it has to be the one raised too.
    monkeypatch.setattr("polyglot.core.client.openai_compat.urllib.error.HTTPError",
                        FakeHTTPError, raising=False)
    return sent, outcomes


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {}}


def test_a_model_that_wants_max_completion_tokens_is_asked_again_that_way(transport):
    """gpt-5-nano's 400 names the parameter it wants; the client takes it at its word."""
    sent, outcomes = transport
    outcomes.extend([
        FakeHTTPError(400, json.dumps({"error": {
            "message": "Unsupported parameter: 'max_tokens' is not supported with "
                       "this model. Use 'max_completion_tokens' instead.",
            "param": "max_tokens", "code": "unsupported_parameter"}})),
        _answer("Der Bus kam zu spät an."),
    ])
    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=4)
    out = c.complete("gpt-5-nano", "write something", max_tokens=400)

    assert out.ok and out.text == "Der Bus kam zu spät an."
    assert "max_tokens" in sent[0]
    assert sent[1]["max_completion_tokens"] == 400 and "max_tokens" not in sent[1]

    # And it is remembered, so the rest of a run pays one 400 and not forty.
    outcomes.append(_answer("again"))
    c.complete("gpt-5-nano", "write something else", max_tokens=400)
    assert "max_completion_tokens" in sent[2]


def test_a_model_that_refuses_temperature_runs_at_its_default_and_says_so(transport):
    """Giving up `temperature: 0` is not a detail — it is a note on the result.

    Two runs sampled differently are not two measurements of the same thing, so
    the concession travels with the item instead of being swallowed here.
    """
    sent, outcomes = transport
    outcomes.extend([
        FakeHTTPError(400, json.dumps({"error": {
            "message": "Unsupported value: 'temperature' does not support 0 with "
                       "this model. Only the default (1) value is supported.",
            "param": "temperature", "code": "unsupported_value"}})),
        _answer("Der Bus kam zu spät an."),
    ])
    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=4)
    out = c.complete("gpt-5-nano", "write something", temperature=0.0)

    assert out.ok
    assert "temperature" in sent[0]
    assert "temperature" not in sent[1]
    assert any("default temperature" in a for a in out.adjusted), out.adjusted


def test_adapting_to_a_provider_does_not_spend_the_retry_budget(transport):
    """Three refused parameters in a row must still leave retries for the network."""
    sent, outcomes = transport
    for message in ("Unsupported parameter: 'reasoning_effort'",
                    "Use 'max_completion_tokens' instead.",
                    "Unsupported value: 'temperature' does not support 0"):
        outcomes.append(FakeHTTPError(400, json.dumps({"error": {"message": message}})))
    outcomes.append(_answer("fine"))

    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=2)
    out = c.complete("gpt-5-nano", "write something")
    assert out.ok and out.text == "fine"
    assert len(sent) == 4
    assert len(out.adjusted) == 3


def _empty_with_reasoning(tokens: int) -> dict:
    return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"completion_tokens": tokens,
                      "completion_tokens_details": {"reasoning_tokens": tokens}}}


def test_a_refused_reasoning_setting_steps_down_before_it_is_given_up(transport):
    """gpt-5 rejects "none" and takes "minimal".

    Dropping the parameter at the first refusal left exactly the models that
    most need telling to think less reasoning at their default — which is how a
    request for three sentences came back as 400 reasoning tokens and no text.
    """
    sent, outcomes = transport
    outcomes.extend([
        FakeHTTPError(400, json.dumps({"error": {
            "message": "Unsupported value: 'reasoning_effort' does not support "
                       "'none' with this model."}})),
        _answer("Der Bus kam zu spät an."),
    ])
    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=4)
    out = c.complete("gpt-5-nano", "write something")

    assert out.ok
    assert sent[0]["reasoning_effort"] == "none"
    assert sent[1]["reasoning_effort"] == "minimal", "the next rung, not nothing"
    assert any("minimal" in a for a in out.adjusted), out.adjusted


def test_reasoning_that_eats_the_whole_budget_is_asked_again_with_room(transport):
    sent, outcomes = transport
    outcomes.extend([_empty_with_reasoning(400), _answer("Der Bus kam zu spät an.")])
    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=4)
    out = c.complete("gpt-5-nano", "write something", max_tokens=400)

    assert out.ok and out.text == "Der Bus kam zu spät an."
    assert sent[0]["max_tokens"] == 400
    assert sent[1]["max_tokens"] == 1600
    assert any("length budget" in a for a in out.adjusted), out.adjusted

    # Remembered, so the next item does not pay for the discovery again.
    outcomes.append(_answer("again"))
    c.complete("gpt-5-nano", "and another", max_tokens=400)
    assert sent[2]["max_tokens"] == 1600


def test_a_model_that_writes_nothing_even_with_room_is_still_no_answer(transport):
    """The honest end of the ladder: void, never a refusal."""
    sent, outcomes = transport
    outcomes.extend([_empty_with_reasoning(400), _empty_with_reasoning(1600)])
    c = OpenAICompatClient("http://x/v1", "sk-test-key-0123456789", retries=4)
    out = c.complete("gpt-5-nano", "write something", max_tokens=400)

    assert not out.ok and out.text is None
    assert out.truncated and "empty content" in (out.error or "")
    assert len(sent) == 2, "one bump, not an unbounded climb"
