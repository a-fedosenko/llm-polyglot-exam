import type { Step } from "../types";
import { Badge, Collapse, Confirm, Info, KeyVal, Section, Text, toneSoft,
         toneText } from "./ui";

/** One step's full evidence, and nothing else on screen.
 *
 * The previous page put a dozen `<dl>` rows under every step and left a reader
 * to triage them; its own owner called it "messy and not really
 * understandable". The rule here is that each kind of step has exactly one
 * thing it is *for*, that thing is the largest element on the stage, and
 * everything else is behind a disclosure.
 *
 * What is never behind a disclosure: the raw model output. Nearly every real
 * defect this project has found came from reading output rather than from
 * tests — two marker defects, a duplicate-row bug, and a table that had Swahili
 * at chrF++ 79.8 sitting in the "did not write Swahili" bucket. */

export function Stage({ step, adjudication, onOverrideFact, onOverrideAttestor,
                       onRuleOnReader }: {
  step: Step;
  /** Corrections the operator has already made on this run, so a fact that was
   *  overruled shows as overruled rather than as whatever the judge said. */
  adjudication?: any;
  onOverrideFact?: (specId: string, index: number, verdict: string) => void;
  onOverrideAttestor?: (probe: string, attestor: string, correct: boolean) => void;
  /** Present only on the latest step of a live run: the operator's ruling on
   *  the reader changes what happens next, so it cannot be made from history. */
  onRuleOnReader?: (decision: string, why?: string) => void;
}) {
  const r = step.result ?? {};
  return (
    <div className="scroll min-h-0 flex-1 p-4">
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-semibold text-ink">{step.title}</h2>
          <Info>{step.explains}</Info>
          {step.edited && <Badge tone="warn">prompt rewritten</Badge>}
          <span className="ml-auto flex gap-1.5">
            {Object.entries(step.calls ?? {}).map(([k, v]) => (
              <span key={k} className="chip">{k} ×{v}</span>
            ))}
          </span>
        </div>
        <p className={`mt-1 text-[13px] font-medium ${toneText[step.tone]}`}>
          {step.summary}
        </p>
      </header>

      <div className="space-y-4">
        <Body step={step} r={r}
              adjudication={adjudication}
              onOverrideFact={onOverrideFact}
              onOverrideAttestor={onOverrideAttestor}
              onRuleOnReader={onRuleOnReader} />
        <Sent step={step} />
      </div>
    </div>
  );
}

function Sent({ step }: { step: Step }) {
  const calls = step.request?.calls ?? [];
  if (!calls.length) return null;
  return (
    <Collapse label="Exactly what was sent" count={calls.length}>
      <div className="space-y-2">
        {calls.map((c, i) => (
          <div key={i}>
            <div className="mb-1 flex items-center gap-2">
              <span className="chip">{c.role}</span>
              <span className="font-mono text-[11px] text-ink-soft">{c.model}</span>
            </div>
            {c.system && <Text className="mb-1 text-ink-soft">{c.system}</Text>}
            <Text>{c.user}</Text>
          </div>
        ))}
      </div>
    </Collapse>
  );
}

function Body({ step, r, adjudication, onOverrideFact, onOverrideAttestor,
                onRuleOnReader }: any) {
  switch (step.kind) {
    case "generate":
    case "extra-item":
    case "judge":
      return <ItemStep r={r} adjudication={adjudication}
                       onOverrideFact={onOverrideFact} />;
    case "designator-trial":
      return <TrialStep r={r} />;
    case "designator-chosen":
      return <ChosenStep r={r} />;
    case "deterministic-negative":
      return <DeterministicStep r={r} />;
    case "qualification":
      return <QualificationStep r={r} onRule={onRuleOnReader} />;
    case "reader-override":
      return <ReaderOverrideStep r={r} />;
    case "no-answer":
      return <NoAnswerStep r={r} />;
    case "unverified":
      return <UnverifiedStep r={r} />;
    case "score":
      return <ScoreStep r={r} />;
    case "plan":
      return <PlanStep r={r} />;
    case "produce":
      return <ProduceStep r={r} />;
    case "convert":
      return <ConvertStep r={r} />;

    case "controls":
      return <ControlsStep r={r} />;
    case "identify":
      return <IdentifyStep r={r} onOverride={onOverrideAttestor} />;
    case "separate":
      return <SeparateStep r={r} onOverride={onOverrideAttestor} />;
    case "verdict":
      return <DialectVerdictStep r={r} />;
    default:
      return <pre className="prose-text">{JSON.stringify(r, null, 2)}</pre>;
  }
}

// -- capability ---------------------------------------------------------------

const VERDICTS = ["present", "missing", "contradicted"] as const;
const VERDICT_TONE: Record<string, string> = {
  present: "ok", contradicted: "bad", missing: "warn",
};

