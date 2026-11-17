import type {
  Call, LanguageRow, ModelRow, Planned, RunSnapshot, Step, VerdictRow,
} from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    // The backend's 400s carry a sentence written for a person. Surfacing the
    // status code instead would throw that away, which is most of what makes
    // a tool that spends money usable.
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = typeof body.detail === "string"
        ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

const post = <T,>(p: string, body?: unknown) =>
  req<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface Config {
  method_version: string;
  settings: Record<string, any>;
  models_configured: boolean;
  models_message: string | null;
  assets: { ready: boolean; missing: string[]; assets: AssetRow[] };
  language_id: { available: boolean; why_it_matters: string };
  data_dir: string;
}

export interface AssetRow {
  key: string; title: string; present: boolean; size_hint: string;
  licence: string; degraded: string;
}

export interface Proposal {
  judge: string | null;
  backtranslator_panel: string[];
  attestors: string[];
  rule: string;
  excluded_by_family_guard: string[];
}

export interface ModelsResponse {
  models: ModelRow[];
  enumerable: boolean;
  /** Whether the list is the whole of what can be reached, or a gateway's
   *  opinion about it. A custom file is binding — a name absent from it has no
   *  endpoint — and a gateway's list is advisory, so the page keeps a free-text
   *  field in that case. */
  binding: boolean;
  source: string;
  propose: Proposal | null;
  pivot: string;
}

export const api = {
  config: () => req<Config>("/config"),

  models: (engine?: string) => req<ModelsResponse>(
    `/models${engine ? `?engine=${encodeURIComponent(engine)}` : ""}`),

  languages: (q: string, opts: { dialectsOnly?: boolean; limit?: number } = {}) => {
    const p = new URLSearchParams({ q, limit: String(opts.limit ?? 60) });
    if (opts.dialectsOnly) p.set("dialects_only", "true");
    return req<{ total: number; languages: LanguageRow[]; scheme: string }>(
      `/languages?${p}`);
  },

  language: (tag: string, engine?: string) => {
    const p = new URLSearchParams();
    if (engine) p.set("engine", engine);
    return req<any>(`/languages/${encodeURIComponent(tag)}?${p}`);
  },

  startRun: (body: Record<string, unknown>) => post<RunSnapshot>("/runs", body),
  run: (id: string) => req<RunSnapshot>(`/runs/${id}`),
  next: (id: string) => req<{ next: Planned | null; spent: Record<string, number>; status: string }>(
    `/runs/${id}/next`),
  step: (id: string, calls?: Call[]) =>
    post<{ step: Step | null; status: string; error: string | null;
           spent: Record<string, number>; next: Planned | null; outcome: any }>(
      `/runs/${id}/step`, calls ? { calls } : {}),
  auto: (id: string) => post<{ ok: boolean }>(`/runs/${id}/auto`),
  pause: (id: string) => post<{ ok: boolean }>(`/runs/${id}/pause`),
  rerun: (id: string, ordinal: number) =>
    post<{ dropped: number; next: Planned | null }>(`/runs/${id}/rerun/${ordinal}`),
  /** The operator's ruling on the back-translator: keep it over the
   *  threshold, throw it out, or pay for the check again rather than take it
   *  from the cache. */
  ruleOnReader: (id: string, decision: string, why = "") =>
    post<{ step: Step; next: Planned | null }>(
      `/runs/${id}/reader`, { decision, why }),
  setPanel: (id: string, attestors: string[]) =>
    post<{ plan: any; next: Planned | null }>(`/runs/${id}/panel`, { attestors }),
  adjudicate: (id: string, overrides: unknown[]) =>
    post<any>(`/runs/${id}/adjudicate`, { overrides }),
  closeRun: (id: string) => req(`/runs/${id}`, { method: "DELETE" }),
  runs: (params: Record<string, string> = {}) =>
    req<{ total: number; runs: any[] }>(`/runs?${new URLSearchParams(params)}`),

  verdicts: (params: Record<string, string> = {}) =>
    req<{ verdicts: VerdictRow[] }>(`/verdicts?${new URLSearchParams(params)}`),
  verdictHistory: (engine: string, tag: string, test_type: string) =>
    req<{ verdicts: VerdictRow[] }>(
      `/verdicts/history?${new URLSearchParams({ engine, tag, test_type })}`),
  approve: (id: number, approved = true) =>
    post<VerdictRow>(`/verdicts/${id}/approve?approved=${approved}`),
  promote: (id: number) => post<VerdictRow>(`/verdicts/${id}/promote`),
};
