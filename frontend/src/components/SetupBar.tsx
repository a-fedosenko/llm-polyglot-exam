import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { LanguageRow, ModelRow, TestType } from "../types";
import { Badge, Combo, Info } from "./ui";

export interface Setup {
  test_type: TestType;
  tag: string;
  engine: string;
  mode: "manual" | "auto";
  judge_model?: string;
  backtranslator_panel?: string[];
  attestors?: string[];
  varieties?: string[];
  neutral_tag?: string;
  scenario?: string;
}

/** The whole configuration of a run, in one bar.
 *
 * The previous page served two different probes through one form and
 * distinguished their fields by hiding some of them — seven controls either
 * way, in a grid that reflowed. Here the test type is the first choice and it
 * changes what the bar *is*, rather than which half of it is invisible. */
export function SetupBar({ models, enumerable, defaults, onStart, busy, disabled }: {
  models: ModelRow[];
  enumerable: boolean;
  defaults: { judge: string; backtranslator_panel: string[]; pivot: string };
  onStart: (s: Setup) => void;
  busy: boolean;
  disabled?: string | null;
}) {
  const [test, setTest] = useState<TestType>("capability");
  const [mode, setMode] = useState<"manual" | "auto">("manual");
  const [engine, setEngine] = useState("");
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [langs, setLangs] = useState<LanguageRow[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [open, setOpen] = useState(false);

  const [judge, setJudge] = useState(defaults.judge);
  const [readers, setReaders] = useState<string[]>(defaults.backtranslator_panel);
  const [attestors, setAttestors] = useState<string[]>([]);
  const [varieties, setVarieties] = useState<string[]>([]);

  useEffect(() => { if (!engine && models.length) setEngine(models[0].id); },
    [models, engine]);

  useEffect(() => {
    let live = true;
    api.languages(query, { dialectsOnly: test === "dialect", limit: 40 })
      .then((r) => live && setLangs(r.languages))
      .catch(() => {});
    return () => { live = false; };
  }, [query, test]);

  useEffect(() => {
    if (!tag) { setDetail(null); return; }
    api.language(tag, engine || undefined).then((d) => {
      setDetail(d);
      // Two named varieties, the subject first. The umbrella is deliberately
      // not offered: it is the conversion source, and pairing it against a
      // variety leaves one answerable option, which is not a question.
      const named = (d.family ?? []).filter((f: any) => !f.inclusive);
      const other = named.find((f: any) => f.tag !== tag);
      setVarieties(other ? [tag, other.tag] : named.slice(0, 2).map((f: any) => f.tag));
    }).catch(() => setDetail(null));
  }, [tag, engine]);

  const known = detail?.known?.[test];
  const family = (detail?.family ?? []).filter((f: any) => !f.inclusive);
  const ready = Boolean(engine && tag &&
    (test === "capability" || varieties.length === 2));

  const start = () => onStart({
    test_type: test, tag, engine, mode,
    judge_model: judge || undefined,
    backtranslator_panel: test === "capability" ? readers : undefined,
    attestors: test === "dialect" && attestors.length ? attestors : undefined,
    varieties: test === "dialect" ? varieties : undefined,
    neutral_tag: test === "dialect" ? detail?.umbrella ?? undefined : undefined,
  });

  return (
    <div className="border-b border-line bg-panel">
      <div className="flex flex-wrap items-end gap-3 px-4 py-2.5">
        <Segment value={test} onChange={(v) => { setTest(v as TestType); setTag(""); }}
                 label="Test"
                 info={<>
                   <b>Language capability</b> asks whether the model can write the
                   language at all: generate, gate, back-translate, judge the facts.
                   <br /><br />
                   <b>Dialect support</b> asks whether it marks <i>this</i> variety:
                   generate, then have control models judge it blind.
                 </>}
                 options={[
                   ["capability", "Language capability"],
                   ["dialect", "Dialect support"],
                 ]} />

        <div className="min-w-56 flex-1">
          <label className="label">Language</label>
          <Combo
            value={tag ? `${tag} — ${detail?.language?.name ?? ""}` : ""}
            query={query} onQuery={setQuery}
            placeholder={test === "dialect"
              ? "a variety with a named sibling…" : "search 602 locales…"}
            items={langs} keyOf={(l) => l.tag} onPick={(l) => setTag(l.tag)}
            hint={test === "dialect"
              ? "Only tags a dialect test can answer: a named variety with at least one named sibling to contrast it against."
              : undefined}
            render={(l) => (
              <span className="flex items-baseline gap-2">
                <span className="w-24 shrink-0 font-mono text-[12px] text-ink-soft">{l.tag}</span>
                <span className="text-[13px] text-ink">{l.name}</span>
                {l.region && <span className="text-[11px] text-ink-faint">{l.region}</span>}
              </span>
            )}
          />
        </div>

        <div className="min-w-48">
          <label className="label">Model under test</label>
          {enumerable ? (
            <select className="field" value={engine} onChange={(e) => setEngine(e.target.value)}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}
            </select>
          ) : (
            <input className="field" value={engine} placeholder="model id"
                   onChange={(e) => setEngine(e.target.value)} />
          )}
        </div>

        <Segment value={mode} onChange={(v) => setMode(v as any)} label="Mode"
                 info={<>
                   <b>Manual</b> stops before every paid call so you can read the
                   prompt and change it. <b>Auto</b> runs the same steps straight
                   through — it is the absence of a pause, not a different method.
                 </>}
                 options={[["manual", "Manual"], ["auto", "Auto"]]} />

        <button className="btn" onClick={() => setOpen(!open)}>
          Instrument {open ? "▴" : "▾"}
        </button>

        <button className="btn btn-primary" disabled={!ready || busy || !!disabled}
                onClick={start} title={disabled ?? undefined}>
          {busy ? "Starting…" : "Start test"}
        </button>
      </div>

      {(known || disabled) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line-soft
                        px-4 py-1.5 text-[12px]">
          {disabled && <span className="text-bad">{disabled}</span>}
          {known && (
            <>
              <Badge tone={known.approved ? "ok" : "warn"}>
                {known.approved ? "already approved" : "measured, not approved"}
              </Badge>
              <span className="text-ink-soft">
                {engine} · {tag} · <b>{known.level}</b>
                {known.measured_at && ` · ${known.measured_at.slice(0, 10)}`}
              </span>
              <span className="text-ink-faint">
                {known.approved
                  ? "A batch scan will skip this unless you ask it to re-scan approved tags."
                  : "A verdict nobody has signed off on is a draft, and a batch scan will measure it again."}
              </span>
            </>
          )}
        </div>
      )}

      {open && (
        <div className="grid gap-4 border-t border-line bg-surface px-4 py-3
                        md:grid-cols-3">
          {test === "capability" ? (
            <>
              <ModelList label="Reader panel" models={models} enumerable={enumerable}
                         value={readers} onChange={setReaders}
                         info="Back-translators, in the order they are tried. The first that qualifies for this language is used — first to qualify, not best-scoring, because the qualification number separates fabrication from faithful reading and is simply wrong in the middle. Results from different readers are not comparable, which is why the reader is stored on every verdict." />
              <ModelPick label="Judge" models={models} enumerable={enumerable}
                         value={judge} onChange={setJudge}
                         info="Grades fact recall on the back-translation. It never sees the target language, never sees which model produced the text, and is never the model under test." />
            </>
          ) : (
            <>
              <div>
                <label className="label">
                  Contrast pair
                  <Info>Two named varieties. The umbrella tag is the conversion source and is never a contrast arm — pairing it against a variety leaves one answerable option, which is not a question.</Info>
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {family.map((f: any) => {
                    const on = varieties.includes(f.tag);
                    const locked = f.tag === tag;
                    return (
                      <button key={f.tag} disabled={locked}
                        onClick={() => setVarieties(on
                          ? varieties.filter((v) => v !== f.tag)
                          : [...varieties, f.tag].slice(-2))}
                        className={`chip ${on ? "border-accent bg-accent-soft text-accent" : ""}
                                    ${locked ? "opacity-70" : "hover:border-accent"}`}>
                        {f.tag}{f.region ? ` · ${f.region}` : ""}
                      </button>
                    );
                  })}
                </div>
                {varieties.length !== 2 && (
                  <p className="mt-1 text-[11.5px] text-warn">Pick exactly two.</p>
                )}
              </div>
              <ModelList label="Control models" models={models} enumerable={enumerable}
                         value={attestors} onChange={setAttestors}
                         placeholder="leave empty to offer every reachable model"
                         info="Who judges the dialect, blind. Leave this empty and every model the configuration can reach is offered — deliberately wide, because the panel is then narrowed by the controls, and that is the only narrowing that is evidence. The producer's whole family is always dropped. Every one of them is re-qualified for this family on every run." />
            </>
          )}
          <div className="text-[11.5px] leading-relaxed text-ink-faint">
            <b className="text-ink-soft">Why this is on screen.</b> A verdict is only
            comparable with another measured through the same reader, the same
            judge and the same method version. All three are stored on every
            result, and changing one here makes this run a different measurement
            rather than a better one.
          </div>
        </div>
      )}
    </div>
  );
}

