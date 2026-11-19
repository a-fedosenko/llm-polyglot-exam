"""A scan of one engine down a list of tags.

**It is not a second traversal.** Every item is a `runner.Session` driven by
`runner.drive` — the same `plan()`/`execute()` pair the single-language page
steps through by hand. If a batch could disagree with the page about what the
method does, the page would be a lie told in the project's own voice, and the
previous project's conventions record what that drift costs: an analysis
harness re-implemented a production call, got it subtly wrong, and 68 bad
verdicts reached a protocol's conclusions.

What this module owns is therefore only the three things a queue has that a
single run does not:

    the plan     which classes will be probed, which tags inherit, what is
                 skipped and why, and what it will cost — worked out before
                 anything is spent, because a plan that costs money is a plan
                 nobody can afford to look at
    the queue    one run at a time. SQLite takes one writer, and two concurrent
                 scans would spend against a budget neither of them set
    the budget   `max_calls`, enforced *before* an item starts rather than
                 discovered after it

Two rules it must not break, both inherited:

**Nothing here may skip a control.** The attestor and qualification records
route — they decide who is asked first — and they never vouch. A batch that
skipped a manufactured control to save money would make a wrongly-recorded
model permanently self-confirming. This module never touches those tables; the
machines do, exactly as they do for a hand-run test.

**A batch never approves its own output.** Approval is a person reading
evidence, and the skip rule is built on it: a scan that signed off on its own
drafts would skip, on its next pass, everything it had just produced.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from polyglot.core import reference
from polyglot.core.probe.ladder_support import candidates_for
from polyglot.core.probe.panel import eligible
from polyglot.core.scheme import Scheme, load_scheme
from polyglot.engine import runner
from polyglot.engine.capability import RUNG1_ITEMS, RUNG3_ITEMS
from polyglot.engine.runner import MAX_ATTESTORS, MIN_ATTESTORS
from polyglot.settings import METHOD_VERSION, settings
from polyglot.store import repo, session

#: How much of what is already stored a scan is allowed to measure again. The
#: three values are the three states `repo.current_verdict` distinguishes, and
#: they are not the same question — which is why this is not a boolean:
#:
#:    none        only tags with nothing stored. The cheap pass that fills gaps.
#:    drafts      also re-measure what was measured but never signed off on.
#:                The default, because a verdict nobody has looked at is a draft.
#:    all         also re-measure approved rows. A deliberate gesture: it does
#:                not destroy the approved row — `put_verdict` lands the new
#:                measurement beside it and leaves the approved one standing —
#:                but it does spend money re-asking a settled question.
RESCAN = ("none", "drafts", "all")


class BatchError(RuntimeError):
    pass


# -- what a scan would do ------------------------------------------------------

def _representative(scheme: Scheme, members: list[str]) -> str:
    """The tag a class is actually probed through.

    The least-qualified member: base-language capability is a property of the
    `(iso639_3, script)` class, so the tag carrying no region is the one whose
    designator names no region either. Ties break alphabetically so a plan is
    reproducible — a scan whose representative depended on dict order would
    make two identical requests measure different things.
    """
    def rank(tag: str) -> tuple:
        lang = scheme.get(tag)
        return (bool(lang and lang.region), len(tag), tag)

    return sorted(members, key=rank)[0]


def _estimate(scheme: Scheme, tag: str) -> dict:
    """What one class costs, from the same definition the run will use.

    `candidates_for` is the single source for which designators get asked, so
    the count here cannot drift from the count the ladder spends. The arithmetic
    mirrors `CapabilityMachine.preflight` deliberately: a batch that estimated
    by its own formula would quote a price the run does not charge.
    """
    lang = scheme.get(tag)
    n = len(candidates_for(scheme, lang)) if lang is not None else 1
    return {
        "minimum": n * RUNG1_ITEMS,
        "typical": RUNG1_ITEMS + 4 + RUNG1_ITEMS * 2,
        "maximum": n * RUNG1_ITEMS + 4 + (RUNG1_ITEMS + RUNG3_ITEMS) * 2,
    }


def _dialect_estimate(attestors: int) -> dict:
    """What one variety costs, from `DialectMachine.preflight`'s own arithmetic.

    Two produces, four conversions, then three control calls and two
    attestations per attestor that survives the family guard. The floor is the
    free half alone: identical arms stop the battery before a conversion, and a
    clean square settles a positive before a panel is paid for.
    """
    return {"minimum": 2,
            "typical": 2 + 4 + 5 * attestors,
            "maximum": 2 + 4 + 5 * attestors}


def preflight(*, engine: str, tags: list[str], test_type: str = "capability",
              rescan: str = "drafts", scheme_name: str | None = None,
              max_calls: int | None = None, attestors: list[str] | None = None,
              include_unconfirmed: bool = False) -> dict:
    """Say what the scan would do. Costs nothing.

    That it costs nothing is the whole point: this is the thing you are meant
    to read *before* committing a few thousand calls, so it may not make a
    single one.

    **The two tests walk different things, and the plan says which.** A
    capability scan walks equivalence classes — one experiment per
    `(iso639-3, script)`, with the class's other tags inheriting it. A dialect
    battery needs a *pair*, so it walks varieties: each against its family's
    reference, which is what turns a family of 26 from 325 pairs into 25
    contrasts, and is also what makes two results in one family comparable.
    """
    if test_type not in ("capability", "dialect"):
        raise BatchError(f"Unknown test type {test_type!r}.")
    if rescan not in RESCAN:
        raise BatchError(f"rescan must be one of {RESCAN}; got {rescan!r}.")

    scheme = load_scheme(scheme_name or settings.scheme)
    known, unknown = [], []
    for tag in tags:
        (known if scheme.get(tag) is not None else unknown).append(tag)
    if not known:
        raise BatchError("Nothing in the request is a tag this scheme knows.")

    if test_type == "dialect":
        return _dialect_preflight(
            scheme=scheme, engine=engine, known=known, unknown=unknown,
            rescan=rescan, max_calls=max_calls, attestors=list(attestors or []),
            include_unconfirmed=include_unconfirmed)

    by_cls: dict[str, list[str]] = {}
    for tag in known:
        lang = scheme.get(tag)
        by_cls.setdefault(lang.cls, []).append(scheme.get(tag).tag)

    items, to_scan = [], 0
    est = {"minimum": 0, "typical": 0, "maximum": 0}
    with session() as s:
        for cls, members in sorted(by_cls.items()):
            rep = _representative(scheme, members)
            lang = scheme.get(rep)
            current = repo.current_verdict(s, engine=engine, tag=rep,
                                           test_type=test_type)
            skip = None
            if current is not None:
                if current.approved and rescan != "all":
                    skip = "approved"
                elif not current.approved and rescan == "none":
                    skip = "measured"

            # Siblings that share this class but would now be asked a
            # *different* designator, because the designator names the region.
            # They inherit an answer that was never asked of them. Honestly
            # labelled on the row and named here, which is the decision this
            # project made rather than paying to close it.
            primary = candidates_for(scheme, lang)[0].value if lang else ""
            divergent = []
            for other in members:
                if other == rep:
                    continue
                ol = scheme.get(other)
                cands = candidates_for(scheme, ol) if ol else []
                if cands and cands[0].value != primary:
                    divergent.append(other)

            item = {
                "cls": cls, "representative": rep, "members": sorted(members),
                "language": lang.name if lang else None,
                "script": lang.script if lang else None,
                "designator": primary,
                "divergent_siblings": sorted(divergent),
                "inherits": len(members) - 1,
                "skip_reason": skip,
                "known_level": None if current is None else current.level,
                "known_approved": bool(current is not None and current.approved),
            }
            if skip is None:
                to_scan += 1
                for k in est:
                    est[k] += _estimate(scheme, rep)[k]
            items.append(item)

    skipped = {}
    for it in items:
        if it["skip_reason"]:
            skipped[it["skip_reason"]] = skipped.get(it["skip_reason"], 0) + 1

    # Fail closed, and say so before anything is spent rather than after. The
    # ceiling is compared against the *minimum*, because stopping a queue is
    # only possible between items: an item admitted on an optimistic estimate
    # can still overshoot, and the honest thing is to refuse to start it.
    over_budget = bool(max_calls is not None and est["minimum"] > max_calls)

    return {
        "engine": engine, "test_type": test_type, "rescan": rescan,
        "scheme": scheme.meta.name, "method_version": METHOD_VERSION,
        #: What one item of this plan *is*. The two tests walk different units —
        #: a class and a variety — and a page that called both "classes" would
        #: be describing a dialect scan in the capability scan's words.
        "unit": "class",
        "requested_tags": len(known),
        "unknown_tags": unknown,
        "classes": len(items), "to_scan": to_scan,
        "inheriting_tags": sum(it["inherits"] for it in items
                               if not it["skip_reason"]),
        "skipped": skipped,
        "divergent_siblings": sorted(
            t for it in items if not it["skip_reason"]
            for t in it["divergent_siblings"]),
        "unconfirmed": [],
        "attestors": [],
        "estimated_calls": est,
        "max_calls": max_calls,
        "over_budget": over_budget,
        "items": items,
    }


# -- what a dialect scan would do ----------------------------------------------

def _dialect_preflight(*, scheme: Scheme, engine: str, known: list[str],
                       unknown: list[str], rescan: str, max_calls: int | None,
                       attestors: list[str], include_unconfirmed: bool) -> dict:
    """One item per variety, each against its family's reference.

    **It walks varieties, not classes, and nothing inherits.** A tier can be a
    property of an equivalence class; whether a model marks *this* variety
    cannot be — that is the whole claim the test makes, so there is no second
    tag it could be propagated to.

    Every refusal here is one the engine would raise at the call. Applying the
    rules in the plan instead is what stops a scan of six hundred tags from
    being six hundred ways to discover something that was knowable for free —
    and `skip_reason` keeps them apart, because "it *is* the baseline" and
    "nobody has decided this family's baseline" are different answers.
    """
    if not MIN_ATTESTORS <= len(attestors) <= MAX_ATTESTORS:
        raise BatchError(
            f"Pick between {MIN_ATTESTORS} and {MAX_ATTESTORS} control models; "
            f"got {len(attestors)}. A dialect battery has no judge — the panel "
            f"is its instrument — and each member costs three control calls "
            f"plus two attestations on every item, so the panel is the scan's "
            f"budget as much as its method.")
    # The family guard is not a setting: a model may not grade its own output.
    # Applied to the estimate as well, or the plan would quote a price for
    # attestors the run will drop.
    field = eligible(attestors, producer=engine)
    if not field:
        raise BatchError(
            f"Every control model named is from {engine}'s own family, and a "
            f"model may not judge its own output — that measures "
            f"self-consistency and reports it as agreement. Name a panel from "
            f"other families.")
    per = _dialect_estimate(len(field))

    items, to_scan = [], 0
    est = {"minimum": 0, "typical": 0, "maximum": 0}
    with session() as s:
        for tag in known:
            lang = scheme.get(tag)
            pair = reference.pairing(scheme, lang)
            current = repo.current_verdict(s, engine=engine, tag=lang.tag,
                                           test_type="dialect")
            skip = pair.blocked
            if skip is None and not pair.decided and not include_unconfirmed:
                # Runnable, and refused by default. The family's baseline is
                # either written down and unconfirmed (`proposed`) or standing
                # in by list order (`provisional`); either way nobody has stood
                # behind it, and measuring hundreds of varieties against such a
                # baseline would turn an open question into a table of claims.
                # Offered as a deliberate gesture instead.
                skip = "unconfirmed-reference"
            if skip is None and current is not None:
                if current.approved and rescan != "all":
                    skip = "approved"
                elif not current.approved and rescan == "none":
                    skip = "measured"

            item = {
                "cls": lang.cls, "representative": lang.tag,
                "members": [lang.tag],
                "contrast": pair.reference,
                "reference_basis": pair.basis,
                "reference_confirmed": pair.decided,
                "reference_why": pair.why,
                "blocked_why": reference.BLOCKED.get(pair.blocked or "", ""),
                "language": lang.name, "script": lang.script,
                "region": lang.region_name or lang.region,
                "designator": None,
                "divergent_siblings": [],
                "inherits": 0,
                "skip_reason": skip,
                "known_level": None if current is None else current.level,
                "known_approved": bool(current is not None and current.approved),
            }
            if skip is None:
                to_scan += 1
                for k in est:
                    est[k] += per[k]
            items.append(item)

    skipped: dict[str, int] = {}
    for it in items:
        if it["skip_reason"]:
            skipped[it["skip_reason"]] = skipped.get(it["skip_reason"], 0) + 1

    over_budget = bool(max_calls is not None and est["minimum"] > max_calls)
    return {
        "engine": engine, "test_type": "dialect", "rescan": rescan,
        "scheme": scheme.meta.name, "method_version": METHOD_VERSION,
        "unit": "variety",
        "requested_tags": len(known),
        "unknown_tags": unknown,
        "classes": len(items), "to_scan": to_scan,
        "inheriting_tags": 0,
        "skipped": skipped,
        "divergent_siblings": [],
        #: Named rather than counted, for the same reason `divergent_siblings`
        #: is: these are measured against a baseline nobody has confirmed, and
        #: a reader has to be able to see *which*.
        "unconfirmed": sorted(it["representative"] for it in items
                              if not it["skip_reason"]
                              and not it["reference_confirmed"]),
        "attestors": list(field),
        "estimated_calls": est,
        "max_calls": max_calls,
        "over_budget": over_budget,
        "items": items,
    }


# -- running one ---------------------------------------------------------------

@dataclass
class BatchSession:
    """A scan in flight, in this process.

    The database row is the record; this is the thing that can be stopped.
    """

    id: str
    engine: str
    test_type: str
    #: The run currently being driven, so the page can open it while it runs.
    run_id: str | None = None
    status: str = "running"
    error: str | None = None
    #: Set by the page to stop after the item in flight. Never mid-run: every
    #: call of that run is paid for by the time it could be checked, so
    #: stopping sooner would only mean throwing away an answer already bought.
    cancel: bool = False
    calls_used: int = 0
    started_at: float = field(default_factory=time.time)


_BATCHES: dict[str, BatchSession] = {}
_LOCK = threading.Lock()


def get(batch_id: str) -> BatchSession | None:
    return _BATCHES.get(batch_id)


def live_for(engine: str | None = None) -> list[BatchSession]:
    return [b for b in _BATCHES.values()
            if b.status == "running" and (engine is None or b.engine == engine)]


def start(*, engine: str, tags: list[str], test_type: str = "capability",
          rescan: str = "drafts", scheme_name: str | None = None,
          judge_model: str | None = None,
          backtranslator_panel: list[str] | None = None,
          attestors: list[str] | None = None, scenario: str | None = None,
          include_unconfirmed: bool = False,
          max_calls: int | None = None, pivot: str | None = None) -> dict:
    """Plan the scan, write it down, and start working the queue."""
    with _LOCK:
        if live_for():
            raise BatchError(
                "A scan is already running. One at a time: SQLite takes one "
                "writer, and two scans would spend against a budget neither of "
                "them set.")

    plan = preflight(engine=engine, tags=tags, test_type=test_type,
                     rescan=rescan, scheme_name=scheme_name, max_calls=max_calls,
                     attestors=attestors, include_unconfirmed=include_unconfirmed)
    if plan["over_budget"]:
        raise BatchError(
            f"The scan needs at least {plan['estimated_calls']['minimum']} "
            f"calls and the ceiling is {max_calls}. Raise the ceiling or "
            f"narrow the scope — it fails closed rather than starting and "
            f"stopping half way.")
    if not plan["to_scan"]:
        raise BatchError(
            f"Every {plan['unit']} in this scope is either settled under the "
            f"current re-scan policy or is one this test cannot answer, so the "
            f"scan would make no calls at all.")

    batch_id = uuid.uuid4().hex[:12]
    settings_blob = {
        "rescan": rescan, "judge_model": judge_model,
        "backtranslator_panel": list(backtranslator_panel or []),
        "attestors": list(attestors or []), "scenario": scenario,
        "include_unconfirmed": include_unconfirmed,
        "pivot": pivot or settings.pivot, "plan": plan,
    }
    with session() as s:
        repo.create_batch(s, {
            "id": batch_id, "test_type": test_type, "engine": engine,
            "scheme": plan["scheme"], "method_version": METHOD_VERSION,
            "status": "running", "requested_tags": tags,
            "unknown_tags": plan["unknown_tags"],
            # The column is a boolean and the policy has three values; it holds
            # the half the schema was built to ask, and `settings` holds the
            # rest. Kept in step rather than re-derived.
            "rescan_approved": rescan == "all",
            "settings": settings_blob, "max_calls": max_calls,
        })
        for it in plan["items"]:
            repo.add_batch_item(s, batch_id, {
                "cls": it["cls"], "representative": it["representative"],
                "members": it["members"],
                # Null on a capability item, which walks classes and has no
                # pair. On a dialect item it is the other arm, carried on the
                # row rather than re-derived at run time: a queue that worked
                # out its own pair could contrast against something the plan
                # did not say it would.
                "contrast": it.get("contrast"),
                "status": "skipped" if it["skip_reason"] else "pending",
                "skip_reason": it["skip_reason"],
            })

    sess = BatchSession(id=batch_id, engine=engine, test_type=test_type)
    with _LOCK:
        _BATCHES[batch_id] = sess
    threading.Thread(target=_work, args=(sess, settings_blob, scheme_name),
                     daemon=True, name=f"batch-{batch_id}").start()
    return {"id": batch_id, "plan": plan}


def pause(batch_id: str) -> bool:
    sess = _BATCHES.get(batch_id)
    if sess is None or sess.status != "running":
        return False
    sess.cancel = True
    return True


def _work(sess: BatchSession, blob: dict, scheme_name: str | None) -> None:
    """The queue. One item, one run, in order."""
    scheme = load_scheme(scheme_name or settings.scheme)
    try:
        while not sess.cancel:
            with session() as s:
                batch = repo.get_batch(s, sess.id)
                item = repo.next_batch_item(s, sess.id)
                if item is None:
                    break
                rep, members = item.representative, list(item.members or [])
                contrast = item.contrast
                ceiling = batch.max_calls
                item.status = "running"
                item.started_at = repo.now()

            need = (_estimate(scheme, rep)["minimum"]
                    if sess.test_type == "capability"
                    else _dialect_estimate(len(blob.get("attestors") or []))
                         ["minimum"])
            if ceiling is not None and sess.calls_used + need > ceiling:
                # Fail closed. Not "stop when the money is gone" — stop before
                # starting something that cannot be paid for, so the queue never
                # contains a half-measured language.
                _halt(sess, f"Stopped at {sess.calls_used} of {ceiling} calls: "
                            f"the next class needs at least {need} more.")
                return

            _run_item(sess, scheme, rep, members, contrast, blob)

        _close(sess, "cancelled" if sess.cancel else "done")
    except Exception as e:  # noqa: BLE001
        sess.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        _close(sess, "failed")


def _run_item(sess: BatchSession, scheme, rep: str, members: list[str],
              contrast: str | None, blob: dict) -> None:
    """One item: one run, driven to completion, then the class inherits.

    The two tests hand `runner.start` different instruments because they *are*
    different instruments — a capability ladder is graded by a judge reading a
    back-translation, and a dialect battery has no judge at all: its instrument
    is the panel of control models, re-qualified on every single run.
    """
    kw: dict = {}
    if sess.test_type == "capability":
        kw = {"judge_model": blob.get("judge_model"),
              "backtranslator_panel": blob.get("backtranslator_panel"),
              "pivot": blob.get("pivot")}
    else:
        if not contrast:
            # Belt and braces: the plan refuses these, so reaching here means
            # the row and the plan disagree, and the honest thing is to fail
            # the item rather than invent a pair for it.
            _finish_item(sess, rep, status="failed",
                         error="no contrast arm on this item — a dialect run "
                               "needs the pair the plan recorded.")
            return
        kw = {"attestors": blob.get("attestors"),
              "varieties": [rep, contrast],
              "scenario": blob.get("scenario")}
    try:
        run = runner.start(
            test_type=sess.test_type, tag=rep, engine=sess.engine, mode="auto",
            scheme_name=scheme.meta.name, **kw)
    except Exception as e:  # noqa: BLE001
        _finish_item(sess, rep, status="failed", error=f"{type(e).__name__}: {e}")
        return

    sess.run_id = run.id
    with session() as s:
        repo.set_batch_item_run(s, sess.id, rep, run.id)
        repo.attach_run_to_batch(s, run.id, sess.id)

    base = sess.calls_used

    def tick(r) -> None:
        # `calls_used` is true *during* the scan. In the previous tool it stayed
        # at zero until the job finished, which made the progress line useless
        # and was found only when somebody tried to use it.
        sess.calls_used = base + sum(runner._spent(r).values())
        with session() as s:
            repo.set_batch_calls(s, sess.id, sess.calls_used)

    runner.drive(run, on_step=tick)
    tick(run)
    runner.close(run.id)
    sess.run_id = None

    if run.status != "done":
        _finish_item(sess, rep, status="failed", error=run.error or run.status)
        return

    # Only a capability tier propagates. Whether a model marks *this* variety
    # is the claim a dialect battery makes, so there is no other tag it could
    # be true of — a dialect item is one tag, measured.
    if sess.test_type == "capability":
        _inherit(sess, rep, members)
    _finish_item(sess, rep, status="done")


def _inherit(sess: BatchSession, rep: str, members: list[str]) -> int:
    """Propagate the class's result to the tags that were not asked.

    **Inheriting a tier is not inheriting a claim.** `inherited_from` is on
    every row written here so a reader is never shown an inherited answer as if
    it had been measured — and `put_verdict`'s default promotion rule means an
    inherited row never displaces a verdict somebody approved.
    """
    written = 0
    with session() as s:
        measured = repo.current_verdict(s, engine=sess.engine, tag=rep,
                                        test_type=sess.test_type)
        if measured is None:
            return 0
        for tag in members:
            if tag == rep:
                continue
            repo.put_verdict(s, {
                "run_id": measured.run_id, "test_type": measured.test_type,
                "engine": measured.engine, "tag": tag, "cls": measured.cls,
                "script": measured.script, "level": measured.level,
                "confidence": measured.confidence, "evidence": measured.evidence,
                "s_lang": measured.s_lang, "s_content": measured.s_content,
                "ci_low": measured.ci_low, "ci_high": measured.ci_high,
                "borderline": measured.borderline, "refusals": measured.refusals,
                "designator": measured.designator,
                "backtranslator": measured.backtranslator,
                "judge": measured.judge, "pivot": measured.pivot,
                "attestors": measured.attestors, "detail": measured.detail,
                "notes": measured.notes,
                "inherited_from": rep,
                "method_version": measured.method_version,
            }, promote=None)
            written += 1
    return written


def _finish_item(sess: BatchSession, rep: str, *, status: str,
                 error: str | None = None) -> None:
    with session() as s:
        repo.finish_batch_item(s, sess.id, rep, status=status, error=error)


def _halt(sess: BatchSession, why: str) -> None:
    sess.error = why
    _close(sess, "halted")


def _close(sess: BatchSession, status: str) -> None:
    sess.status = status
    with session() as s:
        repo.finish_batch(s, sess.id, status=status, error=sess.error,
                          calls_used=sess.calls_used)


# -- reading one ----------------------------------------------------------------

def snapshot(batch_id: str) -> dict:
    with session() as s:
        batch = repo.get_batch(s, batch_id)
        if batch is None:
            raise BatchError(f"No batch {batch_id!r}.")
        items = repo.batch_items(s, batch_id)
        written = repo.batch_verdicts(s, batch_id)
        live = _BATCHES.get(batch_id)
        return {
            "id": batch.id,
            # A row still marked running whose process is gone is not running.
            # Derived rather than stored, for the same reason a run's
            # `interrupted` is: a page that believed the row would offer a
            # pause button that can never work.
            "status": ("interrupted" if batch.status == "running" and live is None
                       else batch.status),
            "engine": batch.engine, "test_type": batch.test_type,
            "rescan": (batch.settings or {}).get("rescan", "drafts"),
            "max_calls": batch.max_calls, "calls_used": batch.calls_used,
            "error": batch.error,
            "unknown_tags": batch.unknown_tags or [],
            "plan": (batch.settings or {}).get("plan"),
            "running_run_id": live.run_id if live else None,
            #: What this scan put on record, and how much of it nobody has
            #: stood behind yet. Counted from the runs the batch paid for, so
            #: a row measured by hand between two scans of the same scope
            #: belongs to neither of them.
            "verdicts": {
                "written": len(written),
                "drafts": sum(1 for v in written if not v.approved),
                "inherited": sum(1 for v in written
                                 if v.inherited_from and not v.approved),
            },
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "finished_at": (batch.finished_at.isoformat()
                            if batch.finished_at else None),
            "items": [{
                "cls": i.cls, "representative": i.representative,
                "members": i.members or [], "contrast": i.contrast,
                "status": i.status,
                "skip_reason": i.skip_reason, "run_id": i.run_id,
                "error": i.error,
                "finished_at": i.finished_at.isoformat() if i.finished_at else None,
            } for i in items],
        }


def listing(engine: str | None = None, limit: int = 20) -> list[dict]:
    with session() as s:
        rows = repo.list_batches(s, engine=engine, limit=limit)
        live = {b.id for b in live_for()}
        return [{
            "id": b.id, "engine": b.engine, "test_type": b.test_type,
            "status": ("interrupted" if b.status == "running" and b.id not in live
                       else b.status),
            "calls_used": b.calls_used, "max_calls": b.max_calls,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "items": len(b.items),
            "done": sum(1 for i in b.items if i.status == "done"),
            "skipped": sum(1 for i in b.items if i.status == "skipped"),
            "failed": sum(1 for i in b.items if i.status == "failed"),
        } for b in rows]


def reset_for_tests() -> None:
    with _LOCK:
        _BATCHES.clear()
