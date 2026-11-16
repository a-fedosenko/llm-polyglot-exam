import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Tone } from "../types";

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
 * The previous tool kept these in one dictionary in the page and they drifted
 * from the code. Here the explanatory text that belongs to a *step* is served
 * with the step; this component is for the handful of words the page itself
 * introduces. */
export function Info({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="grid size-4 place-items-center rounded-full border border-line
                   text-[9px] font-bold text-ink-faint hover:border-accent
                   hover:text-accent"
        aria-label="What this means"
      >
        ?
      </button>
      {open && (
        <span className="absolute left-1/2 top-6 z-50 w-72 -translate-x-1/2 rounded-md
                         border border-line bg-panel p-2.5 text-[12px] font-normal
                         leading-relaxed text-ink-soft shadow-lg">
          {children}
        </span>
      )}
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
export function Combo<T>({ value, items, render, keyOf, onPick, placeholder, query, onQuery, hint }: {
  value: string;
  items: T[];
  keyOf: (t: T) => string;
  render: (t: T) => ReactNode;
  onPick: (t: T) => void;
  placeholder: string;
  query: string;
  onQuery: (q: string) => void;
  hint?: ReactNode;
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
    <div className="relative" ref={box}>
      <input
        className="field"
        placeholder={placeholder}
        value={open ? query : value}
        onFocus={() => { setOpen(true); onQuery(""); }}
        onChange={(e) => { onQuery(e.target.value); setOpen(true); }}
      />
      {open && (
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
