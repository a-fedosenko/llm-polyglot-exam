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

/** The whole configuration of a run, in the order the decisions are made.
 *
 * Three bands, and the order is the argument:
 *
 *   what is measured        model, test, language — and for a dialect battery
 *                           the variety it is contrasted against, which belongs
 *                           beside the language rather than a band below it,
 *                           because the two name one pair. Each narrows what
 *                           follows: the test decides which languages are
 *                           answerable at all, the language decides what there
 *                           is to contrast.
 *   who measures it         readers and judge, or the control panel — the half
 *                           that *differs* by test type.
 *   mode, then start        last, because mode is the only decision here that
 *                           changes nothing measured.
 *
 * Reading order carries that on its own; the bands were numbered for a while
 * and the numerals were furniture to look past rather than read. The second
 * band's own heading went the same way: reader, fallback and judge sit under
 * their own labels, in the style of the band above, and three labelled selects
 * in a row are already legible as one group without a word naming it.
 *
 * **Every control is the same control.** Reader, fallback and judge are three
 * identical selects, because they are three answers to the same kind of
 * question. An earlier version drew the readers as a bespoke reorderable chain
 * -- it was accurate about `route()` and looked like nothing else on the page,
 * which is a bad trade: the ordering it exposed is fully expressed by "reader"
 * and "fallback", and those are words everybody already has.
 *
 * **The instrument stays visible.** A verdict is only comparable to another
 * measured through the same judge and the same reader; both are stored on every
 * row. They used to live in an env file, which is how a run comes to be graded
 * by something nobody remembers choosing. Dropping the band heading does not
 * dim them — the selects are what was ever visible; what it dropped is a label
 * over controls that already name themselves. */
