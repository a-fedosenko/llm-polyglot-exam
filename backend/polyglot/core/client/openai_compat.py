"""OpenAI-compatible chat client.

Two behaviours here are load-bearing, both established by the experiment in
docs/02:

1. **Reasoning must be off.** Left at each model's default, gemini-3 and
   deepseek-v4 deliberate while gpt-4o does not, which makes results
   incomparable; on one prompt deepseek spent 299 tokens reasoning and returned
   empty content. `reasoning_effort: "none"` fixes it where supported — and
   where `"none"` itself is refused, the next-least setting is tried before
   the parameter is given up, because gpt-5 rejects `"none"`, takes
   `"minimal"`, and at its default spends a 400-token budget reasoning about
   three sentences and emits nothing.
2. **"OpenAI-compatible" is not uniform.** gpt-4o rejects `reasoning_effort`
   outright; OpenAI's reasoning models (o-series, gpt-5) reject `max_tokens` and
   want `max_completion_tokens` for the same thing. Support is probed per model
   and cached, never assumed — a hard-coded list of which models take which
   parameter is a list that is wrong by the next model release, and the failure
   it produces is a 400 that the run scores as the model refusing to write the
   language.
3. **The task goes in a system message, the material in a user message.** A
   single blob invites a model to continue the text rather than carry out the
   instruction, and the stable half is what providers cache. `complete()` takes
   an optional `system`; callers that pass none send exactly what they sent
   before, so this is additive.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 180
RETRY_CODES = {429, 500, 502, 503, 529}

#: Reasoning settings to try, least deliberation first, when the configured one
#: is refused. `None` is the end of it: the parameter is dropped and the model
#: thinks as much as it likes.
EFFORT_LADDER: tuple[str | None, ...] = ("none", "minimal", "low", None)

#: How much more room a reasoning model gets when its reasoning ate the whole
#: budget before it wrote a word. One step, not a ladder: if four times the
#: asked-for length is not enough to write three sentences, the problem is not
#: the ceiling.
BUDGET_HEADROOM = 4

#: Ceiling on renegotiation within one call: every parameter in the table, every
#: rung of the effort ladder, and the one budget bump. A provider that still
#: refuses after that is refusing, not negotiating.
MAX_ADAPTATIONS = 8


@dataclass
class Completion:
    text: str | None
    error: str | None
    truncated: bool = False
    usage: dict = field(default_factory=dict)
    model: str = ""
    latency_s: float = 0.0
    #: Parameters this provider refused, and what was done about them. Carried
    #: out of the client because one of them is not a detail: a model that
    #: rejects `temperature: 0` was sampled at its own default, and two runs
    #: sampled differently are not two measurements of the same thing.
    adjusted: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.text is not None and self.error is None


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: int = DEFAULT_TIMEOUT,
                 retries: int = 4, reasoning_effort: str | None = "none") -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self._key = api_key
        self.timeout = timeout
        self.retries = retries
        self.reasoning_effort = reasoning_effort
        self._no_reasoning_param: set[str] = set()
        #: Where each model has ended up on the effort ladder. Refusing
        #: `"none"` does not mean a model cannot be told to think less — gpt-5
        #: takes `"minimal"` and rejects `"none"` — and dropping the parameter
        #: outright left it reasoning at its default, which is how a request
        #: for three sentences came back as 400 reasoning tokens and no text.
        self._effort: dict[str, str | None] = {}
        #: Models that needed more room than the caller asked for, because
        #: their reasoning is charged against the same budget as their answer.
        self._budget: dict[str, int] = {}
        #: Models that want `max_completion_tokens` instead of `max_tokens`.
        #: Learned from the provider's own 400 on the first call and reused for
        #: the rest of the process.
        self._completion_tokens_param: set[str] = set()
        #: Models that refuse an explicit temperature and will only run at
        #: their own default.
        self._no_temperature_param: set[str] = set()

    #: Below this, a "key" is more likely to be a letter that occurs in the
    #: provider's own error text than a credential — and redacting it would
    #: corrupt the one sentence that says what went wrong.
    MIN_REDACTABLE_KEY = 12

    def _redact(self, text: str) -> str:
        if not self._key or len(self._key) < self.MIN_REDACTABLE_KEY:
            return text
        return text.replace(self._key, "[redacted]")

    def complete(self, model: str, prompt: str, *, max_tokens: int = 400,
                 temperature: float = 0.0, system: str | None = None) -> Completion:
        def payload() -> bytes:
            messages = ([{"role": "system", "content": system}] if system else []) + [
                {"role": "user", "content": prompt}]
            body: dict = {"model": model, "messages": messages}
            body["max_completion_tokens" if model in self._completion_tokens_param
                 else "max_tokens"] = max_tokens * self._budget.get(model, 1)
            if model not in self._no_temperature_param:
                body["temperature"] = temperature
            effort = self._effort.get(model, self.reasoning_effort)
            if effort:
                body["reasoning_effort"] = effort
            return json.dumps(body).encode()

        body = payload()
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        started = time.monotonic()
        adjusted: list[str] = list(self._adjustments(model))

        attempt = 0
        adaptations = 0
        while attempt < self.retries:
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                detail = self._redact(e.read().decode("utf-8", "replace")[:300])
                # "OpenAI-compatible" is a family resemblance, not a contract,
                # and the differences surface as a 400 that names the parameter
                # it is unhappy with. Each one is taken at its word and
                # remembered for the model, so a run pays one 400 and not forty
                # — and an adaptation does not spend a retry, since nothing was
                # wrong with the network.
                if e.code == 400 and adaptations < MAX_ADAPTATIONS:
                    fixed = self._adapt(model, detail)
                    if fixed:
                        adaptations += 1
                        adjusted = list(self._adjustments(model))
                        body = payload()
                        continue
                if e.code in RETRY_CODES and attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    attempt += 1
                    continue
                return Completion(None, f"HTTP {e.code}: {detail}", model=model,
                                  latency_s=time.monotonic() - started,
                                  adjusted=tuple(adjusted))
            except Exception as e:  # noqa: BLE001 - network/JSON failures are all retryable
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    attempt += 1
                    continue
                return Completion(None, self._redact(f"{type(e).__name__}: {e}"), model=model,
                                  latency_s=time.monotonic() - started,
                                  adjusted=tuple(adjusted))

            choice = data["choices"][0]
            text = (choice.get("message", {}).get("content") or "").strip()
            usage = data.get("usage", {}) or {}
            latency = time.monotonic() - started
            finish = choice.get("finish_reason")
            if not text:
                # A reasoning model can burn the whole budget before emitting
                # anything. That is truncation, never a refusal, and must not be scored.
                #
                # It is also often recoverable: the reasoning is charged against
                # the same budget as the answer, so the fix is room rather than
                # a different model. Asked again once with a larger budget,
                # remembered for the model, and reported — a run whose answers
                # were written under a different ceiling should say so.
                reasoned = (usage.get("completion_tokens_details") or {}).get(
                    "reasoning_tokens", 0)
                if (finish == "length" and reasoned
                        and self._budget.get(model, 1) < BUDGET_HEADROOM):
                    self._budget[model] = BUDGET_HEADROOM
                    adaptations += 1
                    adjusted = list(self._adjustments(model))
                    body = payload()
                    continue
                return Completion(None, f"empty content (finish_reason={finish}, usage={usage})",
                                  truncated=finish == "length", usage=usage, model=model,
                                  latency_s=latency, adjusted=tuple(adjusted))
            return Completion(text, None, truncated=finish == "length", usage=usage,
                              model=model, latency_s=latency, adjusted=tuple(adjusted))

        return Completion(None, "exhausted retries", model=model,
                          latency_s=time.monotonic() - started,
                          adjusted=tuple(adjusted))

    #: (what the provider's 400 mentions, where the model is remembered, what
    #: the page should be told). Order is the order they are tried in.
    _ADAPTATIONS = (
        ("max_completion_tokens", "_completion_tokens_param",
         "length asked for as max_completion_tokens, which is the name this "
         "model takes"),
        ("temperature", "_no_temperature_param",
         "sampled at the provider's default temperature — this model refuses an "
         "explicit one, so the run is not as reproducible as the rest"),
    )

    def _adapt(self, model: str, detail: str) -> bool:
        """Learn one thing from a 400 and say whether anything was learned."""
        if "reasoning_effort" in detail:
            return self._step_down_effort(model)
        for needle, attr, _ in self._ADAPTATIONS:
            seen: set[str] = getattr(self, attr)
            if needle in detail and model not in seen:
                seen.add(model)
                return True
        return False

    def _step_down_effort(self, model: str) -> bool:
        """The next reasoning setting this model might accept.

        `"none"` is not universal — gpt-5 rejects it and takes `"minimal"` —
        and the old behaviour of dropping the parameter at the first refusal
        left exactly the models that most need telling to think less reasoning
        at their default.
        """
        current = self._effort.get(model, self.reasoning_effort)
        try:
            i = EFFORT_LADDER.index(current)
        except ValueError:
            i = 0
        if i + 1 >= len(EFFORT_LADDER):
            return False
        nxt = EFFORT_LADDER[i + 1]
        self._effort[model] = nxt
        if nxt is None:
            self._no_reasoning_param.add(model)
        return True

    def _adjustments(self, model: str) -> tuple[str, ...]:
        """What this model has made us give up, in words the page can show."""
        out = [note for _, attr, note in self._ADAPTATIONS
               if model in getattr(self, attr)]
        effort = self._effort.get(model, self.reasoning_effort)
        if effort != self.reasoning_effort:
            out.append(
                f"reasoning set to {effort!r} — this model refused "
                f"{self.reasoning_effort!r}, and how much a model deliberates "
                f"is part of what a result means"
                if effort else
                "sent without reasoning_effort — this model rejects every value "
                "for it, so it deliberated at its own default")
        if model in self._budget:
            out.append(
                f"asked again with {self._budget[model]}× the length budget — "
                f"its reasoning had consumed the whole of the first one before "
                f"it wrote anything")
        return tuple(out)
