export type Tone = "ok" | "warn" | "bad" | "";
export type TestType = "capability" | "dialect";
export type Mode = "manual" | "auto";

export interface Call {
  role: string;
  model: string;
  system: string | null;
  user: string;
  max_tokens: number;
  label: string;
}

export interface Planned {
  kind: string;
  phase: string;
  title: string;
  explains: string;
  summary: string;
  calls: Call[];
  cost: number;
  editable: boolean;
  context: Record<string, unknown>;
}

export interface Step {
  ordinal: number;
  attempt: number;
  kind: string;
  phase: string;
  title: string;
  explains: string;
  summary: string;
  tone: Tone;
  status: string;
  request: { context?: Record<string, unknown>; calls?: Call[] };
  result: Record<string, any>;
  calls: Record<string, number>;
  edited: boolean;
}

export interface Outcome {
  test_type: TestType;
  tag: string;
  engine: string;
  level: string;
  confidence: string;
  evidence?: string;
  designator?: string;
  s_lang?: number;
  s_content?: number;
  ci_low?: number;
  ci_high?: number;
  borderline?: boolean;
  refusals?: number;
  backtranslator?: string;
  judge?: string;
  pivot?: string;
  workflow?: string;
  notes?: string[];
  items?: ItemDetail[];
  calls?: Record<string, number>;
  attestors?: string[];
  identified?: Consensus;
  separated?: Consensus;
  conversion?: any;
  grounding?: { cited: number; fabricated: number; rate: number | null; unreliable: boolean };
  panel?: { qualified: string[]; disqualified: string[]; empty: boolean; single: boolean };
  incomplete?: boolean;
}

export interface Consensus {
  agreed: boolean | null;
  split: boolean;
  votes: { attestor: string; correct: boolean; abstained: boolean }[];
}

export interface ItemDetail {
  spec_id: string;
  facts: string[];
  designator: string;
  prompt: string;
  generation: string | null;
  gate: string;
  gate_detail?: string;
  lid: { label: string; confidence: number; backend: string } | null;
  back_translation: string | null;
  verdicts: string[] | null;
  recall: number | null;
  error: string | null;
  /** Parameters the provider refused on this call, in words. One of them —
   *  an explicit temperature — changes what the measurement means. */
  adjusted?: string[];
}

export interface RunSnapshot {
  id: string;
  //: `interrupted` is server-derived, not stored: a row still marked `running`
  //: whose process is gone is not running, and a page that believed the row
  //: would show a "Run next step" button that can never work.
  status: "running" | "done" | "failed" | "cancelled" | "interrupted";
  mode: Mode;
  auto?: boolean;
  live?: boolean;
  error: string | null;
  test_type: TestType;
  tag: string;
  engine: string;
  prompt_edited: boolean;
  plan: Record<string, any>;
  steps: Step[];
  spent: Record<string, number>;
  next: Planned | null;
  outcome: Outcome | null;
  adjudication?: any;
  corpus: string;
}

export interface LanguageRow {
  tag: string;
  name: string | null;
  local_name: string | null;
  region: string | null;
  script: string | null;
  cls: string;
  inclusive: boolean;
  iso639_3: string | null;
  siblings: number;
}

export interface ModelRow {
  id: string;
  title: string;
  endpoint: string;
  family: string;
}

export interface VerdictRow {
  id: number;
  run_id: string | null;
  test_type: TestType;
  engine: string;
  tag: string;
  level: string;
  confidence: string;
  evidence: string | null;
  s_content: number | null;
  ci: [number | null, number | null];
  borderline: boolean;
  backtranslator: string;
  attestors: string[] | null;
  approved: boolean;
  current: boolean;
  measured_at: string | null;
  notes: string[] | null;
  detail: Record<string, any> | null;
  /** Present only on a re-scored row: the corrections a person made to the
   *  judge or the panel. The row is recorded beside the measured one and
   *  becomes the standing answer only when somebody promotes it. */
  adjudicated?: { index?: number; fact?: string; was?: string; now?: string;
                  probe?: string; attestor?: string; correct?: boolean }[] | null;
}

