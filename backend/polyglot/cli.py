"""Command line: serve the app, fetch the instrument, inspect what is stored.

Deliberately small. Running a measurement is the page's job — that is the whole
point of this rewrite — so there is no `scan` verb here yet; it arrives with the
batch page, driving the same engine.
"""
from __future__ import annotations

import argparse
import json
import sys

from polyglot import paths
from polyglot.settings import METHOD_VERSION, settings


def cmd_serve(args) -> int:
    import uvicorn

    paths.ensure()
    from polyglot.store import create_all
    create_all()
    uvicorn.run("polyglot.api.app:app", host=args.host, port=args.port,
                reload=args.reload)
    return 0


def cmd_bootstrap(args) -> int:
    """Fetch the two assets the repository cannot ship.

    Neither is optional in the sense that matters: without GlotLID the gate
    checks the writing system but not the language, and without FLORES controls
    almost every language reports `unverified`. The tool says so rather than
    quietly measuring less than it reports.
    """
    from polyglot.core import bootstrap

    if args.check:
        print(json.dumps(bootstrap.status(), indent=2))
        return 0
    bootstrap.ensure(args.only or None, on_progress=lambda m: print(m, flush=True))
    print(json.dumps(bootstrap.status(), indent=2))
    return 0


def cmd_status(args) -> int:
    from sqlalchemy import func, select

    from polyglot.store import create_all, session
    from polyglot.store.models import Generation, Run, Verdict

    create_all()
    with session() as s:
        runs = s.scalar(select(func.count()).select_from(Run)) or 0
        gens = s.scalar(select(func.count()).select_from(Generation)) or 0
        rows = list(s.scalars(select(Verdict).where(Verdict.current.is_(True))))
    stale = [r for r in rows if r.method_version != METHOD_VERSION]
    approved = [r for r in rows if r.approved]
    print(f"method        {METHOD_VERSION}")
    print(f"scheme        {settings.scheme}")
    print(f"database      {paths.DB}")
    print(f"runs          {runs}")
    print(f"generations   {gens}")
    print(f"verdicts      {len(rows)} current "
          f"({len(approved)} approved, {len(rows) - len(approved)} draft)")
    if stale:
        # Stale is not wrong. It means the instrument moved under the row, and
        # the honest reading is "this was true of an earlier method".
        print(f"  !! {len(stale)} were measured under an older method and are "
              f"not comparable with anything measured now")
    for test in ("capability", "dialect"):
        here = [r for r in rows if r.test_type == test]
        if not here:
            continue
        levels: dict[str, int] = {}
        for r in here:
            levels[r.level] = levels.get(r.level, 0) + 1
        print(f"  {test:12} " + ", ".join(f"{k} {v}" for k, v in sorted(levels.items())))
    return 0


def cmd_import_corpus(args) -> int:
    """Carry the previous tool's raw generations across.

    Only the corpus. Its verdicts are not imported, and the reason is not
    tidiness: every one of them was measured under an older method, so importing
    them would put rows in the verdicts table that look exactly like fresh
    measurements and are not comparable with any of them. The generations are a
    different matter — they are raw model output, they never go stale, and
    re-grading them offline is the only thing that makes a method change
    affordable.
    """
    import sqlite3

    from polyglot.store import create_all, session
    from polyglot.store.models import Generation

    src = args.source
    create_all()
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT kind, engine, tag, spec_id, designator, prompt, response, "
            "error, usage, latency_s, meta, created_at FROM generation").fetchall()
    except sqlite3.Error as e:
        print(f"Could not read a `generation` table from {src}: {e}", file=sys.stderr)
        return 1

    label = args.label
    written = 0
    with session() as s:
        from sqlalchemy import func, select
        already = s.scalar(select(func.count()).select_from(Generation)
                           .where(Generation.imported_from == label)) or 0
        if already and not args.force:
            print(f"{already} rows are already tagged {label!r}. Re-running would "
                  f"duplicate them; pass --force if that is what you want.")
            return 1
        for r in rows:
            s.add(Generation(
                kind=r["kind"], engine=r["engine"], tag=r["tag"],
                spec_id=r["spec_id"], designator=r["designator"],
                prompt=r["prompt"], response=r["response"], error=r["error"],
                usage=_json(r["usage"]), latency_s=r["latency_s"],
                meta=_json(r["meta"]), imported_from=label))
            written += 1
    conn.close()
    print(f"Imported {written} generations from {src}, tagged {label!r}.")
    print("Verdicts were deliberately not imported: they were measured under an "
          "older method and are not comparable with anything measured now.")
    return 0


def _json(v):
    if v in (None, ""):
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="polyglot", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the API and the UI")
    s.add_argument("--host", default=settings.api_bind)
    s.add_argument("--port", type=int, default=settings.api_port)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    b = sub.add_parser("bootstrap", help="fetch GlotLID and the FLORES controls")
    b.add_argument("--check", action="store_true", help="report, download nothing")
    b.add_argument("--only", nargs="*", help="asset keys to fetch")
    b.set_defaults(fn=cmd_bootstrap)

    st = sub.add_parser("status", help="what is measured, and what is stale")
    st.set_defaults(fn=cmd_status)

    i = sub.add_parser("import-corpus",
                       help="carry raw generations over from llm-language-checker")
    i.add_argument("source", help="path to the old llmlc.db")
    i.add_argument("--label", default="llm-language-checker")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_import_corpus)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
