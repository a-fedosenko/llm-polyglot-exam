"""The five dialect probes.

Each asks a different question, and they come apart in practice — which is why
they are five probes and not one score. A model can identify Montenegrin
Serbian on sight and still write generic Serbian when asked for it; another can
write it unprompted and fail to convert a text into it on demand. Collapsing
those into "supports the dialect: yes/no" is what makes a dialect claim
unconvincing, and keeping them separate is what lets a verdict explain itself.

    P1  blind identification   does an attestor name the variety, unlabelled?
    P2  contrast pair          can an attestor tell two varieties apart?
    P3  conversion             does the model change anything when told to?
    P4  self-consistency       does it change the *same* things every time?
    P5  marker grounding       were the cited markers real?

**P3 and P4 need no attestor at all.** That is where cheap breadth lives: they
are the dialect equivalent of the deterministic negative, sound at full breadth
with no panel call and no control text. P1 and P2 are the paid half, and are
only worth spending where the free half found something.

P5 is not a separate call. Grounding happens inside `attest.identify` and
`attest.separate`; this module only aggregates what they found, because a
fabricated marker is evidence about the *attestor* and belongs in the same
place as its verdict.

What is deliberately absent: routing, panels, capability records and the
traversal that drives them. A probe reports what happened; deciding what it
means about a model is a later and separate job, and mixing them is how an
aggregate comes to hide the disagreement it was built from.

The battery that calls these five in order lives in `polyglot.engine.dialect`.
It used to be a generator in this file; it is an explicit step machine now, so
that a person can edit the prompt before a paid call, re-run one attestation, or
change the panel after seeing how the controls went. None of the probes changed
in the move — `DialectResult.verdict` below is still the only place a verdict is
decided, and it is still a transparent function of the outcomes with no weights
in it.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from polyglot.core.client import OpenAICompatClient
from polyglot.core.probe import attest
from polyglot.core.probe import designator as dsg
from polyglot.core.probe.attest import Blind, Identification, Separation
from polyglot.core.probe.corpus import Corpus, Record
from polyglot.core.probe.gate import GateResult
from polyglot.core.probe.gate import check as gate_check
from polyglot.core.scheme import Language, Scheme

#: Above this share of tokens, a "conversion" has stopped converting and started
#: paraphrasing — or translating, which looks the same from here.
#:
#: There is deliberately **no floor**. A dialect conversion is supposed to be a
#: small edit: ekavian to ijekavian moves a handful of morphemes and leaves the
#: sentence standing, so any floor expressed as a share of the text convicts
#: exactly the models that did the job correctly — a single correct substitution
#: in a long paragraph is a conversion, and scoring it as "changed nothing"
#: would be the wrong answer for the right behaviour. Punctuation and case are
#: already discarded by tokenisation, so any surviving edit is a real word
#: substitution and one is enough.
#:
#: The ceiling is the live risk instead, and it is the one a floor would have
#: missed entirely: a model that rewrites the whole text has produced something
#: that is no longer the same passage, and its diff is unreadable as evidence.
#: P3 separates "touched nothing" from "touched something" from "touched
#: everything"; whether what it touched was the *right* thing is P1 and P2's
#: question.
PARAPHRASED_AT = 0.5

#: Repeats for the self-consistency probe. Two is the minimum that can disagree
#: and the most that is worth paying for at this stage: the question is whether
#: the transformation is systematic or improvised, and an improviser disagrees
#: with itself on the first retry.
#: Retired. P4's "do it again and compare" is subsumed by the square below:
#: the diagonal cells are the same question — does the model agree with itself
#: about what this variety is — and they are being paid for anyway.
CONSISTENCY_RUNS = 2

#: A diagonal cell counts as *stable* at or below this. Converting a text into
#: the variety it was just written in should be a no-op; a token of drift is
#: not a finding, a third of the passage is.
STABLE_AT = 0.1

#: Jaccard agreement between one off-diagonal's edits and the *reverse* of the
#: other's. `lijepo -> lepo` one way and `lepo -> lijepo` the other is
#: knowledge of a specific alternation; two unrelated edit sets are improvisation
#: in both directions that happened to touch something.
MIRROR_AT = 0.5


# -- what an attestor is told, built from the scheme --------------------------

def blind_for(scheme: Scheme, family: list[Language], *, salt: str) -> Blind:
    """The option list for a family, shuffled, naming no expected answer.

    Options are **region names**, not tags: `sr-me-cyrl` would hand the answer
    to anything that can read a tag, and "Montenegro" is also the form the
    designator uses, so attestor and producer are speaking about the same thing.

    The unqualified tag contributes no option. It is the neutral arm — the
    thing a variety is contrasted *against* — and offering it as an answer would
    be offering "none of these" twice, since `generic` already means that.

    **`family` is a list the caller chooses, not everything in the scheme.**
    Spanish carries 22 regional tags and English 11, and a 22-way blind choice
    is a different and much harder task than the one being measured — chance
    falls to 4.5%, and an attestor that knows Mexican from Peninsular Spanish
    perfectly well can still miss by picking Uruguay. Pass the varieties
    actually under test; `family_of` is the convenience that returns all of
    them, not an instruction to use all of them.
    """
    language = next((l.name for l in family if l.name), None) or "this language"
    options = []
    for lang in family:
        if lang.inclusive or not lang.region_name:
            continue
        if lang.region_name not in options:
            options.append(lang.region_name)
    script = next((dsg.script_name_for(l.script) for l in family if l.script), None)
    return Blind(language=language, options=attest.shuffled(options, salt=salt),
                 script_name=script)


def family_of(scheme: Scheme, lang: Language) -> list[Language]:
    """Every tag sharing this one's language, umbrella included.

    Grouped by `lang` rather than by equivalence class, because the class is
    `iso639_3|script` and a family's whole point is that it spans regions. For
    Serbian it also spans scripts, and `sr-me-cyrl` and `sr-me-latn` are the
    same variety written twice — which is why options are de-duplicated by
    region name above.
    """
    return [l for l in scheme.languages.values() if l.lang == lang.lang]


# -- producing a variety, and naming it the way the ladder does ---------------

def _designator_for(scheme: Scheme, lang: Language) -> str:
    """The designator the *ladder* would ask by, not a second opinion on it.

    Convention 9's shape: two call sites deriving one prompt independently is
    how an analysis path comes to describe a run that is not the one that
    happened. `candidates_for` is the single definition, here as everywhere.
    """
    from polyglot.core.probe.ladder_support import candidates_for

    return candidates_for(scheme, lang)[0].value


@dataclass
class Produced:
    """One generation under one variety's designator, already gated."""

    text: str | None = None
    gate_verdict: str | None = None
    error: str | None = None
    #: The language check declined to rule rather than passing. The text is
    #: kept — see `GateResult.abstained` — and the caveat travels with it so a
    #: reader is never shown a gated generation and an ungated one as the same
    #: thing.
    abstained: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text)

    @property
    def note(self) -> str:
        if self.abstained:
            return f"kept, language check abstained ({self.gate_verdict})"
        return self.gate_verdict or self.error or "no output"


