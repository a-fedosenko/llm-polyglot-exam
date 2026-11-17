"""The HTTP surface, driven end to end against a scripted model."""
from __future__ import annotations

import json

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
        "convert": PT.replace("ônibus", "autocarro"),
        "backtranslation": "The bus was late because of heavy rain. She reached "
                           "the office after nine.",
        "judge": lambda n, m, s, p: json.dumps(
            {"verdicts": ["present"] * len(
                [ln for ln in p.splitlines() if ln[:1].isdigit()])}),
    }
    fake = ScriptedClient(answers)
    monkeypatch.setattr("polyglot.engine.runner.build_client", lambda **kw: fake)
    from polyglot.engine import runner
    runner.reset_for_tests()
    with TestClient(app) as c:
        c.fake = fake
        yield c


def test_config_reports_what_is_missing_rather_than_hiding_it(client):
    body = client.get("/api/config").json()
    assert "language_id" in body
    assert "why_it_matters" in body["language_id"]
    assert "assets" in body


def test_languages_are_searchable(client):
    body = client.get("/api/languages", params={"q": "portug"}).json()
    tags = [r["tag"] for r in body["languages"]]
    assert "pt" in tags and "pt-br" in tags


def test_dialects_only_hides_tags_the_battery_would_refuse(client):
    body = client.get("/api/languages",
                      params={"dialects_only": True, "limit": 600}).json()
    tags = {r["tag"] for r in body["languages"]}
    assert "pt-br" in tags
    assert "pt" not in tags, "the umbrella is the conversion source, not an arm"
    assert all(r["siblings"] >= 2 for r in body["languages"])


def test_a_run_steps_to_a_verdict_and_records_it(client):
    r = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                       "engine": "acme-a", "mode": "manual",
                                       "backtranslator_panel": ["other-b"],
                                       "judge_model": "other-c"})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["next"]["cost"] == 1
    assert run["spent"] == {}

    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        if step["status"] != "running":
            break
    assert step["status"] == "done", step
    assert step["outcome"]["level"] in ("Proficient", "Assisted", "Unusable")

    verdicts = client.get("/api/verdicts").json()["verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["tag"] == "pt"
    assert verdicts[0]["approved"] is False, "a fresh verdict is a draft"


def test_approving_is_a_separate_gesture_from_measuring(client):
    client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                   "engine": "acme-a", "mode": "auto",
                                   "backtranslator_panel": ["other-b"],
                                   "judge_model": "other-c"})
    import time
    for _ in range(100):
        rows = client.get("/api/verdicts").json()["verdicts"]
        if rows:
            break
        time.sleep(0.05)
    assert rows, "auto mode produced no verdict"
    vid = rows[0]["id"]
    out = client.post(f"/api/verdicts/{vid}/approve").json()
    assert out["approved"] is True
    assert out["current"] is True


def test_an_edited_prompt_marks_the_run_and_keeps_it_out_of_the_verdicts(client):
    run = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                         "engine": "acme-a",
                                         "backtranslator_panel": ["other-b"],
                                         "judge_model": "other-c"}).json()
    calls = run["next"]["calls"]
    calls[0]["user"] = "Describe a rainy morning in three sentences."
    step = client.post(f"/api/runs/{run['id']}/step",
                       json={"calls": calls}).json()
    assert step["step"]["edited"] is True
    assert client.fake.calls[0]["user"] == "Describe a rainy morning in three sentences."

    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        if step["status"] != "running":
            break
    assert step["status"] == "done"
    assert client.get("/api/verdicts").json()["verdicts"] == [], \
        "a run measured through a rewritten prompt is not comparable and must " \
        "not become a standing verdict"


def test_a_control_step_refuses_to_be_rewritten(client):
    run = client.post("/api/runs", json={
        "test_type": "dialect", "tag": "pt-br", "engine": "acme-a",
        "varieties": ["pt-br", "pt-pt"], "attestors": ["other-b", "other-c"],
    }).json()
    # Walk to the first control step.
    for _ in range(40):
        nxt = client.get(f"/api/runs/{run['id']}/next").json()["next"]
        if nxt is None or nxt["kind"] == "controls":
            break
        client.post(f"/api/runs/{run['id']}/step")
    assert nxt and nxt["kind"] == "controls"
    calls = nxt["calls"]
    calls[0]["user"] = "which of these is Brazilian?"
    r = client.post(f"/api/runs/{run['id']}/step", json={"calls": calls})
    assert r.status_code == 400
    assert "cannot be rewritten" in r.json()["detail"]


def test_rerunning_a_step_keeps_the_earlier_attempt(client):
    run = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                         "engine": "acme-a",
                                         "backtranslator_panel": ["other-b"],
                                         "judge_model": "other-c"}).json()
    client.post(f"/api/runs/{run['id']}/step")
    client.post(f"/api/runs/{run['id']}/step")
    out = client.post(f"/api/runs/{run['id']}/rerun/1").json()
    assert out["dropped"] == 1
    assert out["next"]["kind"] == "generate"

    from polyglot.store import session
    from polyglot.store.models import RunStep
    from sqlalchemy import select
    with session() as s:
        rows = list(s.scalars(select(RunStep).where(RunStep.run_id == run["id"])))
    assert any(r.superseded for r in rows), \
        "the replaced attempt must survive as evidence, not be overwritten"


