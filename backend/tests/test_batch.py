"""The scan: what it plans, what it refuses, and what it writes.

Every test here is about a rule the design doc states and the schema cannot
enforce — class collapse, the three-state skip rule, the budget failing closed,
and the one that matters most: a scan never approves its own output.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedClient
from polyglot.api.app import app

PT = ("O ônibus atrasou por causa da chuva forte. Ela chegou ao escritório "
      "depois das nove.")


@pytest.fixture
def client(db, monkeypatch):
    answers = {
        "generation": PT,
        "backtranslation": "The bus was late because of heavy rain. She reached "
                           "the office after nine.",
        "judge": lambda n, m, s, p: json.dumps(
            {"verdicts": ["present"] * len(
                [ln for ln in p.splitlines() if ln[:1].isdigit()])}),
    }
    fake = ScriptedClient(answers)
    monkeypatch.setattr("polyglot.engine.runner.build_client", lambda **kw: fake)
    from polyglot.engine import batch, runner
    runner.reset_for_tests()
    batch.reset_for_tests()
    with TestClient(app) as c:
        yield c


PT_CONVERTED = PT.replace("ônibus", "autocarro")


@pytest.fixture
def dialect_client(db, monkeypatch):
    """The same scripted model, answering a battery instead of a ladder.

    The two arms must come back *different*: one text under two designators is
    not a contrast pair, and the battery stops on that before it pays a panel —
    correctly, which would make this test measure nothing.
    """
    def separate(_n, _m, _system, prompt):
        body = prompt.split("TEXT A:", 1)[-1]
        a, b = body.split("TEXT B:", 1)
        if a.strip() == b.strip():
            # The identity control. One text shown as both halves cannot be two
            # varieties, and an attestor that splits it is manufacturing a
            # difference.
            return json.dumps({"a": "indistinguishable",
                               "b": "indistinguishable",
                               "markers": [], "confidence": "high"})
        return json.dumps({"a": "Portugal" if "autocarro" in a else "Brazil",
                           "b": "Portugal" if "autocarro" in b else "Brazil",
                           "markers": [], "confidence": "high"})

    fake = ScriptedClient({
        "generation": lambda _n, _m, s, _p: (
            PT_CONVERTED if "Portugal" in (s or "") else PT),
        "convert": PT_CONVERTED,
        "identify": json.dumps({"answer": "Brazil", "markers": [],
                                "confidence": "high"}),
        "separate": separate,
    })
    monkeypatch.setattr("polyglot.engine.runner.build_client", lambda **kw: fake)
    from polyglot.engine import batch, runner
    runner.reset_for_tests()
    batch.reset_for_tests()
    with TestClient(app) as c:
        yield c


def scan(client, **over) -> dict:
    body = {"engine": "acme-a", "tags": ["pt", "pt-br"],
            "backtranslator_panel": ["other-b"], "judge_model": "other-c"}
    body.update(over)
    return body


def settle(client, batch_id: str, tries: int = 400) -> dict:
    for _ in range(tries):
        snap = client.get(f"/api/batches/{batch_id}").json()
        if snap["status"] not in ("running",):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"batch {batch_id} never settled: {snap}")


# -- the plan ------------------------------------------------------------------

def test_the_plan_costs_nothing(client):
    r = client.post("/api/batches/preflight",
                    json=scan(client, tags=["pt", "pt-br", "es", "es-mx"]))
    assert r.status_code == 200, r.text
    assert r.json()["estimated_calls"]["minimum"] > 0
    assert client.get("/api/batches").json()["batches"] == []


def test_one_class_is_one_experiment_and_the_rest_inherit(client):
    plan = client.post("/api/batches/preflight",
                       json=scan(client, tags=["af", "af-na", "af-za"])).json()
    assert plan["classes"] == 1, "af, af-NA and af-ZA are one experiment"
    assert plan["to_scan"] == 1
    assert plan["inheriting_tags"] == 2
    item = plan["items"][0]
    assert item["representative"] == "af", "the tag naming no region is asked"


def test_divergent_siblings_are_named_rather_than_discovered(client):
    plan = client.post("/api/batches/preflight",
                       json=scan(client, tags=["sr", "sr-rs-cyrl",
                                               "sr-me-cyrl"])).json()
    # The sharp edge the doc refuses to hide: these share `srp|Cyrl` and would
    # now be asked a different designator, so they inherit an answer that was
    # never asked of them.
    assert set(plan["divergent_siblings"]) == {"sr-rs-cyrl", "sr-me-cyrl"}


def test_an_unknown_tag_is_reported_not_silently_dropped(client):
    plan = client.post("/api/batches/preflight",
                       json=scan(client, tags=["pt", "not-a-tag"])).json()
    assert plan["unknown_tags"] == ["not-a-tag"]
    assert plan["requested_tags"] == 1


def test_a_scan_can_be_approved_in_one_gesture_but_never_approves_itself(client):
    """The bulk button is a *person* approving, which is the distinction the
    skip rule rests on: a scan that signed off on its own drafts would skip,
    next pass, everything it had just produced."""
    started = client.post("/api/batches", json=scan(client, tags=["pt", "pt-br"]))
    snap = settle(client, started.json()["id"])
    assert snap["verdicts"]["drafts"] == 2, "measured and inherited, both drafts"
    assert all(not v["approved"]
               for v in client.get("/api/verdicts").json()["verdicts"])

    out = client.post(f"/api/batches/{snap['id']}/approve").json()
    assert out["changed"] == 2
    assert sorted(out["tags"]) == ["pt", "pt-br"]
    assert out["batch"]["verdicts"]["drafts"] == 0

    rows = {v["tag"]: v for v in client.get("/api/verdicts").json()["verdicts"]}
    assert all(v["approved"] for v in rows.values())
    assert rows["pt-br"]["inherited_from"] == "pt", \
        "approving a tier does not turn it into a claim asked of that tag"

    # As reversible as the single-row chip, and by the same call.
    back = client.post(f"/api/batches/{snap['id']}/approve",
                       params={"approved": False}).json()
    assert back["changed"] == 2
    assert all(not v["approved"]
               for v in client.get("/api/verdicts").json()["verdicts"])


def test_approving_a_scan_leaves_rows_it_did_not_pay_for_alone(client):
    """A scan does not own a tag, it owns the runs it paid for — so a verdict
    measured by hand is not swept up by a batch over the same scope."""
    client.post("/api/runs", json={"test_type": "capability", "tag": "es",
                                   "engine": "acme-a", "mode": "auto",
                                   "backtranslator_panel": ["other-b"],
                                   "judge_model": "other-c"})
    for _ in range(100):
        rows = {v["tag"] for v in client.get("/api/verdicts").json()["verdicts"]}
        if "es" in rows:
            break
        time.sleep(0.05)

    started = client.post("/api/batches", json=scan(client, tags=["pt", "pt-br"]))
    snap = settle(client, started.json()["id"])
    out = client.post(f"/api/batches/{snap['id']}/approve").json()
    assert "es" not in out["tags"]


# -- the dialect plan ----------------------------------------------------------
#
# A capability scan walks classes; a dialect battery walks *varieties*, each
# against its family's reference. These pin the rules that turn a locale list
# into that queue — and every one of them is a refusal the engine would raise
# at the call, applied in the plan so a scan of six hundred tags is not six
# hundred ways to discover something knowable for free.

def dialect_scan(client, **over) -> dict:
    body = {"engine": "acme-a", "tags": ["pt", "pt-br", "pt-pt"],
            "test_type": "dialect", "attestors": ["other-b"]}
    body.update(over)
    return body


def test_a_dialect_scan_walks_varieties_against_the_reference(client):
    plan = client.post("/api/batches/preflight",
                       json=dialect_scan(client)).json()
    assert plan["unit"] == "variety"
    by_tag = {i["representative"]: i for i in plan["items"]}
    assert by_tag["pt-br"]["contrast"] == "pt-pt", \
        "the variety is held against its family's reference"
    assert by_tag["pt-br"]["reference_basis"] == "recorded", \
        "pt/PT is written down like every other family, not derived"
    assert by_tag["pt-br"]["skip_reason"] is None
    assert by_tag["pt"]["skip_reason"] == "umbrella", \
        "the umbrella is the conversion source, never a contrast arm"
    assert by_tag["pt-pt"]["skip_reason"] == "is-reference", \
        "nothing is measured against itself"
    assert plan["to_scan"] == 1
    assert plan["inheriting_tags"] == 0, \
        "whether a model marks *this* variety cannot be inherited"


def test_a_dialect_scan_will_not_run_without_a_panel(client):
    """A dialect battery has no judge: the panel of control models is its
    whole instrument, so an empty one is not a scan with a default."""
    r = client.post("/api/batches/preflight",
                    json=dialect_scan(client, attestors=[]))
    assert r.status_code == 400
    assert "control models" in r.json()["detail"]


def test_a_panel_of_the_engines_own_family_is_refused(client):
    r = client.post("/api/batches/preflight",
                    json=dialect_scan(client, attestors=["acme-b"]))
    assert r.status_code == 400
    assert "own family" in r.json()["detail"]


def test_an_unconfirmed_reference_is_not_scanned_unless_it_is_asked_for(
        client, monkeypatch):
    """A baseline nobody has confirmed is runnable and is a claim nobody has
    stood behind — so scanning against it is a deliberate gesture, not a
    default. The overlay is supplied here: the rule is what is pinned, and
    which families are confirmed is the operator's to move."""
    from polyglot.core import reference

    monkeypatch.setattr(reference, "_overlay", lambda: {
        "afr|Latn": {"baseline": "af-ZA", "why": "proposed", "reviewed": False}})
    body = dialect_scan(client, tags=["af-na", "af-za"])
    plan = client.post("/api/batches/preflight", json=body).json()
    assert plan["to_scan"] == 0
    assert plan["skipped"]["unconfirmed-reference"] == 1

    asked = client.post("/api/batches/preflight",
                        json={**body, "include_unconfirmed": True}).json()
    assert asked["to_scan"] == 1
    assert asked["unconfirmed"] == ["af-na"], \
        "named rather than counted: a reader has to see which"


