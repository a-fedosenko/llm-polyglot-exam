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


def test_a_dialect_scan_says_what_it_is_missing(client):
    r = client.post("/api/batches/preflight",
                    json=scan(client, test_type="dialect"))
    assert r.status_code == 400
    assert "contrast pair" in r.json()["detail"]


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
