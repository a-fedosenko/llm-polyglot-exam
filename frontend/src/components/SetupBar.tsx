import { useEffect, useState } from "react";
import { api, type Proposal } from "../api";
import type { LanguageRow, ModelRow, TestType } from "../types";
import { Badge, Combo, Info } from "./ui";

//: Mirrors `MIN_ATTESTORS` / `MAX_ATTESTORS` in the engine, which is where the
//: rule is enforced. Here it only shapes the controls; the page is not the
//: boundary and must not be the only thing holding the line.
const MIN_PANEL = 1;
const MAX_PANEL = 3;

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
 * Two things are deliberate about what is visible here.
 *
 * The previous page served two different probes through one form and
 * distinguished their fields by hiding some of them — seven controls either
 * way, in a grid that reflowed. Here the test type is the first choice and it
 * changes what the bar *is*, rather than which half of it is invisible.
 *
 * And **the instrument is on the bar, not behind a disclosure**. A verdict is
 * only comparable to another measured through the same judge and the same
 * reader; the tool stores both on every row and refuses to compare across them.
 * They used to be set once in an env file and never seen again, which is how a
 * run comes to be graded by something nobody remembers choosing. They are a
 * per-run choice now, proposed rather than defaulted, and always on screen. */
export function SetupBar({ onStart, busy, disabled, locked }: {
  onStart: (s: Setup) => void;
  busy: boolean;
  disabled?: string | null;
  /** Set while a run is open. Every control here describes the run that is
   *  already in flight, so changing one would make the bar disagree with the
   *  steps on screen — switching the test type did not switch the test, it
   *  just made the page lie about what it was showing. */
  locked?: string | null;
}) {
  const [test, setTest] = useState<TestType>("capability");
  const [mode, setMode] = useState<"manual" | "auto">("manual");
  const [engine, setEngine] = useState("");
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [langs, setLangs] = useState<LanguageRow[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [open, setOpen] = useState(false);

  const [models, setModels] = useState<ModelRow[]>([]);
  const [binding, setBinding] = useState(false);
  const [source, setSource] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);

  const [judge, setJudge] = useState("");
  const [readers, setReaders] = useState<string[]>([]);
  const [attestors, setAttestors] = useState<string[]>([]);
  const [touched, setTouched] = useState(false);
  const [varieties, setVarieties] = useState<string[]>([]);

  // The model list, and a proposed instrument for whichever engine is selected.
  useEffect(() => {
    let live = true;
    api.models(engine || undefined).then((r) => {
      if (!live) return;
      setModels(r.models);
      setBinding(r.binding);
      setSource(r.source);
      setProposal(r.propose);
      if (!engine && r.models.length) setEngine(r.models[0].id);
      // A proposal replaces an untouched instrument and never overwrites a
      // chosen one: changing the engine should move the panel off the engine's
      // own family, and must not silently undo a deliberate pick.
      if (r.propose && !touched) {
        setJudge(r.propose.judge ?? "");
        setReaders(r.propose.backtranslator_panel);
        // Deliberately not proposed. A capability run's reader is one or two
        // calls; a control panel is five per model, and a pre-ticked panel is
        // a spend nobody chose.
        setAttestors([]);
      }
    }).catch(() => {});
    return () => { live = false; };
  }, [engine]);

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
  const eligible = models.filter((m) => !proposal?.excluded_by_family_guard
    .includes(m.id));

  const missing =
    !engine ? "Pick a model to test."
    : !tag ? "Pick a language."
    : test === "capability" && !judge ? "Pick a judge — a verdict is only comparable to one graded by the same judge."
    : test === "capability" && !readers.length ? "Pick at least one reader."
    : test === "dialect" && varieties.length !== 2 ? "Pick exactly two varieties to contrast."
    : test === "dialect" && !attestors.length
      ? `Pick ${MIN_PANEL}–${MAX_PANEL} control models. Each costs three control calls and two attestations.`
    : test === "dialect" && attestors.length > MAX_PANEL
      ? `At most ${MAX_PANEL} control models — ${attestors.length} selected.`
    : null;

  const start = () => onStart({
    test_type: test, tag, engine, mode,
    // A dialect battery has no judge: its instrument is the control panel.
    judge_model: test === "capability" ? judge : undefined,
    backtranslator_panel: test === "capability" ? readers : undefined,
    attestors: test === "dialect" && attestors.length ? attestors : undefined,
    varieties: test === "dialect" ? varieties : undefined,
    neutral_tag: test === "dialect" ? detail?.umbrella ?? undefined : undefined,
  });

  const choose = (fn: () => void) => { if (!locked) { setTouched(true); fn(); } };

  return (
    <div className="border-b border-line bg-panel">
      <div className="flex flex-wrap items-end gap-3 px-4 py-2.5">
        <Segment value={test} locked={!!locked}
                 onChange={(v) => { setTest(v as TestType); setTag(""); }}
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
              ? "a variety with a named sibling…" : "search locales…"}
            items={langs} keyOf={(l) => l.tag} onPick={(l) => setTag(l.tag)}
            locked={!!locked}
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

        <ModelPick label="Model under test" models={models} locked={!!locked}
                   value={engine} onChange={(v) => { setTouched(false); setEngine(v); }}
                   info={<>Every other choice is made relative to this one: a model
                     never reads back or grades its own family's output, because
                     that measures self-consistency and reports it as agreement.</>} />

        <Segment value={mode} locked={!!locked}
                 onChange={(v) => setMode(v as any)} label="Mode"
                 info={<>
                   <b>Manual</b> stops before every paid call so you can read the
                   prompt and change it. <b>Auto</b> runs the same steps straight
                   through — it is the absence of a pause, not a different method.
                 </>}
                 options={[["manual", "Manual"], ["auto", "Auto"]]} />

        <button className="btn btn-primary"
                disabled={!!missing || busy || !!disabled || !!locked}
                onClick={start} title={locked ?? disabled ?? missing ?? undefined}>
          {busy ? "Starting…" : "Start test"}
        </button>
      </div>

      {/* The instrument. On the bar, because it is part of what a verdict
          means — not a setting that can be made once and forgotten.

          Laid out as a label column and a content column so the rows line up
          whatever the labels are called. */}
      <div className="flex items-start gap-3 border-t border-line-soft px-4 py-2">
        <span className="flex shrink-0 items-center gap-1 whitespace-nowrap pt-0.5
                         text-[11px] font-semibold uppercase tracking-wider
                         text-ink-faint">
          Instrument
          <Info>
            A verdict is only comparable to another measured through the same
            judge, the same reader and the same method version. All three are
            stored on every row, and it is not a formality — the same engine read
            by two qualified readers disagreed on tier for 13% of 136 languages.
            Changing one here makes this run a <i>different</i> measurement
            rather than a better one.
          </Info>
        </span>

        <div className="grid min-w-0 flex-1 grid-cols-[5.5rem_minmax(0,1fr)]
                        items-center gap-x-3 gap-y-1.5 text-[12px]">
          {test === "capability" ? (
            <>
              <Row label="judge" info="Grades fact recall on the back-translation. It reads only the pivot language, never the target, and is never the model under test.">
                <Pick models={eligible} binding={binding} value={judge}
                      locked={!!locked}
                      onChange={(v) => choose(() => setJudge(v))} />
                {judge && readers.includes(judge) && (
                  <span className="ml-2 text-[11px] text-warn">
                    also in the reader chain — it would be grading its own
                    translation
                  </span>
                )}
              </Row>

              <Row label="reader chain"
                   info={<>
                     <b>Exactly one reader is used.</b> They are tried in this
                     order and the <i>first that qualifies for this language</i>
                     is the one that reads it back — the rest are fallbacks that
                     cost nothing unless an earlier one fails. Only the winner is
                     recorded on the verdict.
                     <br /><br />
                     It is a chain and not a set because no single model reads
                     every language: one fabricates Chuvash, another reads it
                     correctly. First to qualify, not best-scoring — the
                     qualification number separates fabrication from faithful
                     reading and is simply wrong in the middle.
                   </>}>
                <Chain items={readers} locked={!!locked}
                       onPromote={(i) => choose(() => setReaders(move(readers, i, i - 1)))}
                       onRemove={(i) => choose(() => setReaders(
                         readers.filter((_, j) => j !== i)))} />
              </Row>

              {eligible.some((m) => !readers.includes(m.id)) && (
                <Row label="add">
                  <span className="flex flex-wrap gap-1">
                    {eligible.filter((m) => !readers.includes(m.id)).map((m) => (
                      <Toggle key={m.id} on={false} label={`+ ${m.id}`}
                              onClick={() => choose(() => setReaders([...readers, m.id]))} />
                    ))}
                  </span>
                </Row>
              )}
            </>
          ) : (
            <Row label={`controls ${attestors.length}/${MAX_PANEL}`}
                 info={<>
                   The models that judge the dialect, blind. Unlike a reader
                   chain, <b>every one of these answers</b> — they are a panel,
                   not a fallback — and each costs three control calls plus two
                   attestations. A panel of one is still reported at low
                   confidence, because one judge is one opinion.
                 </>}>
              <span className="flex flex-wrap items-center gap-1">
                {eligible.map((m) => {
                  const on = attestors.includes(m.id);
                  const full = !on && attestors.length >= MAX_PANEL;
                  return (
                    <Toggle key={m.id} on={on} label={m.id} dim={full}
                            onClick={() => choose(() => setAttestors(
                              on ? attestors.filter((a) => a !== m.id)
                                 : full ? attestors : [...attestors, m.id]))} />
                  );
                })}
                {!attestors.length && (
                  <span className="text-[11px] text-warn">
                    none selected — pick {MIN_PANEL}–{MAX_PANEL}
                  </span>
                )}
              </span>
            </Row>
          )}
        </div>

        <button className="btn btn-sm shrink-0" onClick={() => setOpen(!open)}>
          {open ? "Hide detail" : "Why these?"}
        </button>
      </div>

      {open && proposal && (
        <div className="border-t border-line bg-surface px-4 py-2.5 text-[11.5px]
                        leading-relaxed text-ink-faint">
          <p>{proposal.rule}</p>
          {proposal.excluded_by_family_guard.length > 0 && (
            <p className="mt-1">
              Not offered, because a model may not grade or read back its own
              family's output:{" "}
              <span className="font-mono">
                {proposal.excluded_by_family_guard.join(", ")}
              </span>
            </p>
          )}
          <p className="mt-1">
            Model list from the <b>{source}</b>
            {binding
              ? " — binding: a name absent from it has no endpoint and no key."
              : " — advisory: it is the gateway's opinion, and a name missing from it is still worth trying."}
          </p>
        </div>
      )}

      {test === "dialect" && tag && (
        <div className="flex items-start gap-3 border-t border-line-soft
                        px-4 py-2 text-[12px]">
          <span className="flex shrink-0 items-center gap-1 whitespace-nowrap pt-0.5
                           text-[11px] font-semibold uppercase tracking-wider
                           text-ink-faint">
            Contrast pair
            <Info>Two named varieties. The umbrella tag is the conversion source and
              is never a contrast arm — pairing it against a variety leaves one
              answerable option, which is not a question.</Info>
          </span>
          <span className="flex flex-wrap items-center gap-1">
          {family.map((f: any) => (
            <Toggle key={f.tag} on={varieties.includes(f.tag)}
                    label={f.tag + (f.region ? ` · ${f.region}` : "")}
                    onClick={() => { if (!locked) setVarieties(
                      varieties.includes(f.tag)
                        ? varieties.filter((v) => v !== f.tag)
                        : [...varieties, f.tag].slice(-2)); }} />
          ))}
          </span>
        </div>
      )}

      {(known || disabled || (missing && !locked) || locked) && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line-soft
                        px-4 py-1.5 text-[12px]">
          {locked && <span className="text-ink-faint">{locked}</span>}
          {!locked && disabled && <span className="text-bad">{disabled}</span>}
          {!locked && !disabled && missing &&
            <span className="text-ink-faint">{missing}</span>}
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
    </div>
  );
}

