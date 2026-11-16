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


#: The control set the tests measure against.
#:
#: Pinned rather than loaded from disk, and the reason is a failure this suite
#: actually had: once `polyglot bootstrap` fetched the real FLORES controls,
#: `de` acquired a real reference, the scripted back-translation scored chrF++
#: 5.4 against it, the reader stopped qualifying and three tests changed their
#: answers. A suite whose verdict depends on whether an optional 1.6 GB asset
#: has been downloaded is not a suite anyone can trust.
#:
#: The reference here is exactly what `ScriptedClient` back-translates to, so a
#: qualification run scores a perfect match and the tests exercise the *ladder*
#: rather than the quality of a fixture's German.
BACK = ("The bus arrived late. It was raining heavily that morning. "
        "She did not reach the office until after nine.")

CONTROLS = {
    "de": {"kind": "reference",
           "items": [{"text": "Der Bus kam zu spät an.", "reference": BACK}] * 2},
    "pt": {"kind": "reference",
           "items": [{"text": "O ônibus atrasou.", "reference": BACK}] * 2},
}


@pytest.fixture(autouse=True)
def pinned_controls(monkeypatch):
    """Every test measures against the same controls, downloaded or not.

    Patched in both namespaces, and reached through `sys.modules`, because the
    package shadows its own submodule: `polyglot.core.bt/__init__.py` does
    `from .qualify import qualify`, which rebinds the attribute `qualify` on the
    package from the module to the function. So `import polyglot.core.bt.qualify
    as m` binds the *function*, and a dotted monkeypatch string walks into the
    same trap. Worth knowing before adding anything else to that package.

    Both namespaces, because `qualify()` calls `load_controls` from its own
    module while `pivot.panel_for` imports it from the package at call time.
    """
    import sys

    import polyglot.core.bt as bt_pkg
    qualify_mod = sys.modules["polyglot.core.bt.qualify"]

    def fake(pivot: str = "en") -> dict:
        return {k: dict(v) for k, v in CONTROLS.items()}

    monkeypatch.setattr(qualify_mod, "load_controls", fake)
    monkeypatch.setattr(bt_pkg, "load_controls", fake)


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
