"""Running a test: sessions, persistence, and the two modes.

**There is one traversal, and both modes walk it.** Auto mode is a loop over
`plan()`/`execute()` on a background thread; manual mode is the same two calls
driven from the page. They cannot disagree about what the method does, because
there is nothing for them to disagree with — the machine is the only definition
of the order, exactly as the generator was before it.

A session holds a live machine between HTTP calls. Machines are not picklable
and there is no second user to share them with, so that is the whole of the
storage design: a session dies with the process. What survives is the *record* —
every step, its prompt, its reply, what it cost — written to the database as it
happens, so a run can be read, adjudicated and approved after a restart even
though it cannot be resumed.

**A run spends real money.** Roughly 9–15 calls for a capability test and 20–30
for a three-model dialect battery, against the operator's own key, so a ceiling
is enforced and the pre-flight states the cost before the first call.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from polyglot.core.bt import RemoteBackTranslator
from polyglot.core.client import build_client
from polyglot.core.probe.corpus import Corpus
from polyglot.core.probe.specs import Spec, load_specs
from polyglot.core.scheme import load_scheme
from polyglot.engine.capability import CapabilityMachine
from polyglot.engine.dialect import DialectMachine
from polyglot.engine.types import Call, Planned
from polyglot.settings import METHOD_VERSION, settings
from polyglot.store import session
from polyglot.store import repo
from polyglot.store.caches import DbAttestorRecord, DbQualificationCache

#: How many sessions may be open at once. Low on purpose: each holds an open
#: corpus file and a half-finished machine, and a person can watch one.
MAX_SESSIONS = 6

#: A session nobody has touched for this long is abandoned — a closed tab, not a
#: decision — and is reaped so its corpus file is not left open forever.
IDLE_TIMEOUT_S = 60 * 60


class RunError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    machine: object
    corpus: Corpus
    mode: str
    status: str = "running"           # running | done | failed | cancelled
    error: str | None = None
    prompt_edited: bool = False
    #: The step `plan()` last produced, kept so `step()` without an explicit
    #: payload sends exactly what the page was shown.
    pending: Planned | None = None
    auto: bool = False
    #: Set by the page to stop an auto run between steps. Never mid-call: every
    #: call is paid for by the time it could be checked, so stopping sooner
    #: would only mean losing the answer that was already bought.
    cancel: bool = False
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


_SESSIONS: dict[str, Session] = {}
_LOCK = threading.Lock()


def _reap() -> None:
    cutoff = time.time() - IDLE_TIMEOUT_S
    for sid, s in list(_SESSIONS.items()):
        if s.touched_at < cutoff:
            close(sid)


def get(run_id: str) -> Session | None:
    return _SESSIONS.get(run_id)


def require(run_id: str) -> Session:
    s = _SESSIONS.get(run_id)
    if s is None:
        raise RunError(
            f"Run {run_id} is not open in this process. Its record is still in "
            f"the database and can be read and approved; a half-finished run "
            f"cannot be resumed after a restart.")
    return s


def listing() -> list[dict]:
    return [{"id": s.id, "status": s.status, "mode": s.mode, "auto": s.auto,
             "steps": len(s.machine.history),  # type: ignore[attr-defined]
             "test_type": s.machine.test_type,  # type: ignore[attr-defined]
             "tag": s.machine.tag, "engine": s.machine.engine}  # type: ignore[attr-defined]
            for s in _SESSIONS.values()]


# -- building a run ------------------------------------------------------------

def specs_for(overrides: list[dict] | None) -> tuple[list[Spec], bool]:
    """The content specifications, with any the caller rewrote.

    Editing a scenario or its facts changes the measurement, so the fact that it
    happened is returned alongside and travels with the run. The shipped specs
    are mundane on purpose — a memorised encyclopedia opening cannot satisfy
    "an old man bought fish and the price had doubled" — and a rewrite can lose
    that property without anyone noticing.
    """
    shipped = load_specs()
    if not overrides:
        return shipped, False
    by_id = {s.id: s for s in shipped}
    out, modified = [], False
    for row in overrides:
        spec = Spec(id=str(row.get("id") or f"custom-{len(out)}"),
                    scenario=str(row.get("scenario") or ""),
                    facts=[str(f) for f in (row.get("facts") or []) if str(f).strip()])
        original = by_id.get(spec.id)
        if original is None or original.scenario != spec.scenario \
                or original.facts != spec.facts:
            modified = True
        out.append(spec)
    return (out or shipped), modified


def _backtranslators(client, panel: list[str], pivot: str) -> list[RemoteBackTranslator]:
    return [RemoteBackTranslator(client, m, pivot=pivot) for m in panel]


def start(*, test_type: str, tag: str, engine: str, mode: str = "manual",
          scheme_name: str | None = None, judge_model: str | None = None,
          backtranslator_panel: list[str] | None = None, pivot: str | None = None,
          specs: list[dict] | None = None, attestors: list[str] | None = None,
          varieties: list[str] | None = None, neutral_tag: str | None = None,
          scenario: str | None = None, sweep_all: bool = False) -> Session:
    """Open a run. Nothing is spent until the first step."""
    with _LOCK:
        _reap()
        live = [s for s in _SESSIONS.values() if s.status == "running"]
        if len(live) >= MAX_SESSIONS:
            raise RunError(
                f"{len(live)} run(s) are already open, which is the limit. "
                f"Reloading the page mid-run leaves one behind; close them from "
                f"the runs list.")

    scheme = load_scheme(scheme_name or settings.scheme)
    client = build_client()
    run_id = uuid.uuid4().hex[:12]
    corpus = Corpus(to_db=True, run_id=run_id)

    pivot = pivot or settings.pivot

    if test_type == "capability":
        # No fallback. The judge and the reader are part of what a verdict
        # *means* — two runs graded by different judges are not two
        # measurements of the same thing — so a run that silently inherited
        # them from an env file would produce a row that is not comparable to
        # anything and does not say so.
        #
        # A dialect battery has no judge at all: its instrument is the panel of
        # control models, qualified by the manufactured controls on every run.
        if not judge_model:
            raise RunError(
                "No judge was chosen. A verdict is only comparable to another "
                "graded by the same judge, so it is picked per run rather than "
                "configured — see GET /api/models for what this installation "
                "can reach, and the pair it proposes.")
        chosen_specs, edited_specs = specs_for(specs)
        panel = list(backtranslator_panel or [])
        if not panel:
            raise RunError(
                "No reader panel was chosen. Results from different "
                "back-translators are not comparable — the same engine read by "
                "two qualified readers disagreed on tier for 13% of 136 "
                "languages — so the reader is picked per run rather than "
                "configured.")
        # A model that is both under test and in the reader panel would be
        # dropped from its own panel and then measured through a different
        # instrument from every other engine, which invalidates the comparison.
        # Said here rather than silently fixed.
        from polyglot.core.families import model_family
        fam = model_family(engine)
        panel = [m for m in panel if model_family(m) != fam]
        if not panel:
            raise RunError(
                f"Every reader in the panel is from {engine}'s own family. A "
                f"model cannot read back its own output — that measures "
                f"self-consistency. Name a different reader panel.")
        machine: object = CapabilityMachine(
            scheme=scheme, tag=tag, engine=engine, client=client,
            backtranslators=_backtranslators(client, panel, pivot),
            judge_model=judge_model, specs=chosen_specs, corpus=corpus,
            pivot=pivot, sweep_all=sweep_all, cache=DbQualificationCache())
    elif test_type == "dialect":
        if not varieties or len(varieties) < 2:
            raise RunError(
                "A dialect test contrasts two named varieties. Pick two — the "
                "umbrella tag is the conversion source, not a contrast arm.")
        langs = []
        for t in varieties:
            lang = scheme.get(t)
            if lang is None:
                raise RunError(f"Unknown tag {t!r} in scheme {scheme.meta.name!r}.")
            langs.append(lang)
        subject = scheme.get(tag)
        if subject is None:
            raise RunError(f"Unknown tag {tag!r}.")
        neutral = neutral_tag or _umbrella_for(scheme, subject)
        if neutral is None:
            raise RunError(
                f"No umbrella tag for {tag!r} to convert from. The conversion "
                f"probe rewrites neutral text into the variety, so it needs one.")
        chosen_specs, edited_specs = specs_for(specs)
        machine = DialectMachine(
            scheme=scheme, tag=tag, engine=engine, client=client,
            attestors=attestors or _default_attestors(engine),
            varieties=langs, neutral_tag=neutral,
            scenario=scenario or chosen_specs[0].scenario, corpus=corpus,
            record=DbAttestorRecord())
    else:
        raise RunError(f"Unknown test type {test_type!r}.")

    sess = Session(id=run_id, machine=machine, corpus=corpus, mode=mode,
                   prompt_edited=edited_specs)

    pre = machine.preflight()  # type: ignore[attr-defined]
    with session() as s:
        repo.create_run(s, {
            "id": run_id, "test_type": test_type, "mode": mode, "engine": engine,
            "tag": tag, "cls": getattr(machine, "lang").cls, "scheme": scheme.meta.name,
            "pivot": getattr(machine, "pivot", pivot), "judge_model": judge_model,
            "attestors": pre.get("attestors"),
            "method_version": METHOD_VERSION, "status": "running",
            "plan": pre, "prompt_edited": edited_specs,
            "corpus_path": str(corpus.path),
        })

    with _LOCK:
        _SESSIONS[run_id] = sess
    return sess


def _umbrella_for(scheme, lang):
    """The locale list's unqualified tag for this language, if it has one.

    `inclusive`, not ISO scope: a TMS marks `pt` inclusive because it is the
    unqualified spelling standing over `pt-br`, which is a statement about that
    list's shape rather than about the language.
    """
    for cand in scheme.languages.values():
        if cand.lang == lang.lang and cand.inclusive:
            return cand.tag
    return None


def _default_attestors(engine: str) -> list[str]:
    """Every model the configuration can reach, minus the producer's family.

    Deliberately wide. The panel is then narrowed by the controls, which is the
    only narrowing that is evidence — a shortlist chosen in advance is a guess
    about who knows the dialect, and that guess is exactly what the controls
    exist to replace.
    """
    from polyglot.core import discovery

    models, _binding = discovery.available()
    return discovery.propose(engine, models)["attestors"]


# -- driving it ----------------------------------------------------------------

def plan(sess: Session) -> dict | None:
    """What happens next, and what it will cost. Free.

    Cached on the session so that `step()` sends the object the page was shown
    rather than a second construction of it.
    """
    sess.touched_at = time.time()
    if sess.status != "running":
        return None
    p = sess.machine.plan()  # type: ignore[attr-defined]
    sess.pending = p
    return p.as_dict() if p else None


def step(sess: Session, *, calls: list[dict] | None = None) -> dict | None:
    """Take one step. Returns the executed step, or None when the run is over."""
    with sess.lock:
        if sess.status != "running":
            return None
        sess.touched_at = time.time()
        planned = sess.pending or sess.machine.plan()  # type: ignore[attr-defined]
        if planned is None:
            _finish(sess)
            return None

        edited = False
        sent: list[Call] | None = None
        if calls is not None:
            sent = [Call.from_dict(c) for c in calls]
            edited = [c.as_dict() for c in sent] != [c.as_dict()
                                                     for c in planned.calls]
            if edited and not planned.editable:
                raise RunError(
                    f"The {planned.kind!r} step cannot be rewritten. Its prompts "
                    f"are the instrument — an edited control is no longer a "
                    f"control, and an edited qualification uncertifies the "
                    f"reader it was meant to certify.")

        budget = sum((v for v in _spent(sess).values()), 0)
        if budget + planned.cost > settings.max_calls_per_run:
            raise RunError(
                f"This run has spent {budget} calls and the next step needs "
                f"{planned.cost}, which would pass the per-run ceiling of "
                f"{settings.max_calls_per_run}. Raise MAX_CALLS_PER_RUN if that "
                f"is really what you want.")

        try:
            ex = sess.machine.execute(planned, calls=sent, edited=edited)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001 — surfaced to the page, not swallowed
            sess.status = "failed"
            sess.error = f"{type(e).__name__}: {e}"
            _close(sess)
            with session() as s:
                repo.finish_run(s, sess.id, status="failed", error=sess.error)
            raise RunError(sess.error) from e

        if edited:
            sess.prompt_edited = True
        sess.pending = None
        with session() as s:
            repo.add_step(s, sess.id, ex.row())

        # A terminal step leaves nothing to plan, and asking costs nothing, so
        # the verdict arrives with the last step instead of making the reader
        # click once more for a step that does not exist.
        if sess.machine.plan() is None:  # type: ignore[attr-defined]
            _finish(sess)
        return ex.as_dict()


def rerun(sess: Session, ordinal: int) -> dict:
    """Throw away a step and everything after it, and plan again.

    Not a rollback: the machine's state *is* its history, so discarding the tail
    is the entire operation. The discarded steps keep their database rows marked
    superseded — "the first generation was refused and the second was not" is a
    finding, and a record that kept only the second would hide it.
    """
    with sess.lock:
        history = sess.machine.history  # type: ignore[attr-defined]
        if not 0 <= ordinal < len(history):
            raise RunError(f"Step {ordinal} does not exist in this run.")
        dropped = len(history) - ordinal
        del history[ordinal:]
        sess.status = "running"
        sess.error = None
        sess.pending = None
        with session() as s:
            for i in range(ordinal, ordinal + dropped):
                repo.supersede_step(s, sess.id, i)
            repo.finish_run(s, sess.id, status="running")
        return {"dropped": dropped, "from": ordinal}


def set_panel(sess: Session, attestors: list[str]) -> dict:
    machine = sess.machine
    if not hasattr(machine, "set_panel"):
        raise RunError("Only a dialect test has a control-model panel.")
    machine.set_panel(attestors)  # type: ignore[attr-defined]
    sess.pending = None
    pre = machine.preflight()  # type: ignore[attr-defined]
    with session() as s:
        repo.finish_run(s, sess.id, plan=pre, attestors=pre.get("attestors"),
                        finished_at=None, status="running")
    return pre


def run_auto(sess: Session) -> None:
    """Drive the run to completion on a background thread.

    The same `plan`/`execute` pair manual mode uses, in a loop. Auto mode is not
    a second path through the method; it is the absence of a pause.
    """
    def drive() -> None:
        sess.auto = True
        try:
            while sess.status == "running" and not sess.cancel:
                planned = sess.machine.plan()  # type: ignore[attr-defined]
                if planned is None:
                    _finish(sess)
                    break
                sess.pending = planned
                step(sess)
            if sess.cancel and sess.status == "running":
                sess.status = "cancelled"
                _close(sess)
                with session() as s:
                    repo.finish_run(s, sess.id, status="cancelled")
        except Exception as e:  # noqa: BLE001
            sess.status = "failed"
            sess.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            _close(sess)
            with session() as s:
                repo.finish_run(s, sess.id, status="failed", error=sess.error)
        finally:
            sess.auto = False

    threading.Thread(target=drive, daemon=True, name=f"run-{sess.id}").start()


def _spent(sess: Session) -> dict:
    out: dict[str, int] = {}
    for ex in sess.machine.history:  # type: ignore[attr-defined]
        for k, v in (ex.calls or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def _finish(sess: Session) -> None:
    if sess.status != "running":
        return
    sess.status = "done"
    outcome = sess.machine.outcome()  # type: ignore[attr-defined]
    _close(sess)
    with session() as s:
        repo.finish_run(s, sess.id, status="done", outcome=outcome,
                        calls=_spent(sess), prompt_edited=sess.prompt_edited,
                        backtranslator=outcome.get("backtranslator"))
        # A run whose prompts or scenarios were rewritten measured a different
        # instrument, and results are only comparable within one. It is kept in
        # full — the corpus has every text — and it is never promoted to a
        # standing verdict.
        if not sess.prompt_edited and not outcome.get("incomplete"):
            repo.put_verdict(s, _verdict_row(sess, outcome), promote=None)


def _verdict_row(sess: Session, outcome: dict) -> dict:
    machine = sess.machine
    return {
        "run_id": sess.id,
        "test_type": outcome["test_type"], "engine": outcome["engine"],
        "tag": outcome["tag"], "cls": outcome.get("cls"),
        "script": getattr(machine, "lang").script,
        "level": outcome["level"], "confidence": outcome.get("confidence", "medium"),
        "evidence": outcome.get("evidence"),
        "s_lang": outcome.get("s_lang"), "s_content": outcome.get("s_content"),
        "ci_low": outcome.get("ci_low"), "ci_high": outcome.get("ci_high"),
        "borderline": bool(outcome.get("borderline")),
        "refusals": int(outcome.get("refusals") or 0),
        "designator": outcome.get("designator"),
        "backtranslator": outcome.get("backtranslator"),
        "judge": outcome.get("judge"), "pivot": outcome.get("pivot", "en"),
        "attestors": outcome.get("attestors"),
        "detail": {"corpus": str(sess.corpus.path),
                   "workflow": outcome.get("workflow"),
                   "panel": outcome.get("panel"),
                   "conversion": outcome.get("conversion"),
                   "grounding": outcome.get("grounding"),
                   "qualification": outcome.get("qualification")},
        "notes": outcome.get("notes"),
        "method_version": METHOD_VERSION,
    }


def adjudicate(sess: Session, overrides: list[dict]) -> dict:
    result = sess.machine.adjudicate(overrides)  # type: ignore[attr-defined]
    with session() as s:
        repo.finish_run(s, sess.id, adjudication=result)
    return result


def _close(sess: Session) -> None:
    try:
        sess.corpus.close()
    except Exception:  # noqa: BLE001 — closing a file is not worth failing a run over
        pass


def close(run_id: str) -> bool:
    sess = _SESSIONS.pop(run_id, None)
    if sess is None:
        return False
    if sess.status == "running":
        sess.status = "cancelled"
        with session() as s:
            repo.finish_run(s, run_id, status="cancelled")
    _close(sess)
    return True


def snapshot(sess: Session) -> dict:
    """Everything the page needs for one run, in one response."""
    machine = sess.machine
    outcome = None
    if sess.status in ("done",):
        try:
            outcome = machine.outcome()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            outcome = None
    return {
        "id": sess.id, "status": sess.status, "mode": sess.mode,
        "auto": sess.auto, "error": sess.error,
        "test_type": machine.test_type,  # type: ignore[attr-defined]
        "tag": machine.tag, "engine": machine.engine,  # type: ignore[attr-defined]
        "prompt_edited": sess.prompt_edited,
        "plan": machine.preflight(),  # type: ignore[attr-defined]
        "steps": [e.as_dict() for e in machine.history],  # type: ignore[attr-defined]
        "spent": _spent(sess),
        "next": (p.as_dict() if (p := machine.plan()) else None),  # type: ignore[attr-defined]
        "outcome": outcome,
        "corpus": str(sess.corpus.path),
    }


def reset_for_tests() -> None:
    for sid in list(_SESSIONS):
        close(sid)
