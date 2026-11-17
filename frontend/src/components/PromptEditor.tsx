import { useEffect, useState } from "react";
import type { Call, Planned } from "../types";
import { Badge, Info } from "./ui";

/** What the next step will send, before it is paid for — and, where the method
 * allows it, a place to change it.
 *
 * The prompts here are not a reconstruction. The backend builds them with the
 * production builder and hands them over; what is shown is what goes out. That
 * matters more than it sounds: a pre-flight assembled from a second copy of the
 * rule describes a run that is not the one about to happen.
 *
 * Some steps refuse an edit, and the refusal is the point. A control's answer
 * is certain *by construction*; an edited control is no longer a control, and a
 * rewritten qualification uncertifies the reader it was meant to certify. */
export function PromptEditor({ planned, calls, onCalls }: {
  planned: Planned;
  /** What will actually be sent: the plan, or the operator's rewrite of it.
   *  Held by the page rather than here, because the button that sends it is
   *  not in this panel — there is one run button in this tool and it lives
   *  beside the run's own counters. */
  calls: Call[];
  onCalls: (calls: Call[]) => void;
}) {
  const [editing, setEditing] = useState(false);

  useEffect(() => { setEditing(false); }, [planned]);

  const dirty = JSON.stringify(calls) !== JSON.stringify(planned.calls);

  const patch = (i: number, field: "system" | "user", v: string) =>
    onCalls(calls.map((c, j) => (j === i ? { ...c, [field]: v } : c)));

  return (
    <div className="flex h-full flex-col">
      <div className="scroll flex-1 space-y-3 p-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[15px] font-semibold text-ink">{planned.title}</h2>
            <Info>{planned.explains}</Info>
            <span className="ml-auto">
              <Badge tone={planned.cost ? "warn" : "ok"}>
                {planned.cost === 0
                  ? "free — nothing is sent"
                  : `${planned.cost} model call${planned.cost > 1 ? "s" : ""}`}
              </Badge>
            </span>
          </div>
          <p className="mt-1 text-[13px] text-ink-soft">{planned.summary}</p>
        </div>

        {planned.calls.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider
                               text-ink-faint">
                What will be sent
              </span>
              {planned.editable ? (
                <button className="btn btn-sm" onClick={() => setEditing(!editing)}>
                  {editing ? "Done editing" : "Edit"}
                </button>
              ) : (
                <span className="chip" title="These prompts are the instrument. An edited control is no longer a control, and a rewritten qualification uncertifies the reader it exists to certify.">
                  fixed by the method
                </span>
              )}
              {dirty && (
                <button className="btn btn-sm" onClick={() => onCalls(planned.calls)}>
                  Revert
                </button>
              )}
            </div>

            {calls.map((c, i) => (
              <div key={i} className="rounded-md border border-line">
                <div className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
                  <span className="chip">{c.role}</span>
                  <span className="font-mono text-[11px] text-ink-soft">{c.model}</span>
                  {c.label && (
                    <span className="truncate text-[11px] text-ink-faint">{c.label}</span>
                  )}
                </div>
                <div className="space-y-2 p-2.5">
                  {c.system !== null && (
                    <Field label="system" value={c.system} editing={editing}
                           onChange={(v) => patch(i, "system", v)} />
                  )}
                  <Field label="user" value={c.user} editing={editing}
                         onChange={(v) => patch(i, "user", v)} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {dirty && (
        <footer className="border-t border-warn/30 bg-warn-soft px-4 py-2.5
                           text-[11.5px] leading-relaxed text-warn">
          <b>This is your prompt, not the method's.</b> It is what the run button
          will send, and a rewritten prompt measures a different instrument — the
          run is kept in full and will not become a stored verdict.
        </footer>
      )}
    </div>
  );
}

function Field({ label, value, editing, onChange }: {
  label: string; value: string; editing: boolean; onChange: (v: string) => void;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      {editing ? (
        <textarea
          className="field font-mono text-[12px] leading-relaxed"
          rows={Math.min(14, Math.max(3, value.split("\n").length + 1))}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <div className="prose-text max-h-56 overflow-y-auto rounded-md border
                        border-line-soft bg-surface p-2 text-ink-soft scroll">
          {value}
        </div>
      )}
    </div>
  );
}
