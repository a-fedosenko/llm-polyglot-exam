"""The property the whole design rests on: one traversal, two ways to walk it.

If stepping through a run and draining it could reach different answers, the
manual page would be a lie told in the project's own voice — and the previous
tool's conventions record what that drift costs when it happens (an analysis
path re-implemented a production call, got it subtly wrong, and 68 bad verdicts
reached a protocol's conclusions).
"""
from __future__ import annotations

import json

import pytest

from polyglot.core.probe.specs import load_specs
from polyglot.core.scheme import load_scheme
from polyglot.engine.capability import CapabilityMachine
from polyglot.engine.dialect import DialectMachine

GERMAN = ("Der Bus kam zu spät an. Es regnete stark an diesem Morgen. "
          "Sie erreichte das Büro erst nach neun Uhr.")
GERMAN_B = ("Der Bus hatte Verspätung. An jenem Morgen regnete es heftig. "
            "Im Büro war sie erst nach neun.")
BACK = ("The bus arrived late. It was raining heavily that morning. "
        "She did not reach the office until after nine.")


def judge_all_present(n, model, system, prompt):
    facts = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
    return json.dumps({"verdicts": ["present"] * len(facts)})


def capability_answers(generation=GERMAN):
    return {"generation": generation, "backtranslation": BACK,
            "judge": judge_all_present}


def machine(scripted, corpus, answers, **kw):
    from polyglot.core.bt import RemoteBackTranslator

    client = scripted(answers)
    scheme = load_scheme("catmint")
    defaults = dict(
        scheme=scheme, tag="de", engine="acme-model-a", client=client,
        backtranslators=[RemoteBackTranslator(client, "other-reader-b")],
        judge_model="other-judge-c", specs=load_specs(), corpus=corpus,
        pivot="en")
    return CapabilityMachine(**{**defaults, **kw}), client


def drain(m):
    while (p := m.plan()) is not None:
        m.execute(p)
    return m.outcome()


# -- the traversal -------------------------------------------------------------

def test_stepping_and_draining_reach_the_same_verdict(scripted, corpus, tmp_path):
    """Auto mode is manual mode without the pauses, and nothing else."""
    from polyglot.core.probe.corpus import Corpus

    a, _ = machine(scripted, corpus, capability_answers())
    drained = drain(a)

    with Corpus(path=tmp_path / "second.jsonl") as c2:
        b, _ = machine(scripted, c2, capability_answers())
        stepped = None
        while True:
            p = b.plan()
            if p is None:
                stepped = b.outcome()
                break
            b.execute(p)

    assert drained["level"] == stepped["level"]
    assert drained["s_content"] == stepped["s_content"]
    assert drained["calls"] == stepped["calls"]
    assert [e.kind for e in a.history] == [e.kind for e in b.history]


def test_plan_costs_nothing(scripted, corpus):
    m, client = machine(scripted, corpus, capability_answers())
    for _ in range(5):
        m.plan()
    assert client.calls == [], "planning must not spend a call"


def test_the_preflight_prompt_is_the_prompt_that_is_sent(scripted, corpus):
    """The pre-flight is built from the production builder, not a copy of it."""
    m, client = machine(scripted, corpus, capability_answers())
    planned = m.plan()
    m.execute(planned)
    sent = client.calls[0]
    assert sent["system"] == planned.calls[0].system
    assert sent["user"] == planned.calls[0].user


def test_an_edited_prompt_is_what_goes_out(scripted, corpus):
    from polyglot.engine.types import Call

    m, client = machine(scripted, corpus, capability_answers())
    planned = m.plan()
    edited = [Call(role="generation", model="acme-model-a",
                   system="Write in German.", user="Describe a rainy morning.")]
    m.execute(planned, calls=edited, edited=True)
    assert client.calls[0]["system"] == "Write in German."
    assert client.calls[0]["user"] == "Describe a rainy morning."


# -- re-running ----------------------------------------------------------------

