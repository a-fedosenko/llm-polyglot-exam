"""The dialect battery's setup, where the alphabet is not the variety.

A digraphic family spans scripts: `sr-ME-Cyrl` and `sr-ME-Latn` are one variety
written twice. Two things follow, and neither is catchable downstream — the
manufactured controls pass either way, because the texts really are different.
"""
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


def test_the_conversion_source_is_asked_for_in_the_arms_alphabet(scheme):
    """The bug this closes.

    `sr` is Cyrillic, so a Latin-script battery was handed Cyrillic text to
    rewrite. The model transliterated it — correctly, because it had been told
    to write Latin — every token moved, and the conversion scored
    `paraphrased, not converted`: a guaranteed false negative with P4
    unscoreable behind it.
    """
    neutral = battery(scheme, "sr-rs-latn", "sr-me-latn").preflight()["neutral"]
    assert neutral["tag"] == "sr", "it is still the umbrella tag"
    assert neutral["script"] == "Latn"
    assert "Latin" in neutral["designator"]
    assert neutral["script_adjusted"] is True, \
        "the override has to be visible: it changes what was sent"


def test_a_same_script_pair_leaves_the_neutral_arm_alone(scheme):
    neutral = battery(scheme, "sr-rs-cyrl", "sr-me-cyrl").preflight()["neutral"]
    assert neutral["script_adjusted"] is False
    assert "Cyrillic" in neutral["designator"]


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
