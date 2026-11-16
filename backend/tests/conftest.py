from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from polyglot.core.client.openai_compat import Completion


class ScriptedClient:
    """A client that answers from a script instead of a network.

    Keyed by `role` rather than by prompt, because the prompts are the thing
    under test: a fixture that matched on prompt text would have to be rewritten
    every time a prompt moved, and would then be asserting the prompt rather
    than the behaviour.
    """

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    def complete(self, model, prompt, *, max_tokens=400, temperature=0.0,
                 system=None) -> Completion:
        self.calls.append({"model": model, "system": system, "user": prompt,
                           "max_tokens": max_tokens})
        role = self._role(system, prompt)
        a = self.answers.get(role, "")
        if callable(a):
            a = a(len(self.calls), model, system, prompt)
        if isinstance(a, Completion):
            return a
        return Completion(str(a), None, model=model)

    @staticmethod
    def _role(system: str | None, prompt: str) -> str:
        s = system or ""
        if "rewrite the text the user gives you" in s:
            return "convert"
        if "You are a native speaker of" in s:
            return "generation"
        if "which regional variety a piece of text" in s:
            return "identify"
        if "shown two texts" in s:
            return "separate"
        if prompt.startswith("Translate the following text into"):
            return "backtranslation"
        if "checking whether a piece of text contains" in prompt:
            return "judge"
        return "other"


@pytest.fixture
def scripted():
    return ScriptedClient


@pytest.fixture
def db(tmp_path, monkeypatch):
    from polyglot.store import reset_for_tests

    reset_for_tests(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    yield tmp_path


@pytest.fixture
def corpus(tmp_path):
    from polyglot.core.probe.corpus import Corpus

    c = Corpus(path=tmp_path / "corpus.jsonl")
    yield c
    c.close()
