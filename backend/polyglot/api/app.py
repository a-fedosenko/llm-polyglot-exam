"""The HTTP surface.

Thin on purpose. Every decision this exposes already has one definition
somewhere in `engine/` or `probe/`; an endpoint that re-derived one would be the
same mistake the project has made before, where an analysis path re-implemented
a production call and its drift arrived looking like a finding.

**No authentication, and that is a deployment decision rather than an
omission.** This is a single-operator tool bound to loopback, holding the
operator's own API keys and spending their own budget. There are no accounts, no
per-caller quotas and no abuse handling; widening `API_BIND` puts an unguarded
tool that spends money on your network. Publishing results is a separate,
read-only job — see the static export endpoint, which carries verdicts and no
keys.
"""
from __future__ import annotations

import json
import pathlib
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from polyglot import paths
from polyglot.core import bootstrap, hardware
from polyglot.core import discovery
from polyglot.core import reference as reference_mod
from polyglot.core.client import NOT_CONFIGURED_MESSAGE, is_configured
from polyglot.core.probe import lid
from polyglot.core.probe.specs import load_specs
from polyglot.core.scheme import load_scheme
from polyglot.engine import batch, runner
from polyglot.engine.batch import BatchError
from polyglot.engine.runner import RunError
from polyglot.settings import METHOD_VERSION, settings
from polyglot.store import create_all, session
from polyglot.store import repo

@asynccontextmanager
async def lifespan(_app: FastAPI):
    paths.ensure()
    create_all()
    yield


app = FastAPI(title="LLM Polyglot Exam", version=METHOD_VERSION,
              lifespan=lifespan,
              description="Which languages, and which dialects, a model can "
                          "actually produce — measured, not claimed.")

# The dev server runs the UI on another port. In production the built frontend
# is served from this same origin and this does nothing.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173",
                                                  "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# -- what the instrument can and cannot do -------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "method_version": METHOD_VERSION}


@app.get("/api/config")
def config() -> dict:
    """What this installation is set up to do, and what it is missing.

    The missing half matters more than the present half. A scan without GlotLID
    runs fine and silently loses its language check — a model answering in
    Indonesian when asked for Acehnese would pass — so the absence is reported
    rather than discovered.
    """
    assets = bootstrap.status()
    return {
        "method_version": METHOD_VERSION,
        "settings": settings.redacted(),
        "models_configured": is_configured(),
        "models_message": None if is_configured() else NOT_CONFIGURED_MESSAGE,
        "assets": assets,
        "language_id": {
            "available": lid.available(),
            "why_it_matters": "Without GlotLID the gate checks the writing "
                              "system but not the language, so a model "
                              "answering in a neighbouring language passes.",
        },
        "hardware": hardware.detect(),
        "data_dir": str(paths.DATA),
    }


@app.get("/api/models")
def models(engine: str = "") -> dict:
    """Every model this installation can reach.

    An aggregator accepts any id and cannot be enumerated, so the list is empty
    there and the page falls back to free text — which is honest about which
    configuration is in use rather than pretending to a closed list.
    """
    models, binding = discovery.available()
    return {
        "models": [{"id": m.id, "title": m.title, "endpoint": m.endpoint,
                    "family": m.family} for m in models],
        "enumerable": bool(models),
        # Whether the list is the whole of what can be reached. A custom file is
        # the configuration itself and a name absent from it is refused; a
        # gateway's list is the gateway's opinion and a missing name is still
        # worth trying, so the page keeps a free-text field in that case.
        "binding": binding,
        "source": ("custom model list" if binding
                   else "gateway" if models else "nothing configured"),
        "propose": (discovery.propose(engine, models) if engine else None),
        "pivot": settings.pivot,
    }


