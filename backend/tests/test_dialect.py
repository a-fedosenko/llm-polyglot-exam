"""The dialect battery: the square, and the pairs it refuses to measure."""
from __future__ import annotations

import pytest

from polyglot.core.probe.corpus import Corpus
from polyglot.core.scheme import load_scheme
from polyglot.engine.dialect import DialectMachine


@pytest.fixture(scope="module")
def scheme():
    return load_scheme("catmint")


def battery(scheme, tag: str, other: str) -> DialectMachine:
    return DialectMachine(
        scheme=scheme, tag=tag, engine="acme-a", client=None,
        attestors=["other-b"],
        varieties=[scheme.get(tag), scheme.get(other)],
        neutral_tag="sr", scenario="A woman missed the morning bus.",
        corpus=Corpus(to_db=False))


def test_arms_in_two_alphabets_are_refused(scheme):
    """A control model told to tell Cyrillic from Latin would be right for a
    reason that has nothing to do with Montenegrin — and it would read as
    dialect knowledge exactly where a false positive costs most."""
    with pytest.raises(ValueError, match="different alphabets"):
        battery(scheme, "sr-rs-latn", "sr-me-cyrl")


def test_the_subject_is_still_asked_under_its_own_designator(scheme):
    """Only the neutral arm's script is overridden. The arms are asked as
    themselves, region and all."""
    pre = battery(scheme, "sr-rs-latn", "sr-me-latn").preflight()
    by_tag = {v["tag"]: v["designator"] for v in pre["varieties"]}
    assert by_tag["sr-rs-latn"] == "Serbian (Serbia, Latin script)"
    assert by_tag["sr-me-latn"] == "Serbian (Montenegro, Latin script)"


def test_the_blind_menu_is_regions_not_scripts(scheme):
    """Both arms share an alphabet, so what a control model is asked to name is
    the region — which is the thing under test."""
    pre = battery(scheme, "sr-rs-latn", "sr-me-latn").preflight()
    assert sorted(pre["options"]) == ["Montenegro", "Serbia"]


# -- the square ----------------------------------------------------------------

from polyglot.core.probe.dialect import Conversion, Square, diff  # noqa: E402


def cell(before: str, after: str) -> Conversion:
    c = Conversion(designator="x", before=before, after=after)
    c.edits, c.rate = diff(before, after)
    return c


def square(aa, ab, bb, ba) -> Square:
    sq = Square(subject="A", sibling="B")
    sq.cells[("A", "A")] = cell(*aa)
    sq.cells[("A", "B")] = cell(*ab)
    sq.cells[("B", "B")] = cell(*bb)
    sq.cells[("B", "A")] = cell(*ba)
    return sq


SAME = ("Žena je propustila jutarnji autobus. Pešačila je do posla po kiši. "
        "Kada je stigla, kancelarija je bila zatvorena.")

TA = "lepo mleko i voz"
TB = "lijepo mlijeko i voz"


def test_a_clean_square_distinguishes():
    """Diagonals unchanged, off-diagonals moved: the model marks the difference
    on demand, in both directions, with no attestor involved."""
    sq = square((TA, TA), (TA, TB), (TB, TB), (TB, TA))
    assert sq.stable and sq.moved
    assert sq.distinguishes
    assert sq.reading == "distinguishes"


def test_the_two_directions_are_checked_for_being_mirrors():
    """`lepo -> lijepo` one way and `lijepo -> lepo` the other is knowledge of a
    specific alternation. Two unrelated edit sets are improvisation twice."""
    sq = square((TA, TA), (TA, TB), (TB, TB), (TB, TA))
    assert sq.mirror == 1.0 and sq.mirrored

    noise = square((TA, TA), (TA, "lepo mleko i vlak"),
                   (TB, TB), (TB, "lijepo mlijeko i tramvaj"))
    assert noise.distinguishes, "it still moved both ways"
    assert not noise.mirrored, "but the two directions do not undo each other"


def test_changing_nothing_anywhere_is_not_a_distinction():
    sq = square((TA, TA), (TA, TA), (TB, TB), (TB, TB))
    assert not sq.distinguishes
    assert sq.reading == "no-distinction"


def test_changing_everything_including_the_diagonal_is_improvisation():
    """The failure a single conversion could never tell from real command: a
    model that rewrites something every time it is asked."""
    sq = square((TA, "potpuno drugi tekst ovdje"), (TA, TB),
                (TB, "sasvim drukciji tekst tamo"), (TB, TA))
    assert not sq.stable
    assert not sq.distinguishes
    assert sq.reading == "improvisation"


