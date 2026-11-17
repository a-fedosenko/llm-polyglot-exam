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
        setVerdict(r.verdicts.find((v) => v.run_id === runId)
                   ?? r.verdicts.find((v) => v.current) ?? null);
      })
      .catch(() => {});
  };
  useEffect(load, [runId, promptEdited]);

  const approve = async (yes: boolean) => {
    if (!verdict) return;
    setBusy(true);
    try { await api.approve(verdict.id, yes); load(); } finally { setBusy(false); }
  };

  const overruled = !!(outcome as any).instrument_overridden;
  const unstored = promptEdited || overruled;
  const tone = TIER_TONE[outcome.level] ?? "";
  const spent = Object.values(outcome.calls ?? {}).reduce((a, b) => a + b, 0);

  return (
    <div className="scroll h-full space-y-4 p-4">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-semibold text-ink">Verdict</h2>
          <Badge tone={outcome.confidence === "high" ? "ok" : "warn"}>
            confidence {outcome.confidence}
          </Badge>
          <button className="btn btn-sm ml-auto" onClick={onClear}>New test</button>
        </div>
        <div className={`mt-3 text-[30px] font-semibold leading-none ${toneText[tone]}`}>
          {outcome.level}
        </div>
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
          <Cell label="adequacy" value={outcome.s_content?.toFixed(2)}
                sub={outcome.ci_low != null
                  ? `90% CI ${outcome.ci_low.toFixed(2)}–${outcome.ci_high?.toFixed(2)}`
                  : undefined} />
          <Cell label="eligibility" value={outcome.s_lang?.toFixed(2)}
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
        <Section title="If the judge had answered differently"
                 info="This is never written over the measurement. It reports what the same run would have scored — a number you can hand-edit after the fact is not a measurement.">
          <div className="card p-3 text-[12.5px]">
            <div className="flex items-baseline gap-3">
              <span className="text-ink-faint">measured</span>
              <span className="font-semibold tabular-nums">
                {fmt(adjudication.measured)}
              </span>
              <span className="text-ink-faint">→ adjudicated</span>
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
            <p className="mt-2 text-[11.5px] text-ink-faint">{adjudication.note}</p>
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
