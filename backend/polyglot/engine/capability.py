"""The capability test: can this model write this language at all?

    generate -> gate -> back-translate -> judge the facts

Three rungs, and most languages never reach the second:

    rung 1  designator selection, gate-scored, no judge call.
            Every candidate fails every item -> deterministic negative, stop.
    rung 2  qualify a reader against known text, then judge what survived.
    rung 3  borderline only: more items, to tighten the interval rather than
            round a number that cannot be stated flatly.

**The rules did not move; the traversal did.** Every decision here —
which designators to try, what the gate makes of a reply, how a run is
scored, when a result is borderline — is a call into `probe/`, where it
has one definition and twenty-one protocols behind it. What this module
owns is the order, the cost, and the ability to stop between two steps.

The one place that order encodes a claim worth restating: **eligibility is a
filter, not the first term of a grade**. A model that did not write the
requested language or script is unusable for it, full stop, and nothing else
about it is measured. That check is never handed to a language model — asked to
verify a writing system and given the category by name, one flagged 0 of 15 real
mismatches that the local gate caught (protocol 016).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from polyglot.core.bt import RemoteBackTranslator, route
from polyglot.core.bt.qualify import (MIN_CHRF, MIN_RECALL, Qualification,
                                      QualStatus, control_for)
from polyglot.core.probe import designator as dsg
from polyglot.core.probe.gate import GateVerdict
from polyglot.core.probe import pivot as pivot_mod
from polyglot.core.probe.corpus import Corpus
from polyglot.core.probe.ladder_support import (DesignatorTrial, accepted_codes,
                                                candidates_for, gate_tone,
                                                indistinguishable_codes, item_detail,
                                                recall_tone, refusals_in,
                                                relative_codes, score_detail,
                                                score_run, score_summary, tier_tone)
from polyglot.core.probe.judge import build_prompt as judge_prompt
from polyglot.core.probe.score import Evidence, Score, score
from polyglot.core.probe.specs import Spec
from polyglot.core.probe.steps import ItemOutcome, generate_and_gate, judge_item
from polyglot.core.scheme import Language, Scheme
from polyglot.engine.types import Call, Executed, Planned
from polyglot.store.models import NO_INSTRUMENT

RUNG1_ITEMS = 3
RUNG3_ITEMS = 6

PHASE_DESIGNATOR = "designator"
PHASE_READER = "reader"
PHASE_GRADING = "grading"
PHASE_VERDICT = "verdict"

#: What each kind of step is for, in the terms the tool reports in.
#:
#: Served to the page with the step rather than restated in the page, because
#: text describing what the code does belongs beside the code or it goes stale
#: the first time a threshold moves.
HELP: dict[str, tuple[str, str]] = {
    "generate": (
        "Generate, then gate",
        "The model is given a scenario in the pivot language and asked to write "
        "it in the target language, choosing its own words. The reply goes "
        "straight into the local gate — script, then language identification, "
        "then the degenerate and memorised checks. No paid call beyond the "
        "generation itself, which is why most negatives cost almost nothing.",
    ),
    "designator-trial": (
        "Score this designator",
        "An LLM has no language-code interface, so the name we ask by is a "
        "prompt parameter to optimise rather than a fact to look up. Each "
        "candidate is scored on how many of its items cleared the gate.",
    ),
    "designator-chosen": (
        "Keep the best designator",
        "Everything after this runs under one name, so a negative verdict means "
        "“none under our best designator” rather than “none under one arbitrary "
        "string”.",
    ),
    "deterministic-negative": (
        "Stop: proven locally",
        "Every candidate failed every item. The verdict does not depend on "
        "reading the language back, so it needs no back-translator and holds "
        "against every one of them.",
    ),
    "qualification": (
        "Qualify the reader",
        "Before any score depends on the back-translator, it is given text in "
        "this language whose meaning is already known and scored with chrF++ "
        "against the reference. A model that cannot read a language does not "
        "refuse — it invents: one handed known Chuvash returned “A man is "
        "walking. He is wearing a white shirt.”",
    ),
    "reader-override": (
        "The operator ruled on the reader",
        "Qualification is a measurement, and a measurement a person cannot "
        "overrule is an authority. The transcript is on the page — control "
        "text in, reading out, score against the reference — so a reader that "
        "clearly read the language can be kept over a threshold that said "
        "otherwise, and one that clearly invented can be thrown out. Any run "
        "that took this route is kept in full and never becomes a stored "
        "verdict: it measured an instrument the method did not certify.",
    ),
    "no-answer": (
        "Stop: the engine never answered",
        "Every call to the model under test came back without text — an HTTP "
        "error, a timeout, or a reasoning model that spent its whole budget "
        "before emitting a token. None of that is evidence about the language: "
        "a failure of the instrument must never be published as a failure of "
        "the thing being measured, so the run stops with no verdict rather "
        "than recording a model that refused.",
    ),
    "unverified": (
        "Stop: cannot be verified",
        "No back-translator could be qualified for this language, so quality is "
        "not assessable. `no-control` is never a pass, and this is a statement "
        "about the instrument rather than a verdict about the model.",
    ),
    "judge": (
        "Back-translate, then judge",
        "The surviving text is read back into the pivot language, and a blind "
        "judge marks which of the specified facts survived the round trip. The "
        "judge never sees the target language — which is what removes the need "
        "for expert graders in six hundred languages.",
    ),
    "score": (
        "Score it",
        "Eligibility first — did the model write the language at all — then "
        "adequacy over the items that cleared it, with a bootstrap interval. "
        "The tier is derived from the number and never replaces it.",
    ),
    "extra-item": (
        "Another item: the interval straddles the cut",
        "The result came back borderline, so more items are run to tighten the "
        "interval rather than rounding a number that cannot be stated flatly.",
    ),
}


class _Counting:
    """A client that counts what passes through it, for one step.

    Deliberately not a feature of the real client: a global counter would be
    shared between the two threads an auto run and a manual one can occupy at
    the same time, and the number on screen would start depending on what else
    was running.
    """

    def __init__(self, inner) -> None:
        self._inner, self.n = inner, 0

    def complete(self, *a, **kw):
        self.n += 1
        return self._inner.complete(*a, **kw)


# -- the fold ------------------------------------------------------------------

@dataclass
class State:
    """Everything the machine knows, derived from the executed steps alone.

    Nothing here is assigned by `execute`. It is rebuilt from the history on
    every call, which is what makes re-running a step a truncation rather than a
    rollback — there is no mutated state to undo.
    """

    #: candidate index -> the items generated under it, in spec order
    by_candidate: dict[int, list[ItemOutcome]] = field(default_factory=dict)
    trials: list[DesignatorTrial] = field(default_factory=list)
    #: Readers the operator threw out after reading the transcript. They are
    #: not tried again on this run, whatever the cache remembers about them.
    rejected: set = field(default_factory=set)
    #: Set when the operator asked for the check to be paid for again rather
    #: than taken from the cache, which is the only way to get a transcript
    #: for a reader qualified on an earlier run.
    recheck: bool = False
    #: Whether a person overruled the instrument's own verdict on itself.
    overridden: bool = False
    #: The engine never answered — nothing was measured, and the run says so
    #: instead of scoring the silence.
    unanswered: bool = False
    chosen: int | None = None
    deterministic: bool = False
    sweep_done: bool = False
    qual: Qualification | None = None
    bt: RemoteBackTranslator | None = None
    judged: dict[str, ItemOutcome] = field(default_factory=dict)
    extra: list[ItemOutcome] = field(default_factory=list)
    scored_at_rung: int = 0
    borderline: bool = False
    finished: bool = False
    calls: dict = field(default_factory=lambda: {"generation": 0,
                                                 "backtranslation": 0, "judge": 0})


class CapabilityMachine:
    """The capability ladder as an explicit step machine.

    One instance per run. `plan()` is free and pure; `execute()` spends and
    appends. Auto mode is `while (p := plan()): execute(p)` — the same
    traversal manual mode walks one step at a time, not a second copy of it.
    """

    test_type = "capability"

    def __init__(self, *, scheme: Scheme, tag: str, engine: str, client,
                 backtranslators: list[RemoteBackTranslator], judge_model: str,
                 specs: list[Spec], corpus: Corpus, pivot: str = "en",
                 incumbent: str | None = None, sweep_all: bool = False,
                 cache=None) -> None:
        lang = scheme.get(tag)
        if lang is None:
            raise KeyError(f"Unknown tag {tag!r} in scheme {scheme.meta.name!r}")

        self.scheme, self.tag, self.engine, self.client = scheme, tag, engine, client
        self.lang: Language = lang
        self.judge_model, self.specs, self.corpus = judge_model, specs, corpus
        self.pivot, self.sweep_all, self.cache = pivot, sweep_all, cache
        self.history: list[Executed] = []

        self.accept = accepted_codes(lang)
        self.relatives = relative_codes(scheme, lang)
        #: Standards this instrument cannot separate in this language's script.
        #: Voids an item instead of convicting it — "we cannot tell" is a
        #: different claim from "the model reached for a neighbour", and
        #: conflating them publishes the second as the first.
        self.abstain = indistinguishable_codes(lang)
        self.candidates = candidates_for(scheme, lang, incumbent)

        # The language that *is* the pivot is measured through a different one:
        # back-translating English into English grades nothing.
        self.panel, self.pivot, self.controls = pivot_mod.panel_for(
            scheme, lang, pivot, backtranslators)

    # -- the plan, before anything is spent -----------------------------------

    def preflight(self) -> dict:
        """What this run will ask for, and of whom. Costs nothing.

        Exists because two tags can be the same experiment and nothing says so:
        `sr-RS-Cyrl` and `sr-ME-Cyrl` share an equivalence class and would
        produce the identical prompt. That is a documented design decision, but
        a design you can only discover by running the same thing twice and
        noticing is not a design anyone can check.
        """
        siblings = self.scheme.variants_of(self.tag)
        sibling_designators = {}
        for other in siblings:
            ol = self.scheme.get(other)
            if ol is None:
                continue
            cands = candidates_for(self.scheme, ol)
            if cands:
                sibling_designators[other] = cands[0].value
        primary = self.candidates[0].value if self.candidates else ""
        return {
            "test_type": self.test_type,
            "tag": self.tag, "cls": self.lang.cls, "language": self.lang.name,
            "designators": [{"kind": c.kind, "kinds": list(c.kinds), "value": c.value}
                            for c in self.candidates],
            "primary": primary,
            "siblings": siblings,
            "sibling_designators": sibling_designators,
            #: Siblings that would now be asked something different but still
            #: share this tag's class, so a batch scan probes one and the rest
            #: inherit. Named rather than left to be discovered.
            "divergent_siblings": sorted(t for t, v in sibling_designators.items()
                                         if v != primary),
            "pivot": self.pivot,
            "reader_panel": [b.id for b in self.panel],
            "judge": self.judge_model,
            "specs": [{"id": s.id, "scenario": s.scenario, "facts": list(s.facts)}
                      for s in self.specs],
            "accept": sorted(self.accept), "relatives": sorted(self.relatives),
            "abstain": sorted(self.abstain),
            #: Rung 1 is the floor; a borderline result adds up to six more
            #: items, and each graded item costs a reader call and a judge call.
            "estimated_calls": {
                "minimum": len(self.candidates) * RUNG1_ITEMS,
                "typical": RUNG1_ITEMS + 4 + RUNG1_ITEMS * 2,
                "maximum": (len(self.candidates) * RUNG1_ITEMS + 4
                            + (RUNG1_ITEMS + RUNG3_ITEMS) * 2),
            },
        }

    # -- the fold -------------------------------------------------------------

    def state(self) -> State:
        st = State()
        for ex in self.history:
            ctx = ex.request.get("context") or {}
            if ex.kind == "generate":
                st.by_candidate.setdefault(int(ctx["candidate"]), []).append(ex.obj)
            elif ex.kind == "designator-trial":
                st.trials.append(ex.obj)
            elif ex.kind == "designator-chosen":
                st.chosen = int(ctx["candidate"])
                st.deterministic = bool(ex.result.get("deterministic"))
                st.sweep_done = True
            elif ex.kind == "qualification":
                st.bt, st.qual = ex.obj
            elif ex.kind == "reader-override":
                st.overridden = True
                decision = ex.result.get("decision")
                if decision == "accept":
                    st.bt, st.qual = ex.obj
                else:
                    # Reject and recheck both put the run back before the
                    # reader was chosen: there is no state to undo, only
                    # history to add to, so the next `plan()` qualifies again
                    # under the new constraint.
                    if decision == "reject":
                        st.rejected.add(ex.result.get("backtranslator"))
                    else:
                        st.recheck = True
                    st.bt, st.qual = None, None
            elif ex.kind == "judge":
                st.judged[str(ctx["spec_id"])] = ex.obj
            elif ex.kind == "extra-item":
                st.extra.append(ex.obj)
            elif ex.kind == "score":
                st.scored_at_rung = int(ctx.get("rung") or 2)
                st.borderline = bool(ex.result.get("borderline"))
            elif ex.kind == "no-answer":
                st.unanswered = True
            if ex.kind in ("deterministic-negative", "unverified", "no-answer"):
                st.finished = True
            for k, v in (ex.calls or {}).items():
                st.calls[k] = st.calls.get(k, 0) + v
        # A scored run is finished unless the interval straddles the cut, and
        # rung 3 is the last rung there is.
        if st.scored_at_rung == 3 or (st.scored_at_rung and not st.borderline):
            st.finished = True
        return st

    def pool(self, st: State) -> list[ItemOutcome]:
        """Every item the verdict may rest on: the chosen designator's three,
        plus anything rung 3 added. Judging is planned over this, not over rung
        1 alone — an extra item generated and never graded would widen the
        interval it was run to narrow."""
        if st.chosen is None:
            return []
        return list(st.by_candidate.get(st.chosen, [])) + list(st.extra)

    def best_items(self, st: State) -> list[ItemOutcome]:
        """The pool with judge results merged in."""
        return [st.judged.get(i.spec_id, i) for i in self.pool(st)]

    # -- planning -------------------------------------------------------------

    def plan(self) -> Planned | None:
        st = self.state()
        if st.finished:
            return None

        # Before anything else: did the engine answer at all? Two calls that
        # came back with nothing is a broken endpoint, not a model declining to
        # write a language — and the difference is the whole difference between
        # a verdict and a wasted afternoon. Two rather than one, so a single
        # timeout does not end a run the retries would have carried.
        attempted = [i for items in st.by_candidate.values() for i in items]
        if len(attempted) >= 2 and all(
                i.gate.verdict is GateVerdict.NO_ANSWER for i in attempted):
            return self._free(
                "no-answer", PHASE_VERDICT,
                f"{len(attempted)} call(s) to {self.engine} came back with "
                f"nothing: {attempted[-1].error or attempted[-1].gate.detail}")

        if not st.sweep_done:
            return self._plan_sweep(st)

        if st.deterministic:
            return self._free("deterministic-negative", PHASE_VERDICT,
                              "Settle it locally: no reader is needed")

        if st.qual is None:
            return self._plan_qualification(st)

        if not st.qual.trustworthy:
            return self._free("unverified", PHASE_VERDICT,
                              f"No qualified reader for {self.tag}: "
                              f"{st.qual.detail or st.qual.status.value}")

        pending = [i for i in self.pool(st)
                   if i.lang_ok and i.spec_id not in st.judged]
        if pending:
            return self._plan_judge(st, pending[0])

        if not st.scored_at_rung:
            return self._plan_score(st, rung=2)

        # Rung 3, borderline only. One more item at a time, each generated and
        # then judged, until the interval stops straddling the cut or the
        # supply of scenarios runs out.
        extra_specs = self.specs[RUNG1_ITEMS: RUNG1_ITEMS + RUNG3_ITEMS]
        if len(st.extra) < len(extra_specs):
            return self._plan_generate(st.chosen or 0, extra_specs[len(st.extra)],
                                       kind="extra-item")
        return self._plan_score(st, rung=3)

    def _plan_sweep(self, st: State) -> Planned:
        for ci, cand in enumerate(self.candidates):
            items = st.by_candidate.get(ci, [])
            if len(items) < RUNG1_ITEMS:
                return self._plan_generate(ci, self.specs[len(items)])
            if not any(t.value == cand.value for t in st.trials):
                passed = sum(i.lang_ok for i in items)
                return self._free(
                    "designator-trial", PHASE_DESIGNATOR,
                    f"Score {cand.value!r}: {passed} of {len(items)} cleared the gate",
                    context={"candidate": ci})
            # A designator that carried every item is not improved on by trying
            # more, so the sweep stops rather than paying for a tie.
            trial = next(t for t in st.trials if t.value == cand.value)
            if trial.rate == 1.0 and not self.sweep_all:
                break
        return self._free("designator-chosen", PHASE_DESIGNATOR,
                          "Keep the best-performing designator and run everything "
                          "else under it")

    def _plan_qualification(self, st: State) -> Planned:
        """What qualifying the reader will actually send, in full.

        The control text is on disk before the run starts, so there is no
        reason to show a placeholder where it belongs — and every reason not
        to. This is the one step whose subject is the instrument itself; an
        operator who cannot see what the reader was given cannot tell a reader
        that read the language from one that invented a reading of it, which
        is the exact failure the step exists to catch.

        What it still cannot promise is the *order*: the panel is tried in
        measured order and stops at the first that qualifies, so everything
        below the first reader is shown as what it would cost if the one above
        it fails.
        """
        panel = self._panel(st)
        entry = control_for(self.tag, self.controls, self.scheme)
        items = list(entry.get("items") or []) if entry else []
        source = (entry or {}).get("derived_from")

        if not items:
            return Planned(
                kind="qualification", phase=PHASE_READER,
                title=HELP["qualification"][0], explains=HELP["qualification"][1],
                summary=(f"No control text exists for {self.tag}, so no reader "
                         f"can be qualified for it — nothing will be sent and "
                         f"the run stops as unverified"),
                editable=False, calls=[],
                context={"panel": [b.id for b in panel]})

        calls: list[Call] = []
        cached: list[str] = []
        for b in panel:
            was = None if st.recheck or self.cache is None else self.cache.get(b.id, self.tag)
            if was is not None:
                cached.append(f"{b.id}: {was.status.value} on an earlier run")
                continue
            for n, item in enumerate(items, 1):
                calls.append(Call(
                    role="backtranslation", model=b.model,
                    user=b.build_prompt(item["text"]), max_tokens=500,
                    label=f"{b.id} · control {n} of {len(items)}"
                          + ("" if not calls else " — only if the reader above fails")))

        kind = (entry or {}).get("kind")
        how = (f"scored with chrF++ against the known reference, pass at {MIN_CHRF:g}"
               if kind == "reference" else
               f"graded on how many known facts come back, pass at {MIN_RECALL:.0%}")
        where = f", borrowed from {source}" if source else ""
        summary = (f"Read {len(items)} piece(s) of known {self.tag} text{where} "
                   f"back into {self.pivot} and compare: {how}. Readers in "
                   f"measured order: {', '.join(b.id for b in panel) or 'none left'}")
        if cached:
            summary += f" · already answered — {'; '.join(cached)}"
        return Planned(
            kind="qualification", phase=PHASE_READER,
            title=HELP["qualification"][0], explains=HELP["qualification"][1],
            summary=summary,
            # The prompts here are the back-translator's own and a chrF++
            # comparison against reference text; there is nothing useful to
            # rewrite, and rewriting it would uncertify the instrument.
            editable=False, calls=calls,
            context={"panel": [b.id for b in panel]})

    def _panel(self, st: State) -> list:
        """The readers still in the running: the measured order, less anyone the
        operator threw out after reading what they answered."""
        return [b for b in self.panel if b.id not in st.rejected]

    def _plan_generate(self, ci: int, spec: Spec, *, kind: str = "generate") -> Planned:
        cand = self.candidates[ci]
        system, user = dsg.generation_prompt(
            cand.value, spec.scenario, 3,
            script_name=dsg.script_name_for(self.lang.script))
        return Planned(
            kind=kind, phase=PHASE_DESIGNATOR if kind == "generate" else PHASE_GRADING,
            title=HELP[kind][0], explains=HELP[kind][1],
            summary=f"Ask {self.engine} for {cand.value!r} — scenario {spec.id}",
            calls=[Call(role="generation", model=self.engine, system=system,
                        user=user, max_tokens=400,
                        label=f"{cand.value} · {spec.id}")],
            context={"candidate": ci, "spec_id": spec.id})

    def _plan_judge(self, st: State, item: ItemOutcome) -> Planned:
        spec = next(s for s in self.specs if s.id == item.spec_id)
        bt = st.bt
        assert bt is not None
        # The judge prompt cannot be built yet — it needs the back-translation
        # that has not been paid for. Shown as the template it will be filled
        # from, which is honest about what is and is not known at this point.
        return Planned(
            kind="judge", phase=PHASE_GRADING,
            title=HELP["judge"][0], explains=HELP["judge"][1],
            summary=f"Read {item.spec_id} back through {bt.id}, then check "
                    f"{len(spec.facts)} fact(s) with {self.judge_model}",
            calls=[
                Call(role="backtranslation", model=bt.model,
                     user=bt.build_prompt(item.generation or ""),
                     max_tokens=500, label=f"back-translate {item.spec_id}"),
                Call(role="judge", model=self.judge_model,
                     user=judge_prompt("[the back-translation, once it exists]",
                                       spec.facts),
                     max_tokens=300, label=f"judge {item.spec_id}"),
            ],
            context={"spec_id": item.spec_id})

    def _plan_score(self, st: State, *, rung: int) -> Planned:
        return self._free("score", PHASE_VERDICT,
                          "Combine eligibility and adequacy into a tier, with an "
                          "interval", context={"rung": rung})

    def _free(self, kind: str, phase: str, summary: str,
              context: dict | None = None) -> Planned:
        return Planned(kind=kind, phase=phase, title=HELP[kind][0],
                       explains=HELP[kind][1], summary=summary, calls=[],
                       editable=False, context=context or {})

    # -- execution ------------------------------------------------------------

    def execute(self, planned: Planned, *, calls: list[Call] | None = None,
                edited: bool = False) -> Executed:
        """Run one planned step. `calls`, when given, replaces what was planned.

        That replacement is the whole point of the pre-flight: what the operator
        approved on screen is what goes out, byte for byte, rather than a second
        construction of it.
        """
        sent = calls if calls is not None else planned.calls
        mark = self.corpus.count
        ordinal = len(self.history)
        handler = getattr(self, f"_do_{planned.kind.replace('-', '_')}")
        ex: Executed = handler(planned, sent)
        ex.ordinal = ordinal
        ex.edited = edited
        ex.request = {"context": planned.context,
                      "calls": [c.as_dict() for c in sent]}
        ex.result.setdefault("corpus", [
            {"kind": r.kind, "model": r.engine, "error": r.error,
             "latency_s": r.latency_s, "usage": r.usage}
            for r in self.corpus.records[mark:]])
        self.history.append(ex)
        return ex

    def _make(self, planned: Planned, **kw) -> Executed:
        return Executed(ordinal=0, kind=planned.kind, phase=planned.phase,
                        title=planned.title, explains=planned.explains, **kw)

    def _do_generate(self, planned: Planned, sent: list[Call]) -> Executed:
        ci = int(planned.context["candidate"])
        spec = next(s for s in self.specs if s.id == planned.context["spec_id"])
        call = sent[0]
        item = generate_and_gate(
            client=self.client, engine=call.model, lang=self.lang,
            designator=self.candidates[ci].value, spec=spec, corpus=self.corpus,
            relatives=self.relatives, accept=self.accept, abstain=self.abstain,
            messages=(call.system or "", call.user))
        return self._make(
            planned,
            summary=f"{spec.id} · {self.candidates[ci].value!r}: "
                    f"{item.gate.verdict.value}",
            tone=gate_tone(item.gate.verdict),
            result=item_detail(item), calls={"generation": 1}, obj=item)

    _do_extra_item = _do_generate

    def _do_designator_trial(self, planned: Planned, _sent) -> Executed:
        ci = int(planned.context["candidate"])
        cand = self.candidates[ci]
        items = self.state().by_candidate.get(ci, [])
        trial = DesignatorTrial(cand.kind, cand.value,
                                sum(i.lang_ok for i in items), len(items),
                                kinds=cand.kinds)
        return self._make(
            planned,
            summary=f"{cand.value!r} cleared the gate on "
                    f"{trial.gate_pass} of {trial.attempts}",
            tone="ok" if trial.rate == 1.0 else "warn" if trial.rate else "bad",
            result={"designator": cand.value, "kinds": list(cand.kinds),
                    "gate_pass": trial.gate_pass, "attempts": trial.attempts,
                    "rate": round(trial.rate, 3)},
            obj=trial)

    def _do_designator_chosen(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        best_i, best = max(enumerate(st.trials), key=lambda p: p[1].rate)
        ci = next(i for i, c in enumerate(self.candidates) if c.value == best.value)
        items = st.by_candidate.get(ci, [])
        # Every candidate failed every item, and every failure was the gate
        # convicting rather than abstaining: the negative holds without a reader.
        deterministic = best.rate == 0.0 and all(i.counts_against for i in items)
        planned.context["candidate"] = ci
        return self._make(
            planned,
            summary=f"Best designator {best.value!r} at {best.rate:.2f}",
            tone="ok" if best.rate == 1.0 else "warn" if best.rate else "bad",
            result={"designator": best.value, "kinds": list(best.kinds),
                    "rate": round(best.rate, 3), "deterministic": deterministic,
                    "trials": [{"designator": t.value, "kinds": list(t.kinds),
                                "gate_pass": t.gate_pass, "attempts": t.attempts,
                                "rate": round(t.rate, 3)} for t in st.trials]})

    def _do_deterministic_negative(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        items = st.by_candidate.get(st.chosen or 0, [])
        return self._make(
            planned,
            summary=f"Every candidate failed every item; "
                    f"{refusals_in(items)} refusal(s)",
            tone="bad",
            result={"refusals": refusals_in(items),
                    "verdicts": [i.gate.verdict.value for i in items]})

    def _do_qualification(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        tried: list[Qualification] = []
        # Counted rather than estimated. How many calls qualifying costs
        # depends on which kind of control exists for the language and how many
        # readers had to be tried before one qualified, and a run that reported
        # a guess as its spend would be lying about the one number the operator
        # is actually watching.
        counter = _Counting(self.client)
        members = self._panel(st)
        if not members:
            # Every reader on the panel was thrown out by the operator. That is
            # a statement about the instrument, like any other no-control, and
            # the run stops rather than falling back on one of them.
            qual = Qualification(
                QualStatus.NO_CONTROL, 0.0,
                ", ".join(sorted(st.rejected)) or "none", self.tag, "operator",
                "Every reader on the panel was rejected by the operator, so "
                "nothing is left to read this language back.")
            return self._make(
                planned, summary="No reader left on the panel", tone="bad",
                result={"backtranslator": qual.backtranslator,
                        "status": qual.status.value, "score": 0.0,
                        "kind": "operator", "detail": qual.detail,
                        "pivot": self.pivot, "trustworthy": False,
                        "panel": [], "rejected": sorted(st.rejected),
                        "attempts": [], "evidence": [], "threshold": None},
                obj=(RemoteBackTranslator(self.client, self.panel[0].model,
                                          pivot=self.pivot), qual))
        panel = [RemoteBackTranslator(counter, b.model, pivot=b.pivot)
                 for b in members]
        bt, qual = route(panel, counter, self.judge_model, self.tag,
                         controls=self.controls,
                         # A re-check the operator asked for is a re-check: the
                         # cache is what it is being asked to go behind.
                         cache=None if st.recheck else self.cache,
                         scheme=self.scheme, attempts=tried)
        # The reader that is kept must talk to the real client, not the probe's
        # counting wrapper, which exists only for the length of this step.
        bt = RemoteBackTranslator(self.client, bt.model, pivot=bt.pivot)
        return self._make(
            planned,
            summary=f"{bt.id}: {qual.status.value}"
                    + (f" at {qual.score:.1f}" if qual.kind == "reference" else ""),
            tone="ok" if qual.trustworthy else "bad",
            result={"backtranslator": bt.id, "status": qual.status.value,
                    "score": round(qual.score, 2), "kind": qual.kind,
                    "detail": qual.detail, "pivot": self.pivot,
                    "trustworthy": qual.trustworthy,
                    "panel": [b.id for b in members],
                    "rejected": sorted(st.rejected),
                    "rechecked": st.recheck,
                    # What the reader was given and what it answered, item by
                    # item. Empty when the answer came from the cache rather
                    # than from a call, and the page says so instead of showing
                    # a score with nothing behind it.
                    "evidence": [dict(e) for e in qual.evidence],
                    "from_cache": not qual.evidence and qual.kind != "operator",
                    # Every attempt, so "why this reader" is answerable from the
                    # page rather than from the source.
                    "attempts": [{"backtranslator": q.backtranslator,
                                  "status": q.status.value, "score": round(q.score, 2),
                                  "kind": q.kind, "detail": q.detail,
                                  "evidence": [dict(e) for e in q.evidence]}
                                 for q in tried],
                    "threshold": (MIN_CHRF if qual.kind == "reference"
                                  else MIN_RECALL if qual.kind == "facts" else None)},
            calls={"backtranslation": counter.n},
            obj=(bt, qual))

    def _do_no_answer(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        attempted = [i for items in st.by_candidate.values() for i in items]
        errors: list[str] = []
        for i in attempted:
            e = i.error or i.gate.detail
            if e and e not in errors:
                errors.append(e)
        return self._make(
            planned,
            summary=f"{self.engine} returned nothing on every attempt",
            tone="bad",
            result={"engine": self.engine, "attempts": len(attempted),
                    "errors": errors})

    def _do_unverified(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        q = st.qual
        assert q is not None
        return self._make(
            planned,
            summary=f"No qualified reader for {self.tag}: "
                    f"{q.detail or q.status.value}",
            # Not red. No reader could be qualified, which is a statement about
            # the instrument and not a verdict about the model.
            tone="warn",
            result={"backtranslator": q.backtranslator, "status": q.status.value,
                    "detail": q.detail})

    def _do_judge(self, planned: Planned, sent: list[Call]) -> Executed:
        st = self.state()
        spec_id = str(planned.context["spec_id"])
        spec = next(s for s in self.specs if s.id == spec_id)
        # A copy, because `judge_item` fills the item in place and the
        # original belongs to the `generate` step that produced it. Mutating it
        # would mean a re-run could not truncate the history cleanly — the whole
        # reason the state is a fold and not a set of assignments.
        source = copy.deepcopy(next(i for i in self.pool(st) if i.spec_id == spec_id))
        bt_call = next((c for c in sent if c.role == "backtranslation"), None)
        judge_call = next((c for c in sent if c.role == "judge"), None)
        assert st.bt is not None
        item = judge_item(
            source, client=self.client, judge_model=(judge_call.model
                                                     if judge_call else self.judge_model),
            backtranslator=st.bt, spec=spec, tag=self.tag, corpus=self.corpus,
            bt_prompt=bt_call.user if bt_call else None,
            # The judge prompt is only overridable once the operator has seen
            # the back-translation — which they have not, at plan time. An
            # untouched template is not an override, and sending it would grade
            # the placeholder instead of the text.
            judge_prompt=None)
        return self._make(
            planned,
            summary=f"{spec_id}: recall "
                    + (f"{item.judgement.recall:.2f}"
                       if item.judgement and item.judgement.ok else "unavailable"),
            tone=recall_tone(item),
            result=item_detail(item),
            calls={"backtranslation": 1, "judge": 1}, obj=item)

    def _do_score(self, planned: Planned, _sent) -> Executed:
        st = self.state()
        s = self._score(st)
        return self._make(
            planned, summary=score_summary(s), tone=tier_tone(s),
            result={**score_detail(s), "borderline": s.borderline}, obj=s)

    def _score(self, st: State) -> Score:
        if st.deterministic:
            items = st.by_candidate.get(st.chosen or 0, [])
            return score(
                # Items the model *attempted* and got wrong still count as
                # attempts. An empty list here made a model that answered every
                # item in the wrong script indistinguishable from one that
                # refused every item — reliability 0.0 for a model that was
                # entirely willing.
                lang_pass=[False] * (len(items) - refusals_in(items)),
                content=[], evidence=Evidence.DETERMINISTIC_NEGATIVE,
                refusals=refusals_in(items))
        assert st.qual is not None
        return score_run(self.best_items(st), qualification=st.qual, tag=self.tag)

    # -- the operator's ruling on the instrument -------------------------------

    DECISIONS = ("accept", "reject", "recheck")

    def override_reader(self, decision: str, why: str = "") -> Executed:
        """Overrule the qualification step, having read what it did.

        Three rulings, and all three are history rather than state: the step is
        appended like any other, the fold reads it, and `plan()` works out what
        follows. Nothing is mutated, so a ruling can itself be re-run away.

          accept    keep this reader even though the threshold said no. The
                    thresholds are calibrated on two real cases (a fabricating
                    reader at 10.5, a faithful one at 81.9) and are simply
                    wrong in the middle — protocol 016 found 90% of one
                    metric's disagreements with fact recall were adequate
                    translations. A person reading the transcript can see
                    which they are looking at; the number cannot.
          reject    throw this reader out and try the next on the panel. For
                    the opposite case: a score above the line that the
                    transcript shows was earned by inventing plausible text.
          recheck   pay for the check again instead of taking it from the
                    cache, which is the only way to get a transcript for a
                    reader qualified on an earlier run.

        A run that was ruled on is kept in full and never promoted to a stored
        verdict — it measured an instrument the method did not certify.
        """
        if decision not in self.DECISIONS:
            raise ValueError(f"Not a ruling this step takes: {decision!r}. "
                             f"One of {', '.join(self.DECISIONS)}.")
        st = self.state()
        if st.qual is None or st.bt is None:
            raise ValueError("There is no reader to rule on yet — the "
                             "qualification step has not run.")

        reader, prior = st.bt, st.qual
        note = (why or "").strip()
        if decision == "accept":
            qual = Qualification(
                QualStatus.QUALIFIED, prior.score, prior.backtranslator,
                self.tag, "operator",
                (f"Accepted by the operator over a {prior.kind or 'threshold'} "
                 f"result of {prior.score:.2f}"
                 + (f": {note}" if note else ".")),
                prior.evidence)
            summary = f"{reader.id} kept by the operator, against the threshold"
            tone, obj = "warn", (reader, qual)
        elif decision == "reject":
            summary = f"{reader.id} thrown out by the operator"
            tone, obj = "warn", None
        else:
            summary = f"{reader.id} to be checked again, ignoring the cache"
            tone, obj = "warn", None

        ex = Executed(
            ordinal=len(self.history), kind="reader-override", phase=PHASE_READER,
            title=HELP["reader-override"][0], explains=HELP["reader-override"][1],
            summary=summary, tone=tone,
            request={"context": {"decision": decision}, "calls": []},
            result={"decision": decision, "backtranslator": prior.backtranslator,
                    "why": note, "corpus": [],
                    "was": {"status": prior.status.value,
                            "score": round(prior.score, 2),
                            "kind": prior.kind, "detail": prior.detail}},
            obj=obj)
        self.history.append(ex)
        return ex

    # -- the verdict ----------------------------------------------------------

    def outcome(self) -> dict:
        """What the run concluded, in the shape the page and the store want."""
        st = self.state()
        if st.unanswered:
            attempted = [i for items in st.by_candidate.values() for i in items]
            return {
                "test_type": self.test_type, "tag": self.tag,
                "engine": self.engine, "incomplete": True,
                "error": next((i.error for i in attempted if i.error), None),
                "notes": [f"{self.engine} returned nothing on every attempt, so "
                          f"nothing about {self.tag} was measured. This is a "
                          f"statement about the endpoint, not about the model."],
            }
        if st.chosen is None:
            return {"test_type": self.test_type, "tag": self.tag,
                    "engine": self.engine, "incomplete": True}
        s = self._score(st)
        bt_id = NO_INSTRUMENT if st.deterministic else (st.bt.id if st.bt
                                                        else NO_INSTRUMENT)
        return {
            "test_type": self.test_type, "tag": self.tag, "cls": self.lang.cls,
            "engine": self.engine, "designator": self.candidates[st.chosen].value,
            "level": s.tier.value, "evidence": s.evidence.value,
            "confidence": "low" if s.borderline else "high",
            "s_lang": round(s.s_lang, 4), "s_content": round(s.s_content, 4),
            "ci_low": round(s.ci[0], 4), "ci_high": round(s.ci[1], 4),
            "borderline": s.borderline, "refusals": s.refusals,
            "backtranslator": bt_id, "judge": self.judge_model, "pivot": self.pivot,
            "workflow": s.workflow,
            "score": s.as_dict(), "notes": list(s.notes),
            "calls": dict(st.calls),
            "items": [item_detail(i) for i in self.best_items(st)],
            "qualification": (None if st.qual is None else
                              {"backtranslator": st.qual.backtranslator,
                               "status": st.qual.status.value,
                               "score": round(st.qual.score, 2),
                               "kind": st.qual.kind,
                               "detail": st.qual.detail}),
            #: A person overruled the qualification of the instrument. Kept in
            #: full, readable, and never promoted: results are only comparable
            #: within one instrument, and this one was not the method's.
            "instrument_overridden": st.overridden,
        }

    # -- adjudication ---------------------------------------------------------

    def adjudicate(self, overrides: list[dict]) -> dict:
        """Re-score the run with a reader's corrections to the judge.

        **It never edits the measurement.** The stored result stands; this
        reports what the same run would have scored had the judge answered
        differently, and the two sit side by side. A measurement that could be
        hand-edited after the fact is not a measurement, and an adjudicated
        number silently replacing a measured one is how a result comes to mean
        something nobody can reconstruct.

        The re-score goes through `score_run` — the function that produced the
        original number — rather than a copy of it, because two numbers on one
        page inviting a comparison had better be comparable.

        This is the page's reason to exist. The judge is a model and models are
        wrong: protocol 016 found 90% of one metric's disagreements with fact
        recall were adequate translations. Seeing *which* fact was called
        missing, and being able to say “it is in there”, is how that gets caught.
        """
        from polyglot.core.probe.judge import FactVerdict

        st = self.state()
        if st.qual is None:
            raise ValueError("This run has no graded result to adjudicate.")

        # Overrides go onto a copy. Applying them to the history would edit the
        # measurement this function promises not to touch — and, because a
        # verdict already corrected is no longer a change, a second pass over
        # the same correction would report that it had applied nothing.
        items = copy.deepcopy(self.best_items(st))

        wanted: dict[str, dict[int, FactVerdict]] = {}
        for o in overrides:
            try:
                wanted.setdefault(str(o["spec_id"]), {})[int(o["index"])] = \
                    FactVerdict(str(o["verdict"]))
            except (KeyError, ValueError, TypeError) as e:
                raise ValueError(f"Not a usable override: {o!r} ({e})") from e

        applied: list[dict] = []
        for item in items:
            want = wanted.get(item.spec_id)
            if not want or not (item.judgement and item.judgement.ok):
                continue
            for i, v in want.items():
                if not 0 <= i < len(item.judgement.verdicts):
                    raise ValueError(f"{item.spec_id}: fact {i} does not exist "
                                     f"({len(item.judgement.verdicts)} facts)")
                was = item.judgement.verdicts[i]
                if was is v:
                    continue
                item.judgement.verdicts[i] = v
                applied.append({"spec_id": item.spec_id, "index": i,
                                "fact": item.facts[i] if i < len(item.facts) else None,
                                "was": was.value, "now": v.value})

        rescored = score_run(items, qualification=st.qual, tag=self.tag)
        return {
            "applied": applied, "changed": len(applied),
            "measured": self._score(st).as_dict(),
            "adjudicated": rescored.as_dict(),
            "note": "What this run would have scored had the judge answered this "
                    "way. The measurement is unchanged, and an adjudicated run "
                    "is never promoted to a verdict on its own.",
        }