export function SetupBar({ onStart, busy, disabled, locked }: {
  onStart: (s: Setup) => void;
  busy: boolean;
  disabled?: string | null;
  /** Set while a run is open. Every control here describes the run already in
   *  flight, so changing one would make the bar disagree with the steps on
   *  screen — switching the test type did not switch the test, it just made the
   *  page lie about what it was showing. */
  locked?: string | null;
}) {
  const [test, setTest] = useState<TestType>("capability");
  const [mode, setMode] = useState<"manual" | "auto">("manual");
  const [engine, setEngine] = useState("");
  const [tag, setTag] = useState("");
  const [query, setQuery] = useState("");
  const [langs, setLangs] = useState<LanguageRow[]>([]);
  const [detail, setDetail] = useState<any>(null);

  const [models, setModels] = useState<ModelRow[]>([]);
  const [binding, setBinding] = useState(false);
  const [source, setSource] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);

  const [judge, setJudge] = useState("");
  // Two named slots rather than a list. `route()` still takes an ordered panel
  // and stops at the first that qualifies, so the wire format is unchanged —
  // but "reader, and one to fall back to" is the whole of what anyone needs to
  // say, and a bespoke reorderable chain said it in a shape nothing else on
  // this page uses.
  const [reader, setReader] = useState("");
  const [fallback, setFallback] = useState("");
  const [attestors, setAttestors] = useState<string[]>([]);
  const [touched, setTouched] = useState(false);
  const [varieties, setVarieties] = useState<string[]>([]);
  const [contrastQuery, setContrastQuery] = useState("");

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
        setReader(r.propose.backtranslator_panel[0] ?? "");
        setFallback(r.propose.backtranslator_panel[1] ?? "");
        // Deliberately not proposed. A reader is one or two calls; a control
        // panel is five per model, and a pre-ticked panel is a spend nobody
        // chose.
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
      // The subject is always arm one. Only the opposing arm is a choice, and
      // it is seeded with the first sibling so the common case is one click.
      const named = (d.family ?? []).filter((f: any) => !f.inclusive);
      const other = named.find((f: any) => f.tag !== tag);
      setVarieties(other ? [tag, other.tag] : [tag]);
    }).catch(() => setDetail(null));
  }, [tag, engine]);

  const known = detail?.known?.[test];
  const family = (detail?.family ?? []).filter((f: any) => !f.inclusive);
  // The opposing arm is the only thing there is to choose: the subject is
  // always arm one, so it is not offered again as something to pick.
  const siblings = family.filter((f: any) => f.tag !== tag);
  const contrast = siblings.find((f: any) => varieties.includes(f.tag)) ?? null;
  // Filtered here rather than over the API: a family is a handful of rows that
  // are already loaded, and a round trip to narrow six of them would be slower
  // than the typing.
  const matchingSiblings = siblings.filter((f: any) => {
    const q = contrastQuery.trim().toLowerCase();
    return !q || `${f.tag} ${f.region ?? ""}`.toLowerCase().includes(q);
  });
  const eligible = models.filter(
    (m) => !proposal?.excluded_by_family_guard.includes(m.id));

  const missing =
    !engine ? "Pick a model to test."
    : !tag ? "Pick a language."
    : test === "capability" && !reader ? "Pick a reader."
    : test === "capability" && !judge ? "Pick a judge."
    : test === "dialect" && varieties.filter((v) => v !== tag).length !== 1
      ? "Pick one variety to contrast it against."
    : test === "dialect" && !attestors.length
      ? `Pick ${MIN_PANEL}–${MAX_PANEL} control models.`
    : test === "dialect" && attestors.length > MAX_PANEL
      ? `At most ${MAX_PANEL} control models — ${attestors.length} selected.`
    : null;

  const start = () => onStart({
    test_type: test, tag, engine, mode,
    // A dialect battery has no judge: its instrument is the control panel.
    judge_model: test === "capability" ? judge : undefined,
    // Order is the meaning: the fallback is only reached if the reader cannot
    // be qualified for the language.
    backtranslator_panel: test === "capability"
      ? [reader, fallback].filter(Boolean) : undefined,
    attestors: test === "dialect" ? attestors : undefined,
    varieties: test === "dialect" ? varieties : undefined,
    neutral_tag: test === "dialect" ? detail?.umbrella ?? undefined : undefined,
  });

  const choose = (fn: () => void) => { if (!locked) { setTouched(true); fn(); } };

  /** Everything the dismissed "Why these?" button used to hold. It was a button
   *  only because the bubble was too small for it, which is the wrong way
   *  round: this is reference text, read once, and it belongs where the term
   *  it explains is.
   *
   *  Two halves, because the band that used to carry both as one "Instrument"
   *  heading no longer exists. Comparability is about the judge and the reader
   *  and hangs off the judge; which models are *offered* applies to every
   *  select here, and hangs off whichever one grades. */
  const comparabilityHelp = (
    <>
      A verdict is only comparable to another measured through the same judge,
      the same reader and the same method version. All three are stored on every
      row, and it is not a formality — the same engine read by two qualified
      readers disagreed on tier for 13% of 136 languages. Changing one here makes
      this run a <i>different</i> measurement rather than a better one.
    </>
  );

  const offeringHelp = proposal && (
    <>
      <b>Why these.</b> {proposal.rule}
      {proposal.excluded_by_family_guard.length > 0 && (
        <>
          <br /><br />
          <b>Not offered</b>, because a model may not grade or read back its
          own family's output:{" "}
          <span className="font-mono text-[11px]">
            {proposal.excluded_by_family_guard.join(", ")}
          </span>
        </>
      )}
      <br /><br />
      <b>Model list</b> from the {source}
      {binding
        ? " — binding: a name absent from it has no endpoint and no key."
        : " — advisory: it is the gateway's opinion, and a name missing from"
          + " it is still worth trying."}
    </>
  );

  return (
    <div className="border-b border-line bg-panel">
      {/* Each choice narrows what follows: the test decides which languages
          are answerable at all, and the language decides what there is to
          contrast it against. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 px-4 py-2.5">
        <Field label="Model under test"
               info="Every later choice is made relative to this one: a model never reads back or grades its own family's output, because that measures self-consistency and reports it as agreement.">
          <ModelPick models={models} value={engine} locked={!!locked}
                     onChange={(v) => { setTouched(false); setEngine(v); }} />
        </Field>

        <Field label="Test"
               info={<>
                 <b>Language capability</b> asks whether the model can write the
                 language at all: generate, gate, back-translate, judge the facts.
                 <br /><br />
                 <b>Dialect support</b> asks whether it marks <i>this</i> variety:
                 generate, then have control models judge it blind.
               </>}>
          <Segment value={test} locked={!!locked}
                   onChange={(v) => { setTest(v as TestType); setTag(""); }}
                   options={[["capability", "Language capability"],
                             ["dialect", "Dialect support"]]} />
        </Field>

        <Field label="Language"
               info={test === "dialect"
                 ? "Only tags a dialect test can answer: a named variety with at least one named sibling to contrast it against."
                 : "Any tag in the loaded locale list. A language with no control text can still be gated — inability is provable without a reader — but its quality cannot be scored."}>
          <Combo
            value={tag ? `${tag} — ${detail?.language?.name ?? ""}` : ""}
            query={query} onQuery={setQuery} locked={!!locked} width="w-72"
            placeholder={test === "dialect" ? "variety with a sibling…" : "search locales…"}
            items={langs} keyOf={(l) => l.tag} onPick={(l) => setTag(l.tag)}
            render={(l) => (
              <span className="flex items-baseline gap-2">
                <span className="w-24 shrink-0 font-mono text-[12px] text-ink-soft">{l.tag}</span>
                <span className="text-[13px] text-ink">{l.name}</span>
                {l.region && <span className="text-[11px] text-ink-faint">{l.region}</span>}
              </span>
            )}
          />
        </Field>

        {/* Only a dialect battery has a second arm, so it only appears
            for one of the two tests — beside the variety it is held up
            against, because that pair is one decision made twice. */}
        {test === "dialect" && (
          <Field label="Contrast with"
                 info={<>
                   The language to the left is the other arm and is not
                   repeated here — a battery whose subject is not an arm
                   produces no text for the subject at all, so it is never a
                   choice. This is the one arm there is anything to decide
                   about.
                   <br /><br />
                   Exactly one, which is why it is a dropdown and not a set of
                   toggles: a pair is what the contrast probe compares, and
                   offering a second tick for a question that takes one answer
                   invites a click that has to be taken back.
                   <br /><br />
                   The umbrella tag is never offered: it is the conversion
                   source, and pairing it against a variety leaves one
                   answerable option, which is not a question.
                 </>}>
            {!tag ? (
              <div className="flex h-[30px] items-center text-[11px] text-ink-faint">
                pick a language first
              </div>
            ) : siblings.length === 0 ? (
              <div className="flex h-[30px] items-center text-[11px] text-warn">
                no named sibling to contrast {tag} against
              </div>
            ) : (
              <Combo
                value={contrast
                  ? `${contrast.tag}${contrast.region ? ` — ${contrast.region}` : ""}`
                  : ""}
                query={contrastQuery} onQuery={setContrastQuery}
                locked={!!locked} width="w-72"
                placeholder="search varieties…"
                items={matchingSiblings} keyOf={(f: any) => f.tag}
                onPick={(f: any) => { if (!locked) setVarieties([tag, f.tag]); }}
                render={(f: any) => (
                  <span className="flex items-baseline gap-2">
                    <span className="w-24 shrink-0 font-mono text-[12px] text-ink-soft">{f.tag}</span>
                    <span className="text-[13px] text-ink">{f.region ?? ""}</span>
                  </span>
                )}
              />
            )}
          </Field>
        )}
      </div>

      {/* Who measures it. No band heading: under its own label each select
          reads as the same kind of control as the band above, which is exactly
          what it is, and the heading was a word to step over on the way to the
          only things here that are decisions. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 border-t
                      border-line-soft px-4 py-2.5">
        {test === "capability" ? (
          <>
            <Field label="Reader"
                   info={<>
                     Translates the model's output back into the pivot language
                     so the judge can grade it without reading the target
                     language.
                     <br /><br />
                     Qualified before it is trusted: it is scored against text
                     whose meaning is already known, because a model that cannot
                     read a language does not refuse — it invents. One handed
                     known Chuvash returned “A man is walking. He is wearing a
                     white shirt.”
                   </>}>
              <Pick models={eligible} binding={binding} value={reader}
                    locked={!!locked}
                    onChange={(v) => choose(() => {
                      if (v === fallback) setFallback("");
                      setReader(v);
                    })} />
            </Field>

            <Field label="Fallback"
                   info={<>
                     Tried only if the reader cannot be qualified for this
                     language, and it costs nothing otherwise. Worth setting
                     because qualification is per <i>language</i>, not per model:
                     no single model reads every language, and one that
                     fabricates Chuvash reads German perfectly well.
                     <br /><br />
                     Exactly one reader ends up used, and only that one is
                     recorded on the verdict. With no fallback, a reader that
                     cannot be qualified means the result is `unverified`.
                   </>}>
              <Pick models={eligible.filter((m) => m.id !== reader)}
                    binding={binding} value={fallback} locked={!!locked}
                    none="none — unverified if the reader fails"
                    onChange={(v) => choose(() => setFallback(v))} />
            </Field>

            <Field label="Judge" wide
                   info={<>
                     Grades fact recall on the back-translation. It reads only
                     the pivot language, never the target, and is never the model
                     under test.
                     <br /><br />
                     {comparabilityHelp}
                     {offeringHelp && <><br /><br />{offeringHelp}</>}
                   </>}>
              <div className="flex flex-wrap items-center gap-2">
                <Pick models={eligible} binding={binding} value={judge}
                      locked={!!locked}
                      onChange={(v) => choose(() => setJudge(v))} />
                {judge && (judge === reader || judge === fallback) && (
                  <span className="text-[11px] text-warn">
                    also a reader — it would be grading its own translation
                  </span>
                )}
              </div>
            </Field>
          </>
        ) : (
          <Field label={`Controls ${attestors.length}/${MAX_PANEL}`} wide
                 info={<>
                   The models that judge the dialect, blind. Unlike a reader
                   chain, <b>every one of these answers</b> — they are a panel,
                   not a fallback — and each costs three control calls plus two
                   attestations. A panel of one is still reported at low
                   confidence, because one judge is one opinion.
                   {offeringHelp && <><br /><br />{offeringHelp}</>}
                 </>}>
            <div className="flex min-h-[30px] flex-wrap items-center gap-1">
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
                <span className="ml-1 text-[11px] text-warn">
                  none selected — pick {MIN_PANEL}–{MAX_PANEL}
                </span>
              )}
            </div>
          </Field>
        )}
      </div>

      {/* How to watch it, and go. Nothing here changes what is measured. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2 border-t
                      border-line-soft px-4 py-2.5">
        <Field label="Mode"
               info={<>
                 <b>Manual</b> stops before every paid call so you can read the
                 prompt and change it. <b>Auto</b> runs the same steps straight
                 through — it is the absence of a pause, not a different method.
               </>}>
          <Segment value={mode} locked={!!locked}
                   onChange={(v) => setMode(v as any)}
                   options={[["manual", "Manual"], ["auto", "Auto"]]} />
        </Field>

        <button className="btn btn-primary"
                disabled={!!missing || busy || !!disabled || !!locked}
                onClick={start} title={locked ?? disabled ?? missing ?? undefined}>
          {busy ? "Starting…" : "Start test"}
        </button>

        <span className="mb-1.5 flex flex-wrap items-center gap-2 text-[12px]">
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
                  ? "a batch scan will skip this unless told to re-scan approved tags"
                  : "a draft — a batch scan will measure it again"}
              </span>
            </>
          )}
        </span>
      </div>
    </div>
  );
}

/** A group heading. No number on it: the bands already run left to right and
 *  top to bottom, which is how anyone reads them, and a numeral per control is
 *  furniture that has to be looked past rather than read. */
function GroupLabel({ label, info, wide }: {
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

function Field({ label, info, children, wide }: {
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

function Pick({ models, binding, value, onChange, locked, none }: {
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

function ModelPick({ models, value, onChange, locked }: {
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

function Segment({ value, onChange, options, locked }: {
  value: string; onChange: (v: string) => void; options: [string, string][];
  locked?: boolean;
}) {
  return (
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
  );
}
