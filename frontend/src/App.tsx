import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Config } from "./api";
import type { Call, RunSnapshot } from "./types";
import { PromptEditor } from "./components/PromptEditor";
import { SetupBar, type Setup } from "./components/SetupBar";
import { Stage } from "./components/Stage";
import { StepRail } from "./components/StepRail";
import { VerdictPanel } from "./components/VerdictPanel";
import { Confirm, Empty, Info } from "./components/ui";

//: Where the open run's id is kept across a page reload.
//:
//: The run itself lives in the server process, so a reload never destroyed it
//: — the page simply forgot which one it had been watching, which from the
//: operator's side is the same thing as losing it mid-measurement. Session
//: storage rather than local: a run belongs to the tab that opened it.
const OPEN_RUN = "polyglot.open-run";

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adjudication, setAdjudication] = useState<any>(null);
  const es = useRef<EventSource | null>(null);

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(e.message));
  }, []);

  // Reconnect to whatever run this tab had open. A run that outlived its page
  // is resumed exactly where it was; one that outlived its *process* comes back
  // `interrupted` and read-only, which is the honest answer — its evidence is
  // all still on disk and it cannot be continued.
  useEffect(() => {
    const id = sessionStorage.getItem(OPEN_RUN);
    if (!id) return;
    api.run(id)
      .then((snap) => {
        setRun(snap);
        if (snap.status === "running" && snap.auto) watch(id);
      })
      .catch(() => sessionStorage.removeItem(OPEN_RUN));
  }, []);

  // Kept in step with the run, so a reload at any moment finds the right one.
  useEffect(() => {
    if (run) sessionStorage.setItem(OPEN_RUN, run.id);
    else sessionStorage.removeItem(OPEN_RUN);
  }, [run?.id]);

  // The selected step follows the run forward, but only while the reader has
  // not chosen one themselves. Yanking the stage away from someone reading a
  // generation is the fastest way to make a page feel hostile.
  const following = useRef(true);
  useEffect(() => {
    if (run && following.current && run.steps.length)
      setSelected(run.steps[run.steps.length - 1].ordinal);
  }, [run?.steps.length]);

  const closeStream = () => { es.current?.close(); es.current = null; };

  const watch = useCallback((id: string) => {
    closeStream();
    const source = new EventSource(`/api/runs/${id}/events`);
    es.current = source;
    source.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "step") {
        setRun((r) => r && { ...r, steps: [...r.steps, msg.step], spent: msg.spent });
      } else if (msg.type === "end") {
        closeStream();
        api.run(id).then(setRun).catch(() => {});
      }
    };
    source.onerror = () => { closeStream(); api.run(id).then(setRun).catch(() => {}); };
  }, []);

  useEffect(() => closeStream, []);

  const abort = async () => {
    if (!run) return;
    closeStream();
    try { await api.closeRun(run.id); } catch { /* it is going away regardless */ }
    sessionStorage.removeItem(OPEN_RUN);
    setRun(null);
    setAdjudication(null);
    setSelected(null);
  };

  const start = async (s: Setup) => {
    setBusy(true); setError(null); setAdjudication(null); following.current = true;
    try {
      const snap = await api.startRun(s as any);
      setRun(snap);
      setSelected(null);
      if (s.mode === "auto") watch(snap.id);
    } catch (e: any) {
      setError(e.message);
    } finally { setBusy(false); }
  };

  const step = async (calls?: Call[]) => {
    if (!run) return;
    setBusy(true); setError(null); following.current = true;
    try {
      const r = await api.step(run.id, calls);
      const snap = await api.run(run.id);
      setRun(snap);
      if (r.step) setSelected(r.step.ordinal);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const goAuto = async () => {
    if (!run) return;
    setError(null);
    try { await api.auto(run.id); watch(run.id); }
    catch (e: any) { setError(e.message); }
  };

  const pause = async () => {
    if (!run) return;
    await api.pause(run.id);
  };

  const rerun = async (ordinal: number) => {
    if (!run) return;
    setBusy(true); setError(null);
    try {
      await api.rerun(run.id, ordinal);
      const snap = await api.run(run.id);
      setRun(snap);
      setSelected(null); following.current = true;
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const overrideFact = async (specId: string, index: number, verdict: string) => {
    if (!run) return;
    try {
      setAdjudication(await api.adjudicate(run.id,
        [{ spec_id: specId, index, verdict }]));
    } catch (e: any) { setError(e.message); }
  };

  const overrideAttestor = async (probe: string, attestor: string, correct: boolean) => {
    if (!run) return;
    try {
      setAdjudication(await api.adjudicate(run.id, [{ probe, attestor, correct }]));
    } catch (e: any) { setError(e.message); }
  };

  // ← → walk the rail, Enter runs the next step. A page you can drive from the
  // keyboard is the difference between watching a run and reading one.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (!run) return;
      if (e.key === "Enter" && run.next && !busy) { e.preventDefault(); step(); }
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        following.current = false;
        const ords = run.steps.map((s) => s.ordinal);
        const at = selected === null ? ords.length - 1 : ords.indexOf(selected);
        const to = e.key === "ArrowLeft" ? at - 1 : at + 1;
        if (to >= 0 && to < ords.length) setSelected(ords[to]);
        else if (to >= ords.length) { setSelected(null); following.current = true; }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run, selected, busy]);

  const current = run?.steps.find((s) => s.ordinal === selected) ?? null;
  const live = run?.status === "running";
  // Every control in the setup bar describes the run already in flight, so
  // changing one would make the bar disagree with the steps on screen.
  const locked = run
    ? (live
        ? `A ${run.test_type} run on ${run.tag} is open — abort it to change this.`
        : `Showing a finished ${run.test_type} run on ${run.tag}. Start a new test to change this.`)
    : null;
  const blocked = config && !config.models_configured
    ? "No model endpoint is configured — see the banner below."
    : null;

  return (
    <div className="flex h-full flex-col">
      <TopBar config={config} run={run} />

      <SetupBar onStart={start} busy={busy} disabled={blocked} locked={locked} />

      {config && !config.models_configured && (
        <Banner tone="bad" title="No model endpoint is configured">
          {config.models_message}
        </Banner>
      )}
      {config && config.models_configured && !config.language_id.available && (
        <Banner tone="warn" title="Language identification is not installed">
          {config.language_id.why_it_matters}{" "}
          Run <code className="font-mono">polyglot bootstrap</code> to fetch it.
          Until then every verdict on this page rests on a script check alone.
        </Banner>
      )}
      {error && <Banner tone="bad" title="That did not work">{error}</Banner>}

      {!run ? (
        <Empty>
          Pick a language and a model above, then start a test. Nothing is sent
          until you run the first step, and every step says what it will cost
          before you pay for it.
        </Empty>
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_minmax(320px,30%)]">
          <aside className="min-h-0 border-r border-line bg-panel">
            <StepRail steps={run.steps} next={run.next} selected={selected}
                      onSelect={(o) => { following.current = false; setSelected(o); }}
                      running={live} onRerun={rerun} />
          </aside>

          <section className="min-h-0 border-r border-line">
            {current
              ? <Stage step={current}
                       onOverrideFact={run.status === "done" ? overrideFact : undefined}
                       onOverrideAttestor={run.status === "done" ? overrideAttestor : undefined} />
              : run.next
                ? <PromptEditor planned={run.next} busy={busy || !!run.auto}
                                onRun={() => step()} onRunEdited={(c) => step(c)} />
                : <Empty>This run is finished. Pick a step on the left to read it.</Empty>}
          </section>

          <aside className="min-h-0 border-l border-line bg-panel">
            {run.outcome && !run.outcome.incomplete ? (
              <VerdictPanel runId={run.id} outcome={run.outcome}
                            promptEdited={run.prompt_edited}
                            adjudication={adjudication}
                            onClear={abort} />
            ) : (
              <Progress run={run} busy={busy} onStep={() => step()}
                        onAuto={goAuto} onPause={pause} onAbort={abort} />
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

function TopBar({ config, run }: { config: Config | null; run: RunSnapshot | null }) {
  return (
    <header className="flex items-center gap-3 border-b border-line bg-panel px-4 py-2">
      <span className="text-[13px] font-semibold tracking-tight text-ink">
        Polyglot Exam
      </span>
      <span className="chip" title="Part of every verdict's identity. A bump makes stored verdicts stale rather than wrong — they were true of an earlier instrument.">
        method {config?.method_version ?? "…"}
      </span>
      {config?.settings?.scheme && (
        <span className="chip">{config.settings.scheme}</span>
      )}
      {run && (
        <span className="ml-auto flex items-center gap-2 text-[12px] text-ink-faint">
          <span className="font-mono">{run.id}</span>
          <span>·</span>
          <span>{run.status}</span>
        </span>
      )}
    </header>
  );
}

function Progress({ run, busy, onStep, onAuto, onPause, onAbort }: {
  run: RunSnapshot; busy: boolean;
  onStep: () => void; onAuto: () => void; onPause: () => void; onAbort: () => void;
}) {
  const spent = Object.entries(run.spent ?? {}).filter(([, v]) => v > 0);
  const total = spent.reduce((a, [, v]) => a + v, 0);
  const est = run.plan?.estimated_calls ?? {};

  return (
    <div className="scroll h-full space-y-4 p-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-semibold text-ink">This run</h2>
          <Info>
            Nothing here is an estimate of quality. It is what the run has spent
            against your own key, and what it expects to spend before it can say
            anything.
          </Info>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-[28px] font-semibold tabular-nums text-ink">{total}</span>
          <span className="text-[12px] text-ink-faint">
            model call{total === 1 ? "" : "s"} so far
            {est.typical ? ` · ~${est.typical} typical, up to ${est.maximum ?? est.typical}` : ""}
          </span>
        </div>
        {spent.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {spent.map(([k, v]) => <span key={k} className="chip">{k} ×{v}</span>)}
          </div>
        )}
      </div>

      {run.status === "running" && (
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-primary" disabled={busy || !!run.auto || !run.next}
                  onClick={onStep}>
            Run next step
          </button>
          {run.auto ? (
            <button className="btn" onClick={onPause}>Pause after this step</button>
          ) : (
            <button className="btn" onClick={onAuto} disabled={busy || !run.next}>
              Run the rest automatically
            </button>
          )}
          <Confirm
            label="Abort"
            question="Abort this run? Its steps stay on record; nothing further is spent."
            confirmLabel="Abort it"
            onConfirm={onAbort}
          />
        </div>
      )}

      {run.status === "interrupted" && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-3 text-[12.5px]
                      leading-relaxed text-warn">
          <b>This run outlived the process that was driving it.</b> Every step it
          finished is on record and readable, and the corpus kept every text — but
          a half-finished measurement cannot be picked up again, so it stops here.
        </p>
      )}

      {run.status === "cancelled" && (
        <p className="rounded-md border border-line bg-surface p-3 text-[12.5px]
                      leading-relaxed text-ink-soft">
          Aborted. What it had already paid for is kept.
        </p>
      )}

      {run.status === "failed" && (
        <p className="rounded-md border border-bad/30 bg-bad-soft p-3 text-[12.5px] text-bad">
          <b>The run stopped.</b> {run.error}
          <br />
          <span className="opacity-80">
            Everything it had already paid for is kept. Re-run the step that
            failed from the rail — the earlier attempt stays on record.
          </span>
        </p>
      )}

      <Plan plan={run.plan} testType={run.test_type} />
    </div>
  );
}

function Plan({ plan, testType }: { plan: any; testType: string }) {
  if (!plan) return null;
  return (
    <div className="space-y-3 border-t border-line pt-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
        Worked out before anything was spent
      </h3>

      {testType === "capability" ? (
        <>
          <Row label="will be asked as">
            <ul className="space-y-0.5 font-mono text-[12px]">
              {(plan.designators ?? []).map((d: any) => (
                <li key={d.value} className="text-ink-soft">
                  {d.value} <span className="text-ink-faint">({d.kinds.join("")})</span>
                </li>
              ))}
            </ul>
          </Row>
          <Row label="reader panel">
            <span className="font-mono text-[12px] text-ink-soft">
              {(plan.reader_panel ?? []).join(" → ") || "none"}
            </span>
          </Row>
          {plan.divergent_siblings?.length > 0 && (
            <p className="rounded-md border border-warn/30 bg-warn-soft p-2.5
                          text-[11.5px] leading-relaxed text-warn">
              <b>{plan.divergent_siblings.join(", ")}</b> share this tag's
              equivalence class but would now be asked something different. A
              batch scan probes one tag per class and the rest inherit, so those
              would inherit an answer that was never asked of them.
            </p>
          )}
        </>
      ) : (
        <>
          <Row label="contrast">
            <span className="font-mono text-[12px] text-ink-soft">
              {(plan.options ?? []).join(" vs ")}
            </span>
          </Row>
          <Row label="control models">
            <span className="font-mono text-[12px] text-ink-soft">
              {(plan.attestors ?? []).join(", ") || "none eligible"}
            </span>
          </Row>
          {plan.dropped_by_family_guard?.length > 0 && (
            <Row label="dropped">
              <span className="text-[11.5px] text-ink-faint">
                {plan.dropped_by_family_guard.join(", ")} — a model may not grade
                its own family's output
              </span>
            </Row>
          )}
        </>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="label">{label}</div>
      {children}
    </div>
  );
}

function Banner({ tone, title, children }: {
  tone: "bad" | "warn"; title: string; children: React.ReactNode;
}) {
  const cls = tone === "bad"
    ? "border-bad/30 bg-bad-soft text-bad" : "border-warn/30 bg-warn-soft text-warn";
  return (
    <div className={`border-b px-4 py-2 text-[12.5px] leading-relaxed ${cls}`}>
      <b>{title}.</b>{" "}
      <span className="whitespace-pre-wrap opacity-90">{children}</span>
    </div>
  );
}