/** One labelled row of the instrument grid. The label sits in its own column
 *  so `judge` and `reader chain` line up however long either gets. */
function Row({ label, info, children }: {
  label: string; info?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <>
      <span className="flex items-center justify-end gap-1 whitespace-nowrap
                       text-ink-faint">
        {label}{info && <Info>{info}</Info>}
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-1">{children}</span>
    </>
  );
}

/** The reader chain, in the order it will actually be tried.
 *
 * Numbered, because the order is the whole of the meaning: `route()` takes the
 * first member that qualifies for the language and stops, so #1 is the reader
 * you will almost always get and the rest are insurance. Shown as a chain
 * rather than a row of equal chips, which is what it looked like before and
 * read as "several readers will be used". Exactly one ever is. */
function Chain({ items, onPromote, onRemove, locked }: {
  items: string[]; onPromote: (i: number) => void; onRemove: (i: number) => void;
  locked?: boolean;
}) {
  if (!items.length) {
    return <span className="text-[11px] text-warn">none chosen</span>;
  }
  return (
    <>
      {items.map((id, i) => (
        <span key={id} className="flex items-center">
          {i > 0 && <span className="px-1 text-ink-faint" aria-hidden>→</span>}
          <span className={`inline-flex items-center gap-1 rounded-full border
                            py-0.5 pl-1.5 pr-1 font-mono text-[11px] ${
            i === 0 ? "border-accent bg-accent-soft text-accent"
                    : "border-line text-ink-soft"}`}>
            <span className="tabular-nums opacity-60">{i + 1}</span>
            {id}
            {!locked && i > 0 && (
              <button onClick={() => onPromote(i)} title="Try this one first"
                      className="px-0.5 opacity-50 hover:opacity-100">↑</button>
            )}
            {!locked && (
              <button onClick={() => onRemove(i)} title="Remove from the chain"
                      className="px-0.5 opacity-50 hover:opacity-100">×</button>
            )}
          </span>
        </span>
      ))}
      <span className="ml-1 text-[11px] text-ink-faint">
        {items.length === 1
          ? "no fallback — an unqualified reader means `unverified`"
          : `#1 is used unless it fails to qualify`}
      </span>
    </>
  );
}