def test_a_half_built_square_claims_nothing():
    sq = Square(subject="A", sibling="B")
    sq.cells[("A", "B")] = cell(TA, TB)
    assert not sq.complete and not sq.distinguishes
    assert sq.reading == "incomplete"


# -- identical arms ------------------------------------------------------------

def test_identical_arms_stop_the_battery_before_any_attestor(scheme, monkeypatch):
    """One text under two designators is not a contrast pair.

    Shown to a panel it turns the order control into a second identity control
    — with both halves the same there is nothing to follow but the slot — so
    attestors get qualified without being tested. A real run did that and
    reported `production` off a single vote.
    """
    from conftest import ScriptedClient

    m = battery(scheme, "sr-rs-latn", "sr-me-latn")
    # Real Serbian, so the gate passes and the stop is about the *pair* rather
    # than about one arm failing to be produced at all.
    m.client = ScriptedClient({"generation": SAME})
    for _ in range(12):
        planned = m.plan()
        if planned is None:
            break
        m.execute(planned)

    out = m.result()
    assert out.identical_arms is True
    assert out.verdict.value == "no-evidence"
    assert out.confidence == "high", "nothing about this reading is uncertain"
    assert m.state().calls["control"] == 0, "no attestor was paid"


# -- the reference variety -----------------------------------------------------

from polyglot.core import reference as ref  # noqa: E402


def test_a_recorded_reference_is_found_by_region_not_by_tag_spelling(scheme):
    """Region, because a region code is the same fact in every locale list
    while tag spellings are not."""
    r = ref.reference_for(scheme, "srp|Latn")
    assert r.tag == "sr-rs-latn" and r.basis == "recorded"
    assert ref.reference_for(scheme, "srp|Cyrl").tag == "sr-rs-cyrl", \
        "one region, resolved separately in each script class"


def test_the_shipped_list_does_not_contradict_itself(scheme):
    """A row whose class key and ISO code disagree is a phantom language.

    `zh-sg` was filed under `zh|Hans` while every other Simplified row was
    `zho|Hans`, so Singapore and Macao formed a two-member family with no
    baseline and Chinese was measured twice; `de-be` carried the code for Ewe,
    which would have convicted perfect German of being the wrong language. Both
    were invisible until somebody read the rows side by side. `polyglot scheme`
    is the command that reads them; this is the same check, in CI.
    """
    own_codes = {l.iso639_3 for l in scheme.languages.values() if l.iso639_3}
    contains: dict[str, set[str]] = {}
    for l in scheme.languages.values():
        if l.members and l.iso639_3:
            contains.setdefault(l.iso639_3, set()).update(l.members)

    wrong = []
    for tag, l in scheme.languages.items():
        if not l.iso639_3:
            continue
        primary = l.cls.partition("|")[0]
        filed = (primary == l.iso639_3 or primary == l.macro
                 or l.iso639_3 in contains.get(primary, ()))
        # A class key naming a code the list has no row for is a macrolanguage
        # grouping (`zza` over `diq`) and cannot be checked from inside.
        if not filed and primary in own_codes:
            wrong.append((tag, l.cls, l.iso639_3))
    assert not wrong, wrong

    # `scope`, `type` and `members` describe the code, so rows of one code have
    # to agree about it — or the gate accepts different answers for two rows of
    # one language.
    shapes: dict[str, set] = {}
    for l in scheme.languages.values():
        if l.iso639_3:
            shapes.setdefault(l.iso639_3, set()).add(
                (l.scope, len(l.members or [])))
    split = {code: s for code, s in shapes.items() if len(s) > 1}
    assert not split, split


def test_every_family_in_the_shipped_list_is_written_down(scheme):
    """The file used to hold only the exceptions, and a family nobody had
    thought about was therefore invisible — Catalan sat on `ca-AD` by
    alphabetical accident with nothing on the page saying so. One entry per
    family, rule-derivable or not, is what makes the open questions countable
    in one place."""
    cov = ref.coverage(scheme)
    assert cov["unlisted"] == [], \
        "a contrastable family with no entry falls back to list order"
    # The subtag rule still resolves a family this file does not carry, which
    # is what keeps somebody else's locale list working.
    r = ref.reference_for(scheme, "por|Latn")
    assert r.tag == "pt-pt" and r.basis == "recorded"
    assert ref.reference_for(scheme, "rus|Cyrl").tag == "ru-ru"


