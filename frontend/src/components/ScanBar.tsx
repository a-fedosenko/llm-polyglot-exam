import { useEffect, useState } from "react";
import { api, type Proposal } from "../api";
import { usePersisted } from "../persist";
import type { CoverageRow, ModelRow, Rescan, TestType } from "../types";
import { Combo, Field, ModelPick, Pick, Segment, Toggle } from "./ui";

/** The panel is the dialect battery's whole instrument, and each member costs
 *  three control calls plus two attestations on every item — so its size is a
 *  budget decision as much as a methodological one. One is allowed and is
 *  reported at low confidence, because one judge is one opinion. */
const MIN_PANEL = 1;
const MAX_PANEL = 3;

/** How the list is cut down to the part you mean.
 *
 * The axes are the ones the locale list actually carries. There is no
 * linguistic-family field in it, so "language group" has to mean script,
 * macrolanguage or region — naming a facet the data cannot support would be
 * offering a filter that silently returns the wrong set. */
export type Facet = "all" | "script" | "macro" | "region" | "letter";

export interface Scope {
  facet: Facet;
  value: string;
  /** Free text over tag and name, applied on top of the facet. */
  query: string;
}

export interface Instrument {
  engine: string;
  /** Which test the scan runs. The two walk different units — a capability
   *  scan walks equivalence classes, a dialect battery walks varieties — and
   *  they are measured through different instruments, which is why the band
   *  below changes with it rather than offering both. */
  test: TestType;
  judge: string;
  reader: string;
  fallback: string;
  /** The dialect panel: one to three control models. */
  attestors: string[];
  /** Whether varieties whose family baseline nobody has confirmed are
   *  scanned. Off by default — measuring hundreds of varieties against
   *  baselines nobody has stood behind would turn an open question into a
   *  table of claims. */
  includeUnconfirmed: boolean;
  rescan: Rescan;
  maxCalls: number | null;
}

export const FACET_LABEL: Record<Facet, string> = {
  all: "Whole list", script: "Script", macro: "Macrolanguage",
  region: "Region", letter: "First letter",
};

/** The value of one facet for one row, or null when the row has none.
 *
 * One definition, used by the bar to build the menu and by the page to filter
 * the table. Two copies would eventually disagree about what "no region" means
 * and the count beside a facet would stop matching the rows under it. */
export function facetOf(row: CoverageRow, facet: Facet): string | null {
  if (facet === "script") return row.script ?? null;
  if (facet === "macro") return row.macro ?? row.iso639_3 ?? null;
  if (facet === "region") return row.region_name ?? row.region ?? null;
  if (facet === "letter") return (row.name ?? row.tag).slice(0, 1).toUpperCase();
  return null;
}

/** One entry of a facet menu: what it groups, what it is called, and every
 *  spelling somebody might type looking for it.
 *
 *  The value stays the code, because the code is what groups the rows. What is
 *  *shown* is the languages in it: `hbs` and `por` are the right keys and the
 *  wrong thing to read, and a menu nobody can read without a lookup table is a
 *  filter that silently returns the wrong set. The search string carries both
 *  spellings of every member — `pt` and `por`, `sr` and `srp` — because which
 *  one a person reaches for is not predictable. */
export interface FacetEntry {
  value: string;
  label: string;
  detail: string;
  search: string;
  n: number;
}

function facetEntries(rows: CoverageRow[], facet: Facet): FacetEntry[] {
  const groups = new Map<string, CoverageRow[]>();
  for (const r of rows) {
    const v = facetOf(r, facet);
    if (!v) continue;
    (groups.get(v) ?? groups.set(v, []).get(v)!).push(r);
  }
  const out: FacetEntry[] = [];
  for (const [value, members] of groups) {
    if (facet !== "macro") {
      out.push({ value, label: value, detail: "", search: value.toLowerCase(),
                 n: members.length });
      continue;
    }
    // A macrolanguage is a group of languages, so its name is its members'
    // names — `hbs` is Bosnian, Croatian, Serbian and nothing shorter is true.
    const names = [...new Set(members.map((m) => m.name).filter(Boolean))].sort();
    const subtags = [...new Set(members.map((m) => m.tag.split("-")[0]))].sort();
    const label = names.slice(0, 3).join(" · ")
      + (names.length > 3 ? ` +${names.length - 3}` : "");
    out.push({
      value,
      label: label || value,
      detail: [value, ...subtags.filter((s) => s !== value)].join(" · "),
      search: [value, ...subtags, ...names,
               ...members.map((m) => m.iso639_3 ?? "")].join(" ").toLowerCase(),
      n: members.length,
    });
  }
  return out;
}