def _produce(*, client: OpenAICompatClient, engine: str, lang: Language,
             designator: str, scenario: str, corpus: Corpus,
             relatives: set[str], accept: set[str],
             abstain: set[str] | None = None,
             messages: tuple[str, str] | None = None) -> Produced:
    """Generate one item under a variety's designator and gate it.

    Uses the production primitive rather than a copy of it, so a change to how
    generation is prompted reaches the dialect battery automatically.
    """
    from polyglot.core.probe.specs import Spec
    from polyglot.core.probe.steps import generate_and_gate

    item = generate_and_gate(client=client, engine=engine, lang=lang,
                             designator=designator,
                             spec=Spec(id="variety", scenario=scenario, facts=[]),
                             corpus=corpus, relatives=relatives, accept=accept,
                             abstain=abstain, messages=messages)
    if item.error:
        return Produced(None, item.gate.verdict.value, item.error)
    if item.gate.abstained:
        # Kept. The siblings of a pluricentric family are exactly the codes the
        # language check cannot separate, so its abstention is the expected
        # answer here and not evidence about the text. The attestors settle
        # which variety this is; that is what they are for.
        return Produced(item.generation, item.gate.verdict.value, abstained=True)
    if not item.gate.passed:
        return Produced(None, item.gate.verdict.value,
                        item.gate.detail or item.gate.verdict.value)
    return Produced(item.generation, item.gate.verdict.value)


# -- P3 and P4: the free half -------------------------------------------------

