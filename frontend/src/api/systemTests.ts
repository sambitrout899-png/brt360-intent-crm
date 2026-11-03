import { API_BASE, getAccessToken, request } from "./client";

/** The system test suite: cases generated from the tenant's own system, background runs, evidence. */
export interface SuiteSummary {
  id: number;
  name: string;
  case_count: number;
  active: number;
  created_at: string;
  scope: { intents?: number; record_types?: string[]; flows?: number; page_rules?: number; by_kind?: Record<string, number>; by_area?: Record<string, number> };
}
export interface TestCaseRow {
  id: number;
  code: string;
  area: string;
  kind: string;
  title: string;
  target: string;
  priority: string;
  steps: string[];
  expected: string;
  is_active: boolean;
}
export interface RunSummary {
  id: number;
  number: string;
  suite_id: number;
  status: "queued" | "running" | "passed" | "failed" | "cancelled";
  total: number;
  done: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  selection: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  seconds: number | null;
}
export interface EvidenceStep {
  step: number;
  say: string;
  do: string;
  status: string;
  ms?: number;
  request?: unknown;
  response?: unknown;
  writes?: Record<string, number>;
  checks?: { check: string; expected: unknown; actual: unknown; passed: boolean }[];
  note?: string;
  error?: string;
}
export interface CaseResult {
  case: TestCaseRow;
  status: "passed" | "failed" | "error" | "skipped";
  message: string;
  duration_ms: number;
  evidence: EvidenceStep[];
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const listSuites = () => request<SuiteSummary[]>("/api/system-tests/suites");
export const generateSuite = (name?: string) => post<SuiteSummary>("/api/system-tests/suites", { name });
export const getSuite = (id: number) => request<SuiteSummary & { cases: TestCaseRow[] }>(`/api/system-tests/suites/${id}`);
export const toggleCase = (id: number, is_active: boolean) =>
  request<TestCaseRow>(`/api/system-tests/cases/${id}`, { method: "PATCH", body: JSON.stringify({ is_active }) });
export const startRun = (suiteId: number, selection: { areas?: string[]; kinds?: string[]; case_ids?: number[]; use_ai?: boolean }) =>
  post<RunSummary>(`/api/system-tests/suites/${suiteId}/runs`, selection);
export const listRuns = () => request<RunSummary[]>("/api/system-tests/runs");
export const getRun = (id: number, status = "") => request<RunSummary & { results: CaseResult[] }>(`/api/system-tests/runs/${id}${status ? `?status=${status}` : ""}`);
export const cancelRun = (id: number) => post<RunSummary>(`/api/system-tests/runs/${id}/cancel`, {});

async function fetchText(path: string): Promise<string> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`Could not load it (${res.status})`);
  return res.text();
}

/** Opens the evidence report in a new tab (it needs the sign-in, so it is fetched first). */
export async function openReport(runId: number): Promise<void> {
  const html = await fetchText(`/api/system-tests/runs/${runId}/report.html`);
  window.open(URL.createObjectURL(new Blob([html], { type: "text/html" })), "_blank", "noopener");
}

export async function download(path: string, filename: string, type: string): Promise<void> {
  const text = await fetchText(path);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