def test_a_dialect_scan_measures_the_variety_and_nothing_inherits(dialect_client):
    c = dialect_client
    started = c.post("/api/batches", json=dialect_scan(c))
    assert started.status_code == 201, started.text
    snap = settle(c, started.json()["id"])
    assert snap["status"] == "done", snap

    item = next(i for i in snap["items"] if i["representative"] == "pt-br")
    assert item["contrast"] == "pt-pt", "the pair is on the row, not re-derived"
    assert item["run_id"], "the item opens on the run that produced it"

    rows = {v["tag"]: v for v in c.get("/api/verdicts").json()["verdicts"]}
    assert rows["pt-br"]["test_type"] == "dialect"
    assert rows["pt-br"]["approved"] is False, "a scan never signs off on itself"
    assert "pt-pt" not in rows and "pt" not in rows, \
        "a dialect claim is about one variety; there is nothing to inherit it"


# -- the budget ----------------------------------------------------------------

def test_the_budget_fails_closed_before_anything_is_spent(client):
    r = client.post("/api/batches", json=scan(client, max_calls=1))
    assert r.status_code == 400, r.text
    assert "fails closed" in r.json()["detail"]
    assert client.get("/api/batches").json()["batches"] == [], \
        "a refused scan leaves no half-written batch behind"