#: The conversion prompt, identical for all four cells of the square.
#:
#: **It has to be identical.** The diagonal and the off-diagonal are only
#: comparable because they were asked the same question; a prompt that told the
#: model which cell it was in would make the diagonal a no-op it could satisfy
#: by reading the instruction rather than by knowing the variety, which is how
#: the order control degenerates on an identical pair.
#:
#: Four things it is careful about:
#:
#: - **The comparand is named.** "Wherever that variety differs" had nothing to
#:   differ *from*. It used to be implicit — the user message was neutral
#:   umbrella text, so "differs" meant "differs from generic" — and the square
#:   removed that referent without the prompt noticing. "From its sibling
#:   varieties" supplies the baseline without naming the source text, which is
#:   what leaves the diagonal a real test.
#: - **The designator is repeated, never referred to.** "This specific variety"
#:   was an anaphor pointing at `Serbian (Serbia, Latin script)` — a name plus
#:   a bracketed annotation, which reads as formatting, the more so because the
#:   script is named again in the script rule.
#: - **It ends on the task, and the closing instruction is symmetric.** The two
#:   failure modes are opposite and the prompt must lean toward neither:
#:   inventing a difference inflates the off-diagonal, leaving a real one
#:   deflates it. An earlier wording ended on five prohibitions and a standing
#:   permission to return the text unchanged, so everything nearest the end
#:   pushed toward inaction.
#: - **The variety's actual forms are deliberately absent.** A prompt listing
#:   them — *sjutra*, *đed*, *nijesam* — works better, and that is the problem:
#:   P3 asks whether the model knows what the variety requires, and handing it
#:   the answer measures instruction-following instead.
#:
#: **Three rewrites were tried and reverted** (a strict conversion engine, a
#: dialectal localisation engine with five numbered rules, and a two-step
#: version that reasoned inside `<analysis>` tags before emitting `<result>`).
#: All three produced results identical to this one on the same text — which is
#: what finally located the problem somewhere other than the prompt. See
#: `data/specs/dialect.json`: the battery was running on a scenario whose
#: Serbian rendering carries a single optional yat reflex, so there was nothing
#: in it for any wording to find.
CONVERT_SYSTEM = """You are a native speaker of {designator}.

The user gives you a text already written in this language, in some variety \
of it. Rewrite it so that it reads as {designator} and not as any other \
regional variety of the same language.
{script_rule}
Leave everything that is not a difference between varieties exactly as it is: \
do not translate, do not paraphrase, do not improve the style, do not add or \
remove information, and do not explain or comment.

Go through the text and apply every form that {designator} uses where a \
sibling variety would use another — spelling, vocabulary, grammar. Keep every \
form the varieties share. Neither invent a difference to show work, nor leave \
a real one in place.

Output only the resulting text and nothing else."""


@dataclass
class Conversion:
    """One rewrite of a text into a variety, and what it actually changed."""

    designator: str
    before: str
    after: str | None = None
    gate: GateResult | None = None
    error: str | None = None
    #: Token-level edits, as (from, to) pairs. The evidence a reader audits:
    #: this is where a model says, in its own output, what it thinks the
    #: variety *is*.
    edits: list[tuple[str, str]] = field(default_factory=list)
    rate: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.after)

    @property
    def converted(self) -> bool:
        """Did it make a dialect-sized edit? A direct negative when false,
        settled locally with no judge call."""
        return self.ok and bool(self.edits) and not self.paraphrased

    @property
    def paraphrased(self) -> bool:
        """Rewrote so much that this is no longer the same passage.

        Not a conversion, and not scoreable as one: the diff stops being
        readable evidence, and the edits it lists are sentence rewrites rather
        than the variety's own alternations. Kept distinct from "changed
        nothing" because they are opposite failures and a reader needs to know
        which happened.
        """
        return self.ok and self.rate > PARAPHRASED_AT

    @property
    def signature(self) -> frozenset[tuple[str, str]]:
        """The set of substitutions, order and frequency discarded.

        What self-consistency compares. Two runs that rewrite the same words the
        same way have the same signature even if they reach them in a different
        order or repeat one of them.
        """
        return frozenset(self.edits)

    def as_dict(self) -> dict:
        return {"designator": self.designator, "rate": round(self.rate, 3),
                "converted": self.converted, "paraphrased": self.paraphrased,
                "edits": [list(e) for e in self.edits],
                "gate": self.gate.verdict.value if self.gate else None,
                "error": self.error}


def _tokens(text: str) -> list[str]:
    """Words, lowercased, punctuation dropped.

    Punctuation is dropped so that re-wrapping or a changed comma does not read
    as a dialect edit; case is folded so that a sentence-initial substitution
    counts the same as a medial one.
    """
    return re.findall(r"\w+", text.casefold(), re.UNICODE)


def diff(before: str, after: str) -> tuple[list[tuple[str, str]], float]:
    """Token-level edits and the share of the original they touch.

    `difflib` rather than chrF++ on purpose. chrF++ gives a similarity number
    and nothing a person can read; what a reviewer needs here is *which words
    changed*, because that is how they tell a real dialect conversion from a
    paraphrase. Protocol 020 proposed chrF++ for this and its own text concedes
    the metric does not distinguish the two.
    """
    a, b = _tokens(before), _tokens(after)
    if not a:
        return [], 0.0
    edits: list[tuple[str, str]] = []
    touched = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op == "equal":
            continue
        touched += max(i2 - i1, j2 - j1)
        edits.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return edits, touched / len(a)