def test_a_region_that_matches_two_members_falls_through_to_a_tag(scheme):
    """`aze|Latn` holds az-AZ-Latn and az-AZ-Cyrl — a Cyrillic tag filed under
    a Latin class. Region AZ matches both, so the entry names the tag; picking
    the Cyrillic one would make every contrast measure the alphabet."""
    r = ref.reference_for(scheme, "aze|Latn")
    assert r.tag == "az-az-latn" and r.basis == "recorded"


def test_a_written_but_unconfirmed_reference_is_not_a_decision(scheme, monkeypatch):
    """`reviewed: false` is a claim with a reason attached and nobody behind
    it. It must not read as settled, and a scan must not take it by default.

    The overlay is supplied here rather than read from the shipped file: what
    is pinned is the rule, and the file's review state is the operator's to
    move."""
    monkeypatch.setattr(ref, "_overlay", lambda: {
        "afr|Latn": {"baseline": "af-ZA", "why": "proposed", "reviewed": False}})
    r = ref.reference_for(scheme, "afr|Latn")
    assert r.tag == "af-za", "resolved by meaning, not by spelling"
    assert r.basis == "proposed"
    assert r.decided is False, "a proposal must never read as a decision"

    monkeypatch.setattr(ref, "_overlay", lambda: {
        "afr|Latn": {"baseline": "af-ZA", "why": "confirmed", "reviewed": True}})
    assert ref.reference_for(scheme, "afr|Latn").basis == "recorded"


def test_a_baseline_from_another_family_is_not_a_baseline(scheme, monkeypatch):
    """A tag from outside the class is a typo, not a decision. Resolving it
    would hold Portuguese against English and report a confirmed reference
    while doing it."""
    monkeypatch.setattr(ref, "_overlay", lambda: {
        "por|Latn": {"baseline": "en-GB", "why": "typo", "reviewed": True}})
    r = ref.reference_for(scheme, "por|Latn")
    assert r.tag != "en-gb"
    assert r.basis in ("rule", "provisional"), \
        "an unresolvable entry falls through rather than crossing families"


def test_a_family_with_no_entry_falls_back_and_says_it_is_standing_in(scheme,
                                                                      monkeypatch):
    """The fallback still exists for locale lists this file does not cover."""
    monkeypatch.setattr(ref, "_overlay", lambda: {})
    r = ref.reference_for(scheme, "afr|Latn")
    assert r.basis == "provisional" and r.decided is False


def test_every_baseline_is_written_in_the_conventional_form(scheme):
    """`en-US`, not `en-us`. Nothing resolves on the spelling — `_resolve`
    reads subtags — so this is purely about the file being recognisable to the
    person reviewing it, and about not implying it is tied to one list's
    conventions."""
    odd = []
    for cls, entry in ref._overlay().items():
        baseline = entry.get("baseline")
        if not baseline:
            continue
        lang = ref._resolve(scheme, cls, baseline)
        if lang is not None and baseline != ref.canonical_tag(lang):
            odd.append((cls, baseline, ref.canonical_tag(lang)))
    assert not odd, odd


def test_each_entry_describes_itself_truthfully(scheme):
    """`language`, `baseline` and `varieties` are a reading aid — the resolver
    ignores them — which is exactly why they have to be checked: a file whose
    readable half has quietly gone wrong is a file nobody can review. Fix with
    `polyglot references --annotate`."""
    wrong = []
    for cls, entry in ref._overlay().items():
        r = ref.reference_for(scheme, cls)
        lang = scheme.get(r.tag) if r.tag else None
        region = (lang.region_name or lang.region) if lang else None
        want = {
            "language": next((l.name for l in scheme.languages.values()
                              if l.cls == cls), None),
            "script": cls.partition("|")[2] or None,
            "baseline_title": (None if lang is None else
                               f"{lang.name} ({region})" if region else lang.name),
        }
        for key, is_ in want.items():
            if key in entry and entry[key] != is_:
                wrong.append((f"{cls}.{key}", entry[key], is_))
        if entry.get("baseline") and r.basis not in ("recorded", "proposed"):
            wrong.append((f"{cls}.baseline", entry["baseline"],
                          "not in this locale list"))
    assert not wrong, wrong


