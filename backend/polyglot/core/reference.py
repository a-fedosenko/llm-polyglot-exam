"""Which variety a family is contrasted against.

A dialect battery needs a *pair*, and a family of 26 regional tags has 325 of
them. Naming one variety as the reference turns that into 25 contrasts — every
other variety measured against the same baseline, which is also what makes two
of those results comparable to each other.

**It is the contrast baseline, not the correct variety.** Nothing here is a
claim about prestige, correctness or priority; it is a statement about what a
measurement is relative to, and the file is read by people whose own variety is
on the other side of it.

Four ways a reference can be known, and they are not the same answer:

    recorded     a person wrote it down in `references/reference-varieties.json`,
                 with the reason. Entries name a REGION rather than a tag — a
                 region code is the same fact in every locale list, while tag
                 spellings are not (`sr-rs-latn` in one, `sr-Latn-RS` in
                 another). Seven classes hold two language subtags in one
                 region (`ak-gh`, `tw-gh`) and name an explicit tag instead.
                 Includes the deliberate `null`: *this family has no defensible
                 reference* is a finished decision.
    rule         the language subtag equals the region subtag — `pt`/`PT`,
                 `ru`/`RU`, `es`/`ES`. Derivable, so it is not recorded; 12 of
                 the 57 contrastable classes in the shipped catalogue resolve
                 this way.
    provisional  neither of the above, so the first member of the class is used
                 to keep the battery runnable. **Marked, everywhere it is
                 shown.** A provisional reference is a placeholder for a
                 decision nobody has made, and a page that rendered it like a
                 recorded one would turn an open question into a silent claim.
    none         a recorded `null`, or a class with nothing to reference.

The distinction between `recorded` and `provisional` is the whole reason this
module exists rather than a one-line `sorted(members)[0]`: it is what makes the
remaining work countable, the same way `variant.py` separates a finished
`not-distinguishable` decision from an unfilled `untested` gap.
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
    basis: str                      # recorded | rule | provisional | none
    why: str = ""
    reviewed: bool = False

    @property
    def decided(self) -> bool:
        """Whether a person settled this. A `rule` result counts: the rule is
        itself a recorded decision, applied uniformly, rather than a guess."""
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
    does, which is what keying entries on the region avoids. A per-scheme file
    would have meant re-deciding Arabic every time somebody brought their own
    list.

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


def reference_for(scheme: Scheme, cls: str) -> Reference:
    """The reference variety of one class, and the basis for it."""
    members = _members(scheme, cls)
    recorded = _overlay().get(cls)
    if recorded is not None:
        why = recorded.get("why", "")
        reviewed = bool(recorded.get("reviewed"))
        region, tag = recorded.get("region"), recorded.get("tag")

        if region is None and tag is None:
            # A deliberate `null`: this family has no defensible reference.
            # A finished decision, and not the same thing as an absent entry.
            return Reference(cls, None, "none", why, reviewed)

        if region is not None:
            hits = [l for l in members
                    if (l.region or "").lower() == str(region).lower()]
            if len(hits) == 1:
                return Reference(cls, hits[0].tag, "recorded", why, reviewed)
            # Ambiguous or absent. Seven classes in the shipped catalogue hold
            # two language subtags in one region — `ak-gh` and `tw-gh` — and
            # those have to name a tag. Falling through to `tag` rather than
            # picking one of them: a reference chosen by list order is a
            # provisional reference, and it must be labelled as one.

        if tag is not None and scheme.get(tag) is not None:
            return Reference(cls, scheme.get(tag).tag, "recorded", why, reviewed)
        # A recorded tag this list does not carry is a stale decision, not a
        # reference. Falling through rather than returning it: a battery handed
        # an unknown tag fails later with a worse message.

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
    provisional: list[str] = []
    total = 0
    for cls in sorted({l.cls for l in scheme.languages.values()}):
        if len(_members(scheme, cls)) < 2:
            continue
        total += 1
        ref = reference_for(scheme, cls)
        by_basis[ref.basis] = by_basis.get(ref.basis, 0) + 1
        if ref.basis == "provisional":
            provisional.append(cls)
    return {"families": total, "by_basis": by_basis,
            "decided": sum(n for b, n in by_basis.items() if b != "provisional"),
            "provisional": provisional}
