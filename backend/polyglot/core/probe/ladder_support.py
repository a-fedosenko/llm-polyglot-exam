"""Capability-probe support: the rules the traversal is built out of.

Everything here was lifted verbatim from `llmlc/probe/ladder.py` except the
traversal itself. The generator that walked the three rungs has moved to
`polyglot.engine.capability`, where it is an explicit step machine instead —
because a generator cannot be rewound, cannot be told to re-run one step, and
cannot have an edited prompt handed to it, and all three are things a person
sitting at the page needs to do.

**What did not move is the part that decides anything.** Designator candidates,
the void/negative classification, how a run is scored, which codes count as a
relative substitution and which as an abstention: those are the method, they are
validated by twenty-one protocols, and they are still one definition with one
caller each. The engine composes them in the same order under the same
thresholds; convention 9 — an analysis harness calls the production function and
never re-implements it — applies to the engine exactly as it applied to the
ladder.

    rung 1  designator selection, gate-scored, no judge call
            every candidate fails every item -> deterministic negative, stop
    rung 2  qualify a reader, then judge what survived
    rung 3  borderline only: more items to tighten the interval
"""
from __future__ import annotations

from dataclasses import dataclass

from polyglot.core.probe import designator as dsg
from polyglot.core.probe.gate import GateVerdict
from polyglot.core.probe.score import Evidence, Score, score
from polyglot.core.probe.steps import ItemOutcome
from polyglot.core.scheme import Language, Scheme

RUNG1_ITEMS = 3
RUNG3_ITEMS = 6

#: Gate verdicts that neither pass nor count against the model: the item is void,
#: not evidence. Memorised boilerplate, text too short to judge, and LID calls too
#: weak to convict on.
VOID = {GateVerdict.MEMORISED, GateVerdict.TOO_SHORT, GateVerdict.LOW_CONFIDENCE}


def gate_tone(verdict: GateVerdict) -> str:
    """How a gate verdict should read at a glance.

    Three states, because the gate has three kinds of outcome and only two of
    them are about the model. `VOID` verdicts -- memorised text, too short, a
    language-ID call too weak to commit -- are neither a pass nor evidence
    against it, and colouring them like a failure would say the opposite of what
    the arithmetic does with them (protocol 018).
    """
    if verdict is GateVerdict.PASS:
        return "ok"
    if verdict in VOID:
        return "warn"
    return "bad"


def score_run(items: list[ItemOutcome], *, qualification, tag: str) -> Score:
    """Combine a run's items into one score.

    Lifted out of `ladder_steps` because a second caller needs it: the bench
    lets a reader correct a judge verdict they can see is wrong, and the
    corrected run has to be scored by **this** function rather than by a copy
    of it. Convention 9 is explicit about what the copy costs -- an analysis
    harness re-implemented a production call, got it subtly wrong, and 68 bad
    verdicts reached a protocol's conclusions. A re-score that drifted from the
    original would be worse than that, because the two numbers would sit side
    by side on one page inviting a comparison that is not one.
    """
    contents = [i.content for i in items if i.content is not None]
    notes: list[str] = []
    if any(i.gate.verdict is GateVerdict.RELATIVE_SUBSTITUTION for i in items):
        notes.append("Model produced a neighbouring language rather than the target.")
    if not qualification.trustworthy:
        evidence = Evidence.UNVERIFIED
        notes.append(f"Back-translator {qualification.backtranslator} is "
                     f"{qualification.status.value} for {tag}: {qualification.detail}")
    elif not contents:
        evidence = Evidence.UNVERIFIED
    else:
        evidence = Evidence.FACT_RECALL
    refused = [i for i in items if i.gate.verdict is GateVerdict.REFUSED]
    graded = [i for i in items
              if i.gate.verdict not in VOID and i.gate.verdict is not GateVerdict.REFUSED]
    return score(lang_pass=[i.lang_ok for i in graded], content=contents,
                 evidence=evidence, refusals=len(refused),
                 contradictions=sum(i.judgement.contradictions for i in items
                                    if i.judgement and i.judgement.ok),
                 notes=notes)


def item_detail(item: ItemOutcome) -> dict:
    """An item as the bench shows it. Deliberately the whole text: reading the
    raw output is how nearly every real defect in this project was found."""
    return {
        "spec_id": item.spec_id,
        # The checklist itself, index-aligned with `verdicts`. Without it the
        # page can say four facts were present and one missing, and cannot say
        # *which* -- which is the only form in which a reader can tell whether
        # the judge was right.
        "facts": list(item.facts),
        "designator": item.designator,
        "prompt": item.prompt,
        "generation": item.generation,
        "gate": item.gate.verdict.value,
        "gate_detail": item.gate.detail,
        "lid": ({"label": item.gate.lid.label,
                 "confidence": round(item.gate.lid.confidence, 3),
                 "backend": item.gate.lid.backend}
                if item.gate.lid else None),
        "back_translation": item.back_translation,
        "verdicts": ([v.value for v in item.judgement.verdicts]
                     if item.judgement and item.judgement.ok else None),
        "recall": (round(item.judgement.recall, 3)
                   if item.judgement and item.judgement.ok else None),
        "error": item.error,
    }