def test_a_capability_run_refuses_to_inherit_an_instrument(client):
    """The judge and the reader are per-run choices, not configuration.

    They used to come from the env file, and that was wrong: a verdict is only
    comparable to another graded by the same judge and read by the same reader,
    so the most identity-defining part of a measurement would have been set once
    and never seen again.
    """
    r = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                       "engine": "acme-a"})
    assert r.status_code == 400
    assert "No judge was chosen" in r.json()["detail"]

    r = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                       "engine": "acme-a", "judge_model": "other-c"})
    assert r.status_code == 400
    assert "No reader was chosen" in r.json()["detail"]


def test_a_dialect_run_needs_no_judge(client):
    """Its instrument is the panel, qualified by controls on every run."""
    r = client.post("/api/runs", json={
        "test_type": "dialect", "tag": "pt-br", "engine": "acme-a",
        "varieties": ["pt-br", "pt-pt"], "attestors": ["other-b", "other-c"]})
    assert r.status_code == 201, r.text


def test_models_proposes_an_instrument_and_never_one_from_the_engines_family(client,
                                                                             monkeypatch):
    from polyglot.core import discovery

    monkeypatch.setattr(discovery, "available", lambda: ([
        discovery.Model("acme-one"), discovery.Model("acme-two"),
        discovery.Model("bravo-one"), discovery.Model("cedar-one"),
    ], True))

    body = client.get("/api/models", params={"engine": "acme-one"}).json()
    p = body["propose"]
    assert p["judge"] == "bravo-one"
    # A third family for the reader where one exists: the judge and the reader
    # failing the same way at the same time would read as agreement.
    assert p["backtranslator_panel"][0] == "cedar-one"
    assert "acme-two" in p["excluded_by_family_guard"], \
        "the guard drops the whole family, not just the id"
    assert "acme-two" not in p["attestors"]
    assert body["binding"] is True


def test_an_embedding_model_is_not_offered_as_an_instrument():
    from polyglot.core.discovery import _is_chat

    assert _is_chat("openai-gpt-4o")
    assert not _is_chat("openai-text-embedding-3-small")


def test_a_dialect_run_requires_between_one_and_three_control_models(client):
    """Never "all of them" by default.

    Each control model costs three control calls plus two attestations, so the
    convenient gesture — leave it blank, use everything — quietly turns a
    15-call run into a 65-call one against the operator's own key.
    """
    base = {"test_type": "dialect", "tag": "pt-br", "engine": "acme-a",
            "varieties": ["pt-br", "pt-pt"]}

    r = client.post("/api/runs", json=base)
    assert r.status_code == 400
    assert "between 1 and 3 control models" in r.json()["detail"]

    r = client.post("/api/runs", json={**base, "attestors": [
        "b-one", "c-one", "d-one", "e-one"]})
    assert r.status_code == 400
    assert "got 4" in r.json()["detail"]

    r = client.post("/api/runs", json={**base, "attestors": ["other-b"]})
    assert r.status_code == 201, r.text


def test_a_dialect_run_refuses_a_pair_that_excludes_its_own_subject(client):
    """Caught before anything is spent.

    A battery whose subject is not one of its arms produces no text for the
    subject, so `_pair` has nothing to attest — and the run would die at the
    verdict having already paid for three generations and two conversions.
    """
    r = client.post("/api/runs", json={
        "test_type": "dialect", "tag": "pt-br", "engine": "acme-a",
        "varieties": ["pt-pt", "pt-cv"], "attestors": ["other-b"]})
    assert r.status_code == 400
    assert "has to be one of the two arms" in r.json()["detail"]


def _run_until(client, run_id, kind, limit=60):
    """Step until the named kind is the step that just happened."""
    for _ in range(limit):
        step = client.post(f"/api/runs/{run_id}/step").json()
        if step["step"] and step["step"]["kind"] == kind:
            return step
        if step["status"] != "running":
            break
    raise AssertionError(f"the run never reached a {kind!r} step")


def test_a_ruling_on_the_reader_keeps_the_run_out_of_the_verdicts(client):
    r = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                       "engine": "acme-a", "mode": "manual",
                                       "backtranslator_panel": ["other-b"],
                                       "judge_model": "other-c"})
    run = r.json()
    qual = _run_until(client, run["id"], "qualification")
    assert qual["step"]["result"]["evidence"], \
        "the page must be able to show what the reader was given and gave back"

    ruled = client.post(f"/api/runs/{run['id']}/reader",
                        json={"decision": "accept", "why": "it reads fine"})
    assert ruled.status_code == 200, ruled.text
    assert ruled.json()["step"]["kind"] == "reader-override"

    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        if step["status"] != "running":
            break
    assert step["status"] == "done", step
    assert step["outcome"]["instrument_overridden"] is True
    assert client.get("/api/verdicts").json()["verdicts"] == [], \
        "a run whose instrument the operator overruled is never stored"


def test_a_second_correction_does_not_undo_the_first(client):
    """Adjudication re-scores from the measurement, so it needs every override.

    Correcting one fact and then another used to leave only the second applied,
    and the page showed a number for a correction the reader had not made.
    """
    r = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                       "engine": "acme-a", "mode": "manual",
                                       "backtranslator_panel": ["other-b"],
                                       "judge_model": "other-c"})
    run = r.json()
    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        if step["status"] != "running":
            break
    spec = step["outcome"]["items"][0]["spec_id"]

    one = client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
        {"spec_id": spec, "index": 0, "verdict": "missing"}]}).json()
    assert one["changed"] == 1
    two = client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
        {"spec_id": spec, "index": 1, "verdict": "missing"}]}).json()
    assert two["changed"] == 2, "the first correction has to still be applied"
    assert {(a["index"]) for a in two["applied"]} == {0, 1}
