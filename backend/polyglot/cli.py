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


def cmd_references(args) -> int:
    """Every contrastable family, its baseline, and who stood behind it.

    The file is the interface — a baseline is an argument and belongs in review
    with a diff — so nothing here decides anything. What it does is make the
    state readable in one pass; `--scaffold` prints entries for the families a
    given locale list has and the file does not; and `--annotate` refreshes the
    human-readable half of every entry (`language`, `script`,
    `baseline_title`) from the loaded list, which is the one part of the file
    that can go stale on its own.
    """
    import json as _json

    from polyglot.core import reference as ref
    from polyglot.core.scheme import load_scheme

    scheme = load_scheme(args.scheme or settings.scheme)
    cov = ref.coverage(scheme)
    rows = []
    for cls in sorted({l.cls for l in scheme.languages.values()}):
        members = ref._members(scheme, cls)
        if len(members) < 2:
            continue
        r = ref.reference_for(scheme, cls)
        name = next((l.name for l in scheme.languages.values() if l.cls == cls), "?")
        rows.append((cls, name, r, [m.tag for m in members]))

    if args.annotate:
        print(_annotate_references(scheme))
        return 0

    stale = _stale_annotations(scheme)

    if args.scaffold:
        # Only what is missing, in the file's own shape, ready to be pasted and
        # argued over. A reason is not generated: the point of the entry is the
        # reason, and a machine-written one would be a reason nobody gave.
        out = {}
        for cls, name, r, members in rows:
            if cls in ref._overlay():
                continue
            lang = scheme.get(r.tag) if r.tag else None
            out[cls] = {"language": name,
                        "script": cls.partition("|")[2] or None,
                        "baseline": (ref.canonical_tag(lang) if lang else None),
                        "baseline_title": _title(lang),
                        "why": f"TODO — pick one of: {', '.join(members)}",
                        "reviewed": False}
        print(_json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    width = max((len(c) for c, *_ in rows), default=12)
    for cls, name, r, members in rows:
        if args.unconfirmed and r.decided:
            continue
        mark = {"recorded": "ok ", "rule": "ok ", "none": "-- "}.get(r.basis, "?? ")
        print(f"{mark}{cls:<{width}}  {name:<22} {str(r.tag):<14} {r.basis:<12}"
              f" {len(members)} varieties")
    print()
    print(f"{cov['families']} contrastable families in {scheme.meta.name}: "
          f"{cov['decided']} settled, {len(cov['unconfirmed'])} unconfirmed, "
          f"{len(cov['unlisted'])} not in the file")
    if cov["unlisted"]:
        print("  !! families with no entry fall back to list order — "
              "`polyglot references --scaffold` prints them")
    if stale:
        # The annotations are a reading aid and the resolver ignores them, so a
        # stale one costs nobody a measurement — it costs a reviewer their
        # trust in the file, which is worse for a file whose whole job is to be
        # read.
        print(f"  !! {len(stale)} entries describe themselves wrongly for this "
              f"list — `polyglot references --annotate` rewrites that half")
        for cls, said, is_ in stale[:10]:
            print(f"     {cls}: says {said!r}, resolves to {is_!r}")
    return 1 if args.check and (cov["unlisted"] or cov["unconfirmed"]
                                or stale) else 0


def _reference_file() -> pathlib.Path:
    """The file being audited: a working copy under the data directory wins,
    exactly as the resolver's own lookup does."""
    from polyglot.core.reference import REFERENCE_FILE

    local = paths.DATA / REFERENCE_FILE
    return local if local.is_file() else paths.REFERENCES / REFERENCE_FILE


def _title(lang) -> str | None:
    """How a variety reads to a person: `Arabic (Saudi Arabia)`."""
    if lang is None:
        return None
    region = lang.region_name or lang.region
    return f"{lang.name} ({region})" if region else lang.name


def _annotations(scheme, cls: str) -> dict:
    """What an entry says about itself, for a human reading the diff."""
    from polyglot.core import reference as ref

    r = ref.reference_for(scheme, cls)
    return {
        "language": next((l.name for l in scheme.languages.values()
                          if l.cls == cls), None),
        "script": cls.partition("|")[2] or None,
        "baseline_title": _title(scheme.get(r.tag) if r.tag else None),
    }


def _stale_annotations(scheme) -> list[tuple[str, object, object]]:
    """Entries whose readable half no longer matches the list they describe.

    Two ways that happens, and both matter: somebody changed `baseline` and
    not the title beside it, or the loaded list renamed a region under a
    baseline nobody touched. Either way the file misleads the next reader,
    which is fatal for a file whose whole job is to be read.
    """
    from polyglot.core import reference as ref

    out = []
    for cls, entry in ref._overlay().items():
        ann = _annotations(scheme, cls)
        for key in ("language", "script", "baseline_title"):
            if key in entry and entry[key] != ann[key]:
                out.append((f"{cls}.{key}", entry[key], ann[key]))
        # A baseline the list does not carry resolves to nothing: the entry is
        # a decision about a variety this list has never heard of.
        if entry.get("baseline") and ref.reference_for(scheme, cls).basis \
                not in ("recorded", "proposed"):
            out.append((f"{cls}.baseline", entry["baseline"],
                        "not in this locale list"))
    return out


def _annotate_references(scheme) -> str:
    """Rewrite the readable half of every entry, in place, and say what moved.

    Only `language`, `script` and `baseline_title` are touched: the baseline
    itself, the reason and the sign-off are what a person wrote, and a tool
    that rewrote those would be a tool that decides.
    """
    import json as _json

    from polyglot.core import reference as ref

    path = _reference_file()
    doc = _json.loads(path.read_text(encoding="utf-8"))
    about = doc.pop("_about", None)

    def block(cls: str, v: dict) -> str:
        lines = [f"    {_json.dumps(k)}: {_json.dumps(v.get(k), ensure_ascii=False)}"
                 for k in ("language", "script", "baseline", "baseline_title",
                           "why", "reviewed")]
        return f"  {_json.dumps(cls)}: {{\n" + ",\n".join(lines) + "\n  }"

    moved, blocks = [], []
    for cls, entry in sorted(doc.items()):
        ann = _annotations(scheme, cls)
        if entry.get("baseline_title") not in (None, ann["baseline_title"]):
            moved.append(f"{cls}: {entry['baseline_title']} "
                         f"-> {ann['baseline_title']}")
        merged = {"language": ann["language"], "script": ann["script"],
                  "baseline": entry.get("baseline"),
                  "baseline_title": ann["baseline_title"],
                  "why": entry.get("why", ""),
                  "reviewed": bool(entry.get("reviewed"))}
        blocks.append(block(cls, merged))

    text = ["{"]
    if about is not None:
        text.append("  \"_about\": "
                    + _json.dumps(about, ensure_ascii=False,
                                  indent=2).replace("\n", "\n  ") + ",")
        text.append("")
    text.append(",\n\n".join(blocks))
    text.append("}")
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    ref._overlay.cache_clear()
    return (f"annotated {len(blocks)} entries in {path}"
            + ("".join("\n  " + m for m in moved) if moved else ""))


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

    rf = sub.add_parser("references",
                        help="every dialect family's baseline, and who confirmed it")
    rf.add_argument("--scheme", help="locale list to audit (default: configured)")
    rf.add_argument("--unconfirmed", action="store_true",
                    help="only the families nobody has stood behind")
    rf.add_argument("--scaffold", action="store_true",
                    help="print file entries for families the file does not cover")
    rf.add_argument("--annotate", action="store_true",
                    help="refresh each entry's language/baseline/varieties from "
                         "the loaded list; decides nothing")
    rf.add_argument("--check", action="store_true",
                    help="exit non-zero while anything is unconfirmed, unlisted "
                         "or describing itself wrongly")
    rf.set_defaults(fn=cmd_references)

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
