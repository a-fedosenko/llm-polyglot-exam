import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { usePersisted } from "../persist";
import type { BatchPlan, BatchSnapshot, CoverageRow, Rescan } from "../types";
import { FACET_LABEL, ScanBar, facetOf, type Facet, type Instrument, type Scope }
  from "./ScanBar";
import { Empty, Info, toneText } from "./ui";

const TIER_TONE: Record<string, string> = {
  Proficient: "ok", Assisted: "warn", Unusable: "bad",
  "full-command": "ok", production: "ok",
  "recognition-only": "warn", "no-evidence": "bad", indeterminate: "warn",
};

/** A scan with nothing chosen yet, and the shape every restored one is merged
 *  over. */
const BLANK_INSTRUMENT: Instrument = {
  engine: "", test: "capability", judge: "", reader: "", fallback: "",
  attestors: [],
  rescan: "drafts" as Rescan, maxCalls: null,
};

/** Where the running scan's id is kept across a reload.
 *
 * Same reasoning as the single run's: the scan lives in the server process, so
 * a reload never stopped it — the page simply forgot which one it was watching,
 * which from the operator's side is the same thing as losing a scan that is
 * spending money. */
const OPEN_SCAN = "polyglot.open-scan";

/** Everything measured about one engine, and the gesture that measures more.
 *
 * The table is built from the **locale list**, not from the verdict table. A
 * coverage page assembled out of what has been measured can only ever show what
 * has been measured, and the first question anyone brings to one is which
 * languages are missing.
 *
 * **The scope is the scan set.** What the table is showing is what the scan
 * would measure — there is no second definition of "which languages" to drift
 * from the one on screen. Ticking rows narrows it to exactly those. */
