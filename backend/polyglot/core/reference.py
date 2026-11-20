"""Which variety a family is contrasted against.

A dialect battery needs a *pair*, and a family of 26 regional tags has 325 of
them. Naming one variety as the reference turns that into 25 contrasts — every
other variety measured against the same baseline, which is also what makes two
of those results comparable to each other.

**It is the contrast baseline, not the correct variety.** Nothing here is a
claim about prestige, correctness or priority; it is a statement about what a
measurement is relative to, and the file is read by people whose own variety is
on the other side of it.

Five ways a reference can be known, and they are not the same answer:

    recorded     an entry in `references/reference-varieties.json` that a
                 person has confirmed (`reviewed: true`). An entry names the
                 baseline as a TAG, which is what a reader recognises — and it
                 is resolved by what the tag *means* rather than how it is
                 spelled, so `sr-Latn-RS` finds `sr-rs-latn` in a list that
                 writes it the other way round.
    proposed     an entry with a reason that nobody has confirmed yet
                 (`reviewed: false`). It is a written claim rather than an
                 accident, and it is still not a decision — a scan leaves these
                 out unless asked for them by name.
    rule         the language subtag equals the region subtag — `pt`/`PT`,
                 `ru`/`RU`, `es`/`ES`. **A fallback, not the shipped path**:
                 every family in the shipped list is written down, rule or not,
                 so that the file is one place to read. This is what keeps
                 somebody else's locale list working.
    provisional  none of the above, so the first member of the class is used to
                 keep the battery runnable. **Marked, everywhere it is shown.**
                 A provisional reference is a placeholder for a decision nobody
                 has made, and a page that rendered it like a recorded one
                 would turn an open question into a silent claim.
    none         a recorded `null`, or a class with nothing to reference.

The distinction between a *confirmed* reference and an unconfirmed one is the
whole reason this module exists rather than a one-line `sorted(members)[0]`: it
is what makes the remaining work countable, the same way `variant.py` separates
a finished `not-distinguishable` decision from an unfilled `untested` gap.

**Why the file lists families a rule could derive.** It used to hold only the
exceptions, and the families nobody had thought about were therefore invisible
— Catalan sat with `ca-AD` as its baseline, by alphabetical accident, and
nothing on the page said a person had never looked. One entry per family, rule
or not, is what makes that state readable in one place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from polyglot import paths
from polyglot.core.scheme import Language, Scheme

REFERENCE_FILE = "reference-varieties.json"


@dataclass(frozen=True)
class Reference:
    """The reference variety of one equivalence class, and how it is known."""

    cls: str
    tag: str | None
    basis: str                      # recorded | proposed | rule | provisional | none
    why: str = ""
    reviewed: bool = False

    @property
    def decided(self) -> bool:
        """Whether this baseline is settled. A `rule` result counts: the rule
        is itself a recorded decision, applied uniformly, rather than a guess.
        A `proposed` one does not — it is written down, with a reason, and
        nobody has stood behind it."""
        return self.basis in ("recorded", "rule", "none")

    def as_dict(self) -> dict:
        return {"cls": self.cls, "tag": self.tag, "basis": self.basis,
                "why": self.why, "reviewed": self.reviewed,
                "decided": self.decided}


@lru_cache(maxsize=1)
def _overlay() -> dict:
    """The recorded decisions, or an empty overlay if there are none.

    **One file, for every locale list.** Which variety baselines a family does
    not change when a different list is loaded; only the spelling of its tag
    does, and `_resolve` reads a tag by its subtags rather than by its
    spelling for exactly that reason. A per-scheme file would have meant
    re-deciding Arabic every time somebody brought their own list.

    Version-controlled beside `markers/` rather than in the data directory: a
    reference is a claim that should be argued in review, not a local setting.
    A file of the same name under the data directory overrides it, for working
    on a decision before proposing it.
    """
    for path in (paths.DATA / REFERENCE_FILE, paths.REFERENCES / REFERENCE_FILE):
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {k: v for k, v in raw.items() if not k.startswith("_")}
    return {}


def _class_script(cls: str) -> str | None:
    _, _, script = cls.partition("|")
    return script or None


def _members(scheme: Scheme, cls: str) -> list[Language]:
    """The tags a reference may be chosen from: named, regional, in the class's
    own script.

    The umbrella is excluded because it names no region and a reference is a
    statement about regions. The script filter is not redundant: the shipped
    catalogue files `az-AZ-Cyrl` under `aze|Latn`, and a Cyrillic baseline for
    Latin varieties would make every contrast in that family measure the
    alphabet.
    """
    script = _class_script(cls)
    return sorted(
        (l for l in scheme.languages.values()
         if l.cls == cls and not l.inclusive and l.region
         and (script is None or l.script == script)),
        key=lambda l: l.tag)


def _subtags(text: str) -> tuple[str, str | None, str | None]:
    """A tag's primary subtag, its region and its script, however it is spelled.

    `sr-Latn-RS`, `sr-rs-latn` and `sr_RS_Latn` are the same variety written
    three ways, and which spelling a locale list uses is a property of the
    list. Read positionally rather than by order: a 4-letter subtag is a
    script, a 2-letter or 3-digit one is a region.
    """
    parts = [p for p in text.replace("_", "-").split("-") if p]
    if not parts:
        return "", None, None
    primary, region, script = parts[0].lower(), None, None
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            script = part.title()
        elif len(part) == 2 and part.isalpha() or len(part) == 3 and part.isdigit():
            region = part.upper()
    return primary, region, script


def canonical_tag(lang: Language) -> str:
    """A row written the way BCP-47 writes it: `en-US`, `sr-Cyrl-RS`.

    Locale lists spell tags to suit themselves — the shipped one writes
    `sr-rs-cyrl`, lowercase and region before script — and `baseline` entries
    are written in the conventional form instead. Nothing resolves on the
    spelling (`_resolve` reads subtags, not strings), so the file is free to
    use the form a reader recognises, and using it is also the clearest way of
    saying that the file is not tied to one list's conventions.
    """
    # Only the subtags the row itself carries, in BCP-47 order and casing. The
    # script is *not* added where the list omits it: a locale list writes it
    # where it discriminates — `sr-me-cyrl`, but `en-gb` — and `en-Latn-US` is
    # not a tag anybody recognises, it is a correct tag nobody writes.
    primary, region, script = _subtags(lang.tag)
    parts = [primary or lang.lang.lower()]
    if script:
        parts.append(script)
    if region:
        parts.append(region)
    return "-".join(parts)


def _resolve(scheme: Scheme, cls: str, baseline: str) -> Language | None:
    """The row a recorded baseline names, in whatever this list calls it.

    Exact first, then by what the tag *means* — primary subtag, region and
    script — because a baseline is a claim about a variety and not about a
    spelling. This is what lets one file serve a locale list that writes
    `sr-Latn-RS` and one that writes `sr-rs-latn`; a strict lookup would have
    made the file per-list, which is the thing it exists not to be.
    """
    exact = scheme.get(baseline)
    if exact is not None:
        # **Within the class, always.** A baseline is a statement about which
        # variety of *this* family the others are held against, so a tag from
        # somewhere else is a typo and not a decision — and resolving it would
        # contrast Portuguese against English while reporting a confirmed
        # reference. Falling through makes it unresolvable, which is what
        # `polyglot references --check` reports.
        return exact if exact.cls == cls else None
    primary, region, script = _subtags(baseline)
    hits = [l for l in _members(scheme, cls)
            if l.lang.lower() == primary
            and (region is None or (l.region or "").upper() == region)
            and (script is None or l.script == script)]
    return hits[0] if len(hits) == 1 else None


def reference_for(scheme: Scheme, cls: str) -> Reference:
    """The reference variety of one class, and the basis for it."""
    members = _members(scheme, cls)
    recorded = _overlay().get(cls)
    if recorded is not None:
        why = recorded.get("why", "")
        reviewed = bool(recorded.get("reviewed"))
        baseline = recorded.get("baseline")

        if baseline is None:
            # A deliberate `null`: this family has no defensible reference.
            # A finished decision, and not the same thing as an absent entry.
            return Reference(cls, None, "none", why, reviewed)

        # Confirmed or not, it is the same claim read from the same place; what
        # changes is whether anybody has stood behind it, and a scan treats the
        # two differently.
        basis = "recorded" if reviewed else "proposed"

        hit = _resolve(scheme, cls, str(baseline))
        if hit is not None:
            return Reference(cls, hit.tag, basis, why, reviewed)
        # A baseline this list does not carry is a stale decision, not a
        # reference. Falling through rather than returning it: a battery handed
        # an unknown tag fails later with a worse message, and `polyglot
        # references --check` is what names it here.

    if not members:
        return Reference(cls, None, "none",
                         "nothing in this class names a region")

    for lang in members:
        if lang.lang.lower() == (lang.region or "").lower():
            return Reference(cls, lang.tag, "rule",
                             f"the language subtag equals the region subtag "
                             f"({lang.lang}/{lang.region})")

    return Reference(
        cls, members[0].tag, "provisional",
        "first in the class; nobody has decided this family's reference yet")


def reference_of(scheme: Scheme, lang: Language) -> Reference:
    """The reference this tag would be contrasted against."""
    return reference_for(scheme, lang.cls)


def coverage(scheme: Scheme) -> dict:
    """How much of the list has a decided reference, for counting the work left.

    Only classes with two or more regional members are counted: a class with one
    is not a family anyone can contrast, and including them would make the
    number look better for no reason.
    """
    by_basis: dict[str, int] = {}
    unconfirmed: list[str] = []
    unlisted: list[str] = []
    total = 0
    overlay = _overlay()
    for cls in sorted({l.cls for l in scheme.languages.values()}):
        if len(_members(scheme, cls)) < 2:
            continue
        total += 1
        ref = reference_for(scheme, cls)
        by_basis[ref.basis] = by_basis.get(ref.basis, 0) + 1
        if not ref.decided:
            unconfirmed.append(cls)
        if cls not in overlay:
            unlisted.append(cls)
    return {"families": total, "by_basis": by_basis,
            "decided": sum(1 for cls in sorted({l.cls for l in
                                                scheme.languages.values()})
                           if len(_members(scheme, cls)) >= 2
                           and reference_for(scheme, cls).decided),
            #: Written down but not stood behind. The work queue.
            "unconfirmed": unconfirmed,
            #: Not in the file at all, so a fallback is standing in. For the
            #: shipped list this should be empty, and `polyglot references`
            #: is what says so out loud.
            "unlisted": unlisted}


#: Why a tag cannot be a dialect battery's subject, in the words the page says
#: it in. Codes rather than sentences on the row, so the plan can count them —
#: "nothing here could be measured" and "this one *is* the baseline" are
#: different answers and a tally that merged them would be lying.
BLOCKED: dict[str, str] = {
    "umbrella": "the umbrella tag names no variety — it is the conversion "
                "source, never a contrast arm",
    "no-region": "it names no region, so there is nothing regional to contrast",
    "is-reference": "it *is* the family's reference — the baseline the others "
                    "are measured against, and nothing is measured against "
                    "itself",
    "no-reference": "this family has no defensible reference, which is a "
                    "recorded decision rather than a gap",
    "other-script": "the family's reference is written in another alphabet, and "
                    "a contrast across that line measures the script",
}


@dataclass(frozen=True)
class Pairing:
    """What a dialect battery would contrast one tag against, or why it cannot.

    One definition, two callers: the coverage table renders it and the scan
    plan walks it. Two copies would eventually disagree about which tags are
    testable, and the count beside the scan button would stop matching the rows
    under it — the same rule the facet counts already follow.
    """

    tag: str
    reference: str | None
    basis: str                      # recorded | proposed | rule | provisional | none
    why: str = ""
    blocked: str | None = None      # a key of BLOCKED, or None

    @property
    def testable(self) -> bool:
        return self.blocked is None

    @property
    def decided(self) -> bool:
        """Whether the baseline this would be held against is settled.

        `recorded` and `rule` are decisions, and `none` is the decision that
        the family has no defensible baseline. `proposed` and `provisional`
        are not: one is a written claim awaiting review, the other is list
        order standing in, and a battery run against either measures against
        something nobody has confirmed. Runnable, never silently."""
        return self.basis in ("recorded", "rule", "none")

    def as_dict(self) -> dict:
        return {"tag": self.tag, "reference": self.reference,
                "basis": self.basis, "why": self.why, "blocked": self.blocked,
                "blocked_why": BLOCKED.get(self.blocked or "", ""),
                "testable": self.testable, "decided": self.decided}


def pairing(scheme: Scheme, lang: Language) -> Pairing:
    """The pair a dialect battery would run for this tag, or the reason it
    would refuse.

    Every refusal here is one `DialectMachine.__init__` or `runner.start` would
    raise anyway. Applying the rule in the list rather than at the call is what
    stops a scan of six hundred tags from being six hundred ways to discover a
    rule that was knowable for free.
    """
    ref = reference_for(scheme, lang.cls)
    if lang.inclusive:
        return Pairing(lang.tag, ref.tag, ref.basis, ref.why, "umbrella")
    if not lang.region:
        return Pairing(lang.tag, ref.tag, ref.basis, ref.why, "no-region")
    if ref.tag is None:
        return Pairing(lang.tag, None, ref.basis, ref.why, "no-reference")
    if ref.tag == lang.tag:
        return Pairing(lang.tag, ref.tag, ref.basis, ref.why, "is-reference")
    other = scheme.get(ref.tag)
    # Both arms in one alphabet, or the control model answers from the script
    # and is right for a reason that has nothing to do with the variety. The
    # engine refuses such a pair; refusing it here means never being refused.
    if other is not None and other.script != lang.script:
        return Pairing(lang.tag, ref.tag, ref.basis, ref.why, "other-script")
    return Pairing(lang.tag, ref.tag, ref.basis, ref.why)