function Segment({ value, onChange, options, label, info }: {
  value: string; onChange: (v: string) => void; options: [string, string][];
  label: string; info?: React.ReactNode;
}) {
  return (
    <div>
      <label className="label flex items-center gap-1">{label}{info && <Info>{info}</Info>}</label>
      <div className="flex rounded-md border border-line p-0.5">
        {options.map(([v, l]) => (
          <button key={v} onClick={() => onChange(v)}
            className={`rounded px-2.5 py-1 text-[12.5px] font-medium transition-colors ${
              value === v ? "bg-accent text-white" : "text-ink-soft hover:bg-line-soft"}`}>
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}

function ModelPick({ label, models, enumerable, value, onChange, info }: any) {
  return (
    <div>
      <label className="label flex items-center gap-1">{label}<Info>{info}</Info></label>
      {enumerable ? (
        <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
          {!models.some((m: ModelRow) => m.id === value) && <option value={value}>{value}</option>}
          {models.map((m: ModelRow) => <option key={m.id} value={m.id}>{m.title}</option>)}
        </select>
      ) : (
        <input className="field" value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

function ModelList({ label, models, enumerable, value, onChange, info, placeholder }: any) {
  const [raw, setRaw] = useState(value.join(", "));
  useEffect(() => { setRaw(value.join(", ")); }, [value.join(",")]);
  const ids = useMemo(() => models.map((m: ModelRow) => m.id), [models]);

  return (
    <div>
      <label className="label flex items-center gap-1">{label}<Info>{info}</Info></label>
      {enumerable ? (
        <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto scroll">
          {ids.map((id: string) => {
            const on = value.includes(id);
            return (
              <button key={id}
                onClick={() => onChange(on ? value.filter((v: string) => v !== id)
                                           : [...value, id])}
                className={`chip ${on ? "border-accent bg-accent-soft text-accent"
                                      : "hover:border-accent"}`}>
                {id}
              </button>
            );
          })}
        </div>
      ) : (
        <input className="field" value={raw} placeholder={placeholder}
               onChange={(e) => { setRaw(e.target.value);
                 onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean)); }} />
      )}
      {!value.length && placeholder && (
        <p className="mt-1 text-[11.5px] text-ink-faint">{placeholder}</p>
      )}
    </div>
  );
}
