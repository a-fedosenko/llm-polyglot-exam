"""Persistence schema.

Four things are worth keeping, and they have different lifetimes:

    generation   the raw corpus. Never deleted, never updated. When the method
                 changes this is re-graded offline instead of re-running every
                 model, which is the only reason a method change is affordable.
    run          one test, as it happened: every step, its prompt, its reply,
                 what it cost. An audit trail, not an answer.
    verdict      the standing answer for (engine, tag, test). Append-only with
                 a `current` flag; a re-run adds a row and never destroys the
                 one somebody already trusted. This is the table a batch scan
                 reads to decide what it may skip.
    *_cache      competence findings reusable across runs — whether a reader can
                 be qualified for a language, whether an attestor can tell a
                 family apart. Paid once rather than per run.

**A result is only meaningful together with the configuration that produced it**
— back-translator, judge, method version — so those are columns rather than
metadata, and they are part of what makes a verdict unique. Two engines measured
through different readers are not comparable, and the schema has to be able to
say so rather than leaving it to a convention nobody reads.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Recorded as the back-translator when a result never needed one. A
#: deterministic negative — refusal, wrong script, degeneration — is settled by
#: the local gate, so nothing read the language and no instrument can be named.
#:
#: It is a value in a column that decides row identity, which makes it special:
#: such a result is comparable to *every* reader rather than to none, so it must
#: occupy the same row as a graded one rather than a parallel row of its own.
#: `repo.put_verdict` enforces that; a database constraint cannot express it.
NO_INSTRUMENT = "(not needed)"

CAPABILITY = "capability"
DIALECT = "dialect"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# -- the audit trail -----------------------------------------------------------

class Run(Base):
    """One test of one language against one engine.

    A run is an *event*. It is never edited after it finishes, not even to
    correct a judge the operator can see was wrong: an adjudication is recorded
    beside the measurement and the two sit together, because a number that can
    be hand-edited after the fact is not a measurement of anything.
    """

    __tablename__ = "run"
    __table_args__ = (
        Index("ix_run_lookup", "engine", "tag", "test_type", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    test_type: Mapped[str] = mapped_column(String(16), index=True)   # capability | dialect
    mode: Mapped[str] = mapped_column(String(8), default="manual")   # manual | auto

    engine: Mapped[str] = mapped_column(String(128), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    cls: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scheme: Mapped[str] = mapped_column(String(64), default="catmint")

    #: The instrument, in full. Not metadata: two runs that differ here are not
    #: two measurements of the same thing.
    pivot: Mapped[str] = mapped_column(String(16), default="en")
    judge_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    backtranslator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attestors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    method_version: Mapped[str] = mapped_column(String(32), index=True)

    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: What the run concluded, in the shape the page renders. Denormalised on
    #: purpose: reconstructing a verdict by replaying forty step rows is how a
    #: results list comes to be slow and, worse, to disagree with the run page.
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: The plan worked out before anything was spent — designators, varieties,
    #: panel, estimated cost. Kept so "what was this going to ask?" is still
    #: answerable after the fact.
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: Any prompt or scenario the operator rewrote mid-run. Such a run is kept
    #: in full and is **never** promoted to a verdict: it measured a different
    #: instrument, and results are only comparable within one (convention 4).
    prompt_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Judge or attestor verdicts the operator overruled, and what the run would
    #: have scored with them applied. Stored beside the measurement, never over it.
    adjudication: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    corpus_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("batch.id", ondelete="SET NULL"), nullable=True, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
        order_by="RunStep.ordinal, RunStep.attempt")


class RunStep(Base):
    """One action, as it happened — including the ones that were replaced.

    A re-run does not overwrite: the earlier attempt keeps its row and is marked
    `superseded`. Seeing that the first generation was refused and the second
    was not is the finding, and a schema that kept only the second would hide it.
    """

    __tablename__ = "run_step"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", "attempt", name="uq_run_step"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"),
                                        index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)

    kind: Mapped[str] = mapped_column(String(32), index=True)
    phase: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="done")
    tone: Mapped[str] = mapped_column(String(8), default="")
    summary: Mapped[str] = mapped_column(Text, default="")

    #: Exactly what was sent — model, system message, user message, limits.
    #: Not a reconstruction: this is the object the call was made from.
    request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Whether a human rewrote the request before it was sent.
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="steps")


class Generation(Base):
    """The raw corpus. Append-only, never deleted.

    The file in `data/corpus` is authoritative and is written first; this table
    is the queryable mirror, because re-grading a method change is a query and
    not a file scan. A failure to mirror never costs a record.
    """

    __tablename__ = "generation"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("run.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    engine: Mapped[str] = mapped_column(String(128), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    spec_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    designator: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Set on rows carried over from the previous tool, so a re-grade can tell
    #: what it is looking at without guessing from dates.
    imported_from: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                                      index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# -- the standing answer -------------------------------------------------------

class Verdict(Base):
    """What we currently believe about (engine, tag, test), and why.

    **Append-only with a current flag, and a separate approval.** Three states
    that a single boolean would collapse and that mean different things to a
    batch scan:

        nothing stored    never measured  -> scan it
        current, not approved  measured, not signed off -> scan it unless the
                          operator says otherwise; a verdict nobody has looked at
                          is a draft
        current, approved a person read the evidence and stood behind it -> skip
                          it, unless re-scan-approved is explicitly asked for

    A re-run never replaces a verdict somebody approved. It adds a row, and
    promotion is a deliberate gesture that deletes nothing and can be undone.
    There is deliberately no confirmation dialog on it: a password on an
    overwrite still overwrites, and nothing here is destroyed for it to protect.
    """

    __tablename__ = "verdict"
    __table_args__ = (
        # Unique among *current* rows only. Every measurement is kept; one per
        # identity is flagged current and is what the batch scan and the
        # published table read.
        Index("uq_verdict_current", "engine", "tag", "test_type", "method_version",
              "backtranslator", unique=True, sqlite_where=text("current = 1")),
        Index("ix_verdict_browse", "engine", "test_type", "current", "approved"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("run.id", ondelete="SET NULL"), nullable=True, index=True)

    test_type: Mapped[str] = mapped_column(String(16), index=True)
    engine: Mapped[str] = mapped_column(String(128), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    cls: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    script: Mapped[str | None] = mapped_column(String(8), nullable=True)

    #: The headline. `Proficient|Assisted|Unusable` for a capability test;
    #: `full-command|production|recognition-only|no-evidence|indeterminate` for a
    #: dialect one. One column because every consumer wants "what did it say",
    #: and the typed detail below is for the ones that want more.
    level: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    #: Why this is not simply a number: `unverified` and `deterministic-negative`
    #: are different kinds of answer from `fact-recall`, and averaging them would
    #: be a category error.
    evidence: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    s_lang: Mapped[float | None] = mapped_column(Float, nullable=True)
    s_content: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    borderline: Mapped[bool] = mapped_column(Boolean, default=False)
    refusals: Mapped[int] = mapped_column(Integer, default=0)

    designator: Mapped[str | None] = mapped_column(String(256), nullable=True)
    backtranslator: Mapped[str] = mapped_column(String(128), default=NO_INSTRUMENT)
    judge: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pivot: Mapped[str] = mapped_column(String(16), default="en")
    attestors: Mapped[list | None] = mapped_column(JSON, nullable=True)

    #: Everything the page shows that is not worth a column, including the
    #: pointer back into the corpus. A stored verdict with no way back to the
    #: text that justifies it is not much of a verdict.
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    #: Inherited rather than measured: the tag shares an equivalence class with
    #: one that was. Carried so a reader is never shown an inherited answer as
    #: if it had been asked — inheriting a tier is not inheriting a claim.
    inherited_from: Mapped[str | None] = mapped_column(String(64), nullable=True)

    current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    method_version: Mapped[str] = mapped_column(String(32), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, index=True)


# -- competence caches ---------------------------------------------------------

class Qualification(Base):
    """Whether a back-translator can be trusted to read one language.

    Cached per (reader, tag, method) because it changes only when the reader
    does, and paying four chrF++ calls for it on every run is waste. Never
    consulted to *skip* a check the method says is required — it is a cache, not
    an authority.
    """

    __tablename__ = "qualification"
    __table_args__ = (UniqueConstraint("backtranslator", "tag", "method_version",
                                       name="uq_qualification_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    backtranslator: Mapped[str] = mapped_column(String(128), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    method_version: Mapped[str] = mapped_column(String(32))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttestorRecord(Base):
    """What one model can do with one variety, in one role.

    Role is in the key because the three competences dissociate: a model can
    tell ijekavian from ekavian and still mistranslate it, so a row qualified
    for discrimination says nothing about generation. Tag is in the key because
    judge competence is per dialect, not per model — a strong engine can be a
    weak attestor for one variety, and the record has to be able to say so.

    **This table routes; it never vouches.** It decides who is asked first.
    The manufactured controls in the live run decide whether an answer is
    believed, and they run every time regardless of what is stored here. A stale
    row costs wasted routing, never a wrong verdict. Nothing may read this table
    to skip a control — that is the rule that keeps the record from becoming the
    thing it replaced, a stored claim quietly going stale while everything
    downstream trusts it.
    """

    __tablename__ = "attestor_record"
    __table_args__ = (
        Index("uq_attestor_current", "model", "tag", "role", "method_version",
              "script", unique=True, sqlite_where=text("current = 1")),
        Index("ix_attestor_tag_role", "tag", "role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    script: Mapped[str | None] = mapped_column(String(8), nullable=True)

    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    probes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    method_version: Mapped[str] = mapped_column(String(32), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, index=True)


# -- reserved for the batch page ------------------------------------------------

class Batch(Base):
    """A scan of one engine down a list of tags.

    Here already, and deliberately thin: the batch page is the next piece of
    work and its runs must be the *same* runs, joined by `Run.batch_id`, not a
    parallel record of a parallel pipeline. Building the single-language page
    against a schema with no place for a batch is how the two end up measuring
    slightly different things.
    """

    __tablename__ = "batch"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    test_type: Mapped[str] = mapped_column(String(16), index=True)
    engine: Mapped[str] = mapped_column(String(128), index=True)
    scheme: Mapped[str] = mapped_column(String(64), default="catmint")
    method_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    #: What the operator asked for, and what the skip rule made of it.
    requested_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    unknown_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: False (the default) means an approved verdict is left alone.
    rescan_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    max_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    items: Mapped[list["BatchItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan")


class BatchItem(Base):
    """One equivalence class within a batch. The unit of resumption.

    Items carry their own state, so a killed batch restarts from where it
    stopped rather than from zero, and a skipped tag records *why* it was
    skipped — "already approved" and "nothing to measure here" are different
    answers and a progress bar that merges them is lying.
    """

    __tablename__ = "batch_item"
    __table_args__ = (UniqueConstraint("batch_id", "cls", name="uq_batch_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch.id", ondelete="CASCADE"),
                                          index=True)
    cls: Mapped[str] = mapped_column(String(64))
    representative: Mapped[str] = mapped_column(String(64))
    members: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    batch: Mapped[Batch] = relationship(back_populates="items")