def test_rerunning_a_step_truncates_the_history_and_replans(scripted, corpus):
    m, _ = machine(scripted, corpus, capability_answers())
    for _ in range(4):
        m.execute(m.plan())
    before = [e.kind for e in m.history]

    del m.history[2:]
    again = m.plan()
    assert again is not None
    # The machine asks for the same step it asked for the first time, because
    # the plan is a function of the history and the history is back where it was.
    assert again.kind == before[2]


def test_judging_does_not_mutate_the_generation_it_read(scripted, corpus):
    """The fold must stay a fold: a judged item is a copy, not the original."""
    m, _ = machine(scripted, corpus, capability_answers())
    drain(m)
    generated = [e for e in m.history if e.kind == "generate"]
    assert generated, "expected at least one generation"
    assert all(e.obj.judgement is None for e in generated)


# -- the deterministic negative ------------------------------------------------

def test_a_refusal_everywhere_is_settled_without_a_reader(scripted, corpus):
    m, client = machine(scripted, corpus, capability_answers(generation="CANNOT"))
    out = drain(m)
    assert out["level"] == "Unusable"
    assert out["evidence"] == "deterministic-negative"
    assert out["backtranslator"] == "(not needed)"
    assert not any(c["model"] == "other-reader-b" for c in client.calls), \
        "a locally-settled negative must not pay for a reader"


# -- adjudication --------------------------------------------------------------

def test_adjudication_reports_a_number_and_never_replaces_one(scripted, corpus):
    def judge_one_missing(n, model, system, prompt):
        facts = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
        return json.dumps({"verdicts": ["missing"] + ["present"] * (len(facts) - 1)})

    answers = {**capability_answers(), "judge": judge_one_missing}
    m, _ = machine(scripted, corpus, answers)
    out = drain(m)

    spec = out["items"][0]["spec_id"]
    adj = m.adjudicate([{"spec_id": spec, "index": 0, "verdict": "present"}])
    assert adj["changed"] == 1
    assert adj["adjudicated"]["s_content"] > adj["measured"]["s_content"]
    # The measurement is untouched.
    assert m.outcome()["s_content"] == out["s_content"]


# -- the dialect battery -------------------------------------------------------

def dialect_machine(scripted, corpus, answers, **kw):
    client = scripted(answers)
    scheme = load_scheme("catmint")
    defaults = dict(
        scheme=scheme, tag="pt-br", engine="acme-model-a", client=client,
        attestors=["other-judge-c", "third-judge-d"],
        varieties=[scheme.get("pt-br"), scheme.get("pt-pt")],
        neutral_tag="pt", scenario="A bus was late because of heavy rain.",
        corpus=corpus)
    return DialectMachine(**{**defaults, **kw}), client


PT = ("O ônibus atrasou por causa da chuva forte. Ela chegou ao escritório "
      "depois das nove. O trânsito estava parado.")
PT_CONVERTED = ("O autocarro atrasou por causa da chuva forte. Ela chegou ao "
                "escritório depois das nove. O trânsito estava parado.")


def dialect_answers(*, identify="Brazil", separate=("Portugal", "Brazil")):
    def sep(n, model, system, prompt):
        # The identity control shows one text twice; the correct answer there is
        # `indistinguishable`, and a fixture that always split would fail every
        # attestor before the battery could be exercised at all.
        body = prompt.split("TEXT A:", 1)[-1]
        a, b = body.split("TEXT B:", 1)
        if a.strip() == b.strip():
            return json.dumps({"a": "indistinguishable", "b": "indistinguishable",
                               "markers": [], "confidence": "high"})
        first = "Portugal" if "autocarro" in a else "Brazil"
        second = "Portugal" if "autocarro" in b else "Brazil"
        return json.dumps({"a": first, "b": second, "markers": [],
                           "confidence": "high"})

    return {"generation": lambda n, m, s, p: (PT_CONVERTED if "Portugal" in (s or "")
                                              else PT),
            "convert": PT_CONVERTED,
            "identify": json.dumps({"answer": identify, "markers": [],
                                    "confidence": "high"}),
            "separate": sep}