def convert_prompt(lang: Language, designator: str) -> str:
    """The conversion system message, as a pure function.

    The script rule is a sentence inside the prompt rather than a floating
    paragraph, so a language whose entry names no script still gets one instead
    of a gap that reads as a truncated prompt.
    """
    name = dsg.script_name_for(lang.script)
    return CONVERT_SYSTEM.format(
        designator=designator,
        script_rule=("\n" + dsg.SCRIPT_RULE.format(script_name=name).strip() + "\n"
                     if name else
                     "\nKeep the writing system of the input unchanged.\n"))


def convert(*, client: OpenAICompatClient, engine: str, lang: Language, text: str,
            designator: str, corpus: Corpus, relatives: set[str], accept: set[str],
            abstain: set[str] | None = None, max_tokens: int = 600,
            system: str | None = None) -> Conversion:
    """P3. Rewrite macrolanguage text into one variety; measure what moved.

    One producer call, no attestor, no control text. The diff is both the
    measurement and the artifact a human reads, which is what makes this the
    most defensible probe in the battery: a reviewer who distrusts the number
    can look at the substitutions and disagree with them concretely.
    """
    out = Conversion(designator=designator, before=text)
    system = system or convert_prompt(lang, designator)

    gen = client.complete(engine, text, max_tokens=max_tokens, system=system)
    shown = f"{system}\n\n--- user ---\n\n{text}"
    corpus.write(Record("conversion", engine, lang.tag, None, designator, shown,
                        gen.text, gen.error, gen.usage, gen.latency_s))
    if not gen.ok:
        out.error = gen.error or "no output"
        return out

    # Gated like any other generation, with one exception: the copy check sees
    # the **system half only**.
    #
    # In a generation, output echoing the prompt means the model wrote nothing
    # of its own, and the check has to see both messages to catch it. In a
    # conversion the user message *is the text being transformed*, so returning
    # it unchanged is not an echo -- it is the answer "nothing in this text needs
    # to change", which is precisely the negative P3 exists to measure. Passing
    # both halves convicted exactly that answer as `copy`, so a model that
    # correctly declined to invent a difference was recorded as having failed to
    # respond. Found on the Russian null control, where the correct answer is
    # "unchanged" every time and the whole arm came back convicted.
    #
    # Echoing the *system* half is still an echo, and still caught.
    out.gate = gate_check(gen.text, prompt=system, expect_lang=lang.iso639_3,
                          expect_script=lang.script, relatives=relatives,
                          accept_lang=accept, abstain_lang=abstain)
    # Same rule as the generation half: an abstention is the language check
    # being unable to separate siblings, which is the normal case here and says
    # nothing about the conversion.
    if not out.gate.passed and not out.gate.abstained:
        out.error = out.gate.detail or out.gate.verdict.value
        return out

    out.after = gen.text
    out.edits, out.rate = diff(text, gen.text or "")
    return out


@dataclass
class Consistency:
    """P4. Whether repeated conversions agree about what the variety requires."""

    runs: list[Conversion] = field(default_factory=list)
    agreement: float | None = None

    @property
    def ok(self) -> bool:
        return self.agreement is not None

    @property
    def systematic(self) -> bool:
        """Repeatable transformation rather than improvisation.

        The claim this probe can make without any judge: a model cannot
        simulate consistency it does not have. Bluffing produces different
        substitutions each time; genuine command produces the same ones.
        """
        return bool(self.agreement and self.agreement >= 0.5)

    def as_dict(self) -> dict:
        return {"agreement": None if self.agreement is None else round(self.agreement, 3),
                "systematic": self.systematic,
                "runs": [r.as_dict() for r in self.runs]}


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


