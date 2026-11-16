"""Every write the application makes, in one place.

Two rules are enforced here rather than by the schema, because the schema cannot
express either of them:

1. **A deterministic negative collapses onto the graded row.** It stores
   `NO_INSTRUMENT`, which makes it comparable to *every* reader rather than to
   none, so re-measuring a language whose verdict moved between "settled by the
   gate" and "scored" must update one row instead of accumulating two. In the
   previous tool this was discovered through `kk-Latn` quietly appearing twice.

2. **Nothing is destroyed.** A new verdict supersedes the current one by
   clearing its flag, never by deleting it, and promotion is reversible. The
   corpus is append-only for the same reason: the row somebody disagrees with is
   the one they most need to be able to look at.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from polyglot.settings import METHOD_VERSION
from polyglot.store.models import (NO_INSTRUMENT, AttestorRecord, Batch, BatchItem,
                                   Generation, Qualification, Run, RunStep, Verdict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- runs ----------------------------------------------------------------------

def create_run(s: Session, data: dict) -> Run:
    run = Run(**data)
    s.add(run)
    s.flush()
    return run


def get_run(s: Session, run_id: str) -> Run | None:
    return s.get(Run, run_id)


def list_runs(s: Session, *, engine: str | None = None, tag: str | None = None,
              test_type: str | None = None, limit: int = 50, offset: int = 0
              ) -> tuple[list[Run], int]:
    from sqlalchemy import func

    q = select(Run)
    if engine:
        q = q.where(Run.engine == engine)
    if tag:
        q = q.where(Run.tag == tag)
    if test_type:
        q = q.where(Run.test_type == test_type)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(s.scalars(q.order_by(Run.started_at.desc()).limit(limit).offset(offset)))
    return rows, total


def add_step(s: Session, run_id: str, data: dict) -> RunStep:
    step = RunStep(run_id=run_id, **data)
    s.add(step)
    s.flush()
    return step


def supersede_step(s: Session, run_id: str, ordinal: int) -> int:
    """Mark every existing attempt at this ordinal as replaced.

    Returns the next attempt number. The earlier rows stay: "the first
    generation was refused and the second was not" is a finding, and a schema
    that kept only the winner would hide it.
    """
    rows = list(s.scalars(select(RunStep).where(RunStep.run_id == run_id,
                                                RunStep.ordinal == ordinal)))
    for r in rows:
        r.superseded = True
    return max((r.attempt for r in rows), default=-1) + 1


#: Statuses that end a run. `finish_run` stamps `finished_at` for these and
#: clears it for anything else, so re-opening a run to re-run a step does not
#: leave it looking finished.
TERMINAL = {"done", "failed", "cancelled"}


def finish_run(s: Session, run_id: str, **fields) -> Run | None:
    run = s.get(Run, run_id)
    if run is None:
        return None
    for k, v in fields.items():
        setattr(run, k, v)
    if "status" in fields:
        run.finished_at = _now() if fields["status"] in TERMINAL else None
    return run


# -- the corpus ----------------------------------------------------------------

def record_generation(s: Session, data: dict) -> Generation:
    row = Generation(**data)
    s.add(row)
    return row


# -- verdicts ------------------------------------------------------------------

def current_verdict(s: Session, *, engine: str, tag: str, test_type: str,
                    method_version: str = METHOD_VERSION) -> Verdict | None:
    """The row that counts for this identity, whichever reader produced it.

    Reader is deliberately *not* a filter. A caller asking "do we already know
    about this language" wants an answer, and "yes, through a different
    back-translator" is one — it is the batch scan's skip test, and the reader
    is right there on the row for anyone who needs to care.
    """
    rows = list(s.scalars(select(Verdict).where(
        Verdict.engine == engine, Verdict.tag == tag,
        Verdict.test_type == test_type,
        Verdict.method_version == method_version,
        Verdict.current.is_(True))))
    if not rows:
        return None
    # An approved row outranks an unapproved one; otherwise the freshest.
    rows.sort(key=lambda r: (r.approved, r.measured_at), reverse=True)
    return rows[0]


def put_verdict(s: Session, data: dict, *, promote: bool | None = None) -> Verdict:
    """Record a measurement, and decide whether it becomes the standing answer.

    `promote`:
        None   (the default) promote only if nothing is current for this
               identity, or if what is current was never approved. A re-run
               never silently displaces a verdict a person signed off on —
               it lands beside it and the page shows both.
        True   promote regardless. The deliberate gesture.
        False  record only. For a run whose prompts were edited, and for a
               batch item that wants a human pass before it counts.
    """
    bt = data.get("backtranslator") or NO_INSTRUMENT
    data["backtranslator"] = bt

    incumbent = s.scalar(select(Verdict).where(
        Verdict.engine == data["engine"], Verdict.tag == data["tag"],
        Verdict.test_type == data["test_type"],
        Verdict.method_version == data.get("method_version", METHOD_VERSION),
        Verdict.backtranslator == bt,
        Verdict.current.is_(True)))

    # A deterministic negative needs no reader, so it is comparable to all of
    # them. If one is already current under a real reader, this is the same
    # claim re-measured and must take its place rather than open a second row.
    if incumbent is None and bt == NO_INSTRUMENT:
        incumbent = s.scalar(select(Verdict).where(
            Verdict.engine == data["engine"], Verdict.tag == data["tag"],
            Verdict.test_type == data["test_type"],
            Verdict.method_version == data.get("method_version", METHOD_VERSION),
            Verdict.current.is_(True)))

    make_current = promote if promote is not None else (
        incumbent is None or not incumbent.approved)

    row = Verdict(**{**data, "current": bool(make_current)})
    if make_current and incumbent is not None:
        incumbent.current = False
    s.add(row)
    s.flush()
    return row


def promote_verdict(s: Session, verdict_id: int) -> Verdict | None:
    """Make one stored measurement the one that counts. Destroys nothing."""
    row = s.get(Verdict, verdict_id)
    if row is None:
        return None
    s.execute(update(Verdict).where(
        Verdict.engine == row.engine, Verdict.tag == row.tag,
        Verdict.test_type == row.test_type,
        Verdict.method_version == row.method_version,
        Verdict.backtranslator == row.backtranslator,
        Verdict.id != row.id).values(current=False))
    row.current = True
    return row


def approve_verdict(s: Session, verdict_id: int, *, approved: bool = True
                    ) -> Verdict | None:
    """Sign off on a verdict, which is what makes a batch scan skip it.

    Approval and currency are separate on purpose. Current means "this is our
    latest reading"; approved means "a person looked at the evidence and stood
    behind it". A scan that skipped everything merely *current* would skip every
    draft it had just produced itself.
    """
    row = s.get(Verdict, verdict_id)
    if row is None:
        return None
    row.approved = approved
    row.approved_at = _now() if approved else None
    if approved:
        promote_verdict(s, verdict_id)
    return row


def verdict_history(s: Session, *, engine: str, tag: str, test_type: str,
                    method_version: str = METHOD_VERSION) -> list[Verdict]:
    return list(s.scalars(select(Verdict).where(
        Verdict.engine == engine, Verdict.tag == tag,
        Verdict.test_type == test_type,
        Verdict.method_version == method_version)
        .order_by(Verdict.current.desc(), Verdict.measured_at.desc())))


def list_verdicts(s: Session, *, engine: str | None = None,
                  test_type: str | None = None, approved: bool | None = None,
                  method_version: str = METHOD_VERSION) -> list[Verdict]:
    q = select(Verdict).where(Verdict.method_version == method_version,
                              Verdict.current.is_(True))
    if engine:
        q = q.where(Verdict.engine == engine)
    if test_type:
        q = q.where(Verdict.test_type == test_type)
    if approved is not None:
        q = q.where(Verdict.approved.is_(approved))
    return list(s.scalars(q.order_by(Verdict.tag)))


# -- qualification cache -------------------------------------------------------

def get_qualification(s: Session, bt: str, tag: str, method_version: str):
    return s.scalar(select(Qualification).where(
        Qualification.backtranslator == bt, Qualification.tag == tag,
        Qualification.method_version == method_version))


def put_qualification(s: Session, data: dict) -> Qualification:
    row = get_qualification(s, data["backtranslator"], data["tag"],
                            data["method_version"])
    if row is None:
        row = Qualification(**data)
        s.add(row)
    else:
        for k, v in data.items():
            setattr(row, k, v)
        row.checked_at = _now()
    return row


# -- attestor record -----------------------------------------------------------

def get_attestor(s: Session, model: str, tag: str, role: str, method_version: str,
                 script: str | None):
    return s.scalar(select(AttestorRecord).where(
        AttestorRecord.model == model, AttestorRecord.tag == tag,
        AttestorRecord.role == role,
        AttestorRecord.method_version == method_version,
        AttestorRecord.script.is_(script) if script is None
        else AttestorRecord.script == script,
        AttestorRecord.current.is_(True)))


def put_attestor(s: Session, data: dict, *, promote: bool | None = None):
    incumbent = get_attestor(s, data["model"], data["tag"], data["role"],
                             data["method_version"], data.get("script"))
    make_current = promote if promote is not None else incumbent is None
    row = AttestorRecord(**{**data, "current": bool(make_current)})
    if make_current and incumbent is not None:
        incumbent.current = False
    s.add(row)
    s.flush()
    return row


def attestors_for(s: Session, tag: str, role: str, method_version: str,
                  script: str | None):
    q = select(AttestorRecord).where(
        AttestorRecord.tag == tag, AttestorRecord.role == role,
        AttestorRecord.method_version == method_version,
        AttestorRecord.current.is_(True))
    if script is not None:
        q = q.where(AttestorRecord.script == script)
    return list(s.scalars(q))


# -- batches (the next page's tables, already joined to runs) ------------------

def create_batch(s: Session, data: dict) -> Batch:
    row = Batch(**data)
    s.add(row)
    s.flush()
    return row


def add_batch_item(s: Session, batch_id: str, data: dict) -> BatchItem:
    row = BatchItem(batch_id=batch_id, **data)
    s.add(row)
    return row