def test_the_dialect_battery_steps_and_drains_alike(scripted, corpus, tmp_path):
    from polyglot.core.probe.corpus import Corpus

    a, _ = dialect_machine(scripted, corpus, dialect_answers())
    first = drain(a)

    with Corpus(path=tmp_path / "d2.jsonl") as c2:
        b, _ = dialect_machine(scripted, c2, dialect_answers())
        while (p := b.plan()) is not None:
            b.execute(p)
        second = b.outcome()

    assert first["level"] == second["level"]
    assert first["calls"] == second["calls"]


def test_the_producer_is_never_on_its_own_panel(scripted, corpus):
    m, _ = dialect_machine(scripted, corpus,
                           dialect_answers(),
                           attestors=["acme-model-a", "acme-other", "third-judge-d"])
    pre = m.preflight()
    assert "acme-model-a" not in pre["attestors"]
    assert "acme-other" not in pre["attestors"], \
        "the guard drops the producer's whole family, not just its id"
    assert pre["dropped_by_family_guard"] == ["acme-model-a", "acme-other"]


def test_every_control_model_is_re_qualified_on_every_run(scripted, corpus):
    m, client = dialect_machine(scripted, corpus, dialect_answers())
    drain(m)
    controls = [e for e in m.history if e.kind == "controls"]
    assert len(controls) == 2, "both eligible control models must face the controls"
    assert all(len(c.result["controls"]) == 2 for c in controls)


def test_a_control_model_that_splits_a_text_from_itself_is_discarded(scripted, corpus):
    answers = dialect_answers()
    answers["separate"] = json.dumps(
        {"a": "Portugal", "b": "Brazil", "markers": [], "confidence": "high"})
    m, _ = dialect_machine(scripted, corpus, answers)
    out = drain(m)
    assert out["level"] == "indeterminate"
    assert "No control model passed" in " ".join(out["notes"])


def test_the_panel_can_be_changed_before_the_controls_and_not_after(scripted, corpus):
    m, _ = dialect_machine(scripted, corpus, dialect_answers())
    m.execute(m.plan())                      # plan
    m.set_panel(["third-judge-d"])
    assert m.preflight()["attestors"] == ["third-judge-d"]

    while (p := m.plan()) is not None and p.kind != "controls":
        m.execute(p)
    m.execute(m.plan())                      # the first control
    with pytest.raises(ValueError, match="already started"):
        m.set_panel(["other-judge-c"])


def test_a_contrast_needs_two_named_varieties(scripted, corpus):
    scheme = load_scheme("catmint")
    with pytest.raises(ValueError, match="two named varieties"):
        dialect_machine(scripted, corpus, dialect_answers(),
                        varieties=[scheme.get("pt-br")])


def test_overruling_an_attestor_reverses_its_vote_rather_than_withdrawing_it(
        scripted, corpus):
    """“It was wrong” must mean *named another variety*, not *abstained*.

    An abstention leaves the consensus entirely, so recording a wrong answer as
    one would quietly withdraw the vote instead of reversing it, and the verdict
    would move for the wrong reason.
    """
    m, _ = dialect_machine(scripted, corpus, dialect_answers())
    drain(m)
    adj = m.adjudicate([{"probe": "identify", "attestor": "other-judge-c",
                         "correct": False}])
    votes = adj["adjudicated"]["identified"]["votes"]
    mine = next(v for v in votes if v["attestor"] == "other-judge-c")
    assert mine["correct"] is False
    assert mine["abstained"] is False


# -- the reader, and the operator's right to disagree with it ------------------

def test_the_qualification_preflight_carries_the_control_text(scripted, corpus):
    """The one step whose subject is the instrument used to show a placeholder.

    An operator who cannot see what the reader was given cannot tell a reader
    that read the language from one that invented a reading of it, which is the
    exact failure the step exists to catch.
    """
    m, client = machine(scripted, corpus, capability_answers())
    while (p := m.plan()).kind != "qualification":
        m.execute(p)

    assert p.calls, "qualification must say what it will send"
    assert all("Der Bus kam zu spät an." in c.user for c in p.calls), \
        "the control text itself, not a description of it"

    m.execute(p)
    sent = [c["user"] for c in client.calls if c["user"].startswith("Translate")]
    assert sent[0] == p.calls[0].user, "what was shown is what went out"