@dataclass
class Square:
    """P3. Each arm's text converted into **both** varieties — a 2×2.

    The probe the battery used to run asked one thing: rewrite neutral text
    into the variety, and see what moved. Two defects, and the second is worse:

    - The "neutral" text was generated by the model under test, so it was the
      model's own default variety wearing an umbrella tag. Whichever variety a
      model defaults to could never show a conversion, and so could never reach
      `full-command`. The baseline also moved from run to run, because it was
      resampled each time.
    - A single off-diagonal cell cannot tell *knowledge* from *noise*. A model
      that rewrites something every time it is asked scores the same as one
      that marks the variety.

    The square fixes both by making each text its own control, which is the
    same move the manufactured identity and order controls make: an expected
    answer that is certain by construction rather than by authority.

        A→A   should not move — the text was just written as A
        A→B   should move
        B→B   should not move
        B→A   should move

    Four readings fall out, and they were previously indistinguishable:

        real command           diagonal stable, off-diagonal moved
        no distinction         nothing moves anywhere
        improvisation          everything moves, including the diagonal
        unstable idea of A     the diagonal moves and the off-diagonal does not

    And one more that no single cell could give: the two off-diagonals should
    be **mirrors**. Converting A→B and B→A are the same alternation run in
    opposite directions, so a model that knows it produces reciprocal
    substitutions. Confabulation does not.
    """

    subject: str
    sibling: str
    #: (source tag, target tag) -> the conversion. Missing while a cell is
    #: still to be run; a cell that errored is present with its error.
    cells: dict[tuple[str, str], Conversion] = field(default_factory=dict)

    def cell(self, src: str, dst: str) -> Conversion | None:
        return self.cells.get((src, dst))

    @property
    def plan(self) -> list[tuple[str, str]]:
        """The four cells, in the order they are run.

        Diagonals first. They are the cheap half of the finding — a model whose
        own text does not survive a round trip into its own variety has already
        told you the off-diagonals will be unreadable.
        """
        a, b = self.subject, self.sibling
        return [(a, a), (b, b), (a, b), (b, a)]

    @property
    def complete(self) -> bool:
        return all(c in self.cells for c in self.plan)

    def _rate(self, src: str, dst: str) -> float | None:
        c = self.cell(src, dst)
        return None if c is None or not c.ok else c.rate

    @property
    def diagonal(self) -> list[float]:
        return [r for r in (self._rate(self.subject, self.subject),
                            self._rate(self.sibling, self.sibling)) if r is not None]

    @property
    def off_diagonal(self) -> list[float]:
        return [r for r in (self._rate(self.subject, self.sibling),
                            self._rate(self.sibling, self.subject)) if r is not None]

    @property
    def stable(self) -> bool:
        """Both diagonals a no-op, within tolerance."""
        d = self.diagonal
        return len(d) == 2 and all(r <= STABLE_AT for r in d)

    @property
    def moved(self) -> bool:
        """Both off-diagonals made a dialect-sized edit — something, and not a
        wholesale rewrite."""
        cells = [self.cell(self.subject, self.sibling),
                 self.cell(self.sibling, self.subject)]
        return (all(c is not None and c.ok for c in cells)
                and all(c.rate > 0 and not c.paraphrased for c in cells))  # type: ignore[union-attr]

    @property
    def mirror(self) -> float | None:
        """Agreement between one direction's edits and the reverse of the other.

        `None` when either direction made no edits — there is nothing to mirror,
        and scoring that 0.0 would read as a contradiction where there is simply
        no evidence.
        """
        ab = self.cell(self.subject, self.sibling)
        ba = self.cell(self.sibling, self.subject)
        if ab is None or ba is None or not ab.edits or not ba.edits:
            return None
        reversed_ba = frozenset((b, a) for a, b in ba.signature)
        return _jaccard(ab.signature, reversed_ba)

    @property
    def mirrored(self) -> bool:
        m = self.mirror
        return m is not None and m >= MIRROR_AT

    @property
    def distinguishes(self) -> bool:
        """The model marks the difference, on demand, in both directions.

        Certain by construction and bought with no attestor at all: this is
        what lets most families be measured for six calls instead of six plus
        five per control model.
        """
        return self.complete and self.stable and self.moved

    @property
    def reading(self) -> str:
        """Which of the four shapes this is, in a word a person can act on."""
        if not self.complete:
            return "incomplete"
        if self.distinguishes:
            return "distinguishes"
        if not self.diagonal or not self.stable:
            return ("improvisation" if self.moved else "unstable")
        return "no-distinction"

    def as_dict(self) -> dict:
        return {
            "subject": self.subject, "sibling": self.sibling,
            "reading": self.reading,
            "stable": self.stable, "moved": self.moved,
            "distinguishes": self.distinguishes,
            "mirror": None if self.mirror is None else round(self.mirror, 3),
            "mirrored": self.mirrored,
            "cells": [{"source": src, "target": dst,
                       "diagonal": src == dst, **c.as_dict()}
                      for (src, dst), c in self.cells.items()],
        }


def consistency(conversions: list[Conversion]) -> Consistency:
    """Jaccard agreement between the edit signatures of repeated conversions.

    Jaccard rather than a count, so a run that makes every edit the other made
    *plus five more* is not scored as agreement. Both directions matter: adding
    edits is as much a sign of improvisation as omitting them.

    Converted-nothing runs are excluded before scoring. Two empty signatures
    agree perfectly and mean nothing — a model that changed nothing twice is
    consistent in a way that says nothing about dialect command, and scoring it
    1.0 would make the most ignorant possible answer the highest-scoring one.
    That is P3's question, and `systematic` must not be able to answer it.
    """
    out = Consistency(runs=conversions)
    usable = [c for c in conversions if c.converted]
    if len(usable) < 2:
        return out

    scores = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i].signature, usable[j].signature
            union = a | b
            scores.append(len(a & b) / len(union) if union else 0.0)
    out.agreement = sum(scores) / len(scores)
    return out


