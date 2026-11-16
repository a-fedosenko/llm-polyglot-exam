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
    Base.metadata.create_all(engine())


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