function move<T>(xs: T[], from: number, to: number): T[] {
  if (to < 0 || to >= xs.length) return xs;
  const out = [...xs];
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

function Toggle({ on, label, onClick, dim }: {
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

function Pick({ models, binding, value, onChange, locked }: {
  models: ModelRow[]; binding: boolean; value: string;
  onChange: (v: string) => void; locked?: boolean;
}) {
  if (!models.length && !binding) {
    return <input className="field max-w-56 font-mono text-[12px]" value={value}
                  disabled={locked}
                  placeholder="model id" onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <select className="field max-w-56 font-mono text-[12px]" value={value}
            disabled={locked}
            onChange={(e) => onChange(e.target.value)}>
      <option value="">choose…</option>
      {!models.some((m) => m.id === value) && value &&
        <option value={value}>{value}</option>}
      {models.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
    </select>
  );
}

function ModelPick({ label, models, value, onChange, info, locked }: {
  label: string; models: ModelRow[]; value: string;
  onChange: (v: string) => void; info?: React.ReactNode; locked?: boolean;
}) {
  return (
    <div className="min-w-48">
      <label className="label flex items-center gap-1">{label}{info && <Info>{info}</Info>}</label>
      {models.length ? (
        <select className="field" value={value} disabled={locked}
                onChange={(e) => onChange(e.target.value)}>
          {!models.some((m) => m.id === value) && value &&
            <option value={value}>{value}</option>}
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.title || m.id}</option>
          ))}
        </select>
      ) : (
        <input className="field" value={value} placeholder="model id"
               disabled={locked} onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

function Segment({ value, onChange, options, label, info, locked }: {
  value: string; onChange: (v: string) => void; options: [string, string][];
  label: string; info?: React.ReactNode; locked?: boolean;
}) {
  return (
    <div>
      <label className="label flex items-center gap-1">{label}{info && <Info>{info}</Info>}</label>
      <div className={`flex rounded-md border border-line p-0.5 ${
        locked ? "opacity-50" : ""}`}>
        {options.map(([v, l]) => (
          <button key={v} disabled={locked} onClick={() => onChange(v)}
            title={locked ? "A run is open — abort it to change this." : undefined}
            className={`rounded px-2.5 py-1 text-[12.5px] font-medium transition-colors ${
              value === v ? "bg-accent text-white"
                : locked ? "text-ink-faint" : "text-ink-soft hover:bg-line-soft"}`}>
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}
