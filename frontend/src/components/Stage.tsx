import type { Step } from "../types";
import { Badge, Collapse, Info, KeyVal, Section, Text, toneText } from "./ui";

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

export function Stage({ step, onOverrideFact, onOverrideAttestor }: {
  step: Step;
  onOverrideFact?: (specId: string, index: number, verdict: string) => void;
  onOverrideAttestor?: (probe: string, attestor: string, correct: boolean) => void;
}) {
  const r = step.result ?? {};
  return (
    <div className="scroll h-full p-4">
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
              onOverrideFact={onOverrideFact}
              onOverrideAttestor={onOverrideAttestor} />
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

function Body({ step, r, onOverrideFact, onOverrideAttestor }: any) {
  switch (step.kind) {
    case "generate":
    case "extra-item":
    case "judge":
      return <ItemStep r={r} onOverrideFact={onOverrideFact} />;
    case "designator-trial":
      return <TrialStep r={r} />;
    case "designator-chosen":
      return <ChosenStep r={r} />;
    case "deterministic-negative":
      return <DeterministicStep r={r} />;
    case "qualification":
      return <QualificationStep r={r} />;
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
    case "consistency":
      return <ConsistencyStep r={r} />;
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

function ItemStep({ r, onOverrideFact }: any) {
  return (
    <>
      <Section
        title={`What the model wrote — ${r.designator ?? ""}`}
        info="The model's own words, untouched. Reading this is how nearly every real defect in this method was found; a verdict you can only check by opening a log file is a verdict most people take on trust."
        right={<Badge tone={r.gate === "pass" ? "ok" : "bad"}>gate: {r.gate}</Badge>}
      >
        <Text>{r.generation ?? r.error ?? "—"}</Text>
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
              const v = r.verdicts?.[i];
              const tone = v === "present" ? "ok" : v === "contradicted" ? "bad" : "warn";
              return (
                <li key={i} className="flex items-start gap-2.5 px-2.5 py-2">
                  <span className={`mt-0.5 shrink-0 text-[11px] font-semibold ${toneText[v ? tone : ""]}`}>
                    {v ?? "—"}
                  </span>
                  <span className="min-w-0 flex-1 text-[13px] text-ink">{f}</span>
                  {v && onOverrideFact && (
                    <span className="flex shrink-0 gap-1">
                      {["present", "missing", "contradicted"]
                        .filter((x) => x !== v)
                        .map((x) => (
                          <button
                            key={x}
                            className="btn btn-sm"
                            title={`Say this fact is ${x}, and see what the run would have scored. The measurement is not changed.`}
                            onClick={() => onOverrideFact(r.spec_id, i, x)}
                          >
                            {x}
                          </button>
                        ))}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
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

function QualificationStep({ r }: any) {
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
          </div>
          {r.detail && <p className="mt-2 text-[12.5px] text-ink-soft">{r.detail}</p>}
        </div>
      </Section>

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
          {r.neutral && (
            <div className="flex items-center gap-3 rounded-md border border-dashed
                            border-line px-2.5 py-2">
              <span className="font-mono text-[12px] text-ink-soft">{r.neutral.tag}</span>
              <span className="text-[13px] text-ink-soft">{r.neutral.designator}</span>
              <span className="ml-auto chip" title="Neutral text to be rewritten into the variety. Never a contrast arm: pairing the umbrella against a variety leaves one answerable option, which is not a question.">
                conversion source
              </span>
            </div>
          )}
        </div>
      </Section>

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
             right={<Badge tone={r.text ? "ok" : "bad"}>gate: {r.gate ?? "—"}</Badge>}>
      <Text>{r.text ?? r.error ?? "—"}</Text>
    </Section>
  );
}

function ConvertStep({ r }: any) {
  return (
    <>
      <Section
        title="What it changed"
        info="The diff is the measurement, and no judge is involved. This is where the model says, in its own output, what it thinks the variety is — which is what makes this the most defensible probe in the battery: a reviewer who distrusts the number can disagree with the substitutions concretely."
        right={
          <Badge tone={r.paraphrased ? "bad" : r.converted ? "ok" : "warn"}>
            {r.paraphrased ? "paraphrased, not converted"
              : r.converted ? `${r.edits?.length} edit${r.edits?.length === 1 ? "" : "s"}`
              : "changed nothing"}
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
          <p className="rounded-md border border-warn/30 bg-warn-soft p-2.5 text-[12.5px] text-warn">
            {r.error
              ? r.error
              : "The model returned the text unchanged. That is a real answer — “nothing in this text needs to change” — and it is a negative settled locally with no judge call."}
          </p>
        )}
        <p className="mt-2 text-[11.5px] text-ink-faint">
          touched {((r.rate ?? 0) * 100).toFixed(0)}% of the tokens
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

function ConsistencyStep({ r }: any) {
  return (
    <Section title="Did it do the same thing twice?"
             info="A model cannot simulate consistency it does not have: genuine command repeats its substitutions, improvisation does not. Runs that changed nothing are excluded — two empty diffs agree perfectly and mean nothing.">
      <div className="card p-4">
        <div className="flex items-baseline gap-3">
          <span className={`text-[26px] font-semibold tabular-nums ${
            r.agreement === null ? "text-ink-faint"
              : r.systematic ? "text-ok" : "text-warn"}`}>
            {r.agreement === null ? "—" : r.agreement.toFixed(2)}
          </span>
          <span className="text-[13px] text-ink-soft">
            {r.agreement === null
              ? "not scoreable: fewer than two runs changed anything"
              : r.systematic
                ? "systematic — the same substitutions both times"
                : "improvised — the two runs disagreed about what the variety requires"}
          </span>
        </div>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {(r.runs ?? []).map((run: any, i: number) => (
          <div key={i} className="rounded-md border border-line p-2.5">
            <div className="label">run {i + 1}</div>
            <ul className="space-y-0.5 font-mono text-[12px]">
              {(run.edits ?? []).map((e: [string, string], j: number) => (
                <li key={j}>
                  <span className="text-bad line-through">{e[0] || "∅"}</span>
                  <span className="text-ink-faint"> → </span>
                  <span className="text-ok">{e[1] || "∅"}</span>
                </li>
              ))}
              {!(run.edits ?? []).length && (
                <li className="text-ink-faint">no edits</li>
              )}
            </ul>
          </div>
        ))}
      </div>
    </Section>
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
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Markers({ markers, fabricated }: any) {
  if (!markers?.length) return null;
  return (
    <Section title="Markers it cited"
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
      <Markers markers={r.markers} fabricated={r.fabricated} />
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
      <Markers markers={r.markers} fabricated={r.fabricated} />
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
          <Probe name="P3 conversion"
                 state={r.conversion?.runs?.some((x: any) => x.converted) ?? null}
                 detail={`${r.conversion?.runs?.filter((x: any) => x.converted).length ?? 0} of ${r.conversion?.runs?.length ?? 0} runs changed something`} />
          <Probe name="P4 self-consistency"
                 state={r.conversion?.agreement === null ? null : r.conversion?.systematic}
                 detail={r.conversion?.agreement === null
                   ? "not scoreable"
                   : `agreement ${r.conversion?.agreement?.toFixed(2)}`} />
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
