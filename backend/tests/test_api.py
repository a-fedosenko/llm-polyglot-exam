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