# -- the queue -----------------------------------------------------------------

def test_a_scan_measures_the_class_and_the_rest_inherit_without_claiming(client):
    started = client.post("/api/batches", json=scan(client, tags=["pt", "pt-br"]))
    assert started.status_code == 201, started.text
    snap = settle(client, started.json()["id"])
    assert snap["status"] == "done", snap

    rows = {v["tag"]: v for v in client.get("/api/verdicts").json()["verdicts"]}
    assert rows["pt"]["inherited_from"] is None, "the representative was measured"
    assert rows["pt-br"]["inherited_from"] == "pt", \
        "inheriting a tier is not inheriting a claim, and the row has to say so"
    assert rows["pt"]["level"] == rows["pt-br"]["level"]


def test_a_scan_never_approves_its_own_output(client):
    started = client.post("/api/batches", json=scan(client, tags=["pt", "pt-br"]))
    settle(client, started.json()["id"])
    rows = client.get("/api/verdicts").json()["verdicts"]
    assert rows, "the scan recorded nothing"
    assert not any(v["approved"] for v in rows), \
        "approval is a person reading evidence; a self-approving scan makes " \
        "the skip rule meaningless"


def test_an_items_run_is_the_same_run_the_page_shows(client):
    started = client.post("/api/batches", json=scan(client, tags=["pt"]))
    snap = settle(client, started.json()["id"])
    run_id = snap["items"][0]["run_id"]
    assert run_id, snap
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["tag"] == "pt" and run["outcome"]["level"]


def test_calls_used_is_true_during_the_scan_not_only_at_the_end(client):
    started = client.post("/api/batches", json=scan(client, tags=["pt"]))
    snap = settle(client, started.json()["id"])
    assert snap["calls_used"] > 0


# -- the skip rule -------------------------------------------------------------

def test_a_draft_is_rescanned_and_an_approved_row_is_not(client):
    settle(client, client.post("/api/batches",
                               json=scan(client, tags=["pt"])).json()["id"])
    draft = client.get("/api/verdicts").json()["verdicts"][0]

    plan = client.post("/api/batches/preflight", json=scan(client, tags=["pt"])).json()
    assert plan["to_scan"] == 1, "a verdict nobody has looked at is a draft"

    client.post(f"/api/verdicts/{draft['id']}/approve")
    plan = client.post("/api/batches/preflight", json=scan(client, tags=["pt"])).json()
    assert plan["to_scan"] == 0
    assert plan["skipped"] == {"approved": 1}

    plan = client.post("/api/batches/preflight",
                       json=scan(client, tags=["pt"], rescan="all")).json()
    assert plan["to_scan"] == 1, "re-scanning approved rows is allowed, on request"


def test_rescan_none_leaves_every_measured_class_alone(client):
    settle(client, client.post("/api/batches",
                               json=scan(client, tags=["pt"])).json()["id"])
    plan = client.post("/api/batches/preflight",
                       json=scan(client, tags=["pt"], rescan="none")).json()
    assert plan["to_scan"] == 0
    assert plan["skipped"] == {"measured": 1}


def test_a_scan_with_nothing_to_do_refuses_rather_than_pretending(client):
    settle(client, client.post("/api/batches",
                               json=scan(client, tags=["pt"])).json()["id"])
    r = client.post("/api/batches", json=scan(client, tags=["pt"], rescan="none"))
    assert r.status_code == 400
    assert "no calls at all" in r.json()["detail"]