@app.get("/api/schemes")
def schemes() -> dict:
    from polyglot.core.scheme.loader import SCHEMES_DIR

    out = []
    for p in sorted(SCHEMES_DIR.glob("*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, ValueError):
            continue
        out.append({"name": p.stem, "description": meta.get("description"),
                    "counts": meta.get("counts") or {},
                    "active": p.stem == settings.scheme})
    return {"schemes": out, "active": settings.scheme}


# -- languages -----------------------------------------------------------------

@app.get("/api/languages")
def languages(q: str = "", scheme: str | None = None, limit: int = 60,
              dialects_only: bool = False) -> dict:
    """The locale list, searchable.

    `dialects_only` restricts to tags a dialect test can actually answer: a
    named variety with at least one named sibling. Offering a tag the battery
    would refuse is how a page comes to cost somebody a click to learn a rule
    that could have been applied in the list.
    """
    sch = load_scheme(scheme or settings.scheme)
    needle = q.strip().casefold()
    rows = []
    by_lang: dict[str, int] = {}
    for lang in sch.languages.values():
        if not lang.inclusive:
            by_lang[lang.lang] = by_lang.get(lang.lang, 0) + 1
    for lang in sch.languages.values():
        if dialects_only and (lang.inclusive or by_lang.get(lang.lang, 0) < 2):
            continue
        hay = " ".join(filter(None, [lang.tag, lang.name, lang.local_name,
                                     lang.region_name, lang.iso639_3])).casefold()
        if needle and needle not in hay:
            continue
        rows.append({
            "tag": lang.tag, "name": lang.name, "local_name": lang.local_name,
            "region": lang.region_name, "script": lang.script, "cls": lang.cls,
            "inclusive": lang.inclusive, "iso639_3": lang.iso639_3,
            "siblings": by_lang.get(lang.lang, 0),
        })
    rows.sort(key=lambda r: (not r["tag"].startswith(needle), r["tag"]))
    return {"total": len(rows), "languages": rows[:limit], "scheme": sch.meta.name}


@app.get("/api/languages/{tag}")
def language(tag: str, engine: str | None = None, scheme: str | None = None) -> dict:
    """One language, plus whatever is already known about it.

    The "already known" half is what stops a run being paid for twice, and it is
    the same query the batch scan's skip rule will use.
    """
    sch = load_scheme(scheme or settings.scheme)
    lang = sch.get(tag)
    if lang is None:
        raise HTTPException(404, f"Unknown tag {tag!r} in scheme {sch.meta.name!r}")

    family = [l for l in sch.languages.values() if l.lang == lang.lang]
    known: dict[str, Any] = {}
    if engine:
        with session() as s:
            for test in ("capability", "dialect"):
                row = repo.current_verdict(s, engine=engine, tag=tag, test_type=test)
                known[test] = None if row is None else _verdict_dict(row)
    return {
        "language": lang.model_dump(),
        "variants": sorted(sch.variants_of(tag)),
        "family": [{"tag": l.tag, "region": l.region_name, "script": l.script,
                    "inclusive": l.inclusive} for l in family],
        "umbrella": next((l.tag for l in family if l.inclusive), None),
        #: What a dialect battery contrasts this tag against by default, and
        #: on what basis. `provisional` means nobody has decided this family's
        #: reference and the first member is standing in — the page says so
        #: rather than presenting a placeholder as a decision.
        "reference": reference_mod.reference_of(sch, lang).as_dict(),
        "known": known,
        "specs": [s.model_dump() for s in load_specs()],
    }


# -- runs ----------------------------------------------------------------------

class StartRun(BaseModel):
    test_type: str = Field(description="capability | dialect")
    tag: str
    engine: str
    mode: str = "manual"
    scheme_name: str | None = None
    judge_model: str | None = None
    backtranslator_panel: list[str] | None = None
    attestors: list[str] | None = None
    pivot: str | None = None
    #: Rewriting a scenario or its facts changes the instrument, so such a run
    #: is kept in full and never promoted to a standing verdict.
    specs: list[dict] | None = None
    varieties: list[str] | None = None
    neutral_tag: str | None = None
    scenario: str | None = None
    sweep_all: bool = False


@app.post("/api/runs", status_code=201)
def start_run(body: StartRun) -> dict:
    try:
        sess = runner.start(**body.model_dump())
    except RunError as e:
        raise _fail(e) from e
    except Exception as e:  # noqa: BLE001
        raise _fail(e) from e
    if body.mode == "auto":
        runner.run_auto(sess)
    return runner.snapshot(sess)


@app.get("/api/runs")
def list_runs(engine: str | None = None, tag: str | None = None,
              test_type: str | None = None, limit: int = Query(30, le=200),
              offset: int = 0) -> dict:
    with session() as s:
        rows, total = repo.list_runs(s, engine=engine, tag=tag,
                                     test_type=test_type, limit=limit, offset=offset)
        live = {x["id"] for x in runner.listing()}
        return {"total": total, "limit": limit, "offset": offset,
                "runs": [{
                    "id": r.id, "test_type": r.test_type, "mode": r.mode,
                    "engine": r.engine, "tag": r.tag, "status": r.status,
                    "level": (r.outcome or {}).get("level"),
                    "confidence": (r.outcome or {}).get("confidence"),
                    "prompt_edited": r.prompt_edited,
                    "calls": r.calls, "error": r.error,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": (r.finished_at.isoformat()
                                    if r.finished_at else None),
                    "live": r.id in live,
                } for r in rows]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    sess = runner.get(run_id)
    if sess is not None:
        return runner.snapshot(sess)
    with session() as s:
        row = repo.get_run(s, run_id)
        if row is None:
            raise HTTPException(404, f"No run {run_id}")
        # A run from a previous process is a record, not a session. Saying so is
        # the honest answer: it can be read, adjudicated and approved, and it
        # cannot be continued.
        #
        # `interrupted` rather than the stored `running`: the row was written by
        # a process that is gone, and a page that reconnected to it would show a
        # "Run next step" button that can never work.
        status = "interrupted" if row.status == "running" else row.status
        return {
            "id": row.id, "status": status, "mode": row.mode,
            "test_type": row.test_type, "tag": row.tag, "engine": row.engine,
            "live": False, "error": row.error,
            "prompt_edited": row.prompt_edited, "plan": row.plan,
            "outcome": row.outcome, "spent": row.calls or {},
            "adjudication": row.adjudication,
            "corpus": row.corpus_path, "next": None,
            "steps": [{"ordinal": st.ordinal, "attempt": st.attempt,
                       "kind": st.kind, "phase": st.phase, "status": st.status,
                       "tone": st.tone, "summary": st.summary,
                       "superseded": st.superseded, "edited": st.edited,
                       "request": st.request, "result": st.result,
                       "calls": st.calls}
                      for st in row.steps if not st.superseded],
        }


@app.get("/api/runs/{run_id}/next")
def next_step(run_id: str) -> dict:
    try:
        sess = runner.require(run_id)
    except RunError as e:
        raise _fail(e) from e
    return {"next": runner.plan(sess), "spent": runner._spent(sess),
            "status": sess.status}


class StepBody(BaseModel):
    #: The calls to actually send. Omit to send exactly what was planned; supply
    #: them to send something you have rewritten. A step whose prompts are the
    #: instrument — a control, a qualification — refuses an edit rather than
    #: quietly accepting one that would uncertify it.
    calls: list[dict] | None = None


@app.post("/api/runs/{run_id}/step")
def take_step(run_id: str, body: StepBody | None = None) -> dict:
    try:
        sess = runner.require(run_id)
        step = runner.step(sess, calls=(body.calls if body else None))
    except RunError as e:
        raise _fail(e) from e
    return {"step": step, "status": sess.status, "error": sess.error,
            "spent": runner._spent(sess),
            "next": runner.plan(sess) if sess.status == "running" else None,
            "outcome": (sess.machine.outcome()  # type: ignore[attr-defined]
                        if sess.status == "done" else None)}


@app.post("/api/runs/{run_id}/auto")
def go_auto(run_id: str) -> dict:
    try:
        sess = runner.require(run_id)
    except RunError as e:
        raise _fail(e) from e
    if sess.auto:
        raise HTTPException(409, "This run is already running automatically.")
    sess.cancel = False
    runner.run_auto(sess)
    return {"ok": True, "status": sess.status}


@app.post("/api/runs/{run_id}/pause")
def pause(run_id: str) -> dict:
    """Stop an auto run between steps.

    Never mid-call: by the time a cancel flag could be checked the call has been
    paid for, so stopping sooner would only mean discarding an answer already
    bought. The run stays open and can be continued a step at a time.
    """
    try:
        sess = runner.require(run_id)
    except RunError as e:
        raise _fail(e) from e
    sess.cancel = True
    return {"ok": True}


@app.post("/api/runs/{run_id}/rerun/{ordinal}")
def rerun(run_id: str, ordinal: int) -> dict:
    try:
        sess = runner.require(run_id)
        out = runner.rerun(sess, ordinal)
    except RunError as e:
        raise _fail(e) from e
    return {**out, "next": runner.plan(sess), "status": sess.status}


class ReaderRuling(BaseModel):
    #: accept — keep this reader despite the threshold; reject — throw it out
    #: and try the next on the panel; recheck — pay for the check again rather
    #: than take it from the cache.
    decision: str
    why: str = ""


@app.post("/api/runs/{run_id}/reader")
def rule_on_reader(run_id: str, body: ReaderRuling) -> dict:
    """Overrule the qualification of the back-translator.

    The one measurement in the run whose subject is the instrument rather than
    the model, and the one a person reading the transcript can be righter about
    than the threshold — the chrF++ cut separates fabrication from faithful
    reading and is simply wrong in the middle. Any run that took this route is
    kept in full and never becomes a stored verdict.
    """
    try:
        sess = runner.require(run_id)
        step = runner.rule_on_reader(sess, body.decision, body.why)
    except (RunError, ValueError) as e:
        raise _fail(e) from e
    return {"step": step, "status": sess.status,
            "spent": runner._spent(sess), "next": runner.plan(sess)}


class PanelBody(BaseModel):
    attestors: list[str]


@app.post("/api/runs/{run_id}/panel")
def set_panel(run_id: str, body: PanelBody) -> dict:
    try:
        sess = runner.require(run_id)
        plan = runner.set_panel(sess, body.attestors)
    except (RunError, ValueError) as e:
        raise _fail(e) from e
    return {"plan": plan, "next": runner.plan(sess)}


class AdjudicateBody(BaseModel):
    overrides: list[dict]


@app.post("/api/runs/{run_id}/adjudicate")
def adjudicate(run_id: str, body: AdjudicateBody) -> dict:
    try:
        sess = runner.require(run_id)
        return runner.adjudicate(sess, body.overrides)
    except (RunError, ValueError) as e:
        raise _fail(e) from e


@app.delete("/api/runs/{run_id}")
def close_run(run_id: str) -> dict:
    return {"closed": runner.close(run_id)}


@app.get("/api/runs/{run_id}/events")
def events(run_id: str) -> StreamingResponse:
    """Server-sent events for an auto run.

    Polling would work and would be simpler; this exists because an auto run's
    steps arrive seconds apart and a page that only learns about them on a timer
    feels broken in exactly the way the previous UI did.
    """
    try:
        sess = runner.require(run_id)
    except RunError as e:
        raise _fail(e) from e

    def stream():
        seen = 0
        last = time.monotonic()
        while True:
            history = sess.machine.history  # type: ignore[attr-defined]
            while seen < len(history):
                # The spend *as of this step*, not as of the moment the stream
                # caught up. An auto run outruns its own stream, so reporting
                # the live total here makes the first step appear to have cost
                # everything the run eventually spent.
                spent: dict[str, int] = {}
                for ex in history[:seen + 1]:
                    for k, v in (ex.calls or {}).items():
                        spent[k] = spent.get(k, 0) + v
                payload = {"type": "step", "step": history[seen].as_dict(),
                           "spent": spent}
                seen += 1
                last = time.monotonic()
                yield f"data: {json.dumps(payload)}\n\n"
            if sess.status != "running":
                done = {"type": "end", "status": sess.status, "error": sess.error,
                        "outcome": (sess.machine.outcome()  # type: ignore[attr-defined]
                                    if sess.status == "done" else None),
                        "spent": runner._spent(sess)}
                yield f"data: {json.dumps(done)}\n\n"
                return
            if time.monotonic() - last > 15:
                last = time.monotonic()
                yield ": keep-alive\n\n"
            time.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# -- verdicts ------------------------------------------------------------------

def _verdict_dict(r) -> dict:
    return {"id": r.id, "run_id": r.run_id, "test_type": r.test_type,
            "engine": r.engine, "tag": r.tag, "cls": r.cls,
            "level": r.level, "confidence": r.confidence, "evidence": r.evidence,
            "s_lang": r.s_lang, "s_content": r.s_content,
            "ci": [r.ci_low, r.ci_high], "borderline": r.borderline,
            "refusals": r.refusals, "designator": r.designator,
            "backtranslator": r.backtranslator, "judge": r.judge,
            "pivot": r.pivot, "attestors": r.attestors,
            "detail": r.detail, "notes": r.notes,
            "inherited_from": r.inherited_from,
            "current": r.current, "approved": r.approved,
            "method_version": r.method_version,
            "measured_at": r.measured_at.isoformat() if r.measured_at else None}


@app.get("/api/verdicts")
def verdicts(engine: str | None = None, test_type: str | None = None,
             approved: bool | None = None) -> dict:
    with session() as s:
        rows = repo.list_verdicts(s, engine=engine, test_type=test_type,
                                  approved=approved)
        return {"verdicts": [_verdict_dict(r) for r in rows],
                "method_version": METHOD_VERSION}


@app.get("/api/verdicts/history")
def verdict_history(engine: str, tag: str, test_type: str) -> dict:
    with session() as s:
        rows = repo.verdict_history(s, engine=engine, tag=tag, test_type=test_type)
        return {"verdicts": [_verdict_dict(r) for r in rows]}


@app.post("/api/verdicts/{verdict_id}/approve")
def approve(verdict_id: int, approved: bool = True) -> dict:
    """Sign off on a verdict, which is what a batch scan skips on.

    Approval is separate from currency on purpose: current means "our latest
    reading", approved means "a person read the evidence and stood behind it".
    A scan that skipped everything merely current would skip every draft it had
    just produced itself.
    """
    with session() as s:
        row = repo.approve_verdict(s, verdict_id, approved=approved)
        if row is None:
            raise HTTPException(404, f"No verdict {verdict_id}")
        return _verdict_dict(row)


@app.post("/api/verdicts/{verdict_id}/promote")
def promote(verdict_id: int) -> dict:
    """Make one stored measurement the one that counts. Destroys nothing, and
    can be undone by promoting the other back."""
    with session() as s:
        row = repo.promote_verdict(s, verdict_id)
        if row is None:
            raise HTTPException(404, f"No verdict {verdict_id}")
        return _verdict_dict(row)


# -- reference varieties -------------------------------------------------------

@app.get("/api/reference")
def reference(scheme: str | None = None) -> dict:
    """Which variety each family is contrasted against, and how much is decided.

    The provisional list is the work queue: those families have a reference only
    because something had to be first, and a measurement taken against a
    placeholder is comparable to other measurements against the same
    placeholder and to nothing else.
    """
    sch = load_scheme(scheme or settings.scheme)
    cov = reference_mod.coverage(sch)
    return {
        "scheme": sch.meta.name,
        **cov,
        "references": {
            cls: reference_mod.reference_for(sch, cls).as_dict()
            for cls in sorted({l.cls for l in sch.languages.values()})
        },
    }


# -- coverage: every tag, and what is stored about it ---------------------------

@app.get("/api/coverage")
def coverage(engine: str, test_type: str = "capability",
             scheme: str | None = None) -> dict:
    """One row per tag in the loaded list, with its standing verdict or none.

    The scheme is the spine, not the verdict table: a results page built from
    what has been measured can only ever show what has been measured, and the
    first question anyone brings to a coverage table is which languages are
    *missing*. 602 rows is small enough to send whole and filter in the page,
    which keeps every facet — script, macrolanguage, region, first letter —
    one definition of the same list rather than six endpoints that can disagree.
    """
    sch = load_scheme(scheme or settings.scheme)
    with session() as s:
        rows = repo.list_verdicts(s, engine=engine, test_type=test_type)
        by_tag = {r.tag: _verdict_dict(r) for r in rows}

    classes: dict[str, int] = {}
    for lang in sch.languages.values():
        classes[lang.cls] = classes.get(lang.cls, 0) + 1

    out = []
    for lang in sch.languages.values():
        v = by_tag.get(lang.tag)
        out.append({
            "tag": lang.tag, "name": lang.name, "local_name": lang.local_name,
            "script": lang.script, "region": lang.region,
            "region_name": lang.region_name, "macro": lang.macro,
            "iso639_3": lang.iso639_3, "inclusive": lang.inclusive,
            "cls": lang.cls, "class_size": classes.get(lang.cls, 1),
            "verdict": v,
        })
    out.sort(key=lambda r: r["tag"])
    return {"engine": engine, "test_type": test_type, "scheme": sch.meta.name,
            "method_version": METHOD_VERSION, "rows": out}


# -- batches -------------------------------------------------------------------

class ScanBody(BaseModel):
    engine: str
    tags: list[str]
    test_type: str = "capability"
    #: none | drafts | all — which of the three states `current_verdict`
    #: distinguishes may be measured again. See `engine.batch.RESCAN`.
    rescan: str = "drafts"
    scheme_name: str | None = None
    judge_model: str | None = None
    backtranslator_panel: list[str] | None = None
    pivot: str | None = None
    max_calls: int | None = None


@app.post("/api/batches/preflight")
def batch_preflight(body: ScanBody) -> dict:
    """What the scan would do, and what it would cost. Spends nothing."""
    try:
        return batch.preflight(
            engine=body.engine, tags=body.tags, test_type=body.test_type,
            rescan=body.rescan, scheme_name=body.scheme_name,
            max_calls=body.max_calls)
    except BatchError as e:
        raise _fail(e) from e


@app.post("/api/batches", status_code=201)
def start_batch(body: ScanBody) -> dict:
    try:
        started = batch.start(**body.model_dump())
    except (BatchError, RunError) as e:
        raise _fail(e) from e
    return batch.snapshot(started["id"])


@app.get("/api/batches")
def list_batches(engine: str | None = None, limit: int = Query(20, le=100)) -> dict:
    return {"batches": batch.listing(engine, limit)}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    try:
        return batch.snapshot(batch_id)
    except BatchError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/batches/{batch_id}/pause")
def pause_batch(batch_id: str) -> dict:
    """Stop after the item in flight.

    Never mid-run: every call of that run is paid for by the time it could be
    checked, so stopping sooner would only throw away an answer already bought.
    The items it has not reached stay `pending`, which is what lets the same
    scope be started again and pick up where it stopped.
    """
    return {"ok": batch.pause(batch_id), "batch": batch.snapshot(batch_id)}


# -- the built frontend --------------------------------------------------------

_DIST = pathlib.Path(__file__).resolve().parents[2] / "static"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = _DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(_DIST / "index.html")