/** One tag of the loaded locale list, with whatever is stored about it.
 *
 *  The scheme is the spine rather than the verdict table: a coverage page built
 *  from what has been measured can only ever show what has been measured, and
 *  the first question anyone brings to one is which languages are *missing*. */
export interface CoverageRow {
  tag: string;
  name: string | null;
  local_name: string | null;
  script: string | null;
  region: string | null;
  region_name: string | null;
  macro: string | null;
  iso639_3: string | null;
  inclusive: boolean;
  cls: string;
  class_size: number;
  verdict: (VerdictRow & { inherited_from?: string | null; cls?: string | null }) | null;
  /** Only on a dialect coverage read: what a battery would hold this tag
   *  against, or why it would refuse. Sent with the row rather than worked out
   *  in the page, so the table and the scan plan cannot disagree about which
   *  tags are testable — they come from one function. */
  pairing?: Pairing;
}

/** What a dialect battery contrasts one tag against, and on what authority.
 *
 *  `basis` is the thing to read before the result: `recorded` and `rule` are
 *  decisions, `provisional` is the first member of the class standing in for a
 *  decision nobody has made. A page that rendered them alike would turn an open
 *  question into a silent claim. */
export interface Pairing {
  tag: string;
  reference: string | null;
  /** `recorded` a person stood behind it · `proposed` written down with a
   *  reason, unconfirmed · `rule` derived from the subtags · `provisional` the
   *  file does not cover this family and list order is standing in. */
  basis: "recorded" | "proposed" | "rule" | "provisional" | "none";
  why: string;
  blocked: string | null;
  blocked_why: string;
  testable: boolean;
  decided: boolean;
}

/** Which of the three states the skip rule distinguishes a scan may measure
 *  again. Not a boolean, because "never measured", "measured but nobody signed
 *  off" and "a person stood behind it" are three different questions. */
export type Rescan = "none" | "drafts" | "all";

export interface BatchPlanItem {
  cls: string;
  representative: string;
  members: string[];
  /** The other arm, on a dialect item. Null on a capability item, which walks
   *  classes and has no pair. */
  contrast?: string | null;
  reference_basis?: Pairing["basis"];
  reference_confirmed?: boolean;
  reference_why?: string;
  blocked_why?: string;
  language: string | null;
  script: string | null;
  region?: string | null;
  designator: string | null;
  divergent_siblings: string[];
  inherits: number;
  skip_reason: string | null;
  known_level: string | null;
  known_approved: boolean;
}

export interface BatchPlan {
  engine: string;
  test_type: TestType;
  rescan: Rescan;
  scheme: string;
  /** What one item of this plan *is*: a capability scan walks equivalence
   *  classes, a dialect battery walks varieties. Named rather than inferred
   *  from `test_type`, so the page says what the plan says. */
  unit: "class" | "variety";
  requested_tags: number;
  unknown_tags: string[];
  classes: number;
  to_scan: number;
  inheriting_tags: number;
  skipped: Record<string, number>;
  divergent_siblings: string[];
  /** Varieties this scan would measure against a baseline nobody has
   *  confirmed. Listed, not counted: a reader has to be able to see which. */
  unconfirmed: string[];
  /** The panel that survived the family guard, on a dialect plan. */
  attestors: string[];
  estimated_calls: { minimum: number; typical: number; maximum: number };
  max_calls: number | null;
  over_budget: boolean;
  items: BatchPlanItem[];
}

export interface BatchItem {
  cls: string;
  representative: string;
  members: string[];
  contrast?: string | null;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  skip_reason: string | null;
  run_id: string | null;
  error: string | null;
  finished_at: string | null;
}

export interface BatchSnapshot {
  id: string;
  status: "running" | "done" | "cancelled" | "halted" | "failed" | "interrupted";
  engine: string;
  test_type: TestType;
  rescan: Rescan;
  max_calls: number | null;
  calls_used: number;
  error: string | null;
  unknown_tags: string[];
  plan: BatchPlan | null;
  running_run_id: string | null;
  /** What this scan put on record, and how much of it nobody has stood behind
   *  yet. Counted from the runs the batch paid for. */
  verdicts: { written: number; drafts: number; inherited: number };
  created_at: string | null;
  finished_at: string | null;
  items: BatchItem[];
}
