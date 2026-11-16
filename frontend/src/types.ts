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
}