export function CoveragePage({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const [rows, setRows] = useState<CoverageRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Remembered across reloads, except the find box: a text filter restored
  // invisibly is a table that looks wrong for a reason nothing on screen
  // explains. The facet is the part worth keeping — "I am working through the
  // Cyrillic ones" survives a refresh.
  const [facet, setFacet] = usePersisted<{ facet: Facet; value: string }>(
    "scan.scope", { facet: "all", value: "" });
  const [query, setQuery] = useState("");
  const scope: Scope = { ...facet, query };
  const setScope = (next: Scope) => {
    setFacet({ facet: next.facet, value: next.value });
    setQuery(next.query);
  };

  const [stored, setInstrument] = usePersisted<Instrument>("scan.instrument",
                                                           BLANK_INSTRUMENT);
  // Merged over the defaults rather than used raw: what is in `localStorage`
  // was written by whichever version of this bar the operator last used, and a
  // restored object missing a field it did not have yet would render a control
  // with no value and send `undefined` to the scan.
  const instrument: Instrument = { ...BLANK_INSTRUMENT, ...stored };
  const dialect = instrument.test === "dialect";
  // Not remembered. A tick is a working selection rather than a setting, and a
  // forgotten one restored on load would silently make the next scan narrower
  // than the scope on screen.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [plan, setPlan] = useState<BatchPlan | null>(null);
  const [batch, setBatch] = useState<BatchSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  // Which scan this panel is showing, readable from inside a callback that was
  // started before the answer arrives. **Closing a scan has to survive a poll
  // already in flight**: the progress view refreshes every 1.5s, so a reply
  // sent before the close lands after it, and `setBatch` on that reply put the
  // closed scan straight back on screen.
  const open = useRef<string | null>(null);
  const show = useCallback((snap: BatchSnapshot | null) => {
    open.current = snap?.id ?? null;
    setBatch(snap);
    if (snap) sessionStorage.setItem(OPEN_SCAN, snap.id);
    else sessionStorage.removeItem(OPEN_SCAN);
  }, []);
  /** A refresh of the scan already on screen, and nothing else. */
  const refresh = useCallback((snap: BatchSnapshot) => {
    if (open.current === snap.id) setBatch(snap);
  }, []);

  const loadCoverage = useCallback((engine: string, test: string) => {
    if (!engine) return;
    setLoading(true);
    api.coverage(engine, test)
      .then((r) => setRows(r.rows))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  // Re-read when the test changes, not just the engine: a dialect verdict and a
  // capability verdict are different measurements of different things, and a
  // table left showing one while the bar says the other is the page lying about
  // what is stored.
  useEffect(() => { loadCoverage(instrument.engine, instrument.test); },
            [instrument.engine, instrument.test]);

  // Reconnect to a scan this tab had open, exactly as the run page does.
  useEffect(() => {
    const id = sessionStorage.getItem(OPEN_SCAN);
    if (!id) return;
    api.batch(id).then(show).catch(() => sessionStorage.removeItem(OPEN_SCAN));
  }, []);

  const running = batch?.status === "running";
  const lastStatus = useRef<string | null>(null);

  // Polled rather than streamed. A run's steps arrive one at a time and each
  // one is worth seeing the moment it lands, which is what an event stream is
  // for; a queue's progress is a handful of counters, and asking for them once
  // a second is both simpler and honest about what it is.
  useEffect(() => {
    if (!batch) return;
    if (!running) {
      // Verdicts only exist once the item that produced them finished, so the
      // table is refreshed when the scan stops rather than on every tick.
      if (lastStatus.current === "running")
        loadCoverage(instrument.engine, instrument.test);
      lastStatus.current = batch.status;
      return;
    }
    lastStatus.current = "running";
    const t = setInterval(() => {
      api.batch(batch.id).then(refresh).catch(() => {});
      loadCoverage(instrument.engine, instrument.test);
    }, 1500);
    return () => clearInterval(t);
  }, [batch?.id, batch?.status]);

  // -- the scope, applied once ------------------------------------------------
  const inScope = useMemo(() => {
    const q = scope.query.trim().toLowerCase();
    return rows.filter((r) => {
      // The scope is the scan set, so a dialect scan may not show rows it
      // could never answer: the umbrella, the family's own reference, a family
      // with no defensible baseline. The rule is the server's — it rides on the
      // row — rather than a second copy of it written here, which would
      // eventually disagree with the plan about what is testable.
      if (dialect && r.pairing && !r.pairing.testable) return false;
      if (scope.facet !== "all" && scope.value
          && facetOf(r, scope.facet) !== scope.value) return false;
      if (q && !`${r.tag} ${r.name ?? ""} ${r.local_name ?? ""}`
        .toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, scope, dialect]);

  const pickedInScope = inScope.filter((r) => picked.has(r.tag));
  const target = pickedInScope.length ? pickedInScope : inScope;

  const tally = useMemo(() => {
    let approved = 0, drafts = 0, inherited = 0, missing = 0;
    for (const r of inScope) {
      if (!r.verdict) missing += 1;
      else if (r.verdict.approved) approved += 1;
      else drafts += 1;
      if (r.verdict?.inherited_from) inherited += 1;
    }
    return { approved, drafts, inherited, missing, total: inScope.length };
  }, [inScope]);

  const missingPrereq =
    !instrument.engine ? "Pick a model to scan."
    : dialect && !instrument.attestors.length ? "Pick 1–3 control models."
    : !dialect && !instrument.reader ? "Pick a reader."
    : !dialect && !instrument.judge ? "Pick a judge."
    : !target.length ? "Nothing is in scope."
    : null;

  // One body plans the scan and starts it, so what is read in the pre-flight is
  // what runs — and each test sends only its own instrument, because a dialect
  // battery has no judge and a capability ladder has no panel.
  const request = () => ({
    engine: instrument.engine,
    tags: target.map((r) => r.tag),
    test_type: instrument.test,
    rescan: instrument.rescan,
    max_calls: instrument.maxCalls,
    ...(dialect
      // Every family in the shipped list is written down, so a scan takes
      // them all; the plan still names any whose baseline nobody has
      // confirmed, which is the part worth seeing before spending.
      ? { attestors: instrument.attestors, include_unconfirmed: true }
      : { judge_model: instrument.judge,
          backtranslator_panel:
            [instrument.reader, instrument.fallback].filter(Boolean) }),
  });

  const doPlan = async () => {
    setBusy(true); setError(null);
    try { setPlan(await api.planScan(request())); }
    catch (e: any) { setError(e.message); setPlan(null); }
    finally { setBusy(false); }
  };

  const doStart = async () => {
    setBusy(true); setError(null);
    try {
      const snap = await api.startScan(request());
      show(snap);
      // The plan described a scan that has now started, and the snapshot
      // carries its own copy. Dropped here so closing the scan does not fall
      // back to a pre-flight for something that already ran.
      setPlan(null);
      lastStatus.current = "running";
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };

  const doPause = async () => {
    if (!batch) return;
    try { show((await api.pauseScan(batch.id)).batch); }
    catch (e: any) { setError(e.message); }
  };

  // Approving every row one scan wrote. Still the operator's gesture — the
  // scan never signs off on its own output — but applied to a reviewed batch
  // instead of retyped six hundred times.
  const doApproveAll = async () => {
    if (!batch) return;
    try {
      show((await api.approveScan(batch.id)).batch);
      loadCoverage(instrument.engine, instrument.test);
    } catch (e: any) { setError(e.message); }
  };

  const approve = async (verdictId: number, next: boolean) => {
    try {
      await api.approve(verdictId, next);
      loadCoverage(instrument.engine, instrument.test);
    } catch (e: any) { setError(e.message); }
  };

  // The plan describes a request; a changed request makes it stale. Dropping it
  // rather than leaving it on screen, because a plan that no longer matches the
  // button beside it is worse than no plan.
  useEffect(() => { setPlan(null); }, [
    instrument.engine, instrument.test, instrument.rescan, instrument.maxCalls,
    instrument.reader, instrument.fallback, instrument.judge,
    instrument.attestors.join(","),
    scope.facet, scope.value, scope.query, picked.size,
  ]);

  const locked = running
    ? `A scan of ${batch!.engine} is running — pause it to change this.`
    : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ScanBar scope={scope} onScope={setScope}
               instrument={instrument} onInstrument={setInstrument}
               rows={rows} busy={busy} locked={locked}
               onPlan={doPlan} onStart={doStart}
               planned={!!plan && !plan.over_budget}
               missing={missingPrereq} />

      {error && (
        <div className="border-b border-bad/30 bg-bad-soft px-4 py-2 text-[12.5px] text-bad">
          <b>That did not work.</b> <span className="opacity-90">{error}</span>
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(340px,30%)]">
        <section className="flex min-h-0 flex-col border-r border-line">
          <Tally tally={tally} scope={scope} picked={pickedInScope.length}
                 loading={loading}
                 onClearPicked={() => setPicked(new Set())} />
          {inScope.length === 0 ? (
            <Empty>
              {instrument.engine
                ? "Nothing in the list matches this scope."
                : "Pick a model above to see what has been measured of it."}
            </Empty>
          ) : (
            <Table rows={inScope} picked={picked} dialect={dialect}
                   onPick={(tag) => {
                     const next = new Set(picked);
                     next.has(tag) ? next.delete(tag) : next.add(tag);
                     setPicked(next);
                   }}
                   onPickAll={() => setPicked(
                     pickedInScope.length ? new Set()
                       : new Set(inScope.map((r) => r.tag)))}
                   allPicked={pickedInScope.length === inScope.length}
                   onOpenRun={onOpenRun} onApprove={approve} />
          )}
        </section>

        <aside className="scroll min-h-0 border-l border-line bg-panel p-4">
          {batch ? (
            <Progress batch={batch} onPause={doPause} onOpenRun={onOpenRun}
                      onApproveAll={doApproveAll}
                      onClose={() => show(null)} />
          ) : plan ? (
            <PlanPanel plan={plan} />
          ) : (
            <div className="space-y-3 text-[12.5px] leading-relaxed text-ink-soft">
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
                Nothing has been planned yet
              </h3>
              <p>
                The table is the scan set: whatever the scope above is showing is
                what would be measured, and ticking rows narrows it to exactly
                those.
              </p>
              <p>
                <b>Plan it</b> costs nothing. It groups the scope into
                equivalence classes, says how many would actually be probed, how
                many tags inherit, what is skipped and why, and what it would
                cost — which is what makes it safe to read before committing.
              </p>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

function Tally({ tally, scope, picked, loading, onClearPicked }: {
  tally: { approved: number; drafts: number; inherited: number;
           missing: number; total: number };
  scope: Scope; picked: number; loading: boolean; onClearPicked: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b
                    border-line px-4 py-2 text-[12px]">
      <span className="font-medium text-ink">
        {tally.total} tag{tally.total === 1 ? "" : "s"}
        {scope.facet !== "all" && scope.value
          && <span className="text-ink-faint"> in {FACET_LABEL[scope.facet].toLowerCase()} {scope.value}</span>}
      </span>
      <Count n={tally.approved} label="approved" tone="ok" />
      <Count n={tally.drafts} label="drafts" tone="warn" />
      <Count n={tally.missing} label="never measured" tone="" />
      {tally.inherited > 0 && (
        <span className="flex items-center gap-1 text-ink-faint">
          {tally.inherited} inherited
          <Info>
            The tag shares an equivalence class with one that was measured and
            took its tier from it. Inheriting a tier is not inheriting a claim —
            nothing was asked of this tag, which is why it is labelled rather
            than shown as a measurement.
          </Info>
        </span>
      )}
      {picked > 0 && (
        <button className="btn btn-sm ml-auto" onClick={onClearPicked}>
          {picked} ticked — the scan is exactly these · clear
        </button>
      )}
      {loading && <span className="ml-auto text-ink-faint">reading…</span>}
    </div>
  );
}

function Count({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <span className={`flex items-center gap-1 ${n ? toneText[tone] : "text-ink-faint"}`}>
      <span className="font-medium">{n}</span>
      <span className="text-ink-faint">{label}</span>
    </span>
  );
}

function Table({ rows, picked, dialect, onPick, onPickAll, allPicked, onOpenRun,
                 onApprove }: {
  rows: CoverageRow[];
  picked: Set<string>;
  /** A dialect row is only meaningful beside the variety it was held against,
   *  so the pair gets a column of its own rather than being left to the run. */
  dialect: boolean;
  onPick: (tag: string) => void;
  onPickAll: () => void;
  allPicked: boolean;
  onOpenRun: (runId: string) => void;
  onApprove: (verdictId: number, next: boolean) => void;
}) {
  return (
    <div className="scroll min-h-0 flex-1">
      <table className="w-full border-collapse text-[12.5px]">
        <thead className="sticky top-0 z-10 bg-panel">
          <tr className="border-b border-line text-left text-[11px] uppercase
                         tracking-wider text-ink-faint">
            <th className="w-8 px-2 py-1.5">
              <input type="checkbox" checked={allPicked} onChange={onPickAll}
                     title="Tick every row in scope" />
            </th>
            <th className="px-2 py-1.5 font-semibold">Tag</th>
            <th className="px-2 py-1.5 font-semibold">Language</th>
            {dialect && (
              <th className="px-2 py-1.5 font-semibold">Held against</th>
            )}
            <th className="px-2 py-1.5 font-semibold">Tier</th>
            <th className="px-2 py-1.5 font-semibold">Evidence</th>
            <th className="px-2 py-1.5 text-right font-semibold">Recall</th>
            <th className="px-2 py-1.5 font-semibold">Measured</th>
            <th className="px-2 py-1.5 font-semibold">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const v = r.verdict;
            const tone = v ? (TIER_TONE[v.level] ?? "") : "";
            const on = picked.has(r.tag);
            return (
              <tr key={r.tag}
                  className={`border-b border-line-soft ${
                    on ? "bg-accent-soft" : "hover:bg-line-soft"}`}>
                <td className="px-2 py-1">
                  <input type="checkbox" checked={on} onChange={() => onPick(r.tag)} />
                </td>
                <td className="px-2 py-1 font-mono text-[12px] text-ink-soft">
                  {r.tag}
                </td>
                <td className="px-2 py-1 text-ink">
                  {r.name ?? "—"}
                  {r.region_name && (
                    <span className="text-ink-faint"> · {r.region_name}</span>
                  )}
                </td>
                {dialect && (
                  <td className="px-2 py-1 font-mono text-[11.5px] text-ink-soft">
                    {r.pairing?.reference ?? <span className="text-ink-faint">—</span>}
                    {r.pairing && !r.pairing.decided && (
                      <span className="ml-1.5 chip border-warn/40 text-[10px] text-warn"
                            title={`${r.pairing.why}\n\nNobody has confirmed this baseline, so a scan leaves it out unless asked for it. Confirm it by setting "reviewed": true on this family in references/reference-varieties.json.`}>
                        {r.pairing.basis === "proposed" ? "proposed" : "provisional"}
                      </span>
                    )}
                  </td>
                )}
                <td className={`px-2 py-1 font-medium ${toneText[tone]}`}>
                  {v ? v.level : <span className="text-ink-faint">—</span>}
                </td>
                <td className="px-2 py-1 text-[11.5px] text-ink-faint">
                  {v?.evidence ?? ""}
                  {v?.borderline && <span className="text-warn"> · borderline</span>}
                </td>
                <td className="px-2 py-1 text-right font-mono text-[11.5px] text-ink-soft">
                  {v?.s_content == null ? "" : v.s_content.toFixed(2)}
                </td>
                <td className="px-2 py-1 text-[11.5px] text-ink-faint">
                  {v?.measured_at?.slice(0, 10) ?? ""}
                </td>
                <td className="px-2 py-1">
                  <span className="flex flex-wrap items-center gap-1.5">
                    {v?.inherited_from && (
                      <span className="chip text-[10.5px] text-ink-faint"
                            title={`Took its tier from ${v.inherited_from}, which shares its equivalence class. Nothing was asked of this tag.`}>
                        inherited from {v.inherited_from}
                      </span>
                    )}
                    {v && (
                      <button className={`chip text-[10.5px] ${
                                v.approved ? "border-ok/40 bg-ok-soft text-ok"
                                           : "text-ink-faint hover:text-ink"}`}
                              onClick={() => onApprove(v.id, !v.approved)}
                              title={v.approved
                                ? "Approved — a scan skips this unless told to re-scan approved rows. Click to withdraw."
                                : "A draft: measured, but nobody has stood behind it. Click to approve."}>
                        {v.approved ? "approved" : "draft"}
                      </button>
                    )}
                    {v?.run_id && (
                      <button className="chip text-[10.5px] text-ink-faint hover:text-accent"
                              onClick={() => onOpenRun(v.run_id!)}
                              title="Open the run that produced this, step by step.">
                        open run
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The skip reasons, in the words a person would use.
 *
 *  The codes are what the plan counts, because "it *is* the baseline" and
 *  "nobody has decided this family's baseline" are different answers and a
 *  tally that merged them would be lying. These are the same answers, read
 *  aloud. */
const SKIP_LABEL: Record<string, string> = {
  approved: "already approved",
  measured: "already measured",
  umbrella: "umbrella tag, never a contrast arm",
  "no-region": "names no region",
  "is-reference": "it is the family's reference",
  "no-reference": "family has no defensible reference",
  "other-script": "reference is in another alphabet",
  "unconfirmed-reference": "baseline not confirmed yet",
};

/** What the scan would do, before it does any of it. */
function PlanPanel({ plan }: { plan: BatchPlan }) {
  const est = plan.estimated_calls;
  // A capability scan walks classes and a dialect battery walks varieties.
  // Taken from the plan rather than from the test type, so the panel can only
  // describe the plan it was handed.
  const dialect = plan.unit === "variety";
  const unit = dialect ? "varieties" : "classes";
  return (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase
                     tracking-wider text-ink-faint">
        What this scan will ask
        <Info wide>
          Worked out without making a single call — which is what makes it safe
          to read before committing to one.
          <br /><br />
          {dialect ? (
            <>
              A dialect battery walks <b>varieties</b>, each held against its
              family's reference. Nothing inherits: whether a model marks{" "}
              <i>this</i> variety is the whole claim, so there is no second tag
              it could be true of. The varieties a battery cannot answer are
              counted below under why — the umbrella is the conversion source
              and never an arm, the reference is not measured against itself,
              and a family recorded as having no defensible baseline is a
              finished decision rather than a gap.
            </>
          ) : (
            <>
              Base-language capability is a property of the{" "}
              <span className="font-mono">(iso639-3, script)</span> class rather
              than of the tag, so the ladder runs once per class and the class's
              other tags inherit the result, marked as inherited.
            </>
          )}
        </Info>
      </h3>

      {plan.over_budget && (
        <p className="rounded-md border border-bad/30 bg-bad-soft p-2.5
                      text-[12px] leading-relaxed text-bad">
          <b>Over the ceiling.</b> The scan needs at least {est.minimum} calls and
          the ceiling is {plan.max_calls}. It fails closed — raise the ceiling or
          narrow the scope rather than starting something that would stop half
          way.
        </p>
      )}

      <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-[12.5px]">
        <Line k={`${unit} in scope`} v={plan.classes} />
        <Line k="will be measured" v={plan.to_scan} strong />
        {!dialect && <Line k="tags inheriting" v={plan.inheriting_tags} />}
        {Object.entries(plan.skipped).map(([why, n]) => (
          <Line key={why} k={`skipped — ${SKIP_LABEL[why] ?? why}`} v={n} faint />
        ))}
      </dl>

      {dialect && plan.attestors.length > 0 && (
        <p className="text-[11.5px] leading-relaxed text-ink-faint">
          <b className="text-ink-soft">Panel:</b>{" "}
          <span className="font-mono">{plan.attestors.join(", ")}</span> — every
          one re-qualified by manufactured controls on <i>every</i> variety,
          whatever the record says about it. That is what the per-variety cost
          below is mostly made of.
        </p>
      )}

      <div className="rounded-md border border-line bg-surface p-2.5">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
          Calls
        </div>
        <div className="mt-1 flex items-baseline gap-3 text-[12.5px]">
          <span className="text-ink-faint">floor <b className="text-ink">{est.minimum}</b></span>
          <span className="text-ink-faint">typical <b className="text-ink">{est.typical}</b></span>
          <span className="text-ink-faint">ceiling <b className="text-ink">{est.maximum}</b></span>
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-faint">
          {dialect
            ? "The floor is the free half alone: two arms that come back "
              + "identical stop the battery before a conversion is paid for, "
              + "and a clean square settles a positive before the panel is "
              + "asked at all."
            : "A negative settled by the local gate costs almost nothing; a "
              + "borderline result climbs the ladder and costs the most. "
              + "Neither is knowable before the generation, which is why this "
              + "is a range and not a price."}
        </p>
      </div>

      {plan.unknown_tags.length > 0 && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                      text-[11.5px] leading-relaxed text-warn">
          <b>Not in this locale list:</b>{" "}
          <span className="font-mono">{plan.unknown_tags.join(", ")}</span>. They
          are reported rather than dropped quietly.
        </p>
      )}

      {plan.unconfirmed.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                        text-[11.5px] leading-relaxed text-warn">
          <b>{plan.unconfirmed.length} variety(s) will be measured against a
          baseline nobody has confirmed.</b>{" "}
          The family's entry carries a proposal and a reason, and no one has
          stood behind it — so these verdicts are relative to something still
          open. Named here rather than counted, because which ones is the part
          that matters.
          <div className="mt-1.5 font-mono text-[11px] opacity-80">
            {plan.unconfirmed.slice(0, 24).join(", ")}
            {plan.unconfirmed.length > 24
              && ` … ${plan.unconfirmed.length - 24} more`}
          </div>
        </div>
      )}

      {plan.divergent_siblings.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                        text-[11.5px] leading-relaxed text-warn">
          <b>{plan.divergent_siblings.length} tag(s) will inherit an answer that
          was never asked of them.</b>{" "}
          They share a class with the tag being probed, but the designator names
          the region, so they would now be asked something different. The rows
          are labelled, and closing this properly is a decision about scan cost
          rather than a bug fix.
          <div className="mt-1.5 font-mono text-[11px] opacity-80">
            {plan.divergent_siblings.slice(0, 24).join(", ")}
            {plan.divergent_siblings.length > 24
              && ` … ${plan.divergent_siblings.length - 24} more`}
          </div>
        </div>
      )}

      <p className="text-[11.5px] leading-relaxed text-ink-faint">
        Every verdict this writes is a <b>draft</b>. A scan does not approve its
        own output — approval is a person reading evidence, and a scan that signed
        off on its own drafts would skip, next time, everything it had just
        produced.
      </p>
    </div>
  );
}

function Line({ k, v, strong, faint }: {
  k: string; v: number; strong?: boolean; faint?: boolean;
}) {
  return (
    <div className="contents">
      <dt className={faint ? "text-ink-faint" : "text-ink-soft"}>{k}</dt>
      <dd className={`text-right font-mono ${
        strong ? "font-semibold text-ink" : faint ? "text-ink-faint" : "text-ink-soft"}`}>
        {v}
      </dd>
    </div>
  );
}

/** The queue, while it works. */
function Progress({ batch, onPause, onOpenRun, onApproveAll, onClose }: {
  batch: BatchSnapshot; onPause: () => void;
  onOpenRun: (runId: string) => void;
  onApproveAll: () => void;
  onClose: () => void;
}) {
  // Both gestures here ask once, in a dialog that names the number. Approving
  // a few hundred rows is cheap to do and tedious to undo one at a time, and
  // closing is how a scan's drafts get forgotten — the two things worth a
  // sentence before they happen.
  const drafts = batch.verdicts?.drafts ?? 0;
  const inherited = batch.verdicts?.inherited ?? 0;
  const items = batch.items;
  const done = items.filter((i) => i.status === "done").length;
  const skipped = items.filter((i) => i.status === "skipped").length;
  const failed = items.filter((i) => i.status === "failed").length;
  const pending = items.filter((i) => i.status === "pending").length;
  const running = batch.status === "running";
  const measurable = items.length - skipped;
  const pct = measurable ? Math.round((done + failed) / measurable * 100) : 100;

  return (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase
                     tracking-wider text-ink-faint">
        The scan
        <span className="font-mono normal-case tracking-normal">{batch.id}</span>
        <span className="ml-auto normal-case tracking-normal">{batch.status}</span>
      </h3>

      <div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-line-soft">
          <div className={`h-full ${failed ? "bg-warn" : "bg-accent"}`}
               style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-3 text-[12px]">
          <span className="text-ok">{done} measured</span>
          {failed > 0 && <span className="text-bad">{failed} failed</span>}
          <span className="text-ink-faint">{pending} to go</span>
          {skipped > 0 && (
            <span className="flex items-center gap-1 text-ink-faint">
              {skipped} skipped
              <Info>
                Why a class was skipped is kept per item, because "already
                approved" and "nothing here could be measured" are different
                answers and a progress bar that merges them is lying.
              </Info>
            </span>
          )}
        </div>
      </div>

      <div className="rounded-md border border-line bg-surface p-2.5 text-[12.5px]">
        <span className="text-ink-faint">calls used </span>
        <b className="font-mono text-ink">{batch.calls_used}</b>
        {batch.max_calls != null && (
          <span className="text-ink-faint"> of {batch.max_calls}</span>
        )}
        <p className="mt-1 text-[11.5px] leading-relaxed text-ink-faint">
          Counted as the scan runs, not totalled at the end — a spend figure that
          only arrives afterwards is the one number nobody can act on.
        </p>
      </div>

      {batch.running_run_id && (
        <button className="btn w-full justify-center"
                onClick={() => onOpenRun(batch.running_run_id!)}>
          Open the run in flight
        </button>
      )}

      {running ? (
        <button className="btn w-full justify-center" onClick={onPause}>
          Pause after this language
        </button>
      ) : (
        <div className="space-y-2">
          {drafts > 0 && (
            <>
              <button className="btn w-full justify-center"
                      onClick={() => {
                        if (window.confirm(
                          `Approve all ${drafts} draft${drafts === 1 ? "" : "s"} `
                          + `from this scan?\n\n`
                          + `Approval means a person read the evidence and stood `
                          + `behind it, and it is what a later scan skips on.`
                          + (inherited
                              ? `\n\n${inherited} of them were inherited — nothing `
                                + `was asked of those tags; they keep that label `
                                + `whatever is signed.`
                              : "")
                          + `\n\nIt can be withdrawn per row in the table.`))
                          onApproveAll();
                      }}
                      title="Sign off on every draft this scan wrote. The same gesture as the chip on a row, applied to the batch — and withdrawn the same way.">
                Approve all {drafts} draft{drafts === 1 ? "" : "s"}
              </button>
              <p className="text-[11px] leading-relaxed text-ink-faint">
                Approval means a person read the evidence and stood behind it —
                it is what a later scan skips on, so this is the one gesture
                here that changes what the next one costs.
                {inherited > 0 && (
                  <>
                    {" "}
                    <b className="text-warn">{inherited}</b> of these were{" "}
                    <b>inherited</b>: nothing was asked of those tags. They stay
                    labelled as inherited whatever is signed.
                  </>
                )}
              </p>
            </>
          )}
          {drafts > 0 && (
            <p className="rounded-md border border-warn/30 bg-warn-soft p-2
                          text-[11.5px] leading-relaxed text-warn">
              <b>{drafts} verdict{drafts === 1 ? "" : "s"} from this scan{" "}
              {drafts === 1 ? "is" : "are"} still drafts.</b> Nobody has stood
              behind {drafts === 1 ? "it" : "them"} yet, so the next scan of
              this scope will measure {drafts === 1 ? "it" : "them"} again — and
              this panel is the last place the scan is shown as a whole.
            </p>
          )}
          <button className="btn btn-primary w-full justify-center"
                  onClick={() => {
                    // The one thing closing loses is the scan as a unit: the
                    // verdicts stay in the table and can be approved there one
                    // at a time, but the batch does not come back on this page.
                    if (drafts > 0 && !window.confirm(
                      `Close this scan with ${drafts} draft`
                      + `${drafts === 1 ? "" : "s"} unapproved?\n\n`
                      + `The verdicts stay in the table and can still be `
                      + `approved row by row — but a draft is a reading nobody `
                      + `has signed off, so the next scan of this scope will `
                      + `measure ${drafts === 1 ? "it" : "them"} again, and this `
                      + `is the last view of the scan as a whole.`))
                      return;
                    onClose();
                  }}>
            Close this scan
          </button>
        </div>
      )}

      {batch.status === "halted" && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                      text-[12px] leading-relaxed text-warn">
          <b>Stopped on the ceiling.</b> {batch.error} The classes it did not
          reach are still pending, so the same scope started again picks up from
          there rather than from zero.
        </p>
      )}
      {batch.status === "cancelled" && (
        <p className="rounded-md border border-line bg-surface p-2.5 text-[12px]
                      leading-relaxed text-ink-soft">
          Paused. What it had already measured is recorded, and the classes it
          did not reach stay pending.
        </p>
      )}
      {batch.status === "interrupted" && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                      text-[12px] leading-relaxed text-warn">
          <b>This scan outlived the process driving it.</b> Everything it
          finished is on record; the rest stayed pending.
        </p>
      )}
      {batch.status === "failed" && (
        <p className="rounded-md border border-bad/30 bg-bad-soft p-2.5
                      text-[12px] leading-relaxed text-bad">
          <b>The scan stopped.</b> {batch.error}
        </p>
      )}

      <div>
        <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider
                       text-ink-faint">
          Queue
        </h4>
        <ul className="space-y-0.5">
          {items.map((i) => (
            <li key={i.representative}
                className="flex items-baseline gap-2 rounded px-1.5 py-1 text-[12px]
                           hover:bg-line-soft">
              <span className={`font-mono text-[11.5px] ${
                i.status === "done" ? "text-ok"
                : i.status === "failed" ? "text-bad"
                : i.status === "running" ? "text-accent"
                : "text-ink-faint"}`}>
                {i.representative}
              </span>
              {i.contrast && (
                <span className="font-mono text-[11px] text-ink-faint"
                      title={`Held against ${i.contrast}, the family's reference.`}>
                  vs {i.contrast}
                </span>
              )}
              {i.members.length > 1 && (
                <span className="text-[11px] text-ink-faint"
                      title={i.members.join(", ")}>
                  +{i.members.length - 1}
                </span>
              )}
              <span className="ml-auto text-[11px] text-ink-faint">
                {i.skip_reason ? (SKIP_LABEL[i.skip_reason] ?? i.skip_reason)
                               : i.status}
              </span>
              {i.run_id && (
                <button className="text-[11px] text-ink-faint hover:text-accent"
                        onClick={() => onOpenRun(i.run_id!)}>
                  open
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
