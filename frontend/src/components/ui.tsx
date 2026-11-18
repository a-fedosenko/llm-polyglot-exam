import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { ModelRow, Tone } from "../types";

export const toneText: Record<string, string> = {
  ok: "text-ok", warn: "text-warn", bad: "text-bad", "": "text-ink-faint",
};
export const toneBg: Record<string, string> = {
  ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", "": "bg-ink-faint",
};
export const toneSoft: Record<string, string> = {
  ok: "bg-ok-soft text-ok border-ok/30",
  warn: "bg-warn-soft text-warn border-warn/30",
  bad: "bg-bad-soft text-bad border-bad/30",
  "": "bg-line-soft text-ink-soft border-line",
};

export function Dot({ tone, filled = true }: { tone: Tone; filled?: boolean }) {
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${
        filled ? toneBg[tone] : "border-2 border-line bg-transparent"
      }`}
    />
  );
}

export function Badge({ tone = "", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5
                      text-[11px] font-semibold ${toneSoft[tone]}`}>
      {children}
    </span>
  );
}

/** A definition on hover, for every term the method uses in a particular way.
 *
 * **Rendered into a portal, not next to the trigger.** The obvious version —
 * an absolutely positioned bubble inside a `relative` span — is clipped by the
 * first ancestor with `overflow` set, and this layout is full of them: the rail
 * scrolls, the stage scrolls, and the body itself is `overflow: hidden` because
 * the page is not supposed to scroll at all. No `z-index` fixes that; a clipped
 * child is clipped whatever its stacking order. So the bubble is attached to
 * `document.body` and positioned from the trigger's own rect.
 *
 * Two things fall out of the portal that are worth having anyway: the text no
 * longer inherits the `uppercase` and letter-spacing of a `.label` it happens
 * to sit inside, and it can be clamped to the viewport instead of running off
 * the edge of whichever panel it started in. */
export function Info({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [at, setAt] = useState<{ left: number; top?: number; bottom?: number } | null>(null);

  const WIDTH = wide ? 420 : 300;
  const GAP = 8;

  const show = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Clamped to the viewport, so a tooltip on a control at the right-hand
    // edge opens inwards rather than off-screen.
    const left = Math.min(
      Math.max(GAP, r.left + r.width / 2 - WIDTH / 2),
      Math.max(GAP, window.innerWidth - WIDTH - GAP),
    );
    // Flipped above the trigger when there is not room below it, which is the
    // common case for anything in the bottom half of the page.
    const roomBelow = window.innerHeight - r.bottom;
    setAt(roomBelow > (wide ? 300 : 190)
      ? { left, top: r.bottom + GAP }
      : { left, bottom: window.innerHeight - r.top + GAP });
  };

  // A tooltip anchored to a rect it captured once is wrong the moment anything
  // moves, and the panels under it scroll.
  useEffect(() => {
    if (!at) return;
    const close = () => setAt(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);

  return (
    <>
      <button
        ref={ref}
        type="button"
        onMouseEnter={show}
        onMouseLeave={() => setAt(null)}
        onFocus={show}
        onBlur={() => setAt(null)}
        onClick={(e) => { e.preventDefault(); at ? setAt(null) : show(); }}
        className="grid size-4 shrink-0 place-items-center rounded-full border
                   border-line text-[9px] font-bold normal-case tracking-normal
                   text-ink-faint hover:border-accent hover:text-accent"
        aria-label="What this means"
      >
        ?
      </button>
      {at && createPortal(
        <div
          role="tooltip"
          style={{ position: "fixed", left: at.left, top: at.top,
                   bottom: at.bottom, width: WIDTH }}
          className="pointer-events-none z-[9999] max-h-[70vh] overflow-hidden
                     rounded-md border border-line bg-panel p-3 text-[12px]
                     font-normal normal-case leading-relaxed tracking-normal
                     text-ink-soft shadow-xl"
        >
          {children}
        </div>,
        document.body,
      )}
    </>
  );
}


/** A destructive action that asks first, inline — no modal to dismiss. */
export function Confirm({ label, question, confirmLabel, onConfirm, className = "" }: {
  label: string; question: string; confirmLabel: string;
  onConfirm: () => void; className?: string;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button className={`btn ${className}`} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border
                     border-bad/40 bg-bad-soft px-2 py-1">
      <span className="text-[11.5px] leading-snug text-bad">{question}</span>
      <button className="btn btn-sm border-bad/40 text-bad hover:bg-bad/10"
              onClick={() => { setAsking(false); onConfirm(); }}>
        {confirmLabel}
      </button>
      <button className="btn btn-sm" onClick={() => setAsking(false)}>Keep</button>
    </span>
  );
}


export function Section({ title, info, right, children }: {
  title: string; info?: ReactNode; right?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="min-w-0">
      <header className="mb-2 flex items-center gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
          {title}
        </h3>
        {info && <Info>{info}</Info>}
        <div className="ml-auto">{right}</div>
      </header>
      {children}
    </section>
  );
}

export function Collapse({ label, children, defaultOpen = false, count }: {
  label: string; children: ReactNode; defaultOpen?: boolean; count?: number;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-line">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px]
                   font-medium text-ink-soft hover:bg-line-soft"
      >
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        {label}
        {count !== undefined && (
          <span className="ml-auto text-[11px] text-ink-faint">{count}</span>
        )}
      </button>
      {open && <div className="border-t border-line p-2.5">{children}</div>}
    </div>
  );
}

export function Text({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`prose-text rounded-md border border-line bg-surface p-2.5 ${className}`}>
      {children}
    </div>
  );
}

export function KeyVal({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px]">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-ink-faint">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-10 text-center">
      <p className="max-w-sm text-[13px] leading-relaxed text-ink-faint">{children}</p>
    </div>
  );
}

