import type { Planned, Step } from "../types";
import { Dot } from "./ui";

/** The rail: where the run has been, where it is, and what it has not reached.
 *
 * Grouped by phase rather than listed flat, because the phases are the method
 * — designator, reader, grading, verdict — and a flat list of fourteen
 * generations tells a reader nothing about which part of the measurement they
 * are looking at. A completed step stays clickable: re-reading the generation
 * that a verdict rests on is the single most useful thing this page does. */

const PHASE_LABEL: Record<string, string> = {
  designator: "Designator",
  reader: "Reader",
  grading: "Grading",
  verdict: "Verdict",
  plan: "Plan",
  produce: "Produce",
  convert: "Convert",
  panel: "Control panel",
  attest: "Attestation",
};

export function StepRail({ steps, next, selected, onSelect, onSelectNext, running,
                          onRerun }: {
  steps: Step[];
  next: Planned | null;
  selected: number | null;
  onSelect: (ordinal: number) => void;
  /** Takes the stage back to the step that has not been paid for yet. The
   *  card is the only run-adjacent thing in the rail that is not history, and
   *  not being able to click it was how a reader got stranded on an old step
   *  with no way forward but the keyboard. */
  onSelectNext: () => void;
  running: boolean;
  onRerun: (ordinal: number) => void;
}) {
  const phases: { phase: string; steps: Step[] }[] = [];
  for (const s of steps) {
    const last = phases[phases.length - 1];
    if (last && last.phase === s.phase) last.steps.push(s);
    else phases.push({ phase: s.phase, steps: [s] });
  }

  return (
    <nav className="flex h-full flex-col">
      <div className="scroll flex-1 px-2 py-3">
        {phases.map((group, gi) => (
          <div key={gi} className="mb-3 last:mb-0">
            <div className="px-2 pb-1 text-[10px] font-semibold uppercase
                            tracking-widest text-ink-faint">
              {PHASE_LABEL[group.phase] ?? group.phase}
            </div>
            {group.steps.map((s) => (
              <div key={s.ordinal} className="group relative">
                <button
                  onClick={() => onSelect(s.ordinal)}
                  className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5
                              text-left transition-colors ${
                    selected === s.ordinal
                      ? "bg-accent-soft"
                      : "hover:bg-line-soft"
                  }`}
                >
                  <span className="mt-1.5"><Dot tone={s.tone} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] font-medium text-ink">
                      {s.title}
                    </span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {s.summary}
                    </span>
                  </span>
                  {s.edited && (
                    <span className="mt-0.5 shrink-0 text-[10px] font-semibold text-warn"
                          title="The prompt for this step was rewritten by hand.">
                      edited
                    </span>
                  )}
                </button>
                <button
                  onClick={() => onRerun(s.ordinal)}
                  title="Run this step again. Everything after it is discarded; the earlier attempt is kept as evidence."
                  className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded
                             border border-line bg-panel px-1.5 py-0.5 text-[10px]
                             text-ink-soft group-hover:block hover:border-accent
                             hover:text-accent"
                >
                  ↻
                </button>
              </div>
            ))}
          </div>
        ))}

        {next && (
          <button
            onClick={onSelectNext}
            className={`mt-2 w-full rounded-md border border-dashed px-2 py-2
                        text-left transition-colors ${
              selected === null ? "border-accent bg-accent-soft"
                                : "border-line hover:bg-line-soft"}`}
          >
            <div className="flex items-start gap-2">
              <span className="mt-1.5"><Dot tone="" filled={false} /></span>
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-medium text-ink-soft">
                  {next.title}
                </div>
                <div className="text-[11px] leading-snug text-ink-faint">
                  {next.summary}
                </div>
              </div>
            </div>
          </button>
        )}

        {!next && !running && steps.length > 0 && (
          <div className="mt-2 px-2 text-[11px] text-ink-faint">Run complete.</div>
        )}
      </div>
    </nav>
  );
}
