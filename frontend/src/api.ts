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

export const api = {
  config: () => req<Config>("/config"),

  models: () => req<{ models: ModelRow[]; enumerable: boolean; defaults: any }>("/models"),

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
