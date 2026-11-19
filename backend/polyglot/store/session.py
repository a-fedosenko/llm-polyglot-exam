"""Engine and session handling.

**SQLite, deliberately.** This is a single-operator local tool with one writer
and a few thousand rows. A database server would be ceremony, and it would break
the thing that makes the project usable: clone it, add your keys, run it — with
no services to start.

Supporting two backends had a measurable cost in the previous tool: SQLite
discards tzinfo on round-trip while Postgres preserves it, which broke a merge
rule on one and not the other. One backend, one set of behaviours. If this is
ever hosted for concurrent writers, the batch table is the seam and the URL is a
one-line change.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from polyglot import paths
from polyglot.settings import settings
from polyglot.store.models import Base

_engine = None
_Session: sessionmaker | None = None


def url() -> str:
    u = settings.database_url or f"sqlite+pysqlite:///{paths.DB}"
    if not u.startswith("sqlite"):
        raise ValueError(
            f"Only SQLite is supported; got {u.split(':')[0]!r}. "
            "This is a single-operator local tool — see the README.")
    return u


def engine():
    global _engine, _Session
    if _engine is None:
        paths.ensure()
        _engine = create_engine(url(), future=True,
                                connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            """Write-ahead logging, for concurrency rather than speed.

            In SQLite's default rollback-journal mode a writer blocks readers.
            That matters here because a long auto run holds a write open while
            the page is polling for progress, and a UI that freezes mid-run
            because its own run is writing is not usable. NORMAL synchronous is
            WAL's usual companion: durable against a process crash, at risk only
            from an OS-level crash mid-write — which, for a measurement, we
            simply re-run.
            """
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def create_all() -> None:
    eng = engine()
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)
    _reshape_batch_item(eng)


#: Columns added to tables that already exist in somebody's database. SQLite
#: adds a nullable column in place, so these need no rebuild — unlike a changed
#: constraint, which does (see below).
_ADDED: dict[str, dict[str, str]] = {
    "verdict": {"adjudicated": "JSON"},
}


def _add_missing_columns(eng) -> None:
    with eng.begin() as conn:
        for table, columns in _ADDED.items():
            have = {r[1] for r in conn.exec_driver_sql(
                f"PRAGMA table_info({table})").fetchall()}
            if not have:
                continue
            for name, decl in columns.items():
                if name not in have:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _reshape_batch_item(eng) -> None:
    """Bring an older `batch_item` up to the shape the model describes.

    The one migration this tool has, and it is here rather than in a migration
    framework because it is the only one: the dialect scan needs a `contrast`
    column and a uniqueness rule keyed on the representative instead of the
    class, and SQLite can add the column but cannot drop a table-level
    constraint. So the table is rebuilt and its rows copied.

    Nothing else is touched. A batch is a *ledger of a scan* — the verdicts it
    produced live in their own table, joined to the same runs the page
    produces — so the worst case here costs a progress list, never a
    measurement.
    """
    from polyglot.store.models import BatchItem

    with eng.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='batch_item'").fetchone()
        if row is None:
            return
        ddl = row[0] or ""
        if "contrast" in ddl and "representative)" in ddl:
            return
        carried = ("id, batch_id, cls, representative, members, status, "
                   "skip_reason, run_id, error, started_at, finished_at")
        # A half-finished earlier attempt leaves this behind, and the rename
        # below would fail on it rather than on anything that matters.
        conn.exec_driver_sql("DROP TABLE IF EXISTS batch_item_old")
        conn.exec_driver_sql("ALTER TABLE batch_item RENAME TO batch_item_old")
        # A rename carries the old table's indexes with it under their old
        # names, and the fresh table wants those names back.
        for (name,) in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='batch_item_old' AND name IS NOT NULL").fetchall():
            if not name.startswith("sqlite_"):
                conn.exec_driver_sql(f'DROP INDEX "{name}"')
        BatchItem.__table__.create(conn)
        conn.exec_driver_sql(
            f"INSERT INTO batch_item ({carried}) "
            f"SELECT {carried} FROM batch_item_old")
        conn.exec_driver_sql("DROP TABLE batch_item_old")


@contextmanager
def session() -> Iterator[Session]:
    engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset_for_tests(database_url: str) -> None:
    global _engine, _Session
    settings.database_url = database_url
    _engine, _Session = None, None
    create_all()
