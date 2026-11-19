import { useEffect, useState } from "react";
import { api } from "../api";
import type { Outcome, VerdictRow } from "../types";
import { Badge, Section, toneText } from "./ui";

const TIER_TONE: Record<string, string> = {
  Proficient: "ok", Assisted: "warn", Unusable: "bad",
  "full-command": "ok", production: "ok",
  "recognition-only": "warn", "no-evidence": "bad", indeterminate: "warn",
};

/** The end of a run: what it concluded, what it would have concluded, and the
 * one gesture that makes it count.
 *
 * Approval is separate from measurement by design. "Current" means this is our
 * latest reading; "approved" means a person read the evidence and stood behind
 * it — and that is what a batch scan skips on. A scan that skipped everything
 * merely current would skip every draft it had just produced itself. */
export function VerdictPanel({ runId, outcome, promptEdited, adjudication, onClear }: {
  runId: string;
  outcome: Outcome;
  promptEdited: boolean;
  adjudication: any;
  onClear: () => void;
}) {
  const [verdict, setVerdict] = useState<VerdictRow | null>(null);
  const [history, setHistory] = useState<VerdictRow[]>([]);
  const [busy, setBusy] = useState(false);

  const load = () => {
    if (promptEdited || (outcome as any).instrument_overridden) return;
    api.verdictHistory(outcome.engine, outcome.tag, outcome.test_type)
      .then((r) => {
        setHistory(r.verdicts);
        // This run can now have two rows — what the judge said, and what it
        // said once corrected. The standing-answer card is about whichever one
        // *counts*, so the current row wins; failing that, the measurement,
        // because an unpromoted correction is not an answer yet.
        const mine = r.verdicts.filter((v) => v.run_id === runId);
        setVerdict(mine.find((v) => v.current)
                   ?? mine.find((v) => !v.adjudicated)
                   ?? r.verdicts.find((v) => v.current) ?? null);
      })
      .catch(() => {});
  };
  // Re-read when a correction lands: adjudicating writes a second row on this
  // run, and a panel that did not notice would offer to promote nothing.
  useEffect(load, [runId, promptEdited, adjudication]);

  // The row this run's corrections produced, if any. Found in the history
  // rather than remembered from the adjudication response, so it is still
  // there after a reload — a correction that vanished when the page refreshed
  // would be exactly the thing the operator distrusts.
  const corrected = history.find((v) => v.run_id === runId && v.adjudicated);
  const judged = history.find((v) => v.run_id === runId && !v.adjudicated);

  const promote = async (id: number) => {
    setBusy(true);
    try { await api.promote(id); load(); } finally { setBusy(false); }
  };

  const approve = async (yes: boolean) => {
    if (!verdict) return;
    setBusy(true);
    try { await api.approve(verdict.id, yes); load(); } finally { setBusy(false); }
  };

  const overruled = !!(outcome as any).instrument_overridden;
  const unstored = promptEdited || overruled;
  const spent = Object.values(outcome.calls ?? {}).reduce((a, b) => a + b, 0);

  // **What this panel shows is the standing answer, not the judge's.**
  // A correction counts the moment it is made, so the headline has to move
  // with it: a page whose big number stayed on a reading the operator has just
  // disproved is the page telling them their correction did nothing.
  const adj = (adjudication?.applied ?? []).length
    ? adjudication.adjudicated : null;
  const level = adj ? (adj.tier ?? adj.verdict ?? outcome.level) : outcome.level;
  const confidence = adj
    ? (adj.borderline !== undefined
        ? (adj.borderline ? "low" : "high") : adj.confidence ?? outcome.confidence)
    : outcome.confidence;
  const sContent = adj?.s_content ?? outcome.s_content;
  const sLang = adj?.s_lang ?? outcome.s_lang;
  const ci = adj?.ci ?? [outcome.ci_low, outcome.ci_high];
  const tone = TIER_TONE[level] ?? "";

  return (
    <div className="scroll h-full space-y-4 p-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-semibold text-ink">Verdict</h2>
          {adj && <Badge tone="warn">corrected</Badge>}
          <Badge tone={confidence === "high" ? "ok" : "warn"}>
            confidence {confidence}
          </Badge>
          <button className="btn btn-sm ml-auto" onClick={onClear}>New test</button>
        </div>
        <div className={`mt-3 text-[30px] font-semibold leading-none ${toneText[tone]}`}>
          {level}
        </div>
        {adj && (
          <div className="mt-1 text-[11.5px] text-ink-faint">
            your reading · the judge scored{" "}
            <b className="text-ink-soft">{fmt(adjudication.measured)}</b>, and
            that row is still on record below
          </div>
        )}
        <div className="mt-1.5 font-mono text-[12px] text-ink-faint">
          {outcome.engine} · {outcome.tag}
          {outcome.designator && ` · asked as “${outcome.designator}”`}
        </div>
        {spent > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            <span className="chip">{spent} model call{spent === 1 ? "" : "s"}</span>
            {Object.entries(outcome.calls ?? {}).filter(([, v]) => v > 0)
              .map(([k, v]) => <span key={k} className="chip">{k} ×{v}</span>)}
          </div>
        )}
      </div>

      {outcome.test_type === "capability" && (
        <div className="card grid grid-cols-2 divide-x divide-line">
          <Cell label="adequacy" value={sContent?.toFixed(2)}
                sub={ci?.[0] != null
                  ? `90% CI ${ci[0].toFixed(2)}–${ci[1]?.toFixed(2)}`
                  : undefined} />
          <Cell label="eligibility" value={sLang?.toFixed(2)}
                sub={outcome.refusals ? `${outcome.refusals} refusal(s)` : undefined} />
        </div>
      )}

      {outcome.workflow && (
        <p className="rounded-md border border-line bg-surface p-3 text-[12.5px]
                      leading-relaxed text-ink-soft">
          {outcome.workflow}
        </p>
      )}

      {unstored && (
        <p className="rounded-md border border-warn/30 bg-warn-soft p-3 text-[12.5px]
                      leading-relaxed text-warn">
          <b>Not stored as a verdict.</b>{" "}
          {promptEdited
            ? "A prompt or scenario was rewritten during this run, so it measured a different instrument from everything else in the table."
            : "You overruled the qualification of the reader, so this run measured an instrument the method did not certify — which is a different instrument from the one behind every other row in the table."}
          {" "}Every call it made is still on disk — the corpus keeps the text —
          and the run is readable in full. It simply cannot be compared with
          anything, and a number you cannot compare is not a result.
        </p>
      )}

      {!unstored && verdict && (
        <Section title="Standing answer"
                 info="Approving is what makes a batch scan skip this language. Nothing is ever overwritten: a re-run adds a row, and promoting one is a reversible pointer move.">
          <div className="card p-3">
            <div className="flex items-center gap-2">
              <Badge tone={verdict.approved ? "ok" : "warn"}>
                {verdict.approved ? "approved" : "draft"}
              </Badge>
              {verdict.current && <span className="chip">current</span>}
              <button className="btn btn-sm ml-auto" disabled={busy}
                      onClick={() => approve(!verdict.approved)}>
                {verdict.approved ? "Withdraw approval" : "Approve"}
              </button>
            </div>
            <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
              {verdict.approved
                ? "A batch scan will skip this tag unless it is told to re-scan approved ones."
                : "A verdict nobody has signed off on is a draft. A batch scan will measure it again."}
            </p>
          </div>
          {history.length > 1 && (
            <ul className="mt-2 divide-y divide-line rounded-md border border-line
                           text-[12px]">
              {history.map((h) => (
                <li key={h.id} className="flex items-center gap-2 px-2.5 py-1.5">
                  <span className={`font-medium ${toneText[TIER_TONE[h.level] ?? ""]}`}>
                    {h.level}
                  </span>
                  {h.adjudicated && (
                    <span className="chip text-[10.5px] text-warn"
                          title={h.adjudicated
                            .map((a) => a.fact
                              ? `“${a.fact}”: ${a.was} → ${a.now}`
                              : `${a.attestor} · ${a.probe}`)
                            .join("\n")}>
                      corrected ×{h.adjudicated.length}
                    </span>
                  )}
                  <span className="text-ink-faint">{h.measured_at?.slice(0, 16).replace("T", " ")}</span>
                  <span className="truncate font-mono text-[11px] text-ink-faint">
                    {outcome.test_type === "dialect"
                      ? (h.attestors ?? []).join(", ") || "no panel"
                      : h.backtranslator}
                  </span>
                  {h.current
                    ? <span className="chip ml-auto">current</span>
                    : <button className="btn btn-sm ml-auto"
                        onClick={() => api.promote(h.id).then(load)}>Promote</button>}
                </li>
              ))}
            </ul>
          )}
        </Section>
      )}

      {adjudication && (
        <Section title="Corrected reading"
                 info="The judge is a model and models are wrong. A correction is re-scored through the same function that produced the measurement, and recorded as a second row on this run — so both readings stay on record and a person decides which one counts. Nothing is ever written over a measurement.">
          <div className="card p-3 text-[12.5px]">
            <div className="flex items-baseline gap-3">
              <span className="text-ink-faint">judge</span>
              <span className="font-semibold tabular-nums">
                {fmt(adjudication.measured)}
              </span>
              <span className="text-ink-faint">→ corrected</span>
              <span className="font-semibold tabular-nums text-accent">
                {fmt(adjudication.adjudicated)}
              </span>
            </div>
            <ul className="mt-2 space-y-0.5 text-[11.5px] text-ink-soft">
              {(adjudication.applied ?? []).map((a: any, i: number) => (
                <li key={i}>
                  {a.fact ? `“${a.fact}”: ${a.was} → ${a.now}`
                          : `${a.attestor} · ${a.probe}: ${a.correct ? "right" : "wrong"}`}
                </li>
              ))}
            </ul>

            {corrected?.current ? (
              <div className="mt-2.5 space-y-1.5">
                <p className="text-[11.5px] leading-relaxed text-ink-faint">
                  <b className="text-ink-soft">This is the standing answer.</b>{" "}
                  The coverage table and a batch scan read it. The judge's
                  reading is untouched beside it — putting it back is one click,
                  and so is changing a fact back in the checklist.
                </p>
                {judged && (
                  <button className="btn w-full justify-center" disabled={busy}
                          onClick={() => promote(judged.id)}>
                    Put the judge's reading back ({judged.level})
                  </button>
                )}
              </div>
            ) : corrected ? (
              <div className="mt-2.5 space-y-1.5">
                <p className="text-[11.5px] leading-relaxed text-warn">
                  Recorded, but the table is reading the judge's row — somebody
                  promoted it back.
                </p>
                <button className="btn btn-primary w-full justify-center"
                        disabled={busy} onClick={() => promote(corrected.id)}>
                  Make this the standing answer again
                </button>
              </div>
            ) : (
              <p className="mt-2 text-[11.5px] text-ink-faint">{adjudication.note}</p>
            )}
          </div>
        </Section>
      )}

      {outcome.notes && outcome.notes.length > 0 && (
        <Section title="What this does not say">
          <ul className="list-disc space-y-1.5 pl-5 text-[12.5px] leading-relaxed
                         text-ink-soft">
            {outcome.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </Section>
      )}

      <Section title="The instrument this was measured with"
               info="Results are only comparable within one back-translator, one judge and one method version. That is not a formality: the same engine read by two qualified readers disagreed on tier for 13% of 136 languages.">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11.5px]">
          {([["reader", outcome.backtranslator], ["judge", outcome.judge],
             ["pivot", outcome.pivot],
             ["panel", outcome.attestors?.join(", ")]] as [string, any][])
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-ink-faint">{k}</dt>
                <dd className="break-all text-ink-soft">{v}</dd>
              </div>
            ))}
        </dl>
      </Section>
    </div>
  );
}

function fmt(s: any) {
  if (!s) return "—";
  if (s.s_content !== undefined) return `${s.tier} · ${s.s_content?.toFixed(2)}`;
  return `${s.verdict} · ${s.confidence}`;
}

function Cell({ label, value, sub }: any) {
  return (
    <div className="p-3">
      <div className="label">{label}</div>
      <div className="text-[20px] font-semibold tabular-nums text-ink">{value ?? "—"}</div>
      {sub && <div className="mt-0.5 text-[11px] text-ink-faint">{sub}</div>}
    </div>
  );
}