# -- P1 and P2: the paid half -------------------------------------------------

@dataclass
class BlindRead:
    """P1. One text, read by one attestor, with the answer the caller expected.

    `expected` lives on the *result*, never on the call. It is how the caller
    scores the answer; the attestor was never shown it. Keeping it here rather
    than passing it down is what makes the blinding checkable by reading one
    function signature.
    """

    attestor: str
    expected: str
    identification: Identification

    @property
    def correct(self) -> bool:
        return (self.identification.decided
                and self.identification.answer == self.expected)

    @property
    def wrong(self) -> bool:
        """Named a different variety. Informative, and not the same as declining."""
        return (self.identification.decided
                and self.identification.answer != self.expected)

    def as_dict(self) -> dict:
        return {"attestor": self.attestor, "expected": self.expected,
                "correct": self.correct, **self.identification.as_dict()}


def blind_read(client: OpenAICompatClient, attestor: str, *, text: str, blind: Blind,
               expected: str, tag: str, corpus: Corpus,
               messages: tuple[str, str] | None = None) -> BlindRead:
    """P1. Ask one attestor, blind, what variety a text is.

    `expected` is consumed here and goes nowhere near `attest.identify`, which
    has no parameter for it. Scoring happens after the answer comes back.
    """
    ident = attest.identify(client, attestor, text=text, blind=blind,
                            messages=messages)
    corpus.write(Record("attestation", attestor, tag, None, None,
                        f"[blind identification: {', '.join(blind.options)}]",
                        ident.raw, ident.error,
                        meta={"answer": ident.answer, "expected": expected,
                              "fabricated": ident.fabricated}))
    return BlindRead(attestor, expected, ident)


@dataclass
class ContrastRead:
    """P2. One attestor's reading of a pair, scored against the true assignment."""

    attestor: str
    expected_first: str
    expected_second: str
    separation: Separation

    @property
    def correct(self) -> bool:
        """Both labels right, accounting for the order the attestor saw.

        `swapped` is the only reason this is not a plain comparison: the pair is
        presented in an order salted by the texts, so that an attestor answering
        the options in the order they were listed cannot score 100% by habit.
        """
        s = self.separation
        if not s.split:
            return False
        first, second = (s.b, s.a) if s.swapped else (s.a, s.b)
        return first == self.expected_first and second == self.expected_second

    @property
    def merged(self) -> bool:
        """Said the two are the same variety. The negative, and a real answer —
        the one the Russian null control must be able to produce."""
        return self.separation.merged

    def as_dict(self) -> dict:
        return {"attestor": self.attestor, "correct": self.correct,
                "expected": [self.expected_first, self.expected_second],
                **self.separation.as_dict()}


def contrast_read(client: OpenAICompatClient, attestor: str, *, first: str, second: str,
                  blind: Blind, expected_first: str, expected_second: str,
                  tag: str, corpus: Corpus,
                  messages: tuple[str, str] | None = None) -> ContrastRead:
    """P2. Ask one attestor, blind, to tell two varieties apart."""
    sep = attest.separate(client, attestor, first=first, second=second,
                          blind=blind, messages=messages)
    corpus.write(Record("contrast", attestor, tag, None, None,
                        f"[blind pair: {', '.join(blind.options)}]",
                        sep.raw, sep.error,
                        meta={"a": sep.a, "b": sep.b, "swapped": sep.swapped,
                              "expected": [expected_first, expected_second],
                              "fabricated": sep.fabricated}))
    return ContrastRead(attestor, expected_first, expected_second, sep)


# -- P5: aggregating what grounding found -------------------------------------

@dataclass
class Grounding:
    """P5. How much of an attestor's cited evidence was real.

    Not a call of its own: `attest.identify` and `attest.separate` ground their
    own results, and this counts what they found. A fabricated marker is
    evidence about the *attestor*, so it belongs beside that attestor's verdict
    rather than in a probe of its own.
    """

    cited: int = 0
    fabricated: int = 0

    @property
    def rate(self) -> float | None:
        """Share of cited markers actually present. None when none were cited —
        which is not the same as zero, and must not be read as a failure."""
        return None if not self.cited else 1.0 - (self.fabricated / self.cited)

    @property
    def unreliable(self) -> bool:
        """Cited evidence that is not there at all.

        A single invented quotation is enough: a model that fabricates one
        marker to support a verdict has told us the verdict was not read off the
        text, which is the whole claim it was making.
        """
        return self.fabricated > 0

    def as_dict(self) -> dict:
        return {"cited": self.cited, "fabricated": self.fabricated,
                "rate": None if self.rate is None else round(self.rate, 3),
                "unreliable": self.unreliable}