def test_qualification_keeps_what_the_reader_answered(scripted, corpus):
    m, _ = machine(scripted, corpus, capability_answers())
    drain(m)
    qual = next(e for e in m.history if e.kind == "qualification")
    evidence = qual.result["evidence"]
    assert len(evidence) == 2, "one row per control item put to the reader"
    assert evidence[0]["control"] == "Der Bus kam zu spät an."
    assert evidence[0]["got"] == BACK
    assert evidence[0]["expected"] == BACK
    assert evidence[0]["score"] > 90


def test_a_rejected_reader_is_not_tried_again(scripted, corpus):
    from polyglot.core.bt import RemoteBackTranslator

    client = scripted(capability_answers())
    m, _ = machine(scripted, corpus, capability_answers())
    m.client = client
    m.panel = [RemoteBackTranslator(client, "other-reader-b"),
               RemoteBackTranslator(client, "other-reader-c")]

    while (p := m.plan()).kind != "qualification":
        m.execute(p)
    m.execute(p)
    assert m.state().bt.id == "remote:other-reader-b"

    m.override_reader("reject", "it invented the reading")
    again = m.plan()
    assert again.kind == "qualification"
    assert "remote:other-reader-b" not in again.context["panel"]
    m.execute(again)
    assert m.state().bt.id == "remote:other-reader-c"
    assert m.outcome()["instrument_overridden"] is True


def test_a_reader_kept_over_the_threshold_carries_the_run(scripted, corpus):
    answers = {**capability_answers(), "backtranslation": "Ein ganz anderer Text."}
    m, _ = machine(scripted, corpus, answers)
    while (p := m.plan()).kind != "qualification":
        m.execute(p)
    m.execute(p)
    assert not m.state().qual.trustworthy
    assert m.plan().kind == "unverified", "the method stops here on its own"

    m.override_reader("accept", "the reading is sound")
    assert m.state().qual.trustworthy
    assert m.plan().kind == "judge", "the run goes on to grading"


def test_rejecting_every_reader_stops_the_run_rather_than_falling_back(scripted, corpus):
    m, _ = machine(scripted, corpus, capability_answers())
    while (p := m.plan()).kind != "qualification":
        m.execute(p)
    m.execute(p)
    m.override_reader("reject")
    m.execute(m.plan())
    assert not m.state().qual.trustworthy
    assert m.plan().kind == "unverified"


# -- a broken endpoint is not a model that cannot write the language ----------

def test_a_failing_call_is_void_rather_than_a_refusal(scripted, corpus):
    """The one mistake this project refuses to make, in its smallest form.

    gpt-5-nano rejects `max_tokens`; the 400 came back as empty text, the gate
    read that as a refusal, and three of them would have published "Unusable"
    for a language the model was never actually asked about.
    """
    from polyglot.core.client.openai_compat import Completion
    from polyglot.core.probe.gate import GateVerdict

    answers = {"generation": Completion(None, "HTTP 400: unsupported parameter")}
    m, _ = machine(scripted, corpus, answers)
    m.execute(m.plan())
    item = m.history[0].obj
    assert item.gate.verdict is GateVerdict.NO_ANSWER
    assert not item.counts_against, "a failed call is not evidence about the model"


def test_an_engine_that_never_answers_stops_the_run_without_a_verdict(scripted, corpus):
    from polyglot.core.client.openai_compat import Completion

    answers = {"generation": Completion(None, "HTTP 400: unsupported parameter")}
    m, client = machine(scripted, corpus, answers)
    out = drain(m)
    assert [e.kind for e in m.history] == ["generate", "generate", "no-answer"], \
        "two dead calls end it; it does not sweep four designators into silence"
    assert out["incomplete"] is True
    assert "returned nothing" in out["notes"][0]
    assert not any(c["model"] == "other-reader-b" for c in client.calls), \
        "nothing may be paid for an instrument when nothing was measured"