/** The whole configuration of a scan, in the order the decisions are made.
 *
 * The same three bands as the single-language page, built from the same
 * controls, because they are the same questions: what is measured, who
 * measures it, and how far it is allowed to go. The one band that differs is
 * the last — a scan has no manual mode to choose, and two things a single run
 * does not: how much of what is already stored it may measure again, and a
 * ceiling on what it may spend. */
export function ScanBar({ scope, onScope, instrument, onInstrument, rows,
                          busy, locked, onPlan, onStart, planned, missing }: {
  scope: Scope;
  onScope: (s: Scope) => void;
  instrument: Instrument;
  onInstrument: (i: Instrument) => void;
  rows: CoverageRow[];
  busy: boolean;
  /** Set while a scan is running: every control here describes the scan in
   *  flight, so changing one would make the bar disagree with the queue. */
  locked: string | null;
  onPlan: () => void;
  onStart: () => void;
  planned: boolean;
  missing: string | null;
}) {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [binding, setBinding] = useState(false);
  const [source, setSource] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  // Persisted for the same reason the single-test bar's is: it is what stops
  // the proposal from replacing a restored reader the moment the model list
  // comes back, which would read as the page having forgotten.
  const [touched, setTouched] = usePersisted("scan.touched", false);
  const [facetQuery, setFacetQuery] = useState("");

  const set = (patch: Partial<Instrument>) =>
    onInstrument({ ...instrument, ...patch });

  useEffect(() => {
    let live = true;
    api.models(instrument.engine || undefined).then((r) => {
      if (!live) return;
      setModels(r.models);
      setBinding(r.binding);
      setSource(r.source);
      setProposal(r.propose);
      if (!instrument.engine && r.models.length) set({ engine: r.models[0].id });
      // Same rule as the single-language page: a proposal replaces an untouched
      // instrument and never overwrites a chosen one.
      if (r.propose) {
        const p = r.propose;
        const engine = instrument.engine || r.models[0]?.id || "";
        if (!touched) {
          set({
            engine,
            judge: p.judge ?? "",
            reader: p.backtranslator_panel[0] ?? "",
            fallback: p.backtranslator_panel[1] ?? "",
          });
        } else if (!instrument.judge || !instrument.reader) {
          // Restored, touched, and still missing a half. Only the gaps are
          // filled; the fallback is left alone because empty is a real answer
          // for it.
          set({
            engine,
            judge: instrument.judge || (p.judge ?? ""),
            reader: instrument.reader || (p.backtranslator_panel[0] ?? ""),
          });
        }
      }
    }).catch(() => {});
    return () => { live = false; };
  }, [instrument.engine]);

  const eligible = models.filter(
    (m) => !proposal?.excluded_by_family_guard.includes(m.id));

  const choose = (fn: () => void) => { if (!locked) { setTouched(true); fn(); } };

  // Facet values, counted over the rows themselves. A menu entry whose count
  // came from anywhere but the list under it is a menu entry that can lie.
  const entries = scope.facet === "all" ? [] : facetEntries(rows, scope.facet);
  const chosen = entries.find((e) => e.value === scope.value) ?? null;
  const values = entries
    .sort((a, b) => (scope.facet === "letter"
      ? a.value.localeCompare(b.value)
      : b.n - a.n || a.label.localeCompare(b.label)))
    .filter((e) => !facetQuery.trim()
      || e.search.includes(facetQuery.trim().toLowerCase()));

  const offeringHelp = proposal && (
    <>
      <b>Why these.</b> {proposal.rule}
      {proposal.excluded_by_family_guard.length > 0 && (
        <>
          <br /><br />
          <b>Not offered</b>, because a model may not grade or read back its own
          family's output:{" "}
          <span className="font-mono text-[11px]">
            {proposal.excluded_by_family_guard.join(", ")}
          </span>
        </>
      )}
      <br /><br />
      <b>Model list</b> from the {source}
      {binding ? " — binding: a name absent from it has no endpoint and no key."
               : " — advisory: a name missing from it is still worth trying."}
    </>
  );

  return (
    <div className="border-b border-line bg-panel">
      {/* What is measured. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 px-4 py-2.5">
        <Field label="Model under test"
               info="The scan is of one engine. Every stored result on this page is that engine's, and a verdict measured through one instrument is not comparable to one measured through another.">
          <ModelPick models={models} value={instrument.engine} locked={!!locked}
                     onChange={(v) => { setTouched(false); set({ engine: v }); }} />
        </Field>

        <Field label="Test" wide
               info={<>
                 <b>Language capability</b> walks equivalence classes — one
                 experiment per <span className="font-mono">(iso639-3, script)</span>,
                 with the class's other tags inheriting the result, labelled as
                 inherited rather than measured.
                 <br /><br />
                 <b>Dialect support</b> walks <i>varieties</i>, because its
                 claim is about one variety and nothing can inherit it. Each is
                 held against its family's <b>reference variety</b> — the
                 baseline recorded in{" "}
                 <span className="font-mono">references/reference-varieties.json</span>,
                 which is what turns a family of 26 from 325 pairs into 25
                 contrasts and is also what makes two results in one family
                 comparable, since they were measured against the same thing.
                 <br /><br />
                 A reference is a statement about what a measurement is{" "}
                 <i>relative to</i>, never a claim about which variety is
                 correct. The tags a battery cannot answer — the umbrella, the
                 reference itself, a family with no defensible baseline — are
                 named in the plan rather than quietly dropped.
               </>}>
          <Segment value={instrument.test} locked={!!locked}
                   onChange={(v) => set({ test: v as TestType })}
                   options={[["capability", "Language capability"],
                             ["dialect", "Dialect support"]]} />
        </Field>

        {/* Not locked while a scan runs: this band is how the table is read as
            much as how the next scan is chosen, and a scan in flight carries
            its own scope on its record. */}
        <Field label="Scope" wide
               info={<>
                 Which part of the list this is about. It filters the table and
                 it <i>is</i> the scan set — what you are reading is what would
                 be measured, so there is no second definition of scope to get
                 out of step with the one on screen.
                 <br /><br />
                 Script, macrolanguage and region are the only grouping axes the
                 locale list carries; it has no linguistic-family field, and
                 offering one would be a filter that quietly returns the wrong
                 set.
                 <br /><br />
                 Tick rows in the table to narrow it further — with anything
                 ticked, the scan is exactly those rows.
               </>}>
          <Segment value={scope.facet}
                   onChange={(v) => onScope({ ...scope, facet: v as Facet, value: "" })}
                   options={(Object.keys(FACET_LABEL) as Facet[])
                     .map((f) => [f, FACET_LABEL[f]] as [string, string])} />
        </Field>

        {scope.facet !== "all" && (
          <Field label={FACET_LABEL[scope.facet]}>
            <Combo
              value={chosen ? `${chosen.label} — ${chosen.n} tags` : ""}
              query={facetQuery} onQuery={setFacetQuery}
              width="w-72"
              placeholder={scope.facet === "macro"
                ? "name, pt or por…"
                : `search ${FACET_LABEL[scope.facet].toLowerCase()}…`}
              items={values} keyOf={(e) => e.value}
              onPick={(e) => onScope({ ...scope, value: e.value })}
              render={(e) => (
                <span className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 text-[13px] text-ink">
                    <span className="truncate">{e.label}</span>
                    {e.detail && (
                      <span className="ml-1.5 font-mono text-[10.5px] text-ink-faint">
                        {e.detail}
                      </span>
                    )}
                  </span>
                  <span className="text-[11px] text-ink-faint">{e.n}</span>
                </span>
              )}
            />
          </Field>
        )}

        <Field label="Find">
          <input className="field w-56 text-[13px]" value={scope.query}
                 placeholder="tag or name…"
                 onChange={(e) => onScope({ ...scope, query: e.target.value })} />
        </Field>
      </div>

      {/* Who measures it. The same selects as the single-language page, because
          they are the same questions — and which questions they are depends on
          the test: a capability ladder is graded by a judge reading a
          back-translation, and a dialect battery has no judge at all. Its
          instrument is a panel of control models, re-qualified by manufactured
          controls on every single item. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 border-t
                      border-line-soft px-4 py-2.5">
        {instrument.test === "dialect" ? (
          <>
            <Field label={`Controls ${instrument.attestors.length}/${MAX_PANEL}`} wide
                   info={<>
                     The models that judge the dialect, blind. Unlike a reader
                     chain, <b>every one of these answers</b> — they are a
                     panel, not a fallback — and each costs three control calls
                     plus two attestations <i>per variety</i>, which on a scan
                     is the number to think about before the ceiling.
                     <br /><br />
                     Every one is re-qualified on every item by controls whose
                     answers are certain by construction: one text shown as both
                     halves of a pair, and one real pair shown in both orders. A
                     stored record routes — it decides who is asked first — and
                     it never vouches.
                     {offeringHelp && <><br /><br />{offeringHelp}</>}
                   </>}>
              <div className="flex min-h-[30px] flex-wrap items-center gap-1">
                {eligible.map((m) => {
                  const on = instrument.attestors.includes(m.id);
                  const full = !on && instrument.attestors.length >= MAX_PANEL;
                  return (
                    <Toggle key={m.id} on={on} label={m.id} dim={full}
                            onClick={() => choose(() => set({
                              attestors: on
                                ? instrument.attestors.filter((a) => a !== m.id)
                                : full ? instrument.attestors
                                       : [...instrument.attestors, m.id],
                            }))} />
                  );
                })}
                {!instrument.attestors.length && (
                  <span className="ml-1 text-[11px] text-warn">
                    none selected — pick {MIN_PANEL}–{MAX_PANEL}
                  </span>
                )}
              </div>
            </Field>

            <Field label="Unconfirmed baselines" wide
                   info={<>
                     Every contrastable family is written down in{" "}
                     <span className="font-mono">references/reference-varieties.json</span>{" "}
                     with the variety it is held against and the reason — the
                     ones a rule could derive included, so the open questions
                     are countable in one place.
                     <br /><br />
                     A family is <b>confirmed</b> when somebody set{" "}
                     <span className="font-mono">reviewed: true</span> on it.
                     Until then the entry is a <b>proposal</b>: a written claim
                     with a reason and nobody behind it, which is not the same
                     as a decision.
                     <br /><br />
                     Scanning those is runnable and it is not nothing — every
                     verdict is relative to a baseline no one has stood behind.
                     Left out by default, counted in the plan either way, and
                     the varieties affected are named rather than tallied.
                   </>}>
              <Segment value={instrument.includeUnconfirmed ? "yes" : "no"}
                       locked={!!locked}
                       onChange={(v) => set({ includeUnconfirmed: v === "yes" })}
                       options={[["no", "Confirmed baselines only"],
                                 ["yes", "Include unconfirmed"]]} />
            </Field>
          </>
        ) : (
          <>
        <Field label="Reader"
               info="Translates each generation back into the pivot language so the judge can grade it without reading the target language. Qualified per language before it is trusted, on every item — the cache routes, it never vouches.">
          <Pick models={eligible} binding={binding} value={instrument.reader}
                locked={!!locked}
                onChange={(v) => choose(() => set({
                  reader: v, fallback: v === instrument.fallback ? "" : instrument.fallback,
                }))} />
        </Field>

        <Field label="Fallback"
               info={<>
                 Tried only where the reader cannot be qualified, and it costs
                 nothing otherwise. Worth setting on a scan more than anywhere
                 else: qualification is per <i>language</i>, and across six
                 hundred of them a single reader will fail somewhere. Without a
                 fallback those tags land as <span className="font-mono">unverified</span>.
               </>}>
          <Pick models={eligible.filter((m) => m.id !== instrument.reader)}
                binding={binding} value={instrument.fallback} locked={!!locked}
                none="none — unverified where the reader fails"
                onChange={(v) => choose(() => set({ fallback: v }))} />
        </Field>

        <Field label="Judge" wide
               info={<>
                 Grades fact recall on the back-translation, in the pivot
                 language only. Stored on every row the scan writes: a verdict is
                 comparable only to another measured through the same judge, the
                 same reader and the same method version.
                 {offeringHelp && <><br /><br />{offeringHelp}</>}
               </>}>
          <div className="flex flex-wrap items-center gap-2">
            <Pick models={eligible} binding={binding} value={instrument.judge}
                  locked={!!locked}
                  onChange={(v) => choose(() => set({ judge: v }))} />
            {instrument.judge
              && (instrument.judge === instrument.reader
                  || instrument.judge === instrument.fallback) && (
              <span className="text-[11px] text-warn">
                also a reader — it would be grading its own translation
              </span>
            )}
          </div>
        </Field>
          </>
        )}
      </div>

      {/* How far it may go, and go. */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2 border-t
                      border-line-soft px-4 py-2.5">
        <Field label="Re-scan" wide
               info={<>
                 What a scan may measure <i>again</i>. Three states rather than a
                 switch, because they are three different questions:
                 <br /><br />
                 <b>Nothing measured</b> — only tags with nothing stored. The
                 cheap pass that fills gaps.
                 <br />
                 <b>Drafts too</b> — also re-measure what was measured but never
                 signed off on. A verdict nobody has looked at is a draft.
                 <br />
                 <b>Approved too</b> — also re-ask settled questions. It destroys
                 nothing: the new measurement lands beside the approved row and
                 the approved one stays standing. It does spend money.
               </>}>
          <Segment value={instrument.rescan} locked={!!locked}
                   onChange={(v) => set({ rescan: v as Rescan })}
                   options={[["none", "Nothing measured"],
                             ["drafts", "Drafts too"],
                             ["all", "Approved too"]]} />
        </Field>

        <Field label="Call ceiling"
               info="Fails closed. The scan stops before starting a class it cannot pay for, so the queue never holds a half-measured language — and the plan tells you the floor before you commit to it. Leave empty for no ceiling.">
          <input className="field w-32 font-mono text-[12px]" type="number" min={0}
                 disabled={!!locked} placeholder="no ceiling"
                 value={instrument.maxCalls ?? ""}
                 onChange={(e) => set({
                   maxCalls: e.target.value === "" ? null : Number(e.target.value),
                 })} />
        </Field>

        <button className="btn" disabled={!!missing || busy || !!locked}
                onClick={onPlan} title={locked ?? missing ?? undefined}>
          {busy ? "Working…" : "Plan it — costs nothing"}
        </button>

        <button className="btn btn-primary"
                disabled={!!missing || busy || !!locked || !planned}
                onClick={onStart}
                title={locked ?? missing
                       ?? (planned ? undefined : "Read the plan first.")}>
          Start the scan
        </button>

        <span className="mb-1.5 text-[12px]">
          {locked
            ? <span className="text-ink-faint">{locked}</span>
            : missing
              ? <span className="text-ink-faint">{missing}</span>
              : !planned
                ? <span className="text-ink-faint">
                    Nothing is spent until the plan is read and the scan started.
                  </span>
                : null}
        </span>
      </div>
    </div>
  );
}
