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


def test_the_rule_covers_the_families_that_need_no_entry(scheme):
    r = ref.reference_for(scheme, "por|Latn")
    assert r.tag == "pt-pt" and r.basis == "rule"
    assert ref.reference_for(scheme, "rus|Cyrl").tag == "ru-ru"


def test_a_region_that_matches_two_members_falls_through_to_a_tag(scheme):
    """`aze|Latn` holds az-AZ-Latn and az-AZ-Cyrl — a Cyrillic tag filed under
    a Latin class. Region AZ matches both, so the entry names the tag; picking
    the Cyrillic one would make every contrast measure the alphabet."""
    r = ref.reference_for(scheme, "aze|Latn")
    assert r.tag == "az-az-latn" and r.basis == "recorded"


def test_an_undecided_family_is_provisional_and_says_so(scheme):
    r = ref.reference_for(scheme, "afr|Latn")
    assert r.basis == "provisional"
    assert r.decided is False, "a placeholder must never read as a decision"


def test_coverage_counts_only_families_that_can_be_contrasted(scheme):
    cov = ref.coverage(scheme)
    assert cov["families"] == cov["decided"] + len(cov["provisional"])
    assert "srp|Latn" not in cov["provisional"]


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