function ItemStep({ r, adjudication, onOverrideFact }: any) {
  // What the operator has already said about this item, so a fact they
  // overruled reads as overruled instead of reverting to the judge's answer
  // the next time the step is opened.
  const corrected: Record<number, string> = {};
  for (const a of adjudication?.applied ?? [])
    if (a.spec_id === r.spec_id) corrected[a.index] = a.now;

  return (
    <>
      {r.gate === "no_answer" ? (
        <Section
          title="The call did not come back"
          info="An HTTP error, a timeout, or a model that spent its whole budget before emitting a token. None of it is evidence about the language: a failure of the instrument must never be published as a failure of the thing being measured, so this item is void — it neither passes nor counts against the model."
          right={<Badge tone="warn">void — not the model's answer</Badge>}
        >
          <div className="rounded-md border border-bad/30 bg-bad-soft p-3">
            <p className="text-[12.5px] font-medium text-bad">
              {r.error ? "The endpoint answered with an error."
                       : "The endpoint returned no text."}
            </p>
            <pre className="prose-text mt-2 whitespace-pre-wrap text-[12px] text-bad/90">
              {r.error ?? r.gate_detail ?? "—"}
            </pre>
          </div>
        </Section>
      ) : (
      <Section
        title={`What the model wrote — ${r.designator ?? ""}`}
        info="The model's own words, untouched. Reading this is how nearly every real defect in this method was found; a verdict you can only check by opening a log file is a verdict most people take on trust."
        right={<Badge tone={r.gate === "pass" ? "ok" : "bad"}>gate: {r.gate}</Badge>}
      >
        <Text>{r.generation ?? r.error ?? "—"}</Text>
        {(r.adjusted ?? []).length > 0 && (
          <ul className="mt-2 space-y-1">
            {r.adjusted.map((a: string, i: number) => (
              <li key={i} className="rounded-md border border-warn/30 bg-warn-soft
                                     px-2.5 py-1.5 text-[11.5px] leading-relaxed text-warn">
                <b>The provider refused a parameter.</b> {a}
              </li>
            ))}
          </ul>
        )}
        {(r.gate_detail || r.lid) && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-faint">
            {r.gate_detail && <span>{r.gate_detail}</span>}
            {r.lid?.label ? (
              <span className="chip" title={`Identified by ${r.lid.backend}`}>
                reads as {r.lid.label} · {(r.lid.confidence * 100).toFixed(0)}%
              </span>
            ) : (
              <span className="chip" title="GlotLID is not installed, so the gate checked the writing system and not the language.">
                language not checked
              </span>
            )}
          </div>
        )}
      </Section>
      )}

      {r.back_translation && (
        <Section
          title="Read back into the pivot"
          info="A qualified reader translates the text back. The judge grades this and never sees the target language, which is what removes the need for expert graders in six hundred languages."
        >
          <Text className="text-ink-soft">{r.back_translation}</Text>
        </Section>
      )}

      {r.facts?.length > 0 && (
        <Section
          title="The fact checklist"
          info="Fact recall is the measurement. The judge is a model and models are wrong — one study found 90% of a rival metric's disagreements with fact recall were adequate translations — so you can overrule a verdict here and see what the run would have scored."
          right={r.recall !== null && r.recall !== undefined
            ? <Badge tone={r.recall >= 0.95 ? "ok" : r.recall ? "warn" : "bad"}>
                recall {(r.recall as number).toFixed(2)}
              </Badge>
            : undefined}
        >
          <ul className="divide-y divide-line rounded-md border border-line">
            {r.facts.map((f: string, i: number) => {
              const judged = r.verdicts?.[i];
              const mine = corrected[i];
              const v = mine ?? judged;
              return (
                <li key={i} className="flex items-start gap-2.5 px-2.5 py-2">
                  <span className="min-w-0 flex-1 text-[13px] text-ink">
                    {f}
                    {mine && (
                      <span className="ml-2 text-[11px] text-warn">
                        you overruled this — the judge said {judged}
                      </span>
                    )}
                  </span>
                  {judged && onOverrideFact ? (
                    <span className="flex shrink-0 gap-1" role="group"
                          aria-label="What this fact really is">
                      {VERDICTS.map((x) => (
                        <button
                          key={x}
                          className={`btn btn-sm ${x === v
                            ? `${toneSoft[VERDICT_TONE[x]]} font-semibold`
                            : "text-ink-faint"}`}
                          aria-pressed={x === v}
                          title={x === judged
                            ? `What the judge answered. Click to put it back, if you had overruled it.`
                            : `Say this fact is ${x}, and see what the run would have scored. The measurement itself is never changed.`}
                          onClick={() => onOverrideFact(r.spec_id, i, x)}
                        >
                          {x}
                        </button>
                      ))}
                    </span>
                  ) : (
                    <span className={`mt-0.5 shrink-0 text-[11px] font-semibold
                                      ${toneText[judged ? VERDICT_TONE[judged] : ""]}`}>
                      {judged ?? "—"}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
          {Object.keys(corrected).length > 0 && (
            <p className="mt-2 text-[11.5px] leading-relaxed text-warn">
              Your corrections sit beside the measurement and never replace it:
              the run still scores what the judge said, and the verdict panel
              shows what it would have scored had the judge answered your way.
            </p>
          )}
        </Section>
      )}
    </>
  );
}

function TrialStep({ r }: any) {
  const pct = r.attempts ? r.gate_pass / r.attempts : 0;
  return (
    <Section title="How this designator did"
             info="An LLM has no language-code interface — the name we ask by is a prompt parameter to optimise, not a fact to look up.">
      <div className="card p-4">
        <div className="font-mono text-[15px] text-ink">{r.designator}</div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-line-soft">
          <div className={`h-full rounded-full ${pct === 1 ? "bg-ok" : pct ? "bg-warn" : "bg-bad"}`}
               style={{ width: `${pct * 100}%` }} />
        </div>
        <div className="mt-2 text-[12px] text-ink-soft">
          cleared the gate on {r.gate_pass} of {r.attempts}
          {r.kinds?.length > 1 && (
            <span className="ml-2 text-ink-faint">
              (proposed by candidates {r.kinds.join(", ")})
            </span>
          )}
        </div>
      </div>
    </Section>
  );
}

function ChosenStep({ r }: any) {
  return (
    <Section title="Designator sweep"
             info="Everything after this runs under one name, so a negative verdict means “none under our best designator” rather than “none under one arbitrary string”.">
      <table className="w-full text-[12.5px]">
        <thead className="text-left text-[11px] uppercase tracking-wider text-ink-faint">
          <tr><th className="pb-1">designator</th><th className="pb-1">cleared</th><th className="pb-1 text-right">rate</th></tr>
        </thead>
        <tbody className="divide-y divide-line">
          {(r.trials ?? []).map((t: any) => (
            <tr key={t.designator}
                className={t.designator === r.designator ? "font-semibold text-ink" : "text-ink-soft"}>
              <td className="py-1.5 font-mono">{t.designator}</td>
              <td className="py-1.5">{t.gate_pass} / {t.attempts}</td>
              <td className="py-1.5 text-right tabular-nums">{t.rate.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.deterministic && (
        <p className="mt-3 rounded-md border border-bad/30 bg-bad-soft p-2.5 text-[12.5px] text-bad">
          Every candidate failed every item, and every failure was the gate
          convicting rather than abstaining. The negative holds without any
          reader, so nothing further is paid for.
        </p>
      )}
    </Section>
  );
}

function DeterministicStep({ r }: any) {
  return (
    <Section title="Settled locally"
             info="This verdict does not depend on reading the language back, so it needs no back-translator and holds against every one of them.">
      <KeyVal rows={[
        ["refusals", r.refusals],
        ["gate verdicts", (r.verdicts ?? []).join(", ")],
      ]} />
    </Section>
  );
}

function QualificationStep({ r, onRule }: any) {
  const evidence: any[] = r.evidence ?? [];
  return (
    <>
      <Section title="Which reader, and why"
               info="A model that cannot read a language does not refuse — it invents. One handed known Chuvash returned “A man is walking. He is wearing a white shirt.” So a reader is scored against text whose meaning is already known before any verdict depends on it.">
        <div className="card p-4">
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-[14px] text-ink">{r.backtranslator}</span>
            <Badge tone={r.trustworthy ? "ok" : "bad"}>{r.status}</Badge>
            {r.kind === "reference" && (
              <span className="text-[12px] text-ink-soft tabular-nums">
                chrF++ {r.score?.toFixed(1)}{r.threshold != null && ` · threshold ${r.threshold}`}
              </span>
            )}
            {r.kind === "facts" && r.score != null && (
              <span className="text-[12px] text-ink-soft tabular-nums">
                fact recall {(r.score * 100).toFixed(0)}%
                {r.threshold != null && ` · threshold ${(r.threshold * 100).toFixed(0)}%`}
              </span>
            )}
          </div>
          {r.detail && <p className="mt-2 text-[12.5px] text-ink-soft">{r.detail}</p>}
          {r.rejected?.length > 0 && (
            <p className="mt-2 text-[12px] text-warn">
              Not tried: {r.rejected.join(", ")} — you threw {r.rejected.length > 1
                ? "them" : "it"} out earlier in this run.
            </p>
          )}
        </div>
      </Section>

      <ReaderTranscript evidence={evidence} fromCache={!!r.from_cache} />

      {onRule && (
        <Section title="Do you agree?"
                 info="The thresholds are calibrated against two real cases — a fabricating reader at chrF++ 10.5, a faithful one at 81.9 — and are simply wrong in the middle, where 90% of one metric's disagreements with fact recall turned out to be adequate translations. A person reading the transcript above can tell which of the two they are looking at; the number cannot. A run you rule on is kept in full and never becomes a stored verdict.">
          <div className="flex flex-wrap items-center gap-2">
            {!r.trustworthy && (
              <Confirm
                label="Keep this reader anyway"
                question="Keep a reader the method did not qualify? The run continues and is never promoted to a stored verdict."
                confirmLabel="Keep it"
                onConfirm={() => onRule("accept",
                  "the transcript shows the language was read, not invented")}
              />
            )}
            <Confirm
              label="Throw this reader out"
              question="Throw this reader out? The next reader on the panel is tried instead, and this one is not used again on this run."
              confirmLabel="Throw it out"
              onConfirm={() => onRule("reject",
                "the transcript shows an invented reading")}
            />
            {r.from_cache && (
              <button className="btn" onClick={() => onRule("recheck")}
                      title="Pay for the check again instead of taking it from the cache — the only way to see what this reader actually answers for this language.">
                Check it again, for real
              </button>
            )}
          </div>
        </Section>
      )}

      {r.attempts?.length > 1 && (
        <Collapse label="Every reader that was tried" count={r.attempts.length}>
          <table className="w-full text-[12.5px]">
            <tbody className="divide-y divide-line">
              {r.attempts.map((a: any, i: number) => (
                <tr key={i}>
                  <td className="py-1.5 font-mono text-ink-soft">{a.backtranslator}</td>
                  <td className="py-1.5">{a.status}</td>
                  <td className="py-1.5 text-right tabular-nums">{a.score?.toFixed(1)}</td>
                  <td className="py-1.5 pl-3 text-ink-faint">{a.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Collapse>
      )}
    </>
  );
}

/** What the reader was given, and what it gave back.
 *
 * The one thing this step was missing. It said "qualified at 73.1" and showed
 * a placeholder where the control text belongs, which asks the operator to
 * take the instrument's word for the instrument — on the exact step whose
 * whole purpose is that a model which cannot read a language does not refuse,
 * it invents. Fabrication is obvious to a person reading two paragraphs side
 * by side and invisible in a single number. */
function ReaderTranscript({ evidence, fromCache }: {
  evidence: any[]; fromCache: boolean;
}) {
  if (!evidence.length) {
    return (
      <Section title="What it was given, and what it answered">
        <p className="rounded-md border border-line bg-surface p-3 text-[12.5px]
                      leading-relaxed text-ink-soft">
          {fromCache
            ? "Nothing was sent: this reader was qualified for this language on "
              + "an earlier run and the answer was taken from the cache, which "
              + "keeps the verdict and not the transcript. Check it again below "
              + "to see what it actually answers."
            : "No control text was put to a reader on this step."}
        </p>
      </Section>
    );
  }
  return (
    <Section
      title="What it was given, and what it answered"
      info="Control text whose meaning is already known, the reader's rendering of it, and the known reference the rendering was scored against. Read the two columns against each other: a reader that cannot read the language produces fluent, confident text with nothing of the original in it."
      right={<span className="text-[11px] text-ink-faint">
        {evidence.length} control item{evidence.length > 1 ? "s" : ""}
      </span>}
    >
      <div className="space-y-2">
        {evidence.map((e, i) => (
          <div key={i} className="rounded-md border border-line">
            <div className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
              <span className="chip">control {i + 1}</span>
              {e.score != null && (
                <span className={`text-[12px] tabular-nums ${
                  e.threshold != null && e.score >= e.threshold ? "text-ok" : "text-bad"}`}>
                  {e.metric ?? "score"} {e.metric === "fact recall"
                    ? `${(e.score * 100).toFixed(0)}%` : e.score.toFixed(1)}
                </span>
              )}
              {e.error && <span className="text-[12px] text-bad">{e.error}</span>}
            </div>
            <div className="grid gap-2 p-2.5 md:grid-cols-2">
              <div>
                <div className="label">known text, in the language</div>
                <Text className="text-ink-soft">{e.control}</Text>
              </div>
              <div>
                <div className="label">what the reader made of it</div>
                <Text>{e.got ?? "—"}</Text>
              </div>
              {e.expected && (
                <div className="md:col-span-2">
                  <div className="label">what it should have said</div>
                  <Text className="text-ink-soft">{e.expected}</Text>
                </div>
              )}
              {e.facts?.length > 0 && (
                <div className="md:col-span-2">
                  <div className="label">facts the reading had to recover</div>
                  <ul className="divide-y divide-line rounded-md border border-line">
                    {e.facts.map((f: string, j: number) => {
                      const v = e.verdicts?.[j];
                      return (
                        <li key={j} className="flex items-start gap-2.5 px-2.5 py-1.5">
                          <span className={`shrink-0 text-[11px] font-semibold ${
                            toneText[v === "present" ? "ok"
                                     : v === "contradicted" ? "bad" : "warn"]}`}>
                            {v ?? "—"}
                          </span>
                          <span className="text-[12.5px] text-ink">{f}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function ReaderOverrideStep({ r }: any) {
  const said: Record<string, string> = {
    accept: "kept over the method's threshold",
    reject: "thrown out and not used again on this run",
    recheck: "to be checked again, ignoring the cache",
  };
  return (
    <Section title="Your ruling on the reader"
             info="Qualification is a measurement, and a measurement nobody can overrule is an authority. This run is kept in full and readable, and it never becomes a stored verdict: it measured an instrument the method did not certify.">
      <div className="card p-4">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[14px] text-ink">{r.backtranslator}</span>
          <Badge tone="warn">{said[r.decision] ?? r.decision}</Badge>
        </div>
        {r.was && (
          <p className="mt-2 text-[12.5px] text-ink-soft">
            The method had it at <b>{r.was.status}</b>
            {r.was.score != null && <> · {r.was.kind || "score"} {r.was.score}</>}
            {r.was.detail ? ` — ${r.was.detail}` : "."}
          </p>
        )}
        {r.why && <p className="mt-1.5 text-[12.5px] text-ink-soft">Your reason: {r.why}</p>}
      </div>
    </Section>
  );
}

function NoAnswerStep({ r }: any) {
  return (
    <Section title="Nothing was measured">
      <div className="rounded-md border border-bad/30 bg-bad-soft p-3 text-[13px] text-bad">
        <p className="font-medium">
          {r.engine} returned nothing on {r.attempts} attempt
          {r.attempts === 1 ? "" : "s"}.
        </p>
        <p className="mt-1.5 opacity-90">
          This is a statement about the endpoint, not about the model: silence
          is not a refusal, and a run that scored it would publish a failure of
          the instrument as a failure of the language. No verdict is stored.
          Fix the endpoint and start the test again.
        </p>
        {(r.errors ?? []).map((e: string, i: number) => (
          <pre key={i} className="prose-text mt-2 whitespace-pre-wrap
                                  text-[12px] opacity-90">{e}</pre>
        ))}
      </div>
    </Section>
  );
}

function UnverifiedStep({ r }: any) {
  return (
    <Section title="No qualified reader">
      <div className="rounded-md border border-warn/30 bg-warn-soft p-3 text-[13px] text-warn">
        <p className="font-medium">This is a statement about the instrument, not a verdict about the model.</p>
        <p className="mt-1.5 opacity-90">
          Quality cannot be assessed without a reader whose competence in this
          language has been demonstrated, and `no-control` is never a pass.
          The deterministic gate still holds: inability can be proven without a
          reader, ability cannot.
        </p>
        <p className="mt-2 font-mono text-[12px] opacity-80">{r.detail || r.status}</p>
      </div>
    </Section>
  );
}

function ScoreStep({ r }: any) {
  return (
    <>
      <Section title="The number, and the tier derived from it"
               info="The content score is the measurement; the tier is a lossy view of it that exists because a workflow has to route on something.">
        <div className="card grid grid-cols-3 divide-x divide-line">
          <Stat label="eligibility" value={r.s_lang?.toFixed(2)}
                hint="Did it write the language at all — a filter, not the first term of a grade." />
          <Stat label="adequacy" value={r.s_content?.toFixed(2)}
                hint={r.ci ? `90% interval ${r.ci[0]?.toFixed(2)}–${r.ci[1]?.toFixed(2)}` : undefined} />
          <Stat label="tier" value={r.tier} tone={
            r.tier === "Proficient" ? "ok" : r.tier === "Assisted" ? "warn" : "bad"} />
        </div>
        {r.borderline && (
          <p className="mt-2 rounded-md border border-warn/30 bg-warn-soft p-2.5 text-[12.5px] text-warn">
            Borderline: the interval straddles the tier cut, so more items are
            run rather than rounding a number that cannot be stated flatly.
          </p>
        )}
      </Section>
      {r.workflow && (
        <Section title="What this means for a workflow">
          <p className="text-[13px] leading-relaxed text-ink-soft">{r.workflow}</p>
        </Section>
      )}
      {r.notes?.length > 0 && (
        <Section title="Notes the score carries">
          <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-ink-soft">
            {r.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}
          </ul>
        </Section>
      )}
    </>
  );
}

function Stat({ label, value, hint, tone }: any) {
  return (
    <div className="p-4">
      <div className="label">{label}</div>
      <div className={`text-[22px] font-semibold tabular-nums ${tone ? toneText[tone] : "text-ink"}`}>
        {value ?? "—"}
      </div>
      {hint && <div className="mt-1 text-[11px] leading-snug text-ink-faint">{hint}</div>}
    </div>
  );
}

// -- dialect ------------------------------------------------------------------

function PlanStep({ r }: any) {
  return (
    <>
      <Section title="The varieties under test">
        <div className="space-y-1.5">
          {(r.varieties ?? []).map((v: any) => (
            <div key={v.tag}
                 className={`flex items-center gap-3 rounded-md border px-2.5 py-2 ${
                   v.is_subject ? "border-accent bg-accent-soft" : "border-line"}`}>
              <span className="font-mono text-[12px] text-ink-soft">{v.tag}</span>
              <span className="text-[13px] text-ink">{v.designator}</span>
              {v.is_subject && <span className="ml-auto chip">under test</span>}
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
          Two arms and nothing else. The conversion probe rewrites each one into
          both varieties, so there is no third text and no privileged baseline.
        </p>
      </Section>

      {r.neutral?.used === false && (
        <Section title="The umbrella tag"
                 info="Kept in the plan so a caller that sent one is told it was ignored, rather than believing it was used.">
          <div className="flex items-center gap-3 rounded-md border border-dashed
                          border-line px-2.5 py-2 opacity-60">
            <span className="font-mono text-[12px] text-ink-soft">{r.neutral.tag}</span>
            <span className="text-[13px] text-ink-soft">{r.neutral.designator}</span>
            <span className="ml-auto chip">not used</span>
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-ink-faint">
            It used to be the conversion source — neutral text to be rewritten
            into each variety — and it was never neutral: the model under test
            wrote it, so it was that model's own default variety. Whichever
            variety a model defaults to could therefore never show a conversion,
            and the baseline was resampled on every run. The square converts
            between the two arms instead.
          </p>
        </Section>
      )}

      <Section title="Control models eligible to judge"
               info="The producer's whole family is dropped, not just its id: a model grading its own output measures self-consistency and reports it as agreement. Who is asked first may be ordered by past records; whether an answer is believed is decided by the controls, every single run.">
        <div className="flex flex-wrap gap-1.5">
          {(r.attestors ?? []).map((a: string) => (
            <span key={a} className="chip font-mono">{a}</span>
          ))}
          {(r.attestors ?? []).length === 0 && (
            <p className="text-[13px] text-bad">
              No eligible control model. Every candidate is from the producer's
              own family, so nothing can attest this run.
            </p>
          )}
        </div>
        <p className="mt-2 text-[11.5px] text-ink-faint">routing: {r.routing}</p>
        {r.dropped_by_family_guard?.length > 0 && (
          <p className="mt-1 text-[11.5px] text-ink-faint">
            dropped by the family guard: {r.dropped_by_family_guard.join(", ")}
          </p>
        )}
      </Section>
    </>
  );
}

function ProduceStep({ r }: any) {
  return (
    <Section title={`What the model wrote for ${r.tag}`}
             right={
               <Badge tone={!r.text ? "bad" : r.abstained ? "warn" : "ok"}>
                 gate: {r.gate ?? "—"}
               </Badge>
             }>
      <Text>{r.text ?? r.error ?? "—"}</Text>
      {r.abstained && (
        <p className="mt-2 rounded-md border border-warn/30 bg-warn-soft p-2.5
                      text-[11.5px] leading-relaxed text-warn">
          <b>The language check abstained, and the text is kept.</b> This
          family's siblings are exactly the codes language identification cannot
          separate — Serbian abstains on Bosnian, Croatian and Montenegrin —
          which is the reason this battery asks control models rather than a
          classifier. The gate still catches refusals, wrong script, copying and
          degeneration; deciding <i>which</i> sibling a text is was never its
          job.
        </p>
      )}
    </Section>
  );
}

function ConvertStep({ r }: any) {
  // One cell of the square, and which cell it is decides what a good answer
  // looks like. On the diagonal the text was just written as this variety, so
  // changing nothing is the *right* answer; off it, changing nothing is the
  // negative. A badge that read "changed nothing → warn" in both places would
  // call the expected answer a problem half the time.
  const diagonal = !!r.diagonal;
  const changed = (r.rate ?? 0) > 0;
  const met = diagonal
    ? !r.paraphrased && (r.rate ?? 0) <= 0.1
    : changed && !r.paraphrased;
  return (
    <>
      <Section
        title={diagonal ? "What survived the round trip" : "What it changed"}
        info={diagonal
          ? "A diagonal cell of the square: this text was generated as this very variety, so rewriting it into the same variety should change nothing. Drift here means the model does not agree with itself about what the variety is — which is what the old self-consistency probe asked, arriving for free."
          : "An off-diagonal cell: the other arm's text, rewritten into this variety. It should move, and what moved is the model saying in its own output what it thinks the difference is. No judge is involved, and a reviewer who distrusts the number can disagree with the substitutions concretely."}
        right={
          <Badge tone={met ? "ok" : r.paraphrased ? "bad" : diagonal ? "warn" : "bad"}>
            {r.paraphrased ? "paraphrased, not converted"
              : diagonal
                ? (met ? "unchanged, as expected"
                       : `drifted — ${r.edits?.length} edit${r.edits?.length === 1 ? "" : "s"}`)
                : (changed ? `${r.edits?.length} edit${r.edits?.length === 1 ? "" : "s"}`
                           : "changed nothing")}
          </Badge>
        }
      >
        {r.edits?.length > 0 ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {r.edits.map((e: [string, string], i: number) => (
              <li key={i} className="flex items-center gap-2 px-2.5 py-1.5 font-mono text-[12.5px]">
                <span className="text-bad line-through">{e[0] || "∅"}</span>
                <span className="text-ink-faint">→</span>
                <span className="text-ok">{e[1] || "∅"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={`rounded-md border p-2.5 text-[12.5px] ${
            r.error ? "border-bad/30 bg-bad-soft text-bad"
              : diagonal ? "border-ok/30 bg-ok-soft text-ok"
              : "border-warn/30 bg-warn-soft text-warn"}`}>
            {r.error
              ? r.error
              : diagonal
                ? "Returned unchanged, which is what this cell is for: the text was written as this variety, so there was nothing to change. The model agrees with itself."
                : "The model returned the other arm's text unchanged. That is a real answer — “nothing in this text needs to change” — and it is a negative settled locally with no judge call."}
          </p>
        )}
        <p className="mt-2 text-[11.5px] text-ink-faint">
          <span className="font-mono">{r.source} → {r.target}</span>
          {" · "}touched {((r.rate ?? 0) * 100).toFixed(0)}% of the tokens
          {" · expected to "}{diagonal ? "stay the same" : "change"}
          {" · "}above 50% a rewrite stops being a conversion and is scored as a paraphrase
        </p>
      </Section>

      {r.before && (
        <div className="grid gap-3 md:grid-cols-2">
          <Section title="before"><Text className="text-ink-soft">{r.before}</Text></Section>
          <Section title="after"><Text>{r.after ?? "—"}</Text></Section>
        </div>
      )}

    </>
  );
}

/** What the model actually replied, verbatim.
 *
 * The counterpart to "Exactly what was sent", and it exists for the same
 * reason. Everything else on an attestor step is this application's *reading*
 * of a reply — a parsed label, a confidence, a marker list — and a reader who
 * wants to overrule that judgement cannot do it from the judgement. A model
 * that hedged in prose, that named a region which was not on the menu, and one
 * that was simply mis-parsed all look identical once reduced to "generic".
 *
 * It was in the corpus on disk the whole time, which is no help at all while
 * looking at the step that produced it. */
function Replied({ raw, label = "What it actually replied" }: {
  raw?: string | null; label?: string;
}) {
  if (!raw) return null;
  return (
    <Collapse label={label}>
      <Text className="text-ink-soft">{raw}</Text>
    </Collapse>
  );
}

function ControlsStep({ r }: any) {
  return (
    <Section
      title={`Controls for ${r.attestor}`}
      info="Two controls whose answers are certain by construction rather than by authority — which is how this escapes the problem that killed the marker corpus. What they cannot catch: a model answering “indistinguishable” to everything passes both, so a null here means “no evidence of a difference” and never “no difference”."
      right={<Badge tone={r.qualified ? "ok" : "bad"}>
        {r.qualified ? "qualified" : "discarded"}
      </Badge>}
    >
      <ul className="divide-y divide-line rounded-md border border-line">
        {(r.controls ?? []).map((c: any, i: number) => (
          <li key={i} className="px-2.5 py-2">
            <div className="flex items-center gap-2">
              <span className={`text-[12px] font-semibold ${c.passed ? "text-ok" : "text-bad"}`}>
                {c.passed ? "pass" : "fail"}
              </span>
              <span className="text-[13px] font-medium text-ink">{c.kind}</span>
            </div>
            <p className="mt-0.5 text-[12.5px] text-ink-soft">{c.detail}</p>
            {/* On a failed control especially: this is a model being discarded
                for the whole family, and "it answered by position" is a
                conclusion the operator should be able to check against the
                answer itself. */}
            <div className="mt-1.5">
              <Replied raw={c.raw}
                       label={`what ${r.attestor} answered — ${c.kind} control`} />
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Markers({ markers, fabricated, truncated }: any) {
  if (!markers?.length) return null;
  return (
    <Section title={truncated ? "Markers it cited (partial)" : "Markers it cited"}
             info="Every quotation is checked against the text it was cited for. A single invented marker means the verdict was not read off the text, which is the whole claim it was making.">
      <ul className="divide-y divide-line rounded-md border border-line">
        {markers.map((m: any, i: number) => {
          const fake = (fabricated ?? []).includes(m.text);
          return (
            <li key={i} className="flex items-start gap-2.5 px-2.5 py-1.5">
              <span className={`font-mono text-[12.5px] ${fake ? "text-bad line-through" : "text-ink"}`}>
                {m.text}
              </span>
              <span className="min-w-0 flex-1 text-[12px] text-ink-soft">{m.why}</span>
              {fake && <Badge tone="bad">not in the text</Badge>}
            </li>
          );
        })}
      </ul>
      {truncated && (
        <p className="mt-2 text-[11.5px] leading-relaxed text-warn">
          The reply ran out of tokens and was repaired back to its last
          complete value. The answer itself survived — it is the first thing
          the model writes — but this list is shorter than what it cited, so
          read it as some of the evidence rather than all of it.
        </p>
      )}
    </Section>
  );
}

function IdentifyStep({ r, onOverride }: any) {
  const tone = r.correct ? "ok" : r.answer === "generic" || r.answer === "unsupported" ? "warn" : "bad";
  return (
    <>
      <Section title="What it named, unlabelled"
               info="The model was shown the text with no label and no expected answer. “generic” is frequently the true answer — three sentences of neutral prose often carry nothing to go on — and offering it is what stops a model guessing to be helpful.">
        <div className="card p-4">
          <div className="flex items-baseline gap-3">
            <span className={`text-[20px] font-semibold ${toneText[tone]}`}>
              {r.answer ?? "no answer"}
            </span>
            <span className="text-[13px] text-ink-faint">expected {r.expected}</span>
            {r.confidence && <span className="chip">its confidence: {r.confidence}</span>}
          </div>
          {r.error && <p className="mt-2 text-[12.5px] text-bad">{r.error}</p>}
          {onOverride && (
            <div className="mt-3 flex gap-1.5">
              <button className="btn btn-sm"
                      title="Say this control model got it right, and see what the run would have concluded. The measurement is not changed."
                      onClick={() => onOverride("identify", r.attestor, true)}>
                it was right
              </button>
              <button className="btn btn-sm"
                      onClick={() => onOverride("identify", r.attestor, false)}>
                it was wrong
              </button>
            </div>
          )}
        </div>
      </Section>
      <Markers markers={r.markers} fabricated={r.fabricated}
               truncated={r.truncated} />
      <Replied raw={r.raw} />
    </>
  );
}

function SeparateStep({ r, onOverride }: any) {
  const tone = r.correct ? "ok" : r.a === "indistinguishable" ? "warn" : "bad";
  return (
    <>
      <Section title="How it split the pair"
               info="The two texts are shown in an order salted by the texts themselves, so a model answering the options in the order they were listed cannot score 100% by habit. “indistinguishable” is a real answer and is the one a null result needs.">
        <div className="card p-4">
          <div className="flex items-baseline gap-2 text-[16px] font-semibold">
            <span className={toneText[tone]}>{r.a ?? "—"}</span>
            <span className="text-ink-faint">/</span>
            <span className={toneText[tone]}>{r.b ?? "—"}</span>
          </div>
          <p className="mt-1 text-[12.5px] text-ink-faint">
            expected {(r.expected ?? []).join(" / ")}
            {r.swapped && " · the pair was shown in reverse order"}
          </p>
          {r.error && <p className="mt-2 text-[12.5px] text-bad">{r.error}</p>}
          {onOverride && (
            <div className="mt-3 flex gap-1.5">
              <button className="btn btn-sm"
                      onClick={() => onOverride("separate", r.attestor, true)}>
                it was right
              </button>
              <button className="btn btn-sm"
                      onClick={() => onOverride("separate", r.attestor, false)}>
                it was wrong
              </button>
            </div>
          )}
        </div>
      </Section>
      <Markers markers={r.markers} fabricated={r.fabricated}
               truncated={r.truncated} />
      <Replied raw={r.raw} />
    </>
  );
}

function DialectVerdictStep({ r }: any) {
  return (
    <>
      <Section title="What the probes add up to"
               info="A transparent function of the outcomes — no weights, no tuned coefficients, nothing that cannot be traced back to an observation.">
        <div className="card p-4">
          <div className="text-[22px] font-semibold text-ink">{r.verdict}</div>
          <div className="mt-1 text-[12.5px] text-ink-soft">confidence {r.confidence}</div>
        </div>
      </Section>

      <Section title="Probe by probe"
               info="They are five probes and not one score because they come apart: a model can identify a variety on sight and still write generic text when asked for it.">
        <ul className="divide-y divide-line rounded-md border border-line text-[12.5px]">
          <Probe name="P1 blind identification"
                 state={r.identified?.agreed} detail={votes(r.identified)} />
          <Probe name="P2 contrast pair"
                 state={r.separated?.agreed} detail={votes(r.separated)} />
          <Probe name="P3 the square"
                 state={r.conversion?.distinguishes ?? null}
                 detail={{
                   distinguishes: "diagonals held, both cross-conversions moved",
                   "no-distinction": "nothing moved in any of the four cells",
                   improvisation: "every cell moved, including the diagonals — rewriting, not marking",
                   unstable: "the diagonals drifted: the model does not agree with itself about the variety",
                   incomplete: "fewer than four cells ran",
                 }[r.conversion?.reading as string] ?? "not run"} />
          <Probe name="P3b mirror"
                 state={r.conversion?.mirror === null || r.conversion?.mirror === undefined
                   ? null : r.conversion?.mirrored}
                 detail={r.conversion?.mirror == null
                   ? "nothing to mirror: at least one direction made no edits"
                   : `the two directions agree ${r.conversion.mirror.toFixed(2)} after reversing one`} />
          <Probe name="P5 marker grounding"
                 state={r.grounding?.cited ? !r.grounding.unreliable : null}
                 detail={r.grounding?.cited
                   ? `${r.grounding.fabricated} of ${r.grounding.cited} cited markers were not in the text`
                   : "nothing was cited"} />
        </ul>
      </Section>

      {r.panel && (
        <Section title="The panel">
          <KeyVal rows={[
            ["qualified", (r.panel.qualified ?? []).join(", ") || "none"],
            ["discarded", (r.panel.disqualified ?? []).join(", ") || "none"],
          ]} />
        </Section>
      )}

      {r.notes?.length > 0 && (
        <Section title="What this result does not say">
          <ul className="list-disc space-y-1.5 rounded-md border border-warn/30
                         bg-warn-soft p-3 pl-7 text-[12.5px] text-warn">
            {r.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}
          </ul>
        </Section>
      )}
    </>
  );
}

function votes(c: any) {
  if (!c?.votes?.length) return "nobody answered";
  const right = c.votes.filter((v: any) => v.correct).length;
  const abst = c.votes.filter((v: any) => v.abstained).length;
  return `${right} of ${c.votes.length} correct` + (abst ? `, ${abst} abstained` : "")
    + (c.split ? " — the panel split, which is the finding" : "");
}

function Probe({ name, state, detail }: any) {
  const tone = state === true ? "ok" : state === false ? "bad" : "warn";
  return (
    <li className="flex items-start gap-2.5 px-2.5 py-2">
      <span className={`mt-px w-10 shrink-0 text-[11px] font-semibold ${toneText[tone]}`}>
        {state === true ? "yes" : state === false ? "no" : "—"}
      </span>
      <span className="w-48 shrink-0 font-medium text-ink">{name}</span>
      <span className="min-w-0 flex-1 text-ink-soft">{detail}</span>
    </li>
  );
}