def grounding(results) -> Grounding:
    """Tally markers and fabrications over any mix of reads."""
    out = Grounding()
    for r in results:
        inner = getattr(r, "identification", None) or getattr(r, "separation", None)
        if inner is None:
            continue
        out.cited += len(inner.markers)
        out.fabricated += len(inner.fabricated)
    return out


# -- the verdict, aggregated from what the probes found -----------------------

@dataclass
class DialectResult:
    """One variety, one producer: every probe's outcome and what they add up to.

    Aggregation is a transparent function of the per-probe outcomes and nothing
    else — no weights, no tuned coefficients, no number that cannot be traced
    back to an observation. A reader who distrusts the verdict can read the
    probes; a reader who distrusts the probes can read the raw texts the corpus
    kept. That chain is the deliverable, more than the verdict is.
    """

    tag: str
    engine: str
    designator: str | None = None
    conversions: "Consistency | None" = None
    #: P3, as a 2×2. `None` until both arms have been produced.
    square: "Square | None" = None
    #: The engine wrote the same text under both designators. A finding, settled
    #: free and before any attestor is paid: it did not mark the variety when
    #: asked to write it, and a contrast pair that is one text twice cannot be
    #: shown to anybody — the order control degenerates into a second identity
    #: control and qualifies attestors without testing them.
    identical_arms: bool = False
    reads: list[BlindRead] = field(default_factory=list)
    contrasts: list[ContrastRead] = field(default_factory=list)
    panel: "Panel | None" = None
    notes: list[str] = field(default_factory=list)
    #: The corpus file this run wrote to, and the sibling it was contrasted
    #: against. Carried on the result so a stored record can point back at the
    #: text without the caller having to reconstruct either.
    corpus_path: str | None = None
    sibling_tag: str | None = None

    # -- what the paid half concluded ----------------------------------------

    @property
    def identified(self) -> "Consensus":
        from polyglot.core.probe.panel import Consensus, Vote

        return Consensus([
            Vote(r.attestor, r.correct,
                 abstained=not r.identification.decided)
            for r in self.reads])

    @property
    def separated(self) -> "Consensus":
        from polyglot.core.probe.panel import Consensus, Vote

        return Consensus([
            Vote(r.attestor, r.correct, abstained=r.merged or not r.separation.ok)
            for r in self.contrasts])

    @property
    def grounding(self) -> Grounding:
        return grounding([*self.reads, *self.contrasts])

    # -- what the free half concluded ----------------------------------------

    @property
    def converts(self) -> bool:
        return bool(self.square and self.square.moved)

    @property
    def systematic(self) -> bool:
        """Reciprocal rather than improvised. The square's mirror check
        replaced P4's repeat: running the same prompt twice asks whether the
        model repeats itself, which a stubborn improviser also does."""
        return bool(self.square and self.square.mirrored)

    # -- the ladder ----------------------------------------------------------

    @property
    def verdict(self):
        """Where this variety lands, from the probes and nothing else.

        Order of the checks is the whole of the logic, so it is written out
        rather than scored:

        1. **The two arms came back identical -> `no-evidence`.** Checked
           first, free, and before a single attestor is paid. The engine wrote
           the same text under both designators, which is a direct negative
           about production — and everything below it would be measuring a pair
           that is one text twice.
        2. **No competent attestor, or one that fabricated its evidence ->
           `indeterminate`**, unless the square already settled it. "Nobody
           could check" is not "the model cannot do it".
        3. **Every qualified attestor abstained -> `indeterminate`.** Zero
           counted votes is the same epistemic state as an empty panel: nothing
           was measured. Reporting it as `no-evidence` convicted the model for
           the panel's silence.
        4. **The square distinguishes, and an attestor named the variety blind
           -> `full-command`.** The model marks the difference on demand in both
           directions *and* a third party recognised it unprompted.
        5. **The square distinguishes -> `production`**, with no panel needed.
           Certain by construction: converting a text into the variety it was
           written in is a no-op, into the other one is not, and the two
           directions mirror. Confidence is capped at `medium` because nothing
           independent corroborated it.
        6. **Blind identification confirmed -> `production`.** A third party
           named the variety with no label and no prompting.
        7. **Separation without identification -> `recognition-only`.**
        8. **Otherwise `no-evidence`**, the weak negative.
        """
        from polyglot.core.probe.variant import DialectVerdict

        if self.identical_arms:
            return DialectVerdict.NO_EVIDENCE

        square_says = bool(self.square and self.square.distinguishes)

        if self.panel is None or self.panel.empty:
            return (DialectVerdict.PRODUCTION if square_says
                    else DialectVerdict.INDETERMINATE)
        if self.grounding.unreliable:
            return (DialectVerdict.PRODUCTION if square_says
                    else DialectVerdict.INDETERMINATE)

        if self.identified.agreed is True:
            return (DialectVerdict.FULL_COMMAND if square_says
                    else DialectVerdict.PRODUCTION)
        if square_says:
            return DialectVerdict.PRODUCTION
        if self.silent:
            return DialectVerdict.INDETERMINATE
        if self.separated.agreed is True:
            return DialectVerdict.RECOGNITION_ONLY
        return DialectVerdict.NO_EVIDENCE

    @property
    def silent(self) -> bool:
        """No qualified attestor answered either probe.

        Distinct from disagreement, which `Consensus.agreed` also reports as
        `None`: a split panel measured something and could not settle it, a
        silent one measured nothing at all.
        """
        return not self.identified.counted and not self.separated.counted

    @property
    def confidence(self) -> str:
        """How much weight the verdict carries.

        A single qualified attestor is `low` however clean its answer: one
        judge is one opinion, and the panel rule says a single-member verdict is
        never silently promoted to a panel result. A split panel is also `low`,
        because the disagreement *is* the finding.
        """
        if self.identical_arms:
            # Free, deterministic and unambiguous: the model produced one text
            # for two designators. Nothing about that reading is uncertain.
            return "high"
        if self.panel is None or self.panel.empty or self.panel.single:
            return "medium" if self.square and self.square.distinguishes else "low"
        if self.identified.split or self.separated.split:
            return "low"
        # **Counted votes, not panel members.** Three attestors of whom two
        # abstained is one opinion, and `panel.single` cannot see that: it
        # counts who was asked. A run reported `production` at high confidence
        # off a single non-abstaining vote, which is what this closes.
        if len(self.identified.counted) < 2:
            return "medium" if self.square and self.square.distinguishes else "low"
        return "high" if self.identified.agreed is not None else "medium"

    def as_dict(self) -> dict:
        return {
            "tag": self.tag, "engine": self.engine, "designator": self.designator,
            "verdict": self.verdict.value, "confidence": self.confidence,
            "identified": self.identified.as_dict(),
            "separated": self.separated.as_dict(),
            "conversion": self.square.as_dict() if self.square else None,
            "identical_arms": self.identical_arms,
            "grounding": self.grounding.as_dict(),
            "panel": self.panel.as_dict() if self.panel else None,
            "notes": list(self.notes),
        }


