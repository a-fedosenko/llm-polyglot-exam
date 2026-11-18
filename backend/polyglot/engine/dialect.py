"""The dialect test: does this model actually mark *this* variety?

    generate -> judge by control models

Five probes, and they come apart in practice, which is why they are five and
not one score. A model can identify Montenegrin Serbian on sight and still write
generic Serbian when asked for it; another can write it unprompted and fail to
convert a text into it on demand. Collapsing those into "supports the dialect:
yes/no" is what makes a dialect claim unconvincing.

    P1  blind identification   does an attestor name the variety, unlabelled?
    P2  contrast pair          can an attestor tell two varieties apart?
    P3  the square             each arm's text rewritten into *both* varieties
    P5  marker grounding       were the cited markers real?

**P3 needs no attestor at all**, so it runs first: it is the dialect
equivalent of the deterministic negative, sound at full breadth with no panel
call and no control text, and a negative it settles costs nothing further. It
is also now strong enough to settle a *positive*, which is what makes a family
measurable for six calls rather than six plus five per control model.

P4 is gone as a separate probe. "Do it again and compare" asked whether the
model repeats itself, which a stubborn improviser also does; the square's
diagonal cells ask the same question better and are paid for anyway.

**Every control model is re-qualified on every run, for this family.** That is
the operator's own requirement and it is also the method's: the stored record
routes — it decides who is asked first — and it never vouches. Two controls,
whose answers are certain by construction rather than by authority:

    identity   one text shown as both halves of a pair. It cannot be two
               varieties, so any answer that splits it is the attestor
               manufacturing a difference.
    order      one real pair shown in both presentations. A reliable attestor's
               answers follow the text; one answering by position gives the same
               answer twice, and that failure reads as dialect knowledge
               precisely where we most want a positive.

What they cannot catch, stated plainly: an attestor answering
`indistinguishable` to everything passes both. Catching over-merging needs a
pair known to be distinguishable, which is the knowledge we lack by assumption.
So a null here is reported as "no evidence of a difference" and never as "no
difference".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from polyglot.core.probe import attest
from polyglot.core.probe.attest import Blind
from polyglot.core.probe.corpus import Corpus
from polyglot.core.probe.dialect import (STABLE_AT, BlindRead, ContrastRead,
                                         Conversion, DialectResult, Square,
                                         _designator_for, _produce,
                                         _producer_capability, attest_tone,
                                         blind_for, convert, convert_prompt)
from polyglot.core.probe.ladder_support import (accepted_codes,
                                                indistinguishable_codes,
                                                relative_codes)
from polyglot.core.probe.panel import (Competence, Panel, Role, capabilities_from,
                                       eligible, order_by_record)
from polyglot.core.scheme import Language, Scheme
from polyglot.engine.types import Call, Executed, Planned

PHASE_PLAN = "plan"
PHASE_PRODUCE = "produce"
PHASE_CONVERT = "convert"
PHASE_PANEL = "panel"
PHASE_ATTEST = "attest"
PHASE_VERDICT = "verdict"

HELP: dict[str, tuple[str, str]] = {
    "plan": (
        "What this will ask",
        "The varieties under test, the designators they will be asked by, and "
        "the control models eligible to judge — the producer's whole family is "
        "dropped, because a model grading its own output measures "
        "self-consistency and reports it as agreement. Nothing has been spent "
        "yet, and the panel can still be changed.",
    ),
    "produce": (
        "Write the variety",
        "The model is asked for the same scenario under each variety's own "
        "designator, and every reply goes through the local gate before "
        "anything else looks at it. Eligibility is deterministic and is never "
        "handed to a model.",
    ),
    "convert": (
        "Rewrite into the variety",
        "One arm's text, rewritten into one variety. The diff is the "
        "measurement and no "
        "judge is involved — this is where a model says, in its own output, "
        "what it thinks the variety *is*. An empty diff is a negative settled "
        "locally; rewriting the whole passage is the opposite failure and is "
        "scored as a paraphrase, not a conversion.",
    ),
    "square": (
        "The four rewrites together",
        "Each arm's text was rewritten into both varieties. Converting a text "
        "into the variety it was just written in should change nothing; into "
        "the other one it should change something. Both expectations are "
        "certain by construction, so no judge is involved — and the two "
        "cross-conversions should be mirrors of each other, because they are "
        "the same alternation run in opposite directions.",
    ),
    "controls": (
        "Qualify the control model",
        "Two controls whose answers are certain by construction: one text shown "
        "as both halves of a pair, which cannot be two varieties; and one pair "
        "shown in both orders, which must get corresponding answers. A model "
        "failing either is discarded for this family rather than counted — and "
        "this runs every time, whatever the record says about it.",
    ),
    "identify": (
        "Name the variety, blind",
        "The control model is shown the text with no label and no expected "
        "answer, and names which variety it is. Every marker it cites is "
        "checked against the text it was cited for; one invented quotation is "
        "enough to make the verdict it propped up worthless.",
    ),
    "separate": (
        "Tell the pair apart, blind",
        "Both texts, unlabelled, in an order decided by the texts themselves. "
        "The model may answer that they cannot be told apart, which is "
        "frequently the true answer and is the one a null result needs.",
    ),
    "verdict": (
        "What the probes add up to",
        "A transparent function of the outcomes above — no weights, no tuned "
        "coefficients, nothing that cannot be traced to an observation. No "
        "competent control model means `indeterminate`, which is a result and "
        "never a negative: “nobody could check” is not “the model cannot do it”.",
    ),
}


@dataclass
class State:
    produced: dict[str, str | None] = field(default_factory=dict)
    produced_order: list[str] = field(default_factory=list)
    #: (source tag, target tag) -> the conversion. The 2×2: each arm's text
    #: rewritten into both varieties.
    cells: dict[tuple[str, str], Conversion] = field(default_factory=dict)
    planned: bool = False
    competences: list[Competence] = field(default_factory=list)
    reads: list[BlindRead] = field(default_factory=list)
    contrasts: list[ContrastRead] = field(default_factory=list)
    finished: bool = False
    stop_note: str | None = None
    calls: dict = field(default_factory=lambda: {"produce": 0, "convert": 0,
                                                 "control": 0, "attest": 0})


class DialectMachine:
    """The dialect battery as an explicit step machine.

    `varieties` are the named varieties under test and `neutral_tag` is the
    umbrella, and they do different jobs. The umbrella is the *source* for the
    conversion probe — neutral text to be rewritten into a variety — and it is
    never a contrast arm. Pairing it against a variety leaves one answerable
    option, which is not a question; and it would be the wrong question anyway,
    since "Serbian" and "Serbian (Montenegro)" are not two varieties a speaker
    could be asked to tell apart. The contrast runs between two *named*
    varieties, which is why at least two are required.
    """

    test_type = "dialect"

    def __init__(self, *, scheme: Scheme, tag: str, engine: str, client,
                 attestors: list[str], varieties: list[Language],
                 scenario: str, corpus: Corpus, neutral_tag: str | None = None,
                 record=None, forced_attestors: list[str] | None = None) -> None:
        lang = scheme.get(tag)
        if lang is None:
            raise KeyError(f"Unknown tag {tag!r} in scheme {scheme.meta.name!r}")
        # **The umbrella is no longer part of the battery.** It used to be the
        # conversion source — "neutral text" to be rewritten into a variety —
        # and it was never neutral: it was generated by the model under test,
        # so it was that model's own default variety wearing an umbrella tag.
        # Whichever variety a model defaults to could therefore never show a
        # conversion, and the baseline moved between runs because it was
        # resampled each time. The square converts between the two arms
        # instead, which has no privileged baseline at all.
        #
        # Still accepted so a caller that sends one is not an error, and still
        # reported in the pre-flight as *not used*, because silently ignoring a
        # parameter is how a caller comes to believe something it is not.
        neutral = scheme.get(neutral_tag) if neutral_tag else None

        named = [v for v in varieties if not v.inclusive]
        if len(named) < 2:
            raise ValueError(
                f"A contrast needs two named varieties; got {[v.tag for v in named]}. "
                "The umbrella tag is the conversion source, not a contrast arm.")

        # **Both arms must be written in the same alphabet.**
        #
        # A digraphic language's family spans scripts: `sr-ME-Cyrl` and
        # `sr-ME-Latn` are one variety written twice. Pairing arms across that
        # line does not measure dialect knowledge — a control model asked which
        # variety a text is would answer from the alphabet, and would be right
        # for a reason that has nothing to do with Montenegrin. That is a false
        # positive of exactly the kind the manufactured controls exist to
        # prevent, and it cannot be caught by them: both arms really are
        # different, so identity and order both pass.
        scripts = {v.script for v in named}
        if len(scripts) > 1:
            raise ValueError(
                f"The two arms are written in different alphabets "
                f"({', '.join(sorted(str(s) for s in scripts))}). A control "
                f"model told to tell them apart would be reading the script, "
                f"not the variety, and would look like dialect knowledge while "
                f"being none. Contrast two varieties in one alphabet.")
        arm_script = next(iter(scripts))

        self.arm_script = arm_script
        self.scheme, self.tag, self.engine, self.client = scheme, tag, engine, client
        self.lang, self.neutral, self.named = lang, neutral, named
        self.scenario, self.corpus, self.record = scenario, corpus, record
        self.history: list[Executed] = []

        self.accept = accepted_codes(lang)
        self.relatives = relative_codes(scheme, lang)
        self.abstain = indistinguishable_codes(lang)
        self.blind: Blind = blind_for(scheme, named, salt=scenario)
        self.designator = _designator_for(scheme, lang)
        self.sibling = next((v for v in named if v.tag != tag), None)

        self._all_attestors = list(attestors)
        self._forced = forced_attestors
        self.routing = ""
        self.field: list[str] = []
        self._route()

    # -- the panel ------------------------------------------------------------

    def _route(self) -> None:
        """Decide who is asked, and in what order, and say so out loud.

        A record that silently reorders the panel is invisible machinery, and
        auditability is the whole argument for this method over the marker
        corpus it replaced.
        """
        source = self._forced if self._forced is not None else self._all_attestors
        # The family guard is not a setting. It is the rule that a model may not
        # grade its own output, and it applies to a hand-picked panel too.
        self.field = eligible(source, producer=self.engine)
        if self._forced is not None:
            self.routing = "chosen by hand"
        elif self.record is not None:
            known = self.record.known(self.field, tag=self.tag,
                                      role=Role.DISCRIMINATION, script=self.lang.script)
            self.field = order_by_record(self.field, known=known)
            self.routing = (f"ordered by {len(known)} prior record(s)" if known
                            else "no priors; the field is in panel order")
        else:
            self.routing = "no record consulted"

    def set_panel(self, attestors: list[str]) -> None:
        """Replace the panel. Only before a control has been paid for.

        The point at which this is useful is exactly after the plan step and
        before the first control: the operator has seen who is eligible and
        wants somebody else. Once a control has run, changing the panel would
        make the competences on screen describe a field that no longer exists.
        """
        if any(e.kind == "controls" for e in self.history):
            raise ValueError(
                "The controls have already started. Changing the panel now "
                "would leave competences on screen for a field that is no "
                "longer the one being used — re-run from the plan step instead.")
        self._forced = list(attestors)
        self._route()

    # -- the plan, before anything is spent -----------------------------------

    def preflight(self) -> dict:
        return {
            "test_type": self.test_type,
            "tag": self.tag, "language": self.lang.name,
            "designator": self.designator,
            "neutral": None if self.neutral is None else {
                        "tag": self.neutral.tag,
                        "designator": _designator_for(self.scheme, self.neutral),
                        #: The umbrella is accepted and not used. P3 converts
                        #: between the two arms now, so there is no neutral
                        #: source and no default-variety baseline.
                        "used": False,
                        "script": self.arm_script},
            "varieties": [{"tag": v.tag, "region": v.region_name,
                           "designator": _designator_for(self.scheme, v),
                           "is_subject": v.tag == self.tag} for v in self.named],
            "options": list(self.blind.options),
            "attestors": list(self.field),
            "available_attestors": list(self._all_attestors),
            "dropped_by_family_guard": [a for a in self._all_attestors
                                        if a not in self.field],
            "routing": self.routing,
            "scenario": self.scenario,
            "abstain": sorted(self.abstain),
            "estimated_calls": {
                # One produce per arm, four conversions, then 3 control calls
                # per attestor and 2 attestations per model that qualifies.
                # The minimum is the free half alone: identical arms stop the
                # battery before a conversion, and a clean square settles a
                # positive before a panel is paid for.
                "minimum": len(self.named),
                "typical": len(self.named) + 4 + 5 * len(self.field),
            },
        }

    # -- the fold -------------------------------------------------------------

    def state(self) -> State:
        st = State()
        for ex in self.history:
            ctx = ex.request.get("context") or {}
            if ex.kind == "plan":
                st.planned = True
            elif ex.kind == "produce":
                tag = str(ctx["tag"])
                st.produced[tag] = ex.obj.text if ex.obj and ex.obj.ok else None
                if tag not in st.produced_order:
                    st.produced_order.append(tag)
            elif ex.kind == "convert":
                ctx = ex.request.get("context") or {}
                st.cells[(str(ctx["source"]), str(ctx["target"]))] = ex.obj
            elif ex.kind == "controls":
                st.competences.append(ex.obj)
            elif ex.kind == "identify":
                st.reads.append(ex.obj)
            elif ex.kind == "separate":
                st.contrasts.append(ex.obj)
            elif ex.kind == "verdict":
                st.finished = True
                st.stop_note = ex.result.get("stop_note")
            for k, v in (ex.calls or {}).items():
                st.calls[k] = st.calls.get(k, 0) + v
        return st

    def result(self, st: State | None = None) -> DialectResult:
        """Assemble the result object from the fold. One definition of the
        verdict, in `DialectResult`, exactly as before."""
        st = st or self.state()
        panel = Panel(competences=list(st.competences))
        out = DialectResult(
            tag=self.tag, engine=self.engine, designator=self.designator,
            square=self._square(st), identical_arms=self._identical(st),
            reads=list(st.reads),
            contrasts=list(st.contrasts), panel=panel,
            corpus_path=str(getattr(self.corpus, "path", "") or "") or None,
            sibling_tag=self.sibling.tag if self.sibling else None)
        if st.stop_note:
            out.notes.append(st.stop_note)
        return out

    def _square(self, st: State) -> Square | None:
        """The 2×2, as far as it has been filled in.

        `None` until both arms exist: a square with one arm is not a square,
        and offering a half-built one would let `distinguishes` be read off
        two cells.
        """
        if self.sibling is None:
            return None
        if not st.produced.get(self.tag) or not st.produced.get(self.sibling.tag):
            return None
        sq = Square(subject=self.tag, sibling=self.sibling.tag)
        sq.cells.update(st.cells)
        return sq

    def _identical(self, st: State) -> bool:
        """Did the engine write the same text under both designators?

        Whitespace-insensitive and nothing more: this is not a similarity
        threshold. "Almost the same" is a measurement the square makes properly,
        cell by cell; only *the same text twice* makes the pair unshowable.
        """
        if self.sibling is None:
            return False
        a = (st.produced.get(self.tag) or "").split()
        b = (st.produced.get(self.sibling.tag) or "").split()
        return bool(a) and a == b

    def _pair(self, st: State) -> tuple[str | None, str | None]:
        """The contrast pair: the sibling's text and the subject's.

        The neutral text stays out of it. It is the conversion source, and it is
        not a variety anyone could be asked to name.
        """
        return ((st.produced.get(self.sibling.tag) if self.sibling else None),
                st.produced.get(self.tag))

    # -- planning -------------------------------------------------------------

    def plan(self) -> Planned | None:
        st = self.state()
        if st.finished:
            return None

        if not st.planned:
            # Two different lists, and the old line conflated them: the arm this
            # variety is contrasted against is the *sibling*, while
            # `blind.options` is the answer menu a control model picks its name
            # from — which contains both arms, and so read as "Spain against
            # Mexico and Spain". Summary text only; nothing here is sent.
            against = (repr(_designator_for(self.scheme, self.sibling))
                       if self.sibling else "no named sibling")
            return self._free(
                "plan", PHASE_PLAN,
                f"{self.designator!r} against {against}; "
                f"named blind from: {', '.join(self.blind.options)}; "
                f"{len(self.field)} eligible control model(s)")

        for variety in self.named:
            if variety.tag not in st.produced:
                return self._plan_produce(variety)

        # The free, deterministic negative, and it comes before every paid
        # call: one text under two designators is not a contrast pair. Shown to
        # a panel it would turn the order control into a second identity
        # control — the two halves being identical, nothing can follow the text
        # rather than the slot — and attestors would be qualified without being
        # tested. A run did exactly that and reported `production` off it.
        if self._identical(st):
            return self._free(
                "verdict", PHASE_VERDICT,
                f"Stop: {self.engine} wrote the same text for both varieties, "
                f"so there is no pair to show anyone")

        square = self._square(st)
        if square is not None and not square.complete:
            for src, dst in square.plan:
                if (src, dst) not in st.cells:
                    return self._plan_convert(st, src, dst)

        pair = self._pair(st)
        if not all(pair):
            return self._free(
                "verdict", PHASE_VERDICT,
                "Stop: no usable pair was produced, so nothing can be attested")

        done = [c.attestor for c in st.competences]
        remaining = [a for a in self.field if a not in done]
        if remaining:
            return self._plan_controls(st, remaining[0])

        qualified = [c.attestor for c in st.competences if c.qualified]
        if not qualified:
            return self._free(
                "verdict", PHASE_VERDICT,
                "Stop: no control model passed the controls for this family")

        for attestor in qualified:
            if not any(r.attestor == attestor for r in st.reads):
                return self._plan_identify(st, attestor)
            if self.sibling and not any(c.attestor == attestor
                                        for c in st.contrasts):
                return self._plan_separate(st, attestor)

        return self._free("verdict", PHASE_VERDICT,
                          "Add up what the five probes found")

    def _plan_produce(self, variety: Language) -> Planned:
        from polyglot.core.probe import designator as dsg

        variety = self._arm(variety.tag) or variety
        d = _designator_for(self.scheme, variety)
        system, user = dsg.generation_prompt(
            d, self.scenario, 3,
            script_name=dsg.script_name_for(variety.script))
        subject = ("the variety under test" if variety.tag == self.tag
                   else "the neutral arm" if variety.inclusive
                   else "the contrast arm")
        return Planned(
            kind="produce", phase=PHASE_PRODUCE,
            title=HELP["produce"][0], explains=HELP["produce"][1],
            summary=f"Ask {self.engine} for {d!r} — {subject}",
            calls=[Call(role="generation", model=self.engine, system=system,
                        user=user, max_tokens=400, label=d)],
            context={"tag": variety.tag})

    def _plan_convert(self, st: State, src: str, dst: str) -> Planned:
        """One cell of the square: `src`'s text, rewritten into `dst`."""
        target = self._arm(dst)
        assert target is not None
        text = st.produced.get(src) or self.scenario
        designator = _designator_for(self.scheme, target)
        system = convert_prompt(target, designator)
        expect = ("it was written as this variety, so nothing should change"
                  if src == dst else "the other arm's text, so something should")
        return Planned(
            kind="convert", phase=PHASE_CONVERT,
            title=HELP["convert"][0], explains=HELP["convert"][1],
            summary=f"{src} → {dst}: rewrite into {designator!r} — {expect}",
            calls=[Call(role="convert", model=self.engine, system=system,
                        user=text, max_tokens=600,
                        label=f"{src}->{dst}")],
            context={"source": src, "target": dst})

    def _plan_controls(self, st: State, attestor: str) -> Planned:
        first, second = self._pair(st)
        assert first and second
        # Shown rather than editable. These prompts are the control: their
        # answers are certain *by construction*, and an edited control is no
        # longer one.
        id_sys, id_user = attest.separate_prompt(first, first, self.blind)
        return Planned(
            kind="controls", phase=PHASE_PANEL,
            title=HELP["controls"][0], explains=HELP["controls"][1],
            summary=f"Qualify {attestor} on this family: identity, then order "
                    f"in both presentations",
            editable=False,
            calls=[
                Call(role="control", model=attestor, system=id_sys, user=id_user,
                     max_tokens=600, label="identity control"),
                Call(role="control", model=attestor, system=id_sys,
                     user=attest.separate_prompt(first, second, self.blind)[1],
                     max_tokens=600, label="order control (A then B)"),
                Call(role="control", model=attestor, system=id_sys,
                     user=attest.separate_prompt(second, first, self.blind)[1],
                     max_tokens=600, label="order control (B then A)"),
            ],
            context={"attestor": attestor})

    def _plan_identify(self, st: State, attestor: str) -> Planned:
        text = st.produced.get(self.tag) or ""
        system, user = attest.identify_prompt(text, self.blind)
        return Planned(
            kind="identify", phase=PHASE_ATTEST,
            title=HELP["identify"][0], explains=HELP["identify"][1],
            summary=f"Ask {attestor} to name the variety, unlabelled",
            calls=[Call(role="attest", model=attestor, system=system, user=user,
                        max_tokens=500, label=f"identify · {attestor}")],
            context={"attestor": attestor})

    def _plan_separate(self, st: State, attestor: str) -> Planned:
        first, second = self._pair(st)
        assert first and second
        # The pair is shown in an order salted by the texts themselves, so the
        # expected answer is not always A — a model that always answers the
        # options in the order they were listed would otherwise score 100% on
        # every pair and we would read a formatting habit as dialect knowledge.
        # `presentation` is the same function `separate` uses, so what is shown
        # here is what will be sent.
        a, b, _swapped = attest.presentation(first, second)
        system, user = attest.separate_prompt(a, b, self.blind)
        return Planned(
            kind="separate", phase=PHASE_ATTEST,
            title=HELP["separate"][0], explains=HELP["separate"][1],
            summary=f"Ask {attestor} to tell the pair apart",
            calls=[Call(role="attest", model=attestor, system=system, user=user,
                        max_tokens=600, label=f"contrast · {attestor}")],
            context={"attestor": attestor})

    def _free(self, kind: str, phase: str, summary: str,
              context: dict | None = None) -> Planned:
        return Planned(kind=kind, phase=phase, title=HELP[kind][0],
                       explains=HELP[kind][1], summary=summary, calls=[],
                       editable=False, context=context or {})

    # -- execution ------------------------------------------------------------

    def execute(self, planned: Planned, *, calls: list[Call] | None = None,
                edited: bool = False) -> Executed:
        sent = calls if calls is not None else planned.calls
        mark = self.corpus.count
        ordinal = len(self.history)
        handler = getattr(self, f"_do_{planned.kind}")
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

    def _do_plan(self, planned: Planned, _sent) -> Executed:
        return self._make(
            planned, summary=planned.summary,
            tone="ok" if self.field else "bad",
            result=self.preflight())

    def _arm(self, tag: str) -> Language | None:
        """The Language object an arm is actually asked under.

        The neutral arm may carry an overridden script, and the override has to
        reach the gate as well as the prompt: text generated under "Serbian
        (Latin script)" and then gated against Cyrillic would fail the gate for
        doing exactly what it was told.
        """
        if self.neutral is not None and tag == self.neutral.tag:
            return self.neutral
        return self.scheme.get(tag)

    def _do_produce(self, planned: Planned, sent: list[Call]) -> Executed:
        variety = self._arm(str(planned.context["tag"]))
        assert variety is not None
        call = sent[0]
        item = _produce(client=self.client, engine=call.model, lang=variety,
                        designator=_designator_for(self.scheme, variety),
                        scenario=call.user, corpus=self.corpus,
                        messages=(call.system or "", call.user),
                        relatives=relative_codes(self.scheme, variety),
                        accept=accepted_codes(variety),
                        abstain=indistinguishable_codes(variety))
        role = ("under test" if variety.tag == self.tag
                else "neutral source" if variety.inclusive else "contrast arm")
        return self._make(
            planned, summary=f"{variety.tag} ({role}): {item.note}",
            # Three states, because an abstention is neither. Colouring it like
            # a failure would say the opposite of what the battery does with
            # it: the text is kept and measured.
            tone="ok" if item.ok and not item.abstained
                 else "warn" if item.ok else "bad",
            result={"tag": variety.tag, "text": item.text,
                    "gate": item.gate_verdict, "abstained": item.abstained,
                    "error": item.error},
            calls={"produce": 1}, obj=item)

    def _do_convert(self, planned: Planned, sent: list[Call]) -> Executed:
        call = sent[0]
        src = str(planned.context["source"])
        dst = str(planned.context["target"])
        target = self._arm(dst)
        assert target is not None
        c = convert(client=self.client, engine=call.model, lang=target,
                    text=call.user,
                    designator=_designator_for(self.scheme, target),
                    corpus=self.corpus,
                    relatives=relative_codes(self.scheme, target),
                    accept=accepted_codes(target),
                    abstain=indistinguishable_codes(target),
                    max_tokens=call.max_tokens, system=call.system)
        diagonal = src == dst
        if not c.ok:
            tone = "bad"
        elif diagonal:
            # A no-op is the *expected* answer on the diagonal, so an empty
            # diff here is `ok` and a large one is the warning. The opposite of
            # how an off-diagonal cell reads, which is why tone is not shared.
            tone = "ok" if c.rate <= STABLE_AT else "warn"
        else:
            tone = "ok" if (c.rate > 0 and not c.paraphrased) else "bad"
        met = ("unchanged, as expected" if diagonal and c.ok and c.rate <= STABLE_AT
               else "changed, as expected" if not diagonal and c.ok
               and c.rate > 0 and not c.paraphrased
               else "paraphrased, not converted" if c.ok and c.paraphrased
               else "no change" if c.ok and c.rate == 0
               else "drifted" if c.ok else (c.error or "failed"))
        return self._make(
            planned,
            summary=(f"{src} → {dst}: {len(c.edits)} edit(s), "
                     f"{c.rate:.0%} of the text — {met}"
                     if c.ok else f"{src} → {dst}: {c.error}"),
            tone=tone,
            result={**c.as_dict(), "source": src, "target": dst,
                    "diagonal": diagonal, "expected":
                        "unchanged" if diagonal else "changed",
                    "before": c.before, "after": c.after},
            calls={"convert": 1}, obj=c)

    def _do_controls(self, planned: Planned, sent: list[Call]) -> Executed:
        from polyglot.core.probe.panel import qualify_attestor

        st = self.state()
        first, second = self._pair(st)
        attestor = str(planned.context["attestor"])
        comp = qualify_attestor(self.client, attestor, family=self.blind.language,
                                blind=self.blind, neutral=first or "",
                                first=first or "", second=second or "",
                                tag=self.tag, corpus=self.corpus)
        return self._make(
            planned,
            summary=f"{attestor}: {'qualified' if comp.qualified else 'discarded'}"
                    + (f" ({', '.join(comp.failed)})" if comp.failed else ""),
            tone="ok" if comp.qualified else "bad",
            result=comp.as_dict(), calls={"control": 3}, obj=comp)

    def _do_identify(self, planned: Planned, sent: list[Call]) -> Executed:
        from polyglot.core.probe.dialect import blind_read

        st = self.state()
        attestor = str(planned.context["attestor"])
        call = sent[0]
        expected = self.lang.region_name or self.lang.tag
        # `expected` is consumed here and goes nowhere near `attest.identify`,
        # which has no parameter for it. That is the whole of the blinding
        # mechanism: it is enforced by signature, not by prompt wording.
        r = blind_read(self.client, call.model, text=st.produced.get(self.tag) or "",
                       blind=self.blind, expected=expected, tag=self.tag,
                       corpus=self.corpus,
                       messages=(call.system or "", call.user))
        return self._make(
            planned,
            summary=f"{attestor}: {r.identification.answer!r}"
                    + ("" if r.correct else f" (expected {expected!r})"),
            tone=attest_tone(True if r.correct
                             else None if not r.identification.decided else False),
            result=r.as_dict(), calls={"attest": 1}, obj=r)

    def _do_separate(self, planned: Planned, sent: list[Call]) -> Executed:
        from polyglot.core.probe.dialect import contrast_read

        st = self.state()
        attestor = str(planned.context["attestor"])
        first, second = self._pair(st)
        assert self.sibling is not None
        call = sent[0]
        s = contrast_read(self.client, call.model, first=first or "",
                          second=second or "", blind=self.blind,
                          expected_first=self.sibling.region_name or self.sibling.tag,
                          expected_second=self.lang.region_name or self.lang.tag,
                          tag=self.tag, corpus=self.corpus,
                          messages=(call.system or "", call.user))
        return self._make(
            planned,
            summary=f"{attestor}: {s.separation.a!r} / {s.separation.b!r}",
            tone=attest_tone(True if s.correct else None if s.merged else False),
            result=s.as_dict(), calls={"attest": 1}, obj=s)

    def _do_verdict(self, planned: Planned, _sent) -> Executed:
        from polyglot.core.probe.variant import NOT_A_CLAIM, POSITIVE

        st = self.state()
        note = None
        pair = self._pair(st)
        if not all(pair):
            note = ("No usable pair was produced, so no control model could be "
                    "qualified and nothing was attested. This is a gap in the "
                    "production half, not a verdict about discrimination.")
        elif st.competences and not any(c.qualified for c in st.competences):
            note = ("No control model passed the controls for this family, so "
                    "nothing was measured. `indeterminate` is a statement about "
                    "the instrument and must not be read as a negative about "
                    "the model.")
        if note:
            st.stop_note = note

        out = self.result(st)
        if note:
            out.notes.append(note)
        self._keep(out, st)

        verdict = out.verdict
        return self._make(
            planned,
            summary=(f"{verdict.value} (confidence {out.confidence})"
                     if not note else f"{verdict.value} — {note}"),
            tone=attest_tone(True if verdict in POSITIVE
                             else None if verdict in NOT_A_CLAIM else False),
            result={**out.as_dict(), "stop_note": note}, obj=out)

    def _keep(self, out: DialectResult, st: State) -> None:
        """Write what this run found, on **every** path out of the battery.

        The two early exits — no usable pair, no competent control model — are
        exactly the ones that most need recording, and in the previous tool were
        the two that silently skipped it: an absent row reads as "never
        measured", which is a third answer again from "measured and could not
        tell".

        What is recorded is the control models' competence *and* the finding
        about the engine actually under test. Until the second existed a run
        kept only how well the attestors could discriminate and let its own
        verdict die with the session — the one thing the run was for was the one
        thing it did not keep.
        """
        if self.record is None:
            return
        panel = Panel(competences=list(st.competences))
        for cap in capabilities_from(panel, tag=self.tag, role=Role.DISCRIMINATION,
                                     script=self.lang.script,
                                     probes=["identity", "order"]):
            self.record.put(cap)
        self.record.put(_producer_capability(out, self.lang))

    # -- the verdict ----------------------------------------------------------

    def outcome(self) -> dict:
        st = self.state()
        out = self.result(st)
        return {
            "test_type": self.test_type, "tag": self.tag, "cls": self.lang.cls,
            "engine": self.engine, "designator": self.designator,
            "level": out.verdict.value, "confidence": out.confidence,
            "evidence": "attestation",
            "attestors": [c.attestor for c in st.competences if c.qualified],
            "calls": dict(st.calls),
            "contrasted_with": self.sibling.tag if self.sibling else None,
            "produced": dict(st.produced),
            **out.as_dict(),
        }

    def adjudicate(self, overrides: list[dict]) -> dict:
        """Re-derive the verdict with an attestor's answer overruled.

        Same rule as the capability test: the measurement stands and this
        reports what the run *would* have concluded. The verdict is recomputed
        by `DialectResult.verdict` — the one definition — rather than by a copy
        of its reasoning, so the two numbers on screen are comparable.

        An override here is a statement about the attestor, not about the model.
        Overruling "generic" to "correct" on a text you can read yourself is the
        operator supplying the competence the panel lacked; it does not make the
        panel competent, and an adjudicated dialect run is never promoted on its
        own.
        """
        import copy

        st = self.state()
        out = self.result(st)
        applied: list[dict] = []

        reads = copy.deepcopy(out.reads)
        contrasts = copy.deepcopy(out.contrasts)
        for o in overrides:
            try:
                probe = str(o["probe"])
                attestor = str(o["attestor"])
                correct = bool(o["correct"])
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"Not a usable override: {o!r} ({e})") from e
            pool = reads if probe == "identify" else contrasts
            row = next((r for r in pool if r.attestor == attestor), None)
            if row is None:
                raise ValueError(f"No {probe} answer from {attestor!r} to overrule.")
            # Forcing the answer rather than forcing a boolean, so that
            # `correct`, `decided` and the consensus all stay derived properties
            # instead of becoming three facts that can disagree.
            #
            # "It was wrong" has to mean *named another variety*, not *declined
            # to name one*. An abstention is excluded from the consensus
            # entirely, so recording a wrong answer as an abstention would
            # quietly withdraw the vote instead of reversing it — and the
            # verdict would move for the wrong reason.
            if probe == "identify":
                row.identification.answer = (
                    row.expected if correct else _other_than(row.expected, self.blind))
            else:
                row.separation.a = (row.expected_first if correct
                                    else _other_than(row.expected_first, self.blind))
                row.separation.b = (row.expected_second if correct
                                    else _other_than(row.expected_second, self.blind))
                row.separation.swapped = False
            applied.append({"probe": probe, "attestor": attestor,
                            "correct": correct})

        adjusted = DialectResult(
            tag=out.tag, engine=out.engine, designator=out.designator,
            square=out.square, identical_arms=out.identical_arms,
            reads=reads, contrasts=contrasts,
            panel=out.panel, corpus_path=out.corpus_path,
            sibling_tag=out.sibling_tag)
        return {
            "applied": applied, "changed": len(applied),
            "measured": {"verdict": out.verdict.value,
                         "confidence": out.confidence},
            "adjudicated": {"verdict": adjusted.verdict.value,
                            "confidence": adjusted.confidence,
                            "identified": adjusted.identified.as_dict(),
                            "separated": adjusted.separated.as_dict()},
            "note": "What this run would have concluded had the panel answered "
                    "this way. The measurement is unchanged, and an adjudicated "
                    "run is never promoted to a verdict on its own.",
        }


def _other_than(expected: str, blind: Blind) -> str:
    """Any option but the right one.

    Used only by adjudication, to express "this control model named the wrong
    variety" in the same vocabulary a real answer uses. Falls back to
    `indistinguishable` in the degenerate case where there is nothing else to
    name, which cannot happen for a real pair — `Blind` refuses fewer than two
    options — but is the right answer if it ever did.
    """
    for option in blind.options:
        if option != expected:
            return option
    return attest.INDISTINGUISHABLE
