import { downloadFile, request } from "./client";

/** Security testing (VAPT): assessments of the platform, its tenancy and its AI (backend orchestration/security_tests.py). */
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type ResultStatus = "passed" | "failed" | "error" | "skipped" | "not_applicable" | "pending";

export interface Refs {
  top10?: string;
  api?: string;
  llm?: string;
  asvs?: string;
  cwe?: string;
  wstg?: string;
  iso?: string;
  nist?: string;
}
export interface Evidence {
  probe: string;
  request?: string;
  status?: string | number | null;
  ok: boolean;
  detail?: string;
}
export interface Category {
  key: string;
  name: string;
  blurb: string;
  count: number;
}
export interface Catalog {
  categories: Category[];
  checks: { code: string; cat: string; category: string; title: string; objective: string; where: string; cvss: number; severity: Severity; refs: Refs }[];
  procedures: { code: string; title: string; tool: string; steps: string[]; refs: Refs }[];
  methodology: { name: string; about: string }[];
}
export interface Assessment {
  id: number;
  number: string;
  name: string;
  status: "queued" | "running" | "completed" | "cancelled" | "error";
  total: number;
  done: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  score: number | null;
  grade: string | null;
  summary: {
    open?: Record<Severity, number>;
    manual?: { total: number; recorded: number; failed: number };
    sandbox?: { requests: number; endpoints: number; setup_seconds: number; server_errors: number };
  };
  scope: { categories?: string[] };
  message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  seconds: number | null;
}
export interface Result {
  id: number;
  code: string;
  category: string;
  cat: string;
  title: string;
  where: "sandbox" | "live" | "edge" | "manual";
  objective: string;
  status: ResultStatus;
  severity: Severity;
  cvss: number;
  vector: string;
  message: string;
  duration_ms: number;
  evidence: Evidence[];
  refs: Refs;
  fix: string;
  steps?: string[] | null;
  tool?: string | null;
  tester_note: string;
  recorded_at: string | null;
}

const BASE = "/api/security-tests";
export const securityCatalog = () => request<Catalog>(`${BASE}/catalog`);
export const listAssessments = () => request<Assessment[]>(`${BASE}/runs`);
export const getAssessment = (id: number) => request<Assessment & { results: Result[] }>(`${BASE}/runs/${id}`);
export const startAssessment = (name: string, categories: string[]) =>
  request<Assessment>(`${BASE}/runs`, { method: "POST", body: JSON.stringify({ name, categories }) });
export const cancelAssessment = (id: number) => request<Assessment>(`${BASE}/runs/${id}/cancel`, { method: "POST" });
export const recordProcedure = (id: number, code: string, status: string, note: string, severity?: Severity) =>
  request<Assessment & { results: Result[] }>(`${BASE}/runs/${id}/procedures/${code}`, { method: "PUT", body: JSON.stringify({ status, note, severity }) });
export const downloadAssessment = (a: Assessment, kind: "pdf" | "xlsx") =>
  downloadFile(`${BASE}/runs/${a.id}/${kind === "pdf" ? "report.pdf" : "results.xlsx"}`, `security-assessment-${a.number}.${kind}`);