/** A searchable picker that stays open on the keyboard. */
export function Combo<T>({ value, items, render, keyOf, onPick, placeholder, query, onQuery, hint, locked, width = "w-64" }: {
  value: string;
  items: T[];
  keyOf: (t: T) => string;
  render: (t: T) => ReactNode;
  onPick: (t: T) => void;
  placeholder: string;
  query: string;
  onQuery: (q: string) => void;
  hint?: ReactNode;
  locked?: boolean;
  /** Fixed rather than fluid: a search field that stretches to fill a toolbar
   *  reads as the most important control on it, and it is the second step. */
  width?: string;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  return (
    <div className={`relative ${width}`} ref={box}>
      <input
        className="field"
        placeholder={placeholder}
        disabled={locked}
        value={open && !locked ? query : value}
        onFocus={() => { if (!locked) { setOpen(true); onQuery(""); } }}
        onChange={(e) => { onQuery(e.target.value); setOpen(true); }}
      />
      {open && !locked && (
        <div className="absolute z-40 mt-1 max-h-72 w-full overflow-y-auto rounded-md
                        border border-line bg-panel py-1 shadow-xl scroll">
          {hint && <div className="px-2.5 py-1.5 text-[11px] text-ink-faint">{hint}</div>}
          {items.length === 0 && (
            <div className="px-2.5 py-2 text-[12px] text-ink-faint">Nothing matches.</div>
          )}
          {items.map((it) => (
            <button
              key={keyOf(it)}
              onMouseDown={(e) => { e.preventDefault(); onPick(it); setOpen(false); }}
              className="block w-full px-2.5 py-1.5 text-left hover:bg-accent-soft"
            >
              {render(it)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* -- the setup controls -------------------------------------------------------
 *
 * Shared, and that is the point rather than a convenience: the single-language
 * page and the scan page ask the same kinds of question — which model, which
 * reader, which judge — and two sets of controls that merely *looked* alike
 * would drift the first time one of them was improved. Every bar on this
 * application is built from these.
 */

/** A group heading. No number on it: the bands already run left to right and
 *  top to bottom, which is how anyone reads them, and a numeral per control is
 *  furniture that has to be looked past rather than read. */
export function GroupLabel({ label, info, wide }: {
  label: string; info?: React.ReactNode; wide?: boolean;
}) {
  return (
    <span className="flex shrink-0 items-center gap-1 whitespace-nowrap pt-0.5
                     text-[11px] font-semibold uppercase tracking-wider
                     text-ink-faint">
      {label}
      {info && <Info wide={wide}>{info}</Info>}
    </span>
  );
}

export function Field({ label, info, children, wide }: {
  label: string; info?: React.ReactNode; children: React.ReactNode;
  /** Widens the info bubble, for the fields whose note is reference text
   *  rather than a sentence. */
  wide?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="mb-1"><GroupLabel label={label} info={info} wide={wide} /></div>
      {children}
    </div>
  );
}

export function Toggle({ on, label, onClick, dim }: {
  on: boolean; label: string; onClick: () => void; dim?: boolean;
}) {
  return (
    <button onClick={onClick}
      title={dim ? "The panel is full — deselect one first." : undefined}
      className={`chip font-mono transition-colors ${
        on ? "border-accent bg-accent-soft text-accent"
           : dim ? "opacity-25"
           : "opacity-60 hover:opacity-100"}`}>
      {label}
    </button>
  );
}

export function Pick({ models, binding, value, onChange, locked, none }: {
  models: ModelRow[]; binding: boolean; value: string;
  onChange: (v: string) => void; locked?: boolean;
  /** Label for the empty option where empty is a real answer, as it is for a
   *  fallback. Absent means the field is required and reads "choose…". */
  none?: string;
}) {
  if (!models.length && !binding) {
    return <input className="field w-64 font-mono text-[12px]" value={value}
                  disabled={locked} placeholder="model id"
                  onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <select className="field w-64 font-mono text-[12px]" value={value}
            disabled={locked} onChange={(e) => onChange(e.target.value)}>
      <option value="">{none ?? "choose…"}</option>
      {!models.some((m) => m.id === value) && value &&
        <option value={value}>{value}</option>}
      {models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
    </select>
  );
}

export function ModelPick({ models, value, onChange, locked }: {
  models: ModelRow[]; value: string; onChange: (v: string) => void; locked?: boolean;
}) {
  if (!models.length) {
    return <input className="field w-56" value={value} placeholder="model id"
                  disabled={locked} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <select className="field w-56" value={value} disabled={locked}
            onChange={(e) => onChange(e.target.value)}>
      {!models.some((m) => m.id === value) && value &&
        <option value={value}>{value}</option>}
      {models.map((m) => (
        <option key={m.id} value={m.id}>{m.title || m.id}</option>
      ))}
    </select>
  );
}

export function Segment({ value, onChange, options, locked, unavailable }: {
  value: string; onChange: (v: string) => void; options: [string, string][];
  locked?: boolean;
  /** Options that exist but cannot be chosen here, mapped to the sentence
   *  saying why. Shown rather than hidden: an option missing from a segment
   *  reads as an option the tool does not have, and "the scan cannot do this
   *  *yet*, for this reason" is a different and more useful statement. */
  unavailable?: Record<string, string>;
}) {
  return (
    <div className={`flex rounded-md border border-line p-0.5 ${
      locked ? "opacity-50" : ""}`}>
      {options.map(([v, l]) => {
        const why = unavailable?.[v];
        return (
          <button key={v} disabled={locked || !!why} onClick={() => onChange(v)}
            title={why ?? (locked ? "A run is open — abort it to change this." : undefined)}
            className={`rounded px-2.5 py-1 text-[12.5px] font-medium transition-colors ${
              value === v ? "bg-accent text-white"
                : why ? "cursor-not-allowed text-ink-faint opacity-40"
                : locked ? "text-ink-faint" : "text-ink-soft hover:bg-line-soft"}`}>
            {l}
          </button>
        );
      })}
    </div>
  );
}
