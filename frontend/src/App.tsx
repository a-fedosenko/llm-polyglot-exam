import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Config } from "./api";
import { usePersisted } from "./persist";
import type { Call, RunSnapshot } from "./types";
import { PromptEditor } from "./components/PromptEditor";
import { SetupBar, type Setup } from "./components/SetupBar";
import { Stage } from "./components/Stage";
import { StepRail } from "./components/StepRail";
import { VerdictPanel } from "./components/VerdictPanel";
import { CoveragePage } from "./components/CoveragePage";
import { Confirm, Empty, Info } from "./components/ui";

//: Where the open run's id is kept across a page reload.
//:
//: The run itself lives in the server process, so a reload never destroyed it
//: — the page simply forgot which one it had been watching, which from the
//: operator's side is the same thing as losing it mid-measurement. Session
//: storage rather than local: a run belongs to the tab that opened it.
const OPEN_RUN = "polyglot.open-run";

//: The two things this tool does, and they are two pages rather than two modes
//: of one: measuring a single language is something you *watch*, and what has
//: been measured of a model is something you *read*. They share the engine, the
//: schema and every control on their setup bars — a run opened from the
//: coverage table is the same run the single-language page recorded, because
//: there is only ever one kind of run.
type Page = "test" | "coverage";

export default function App() {
  // Which page you were on is remembered too: a reload in the middle of
  // reading a coverage table should not land you back on the setup bar.
  const [page, setPage] = usePersisted<Page>("page", "test");
  const [config, setConfig] = useState<Config | null>(null);
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adjudication, setAdjudication] = useState<any>(null);
  //: What the next step will send: the plan as it came, or the operator's
  //: rewrite of it. Held here rather than inside the pre-flight panel because
  //: the one button that sends it is in the run panel, and a draft the button
  //: cannot see is a draft the button cannot honour.
  const [draft, setDraft] = useState<Call[] | null>(null);
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
        setAdjudication(snap.adjudication ?? null);
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

  useEffect(() => { setDraft(null); }, [run?.steps.length, run?.next?.kind]);

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

  //: The single run gesture. It sends the draft when there is one, so the
  //: button in the run panel and the prompt on the stage can never disagree
  //: about what goes out.
  const runStep = () => {
    if (!run?.next || busy || run.auto) return;
    const dirty = draft && JSON.stringify(draft)
                           !== JSON.stringify(run.next.calls);
    step(dirty ? draft! : undefined);
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

  // The operator's ruling on the back-translator. It changes what happens
  // next — a thrown-out reader sends the panel round again — so the run is
  // reloaded whole rather than patched.
  const ruleOnReader = async (decision: string, why = "") => {
    if (!run) return;
    setBusy(true); setError(null);
    try {
      await api.ruleOnReader(run.id, decision, why);
      const snap = await api.run(run.id);
      setRun(snap);
      setSelected(null); following.current = true;
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
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
      if (e.key === "Enter" && run.next && !busy && !run.auto) {
        e.preventDefault();
        runStep();
      }
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
  // Locked only while a run is actually in flight: changing a control then
  // would make the bar disagree with the steps on screen. A run that has
  // stopped — finished, aborted, failed, or interrupted by a restart — is
  // something you are *reading*, and locking the bar against it left the page
  // with no way out at all: the abort button is for live runs and the "new
  // test" button lives on a verdict panel an interrupted run never reaches.
  const locked = live
    ? `A ${run!.test_type} run on ${run!.tag} is open — abort it to change this.`
    : null;
  const blocked = config && !config.models_configured
    ? "No model endpoint is configured — see the banner below."
    : null;

  // Any item of a scan opens on this page, read-only if it has finished. That
  // is most of the value of having built both pages against one schema.
  const openRun = async (runId: string) => {
    setError(null);
    try {
      const snap = await api.run(runId);
      setRun(snap);
      setAdjudication(snap.adjudication ?? null);
      setSelected(null); following.current = true;
      setPage("test");
      if (snap.status === "running" && snap.auto) watch(runId);
    } catch (e: any) { setError(e.message); }
  };

  if (page === "coverage") {
    return (
      <div className="flex h-full flex-col">
        <TopBar config={config} run={run} page={page} onPage={setPage} />
        <CoveragePage onOpenRun={openRun} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar config={config} run={run} page={page} onPage={setPage} />

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
                      onSelectNext={() => { following.current = true; setSelected(null); }}
                      running={live} onRerun={rerun} />
          </aside>

          <section className="flex min-h-0 flex-col border-r border-line">
            {current ? (
              <>
                <Stage step={current}
                       adjudication={adjudication}
                       onOverrideFact={overrideFact}
                       onOverrideAttestor={run.status === "done" ? overrideAttestor : undefined}
                       onRuleOnReader={live && !run.auto && !busy
                                       && current.ordinal === run.steps.length - 1
                                         ? ruleOnReader : undefined} />
              </>
            ) : run.next
                ? <PromptEditor planned={run.next}
                                calls={draft ?? run.next.calls}
                                onCalls={setDraft} />
                : <Empty>This run is finished. Pick a step on the left to read it.</Empty>}
          </section>

          <aside className="min-h-0 border-l border-line bg-panel">
            {run.outcome && !run.outcome.incomplete ? (
              <VerdictPanel runId={run.id} outcome={run.outcome}
                            promptEdited={run.prompt_edited}
                            adjudication={adjudication}
                            onClear={abort} />
            ) : (
              <Progress run={run} busy={busy} edited={!!draft
                          && JSON.stringify(draft) !== JSON.stringify(run.next?.calls)}
                        onStep={runStep} onReadNext={() => {
                          following.current = true; setSelected(null);
                        }}
                        onAuto={goAuto} onPause={pause} onAbort={abort} />
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

function TopBar({ config, run, page, onPage }: {
  config: Config | null; run: RunSnapshot | null;
  page: Page; onPage: (p: Page) => void;
}) {
  return (
    <header className="flex items-center gap-3 border-b border-line bg-panel px-4 py-2">
      <span className="text-[13px] font-semibold tracking-tight text-ink">
        Polyglot Exam
      </span>
      <nav className="flex rounded-md border border-line p-0.5">
        {([["test", "Single test"], ["coverage", "Coverage & scan"]] as [Page, string][])
          .map(([p, label]) => (
            <button key={p} onClick={() => onPage(p)}
              className={`rounded px-2.5 py-1 text-[12.5px] font-medium transition-colors ${
                page === p ? "bg-accent text-white"
                           : "text-ink-soft hover:bg-line-soft"}`}>
              {label}
            </button>
          ))}
      </nav>
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

/** What the run has spent, and every decision about where it goes next.
 *
 * **One run button, and this is it.** There used to be a second at the foot of
 * the pre-flight — "Run this step" beside "Run next step", a hand's width
 * apart, doing the same thing. Two primary buttons read as a choice, and the
 * operator spends their first minute looking for a difference that is not
 * there. What the step will send still lives on the stage, where it can be
 * read and rewritten; what it costs and when it goes lives here. */
function Progress({ run, busy, edited, onStep, onReadNext, onAuto, onPause, onAbort }: {
  run: RunSnapshot; busy: boolean;
  /** The prompt on the stage has been rewritten, so the button is about to
   *  send something other than the method's own words. It says so. */
  edited: boolean;
  onStep: () => void; onReadNext: () => void;
  onAuto: () => void; onPause: () => void; onAbort: () => void;
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

      {run.status === "running" && run.next && (
        <div className="rounded-md border border-line bg-surface p-3">
          <div className="label">next step</div>
          <button onClick={onReadNext}
                  className="mt-0.5 block w-full text-left text-[13px] font-medium
                             text-ink hover:text-accent"
                  title="Read what this step will send before you pay for it.">
            {run.next.title}
          </button>
          <div className="mt-0.5 text-[11.5px] text-ink-faint">
            {run.next.cost === 0
              ? "free — nothing is sent"
              : `${run.next.cost} model call${run.next.cost > 1 ? "s" : ""}`}
            {edited && <span className="text-warn"> · your rewritten prompt</span>}
          </div>
          <button className="btn btn-primary relative mt-2.5 w-full justify-center
                             py-2 text-[13.5px]"
                  disabled={busy || !!run.auto} onClick={onStep}
                  title="Send this step. Enter does the same.">
            {busy ? "Running…" : "Run step"}
            <span aria-hidden className="absolute right-2.5 text-[12px] opacity-60">
              ↵
            </span>
          </button>
        </div>
      )}

      {run.status === "running" && (
        <div className="flex flex-wrap gap-2">
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

      {run.status !== "running" && (
        <button className="btn btn-primary w-full" onClick={onAbort}>
          Close this run and set up another
        </button>
      )}

      {run.status === "interrupted" && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-3 text-[12.5px]
                      leading-relaxed text-warn">
          <b>This run outlived the process that was driving it.</b> Every step it
          finished is on record and readable, and the corpus kept every text — but
          a half-finished measurement cannot be picked up again, so it stops
          here. Close it above, or read its steps on the left first.
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
          {plan.neutral && (
            <Row label="rewritten from">
              <span className="font-mono text-[12px] text-ink-soft">
                {plan.neutral.designator}
              </span>
              {plan.neutral.script_adjusted && (
                <p className="mt-1 text-[11.5px] leading-relaxed text-ink-faint">
                  The umbrella tag <span className="font-mono">{plan.neutral.tag}</span>{" "}
                  is written in another alphabet, so the conversion source was
                  asked for in this pair's. Handing the rewrite text in the
                  wrong script makes a correct conversion score as a paraphrase:
                  every token moves, and what moved is transliteration rather
                  than the variety.
                </p>
              )}
            </Row>
          )}
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