def test_coverage_counts_only_families_that_can_be_contrasted(scheme):
    cov = ref.coverage(scheme)
    assert cov["families"] == cov["decided"] + len(cov["unconfirmed"])
    assert "srp|Latn" not in cov["unconfirmed"]


# -- the evidence an operator overrules ----------------------------------------

def test_every_attestor_answer_carries_the_reply_it_was_read_from():
    """The parsed label is this application's *reading* of a reply.

    An operator correcting a judgement cannot do it from the judgement: a model
    that hedged in prose, that named a region which was not on the menu, and one
    that was simply mis-parsed all reduce to the same label. The reply was in
    the corpus on disk the whole time, which is no help while looking at the
    step that produced it.
    """
    from polyglot.core.probe.attest import Identification, Separation
    from polyglot.core.probe.panel import ControlKind, ControlResult

    ident = Identification(answer="Serbia", raw='{"answer": "Serbia"}')
    assert ident.as_dict()["raw"] == '{"answer": "Serbia"}'

    sep = Separation(a="Serbia", b="Montenegro", raw="…both answers…")
    assert sep.as_dict()["raw"] == "…both answers…"

    ctl = ControlResult(ControlKind.ORDER, False, "followed the position", "…")
    assert ctl.as_dict()["raw"] == "…", \
        "a failed control discards a model for the whole family — the answer " \
        "it gave is the evidence for that"


# -- the language check cannot referee a dialect --------------------------------

def test_an_abstaining_language_check_keeps_the_text(scheme):
    """`sr-ME-Latn` abstains on bos, cnr, hbs and hrv — its own siblings.

    That abstention is the expected answer for a pluricentric family and says
    nothing about the text; it is the reason the battery asks attestors instead
    of a classifier. Voiding the generation on it killed the whole family
    whenever LID happened to name a sibling.
    """
    from polyglot.core.probe.gate import GateResult, GateVerdict
    from polyglot.core.probe.ladder_support import indistinguishable_codes

    assert "bos" in indistinguishable_codes(scheme.get("sr-me-latn"))

    abstention = GateResult(GateVerdict.LOW_CONFIDENCE)
    assert abstention.abstained is True
    assert abstention.passed is False
    assert abstention.is_negative is False, \
        "an abstention is not evidence against the model"

    for verdict in (GateVerdict.REFUSED, GateVerdict.WRONG_SCRIPT,
                    GateVerdict.DEGENERATE):
        assert GateResult(verdict).abstained is False, \
            f"{verdict} is a real negative and must still void the generation"


# -- a reply that ran out of budget --------------------------------------------

def test_a_truncated_reply_keeps_its_verdict_and_loses_only_citations():
    """An unreadable control reply disqualifies an attestor for a whole family.

    The verdict is the first thing in the object and the citations are the
    tail, so a reply cut off mid-citation still contains the answer. Throwing
    it away published a failure of the instrument's token budget as a failure
    of the model — the one mistake this project refuses to make.
    """
    from polyglot.core.probe.attest import _parse

    cut = ('{"a": "Serbia", "b": "Montenegro", "markers": ['
           '{"text": "Dete", "in": "A", "why": "Ekavian reflex"}, '
           '{"text": "mleko", "in": "A", "why": "Ekavian ref')
    data, err, repaired = _parse(cut)
    assert err is None and repaired is True
    assert data["a"] == "Serbia" and data["b"] == "Montenegro"
    assert len(data["markers"]) == 2, "the partial citation is dropped, not kept"
    assert "why" not in data["markers"][1]


def test_repair_only_ever_drops_content():
    from polyglot.core.probe.attest import _parse

    for cut, kept in (('{"a": "Serbia", "b": "Mont', {"a": "Serbia"}),
                      ('{"a": "Serbia", "b": "Montenegro", "mar',
                       {"a": "Serbia", "b": "Montenegro"})):
        data, err, repaired = _parse(cut)
        assert err is None and repaired is True
        assert data == kept, "a repair may shorten a reply, never alter it"


def test_an_intact_reply_is_not_marked_repaired():
    from polyglot.core.probe.attest import _parse

    data, err, repaired = _parse('{"a": "Serbia", "b": "Montenegro", "markers": []}')
    assert err is None and repaired is False


def test_prose_is_still_unparseable():
    """Repair must not turn a refusal into an answer."""
    from polyglot.core.probe.attest import _parse

    data, err, _ = _parse("I am not able to judge this.")
    assert data is None and "no JSON object" in err