@dataclass
class DesignatorTrial:
    kind: str                      # primary candidate kind
    value: str
    gate_pass: int
    attempts: int
    kinds: tuple[str, ...] = ()    # every candidate kind that produced this string

    def __post_init__(self) -> None:
        if not self.kinds:
            self.kinds = (self.kind,)

    @property
    def rate(self) -> float:
        return self.gate_pass / self.attempts if self.attempts else 0.0

    def is_(self, kind: str) -> bool:
        return kind in self.kinds


def refusals_in(items: list[ItemOutcome]) -> int:
    return sum(1 for i in items if i.gate.verdict is GateVerdict.REFUSED)


def accepted_codes(lang: Language) -> set[str]:
    """Codes that also satisfy a request for this language -- a macrolanguage's
    members. Asking for `sw` and getting `swh` is resolution, not substitution."""
    return set(lang.members) if lang.is_macro else set()


def relative_codes(scheme: Scheme, lang: Language) -> set[str]:
    out: set[str] = set()
    if lang.macro:
        out.add(lang.macro)
        for other in scheme.languages.values():
            if other.iso639_3 == lang.macro and other.members:
                out.update(other.members)
    out.discard(lang.iso639_3 or "")
    return out - accepted_codes(lang)


#: Written standards a general-purpose LID cannot separate *in a given script*,
#: keyed by script. Authored, not derived: this is a claim about orthographies,
#: and no table in the catalogue encodes it.
#:
#: Serbo-Croatian in Latin is the case that forced it. GlotLID reads ijekavian
#: Serbian in Latin as `bos` or `hrv`, and is right to: Bosnian, Croatian,
#: Montenegrin and Latin-script Serbian very nearly coincide on the page, and
#: the differences that remain are not what a sentence-level classifier sees.
#: In **Cyrillic** the same four do not collapse -- Croatian is not written in
#: it -- which is why this is keyed by script and why `srp_Cyrl` is identified
#: cleanly and needs no abstention.
#:
#: Entries here void items; they never pass them. Adding a pair is saying "we
#: cannot tell", not "these satisfy each other" -- see `gate.check`.
INDISTINGUISHABLE: dict[str, tuple[frozenset[str], ...]] = {
    "Latn": (frozenset({"srp", "bos", "hrv", "cnr", "hbs"}),),
}


def indistinguishable_codes(lang: Language) -> set[str]:
    """Codes the instrument cannot tell from this language, in its own script.

    Returned separately from `relative_codes` because the two mean opposite
    things about where the fault lies: a relative substitution is the model
    reaching for a neighbour, and an indistinguishable pair is us being unable
    to look. Conflating them would publish the second as the first.
    """
    code = lang.iso639_3 or ""
    out: set[str] = set()
    for group in INDISTINGUISHABLE.get(lang.script or "", ()):
        if code in group:
            out |= set(group)
    out.discard(code)
    return out


def candidates_for(scheme: Scheme, lang: Language, incumbent: str | None = None):
    """The designators this language will actually be asked by.

    One definition, because two callers need it and they must not disagree: the
    ladder spends money on these, and the bench's pre-flight tells a reader
    what is about to be asked. A pre-flight built from a second copy of this
    logic would describe a run that is not the one about to happen -- the same
    failure as an analysis harness re-implementing a production call
    (convention 9).
    """
    from polyglot.core.probe.variant import is_script_variant

    # The region is named only when the *tag* carries one. `region_name` alone
    # is not that test: a locale list's unqualified tag has no region subtag and
    # still inherits a region name from the reference catalogue's likely-subtag
    # resolution -- `pt` carries "Brazil", `en` "United States", `sr` "Serbia".
    # Passing it unconditionally asked for `pt` with the identical designator as
    # `pt-br` ("Portuguese (Brazil)"), and for `en` as `en-us`, so the
    # unqualified tag -- the neutral arm a dialect is contrasted against -- was
    # silently asked to be a dialect, and the contrast was a null comparison by
    # construction.
    #
    # This is protocol 020's trigger in mirror image: there two tags shared one
    # prompt because the region was never passed, here because it was passed
    # where the tag had none. A designator must say exactly what the tag says.
    return dsg.candidates(lang, incumbent=incumbent,
                          region_name=lang.region_name if lang.region else None,
                          qualify_script=is_script_variant(scheme, lang) or None)


def recall_tone(item: ItemOutcome) -> str:
    """An item that was judged, by how much of the meaning came back.

    The cut is the adequacy cut, so a green item is one that would carry a
    `Proficient` verdict on its own -- the colour and the tier agree rather than
    being two separate opinions about the same number.
    """
    from polyglot.core.probe.score import PROFICIENT_MIN_CONTENT

    if item.judgement is None or not item.judgement.ok:
        return "warn"
    if item.judgement.recall >= PROFICIENT_MIN_CONTENT:
        return "ok"
    return "warn" if item.judgement.recall else "bad"


def tier_tone(s: Score) -> str:
    from polyglot.core.probe.score import Tier

    return {Tier.PROFICIENT: "ok", Tier.ASSISTED: "warn", Tier.UNUSABLE: "bad"}[s.tier]


def score_summary(s: Score) -> str:
    return (f"{s.tier.value} — eligibility {s.s_lang:.2f}, adequacy {s.s_content:.2f} "
            f"[{s.ci[0]:.2f}, {s.ci[1]:.2f}]"
            + (" (borderline)" if s.borderline else ""))


def score_detail(s: Score) -> dict:
    d = s.as_dict()
    # The workflow sentence is what a planner actually reads, and it carries the
    # availability caveat the tier deliberately does not.
    d["workflow"] = s.workflow
    return d