def attest_tone(ok: bool | None) -> str:
    """Three states, because an abstention is not a failure."""
    return "ok" if ok is True else "warn" if ok is None else "bad"


def _producer_capability(out: DialectResult, lang: Language):
    """What the battery concluded about the engine, as a durable record.

    `qualified` is the positive half of the ladder -- production or better --
    so a reader asking "can this model write this variety" gets a boolean, and
    `detail` keeps the level, because `recognition-only` and `no-evidence` are
    both False and are not the same answer.

    `indeterminate` is recorded as not-qualified with its reason, never dropped.
    A gap in the record would read as "never measured", which is a third thing
    again: we measured, and could not tell.
    """
    from polyglot.core.probe.panel import Capability, CapabilityKey, Role

    return Capability(
        key=CapabilityKey(out.engine, out.tag, Role.GENERATION, lang.script),
        qualified=out.verdict in _POSITIVE(),
        confidence=out.confidence,
        probes=["identification", "contrast", "conversion", "consistency"],
        detail=out.verdict.value + (f" — {out.notes[0]}" if out.notes else ""),
        # Where the text is, and enough of the finding to read the row without
        # opening it. A stored verdict with no way back to the output that
        # justifies it is not much of a verdict (convention 6).
        evidence={
            "verdict": out.verdict.value,
            "confidence": out.confidence,
            "designator": out.designator,
            "corpus": out.corpus_path,
            "panel": (out.panel.qualified if out.panel else []),
            "discarded": (out.panel.disqualified if out.panel else []),
            "contrasted_with": out.sibling_tag,
        },
    )


def _POSITIVE():
    from polyglot.core.probe.variant import POSITIVE
    return POSITIVE


def _NOT_A_CLAIM():
    from polyglot.core.probe.variant import NOT_A_CLAIM
    return NOT_A_CLAIM
