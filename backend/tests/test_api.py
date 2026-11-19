"""The HTTP surface, driven end to end against a scripted model."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedClient, varied_generation
from polyglot.api.app import app

PT = ("O ônibus atrasou por causa da chuva forte. Ela chegou ao escritório "
      "depois das nove.")


@pytest.fixture
def client(db, monkeypatch):
    answers = {
        # Varies by variety: two arms that come back identical are not a pair,
        # and the battery stops on that before any attestor is paid.
        "generation": varied_generation(PT),
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


def graded_run(client, tag: str = "pt") -> dict:
    """A capability run driven to a verdict, and its last step."""
    run = client.post("/api/runs", json={"test_type": "capability", "tag": tag,
                                         "engine": "acme-a", "mode": "manual",
                                         "backtranslator_panel": ["other-b"],
                                         "judge_model": "other-c"}).json()
    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        if step["status"] != "running":
            break
    assert step["status"] == "done", step
    return {"id": run["id"], "step": step}


def test_a_correction_counts_immediately_and_leaves_the_judge_on_record(client):
    """The judge is a model and models are wrong. A reader who can see that a
    fact is in the text is the better instrument on that fact, so the
    correction becomes the standing answer at once — and what makes it a
    correction rather than a rewrite is that the judge's row is untouched
    beside it, one click from being the answer again."""
    run = graded_run(client)
    spec = run["step"]["outcome"]["items"][0]["spec_id"]
    measured = run["step"]["outcome"]["level"]

    out = client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
        {"spec_id": spec, "index": 0, "verdict": "missing"}]}).json()
    assert out["verdict_id"]
    assert out["adjudicated"]["tier"] != out["measured"]["tier"], \
        "this fixture's correction is supposed to move the tier"

    rows = client.get("/api/verdicts/history",
                      params={"engine": "acme-a", "tag": "pt",
                              "test_type": "capability"}).json()["verdicts"]
    adj = next(v for v in rows if v["adjudicated"])
    judged = next(v for v in rows if not v["adjudicated"])
    assert adj["current"] is True, "the correction is what the table reads"
    assert judged["current"] is False
    assert judged["level"] == measured, "the judge's reading is untouched"

    standing = [v for v in client.get("/api/verdicts",
                                      params={"engine": "acme-a"}).json()["verdicts"]
                if v["tag"] == "pt"]
    assert len(standing) == 1 and standing[0]["level"] == adj["level"]

    # And back: promoting the judge's row returns the table to its reading,
    # with the correction still on record.
    client.post(f"/api/verdicts/{judged['id']}/promote")
    standing = [v for v in client.get("/api/verdicts",
                                      params={"engine": "acme-a"}).json()["verdicts"]
                if v["tag"] == "pt"]
    assert len(standing) == 1 and standing[0]["level"] == measured


def test_putting_every_fact_back_removes_the_corrected_row(client):
    """A correction that corrects nothing is not a reading of anything. Going
    back to the judge's answers has to leave the table exactly where it was."""
    run = graded_run(client)
    item = run["step"]["outcome"]["items"][0]
    spec, judged_first = item["spec_id"], item["verdicts"][0]
    measured = run["step"]["outcome"]["level"]

    client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
        {"spec_id": spec, "index": 0, "verdict": "missing"}]})
    back = client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
        {"spec_id": spec, "index": 0, "verdict": judged_first}]}).json()
    assert back["changed"] == 0
    assert not back.get("verdict_id")

    rows = client.get("/api/verdicts/history",
                      params={"engine": "acme-a", "tag": "pt",
                              "test_type": "capability"}).json()["verdicts"]
    assert not [v for v in rows if v["adjudicated"]]
    assert len(rows) == 1 and rows[0]["current"] and rows[0]["level"] == measured


def test_correcting_again_replaces_the_corrected_row_rather_than_stacking(client):
    run = graded_run(client)
    spec = run["step"]["outcome"]["items"][0]["spec_id"]
    for index in (0, 1):
        out = client.post(f"/api/runs/{run['id']}/adjudicate", json={"overrides": [
            {"spec_id": spec, "index": index, "verdict": "missing"}]}).json()
    rows = client.get("/api/verdicts/history",
                      params={"engine": "acme-a", "tag": "pt",
                              "test_type": "capability"}).json()["verdicts"]
    adj = [v for v in rows if v["adjudicated"]]
    assert len(adj) == 1
    assert len(adj[0]["adjudicated"]) == 2, "it carries the whole correction"
    assert out["verdict_id"] == adj[0]["id"]


def test_a_correction_made_mid_run_is_re_scored_over_the_finished_run(client):
    """A correction clicked at step seven was scored against a run that was not
    finished, and the page kept showing that pair of numbers afterwards."""
    run = client.post("/api/runs", json={"test_type": "capability", "tag": "pt",
                                         "engine": "acme-a", "mode": "manual",
                                         "backtranslator_panel": ["other-b"],
                                         "judge_model": "other-c"}).json()
    spec = None
    for _ in range(60):
        step = client.post(f"/api/runs/{run['id']}/step").json()
        done = step["status"] != "running"
        st = step.get("step") or {}
        if spec is None and st.get("kind") == "judge":
            spec = (st.get("result") or {}).get("spec_id")
            mid = client.post(f"/api/runs/{run['id']}/adjudicate",
                              json={"overrides": [{"spec_id": spec, "index": 0,
                                                   "verdict": "missing"}]}).json()
            assert mid["changed"] == 1
            assert not mid.get("verdict_id"), \
                "a half-measured run has no whole reading to record"
        if done:
            break
    assert spec and step["status"] == "done"

    snap = client.get(f"/api/runs/{run['id']}").json()
    assert snap["adjudication"]["changed"] == 1
    # The pair now describes the finished run, not the moment it was clicked.
    assert snap["adjudication"]["measured"]["s_content"] == \
        step["outcome"]["score"]["s_content"]

    rows = client.get("/api/verdicts/history",
                      params={"engine": "acme-a", "tag": "pt",
                              "test_type": "capability"}).json()["verdicts"]
    adj = [v for v in rows if v["adjudicated"]]
    assert len(adj) == 1, "the correction is on record once the run has finished"
    assert adj[0]["s_content"] < next(
        v for v in rows if not v["adjudicated"])["s_content"]


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
