/** Functional use cases on top of the Functional Map (backend: orchestration/use_cases.py,
 * /api/process-builder/use-cases): uploaded on the use case template, mapped, reviewed,
 * committed into a map version, and tested. */
import { API_BASE, getAccessToken, request } from "./client";

/** brt / in_use: already done (no change) · delta: an existing action added to the process ·
 * configure: built by the engines · new: a new requirement, built as new actions · gap: a process gap, not built */
export type UseCaseStatus = "brt" | "in_use" | "delta" | "configure" | "new" | "gap";

export interface UseCaseStep {
  text: string;
  intent: string | null;
  /** the reviewer chose the action (or "none") - kept when it is mapped again */
  pinned: boolean;
}

export interface UseCaseMapping {
  process: { mode: "append" | "new"; key?: string; label?: string; inferred?: boolean };
  steps: { status: UseCaseStatus; intent: string | null; label: string | null; how: string }[];
  links: { from: string; to: string; from_label: string; to_label: string }[];
  lines: { field: string; text: string; status: UseCaseStatus; how: string }[];
}

export const LIST_FIELDS = [
  ["preconditions", "Preconditions"],
  ["alternate_flows", "Alternate flows"],
  ["business_rules", "Business rules"],
  ["data", "Data and screens"],
  ["notifications", "Notifications"],
  ["assignment", "Assignment"],
  ["integrations", "Integrations"],
  ["postconditions", "Postconditions"],
  ["acceptance", "Acceptance criteria"],
] as const;
export type UseCaseListField = (typeof LIST_FIELDS)[number][0];

export type UseCase = {
  id: string;
  title: string;
  business_function: string;
  actor: string;
  trigger: string;
  main_flow: UseCaseStep[];
  mapping?: UseCaseMapping;
} & Record<UseCaseListField, string[]>;

export interface UseCaseSummary {
  use_cases: number;
  covered: number;
  delta: number;
  configure: number;
  new: number;
  gap: number;
  links: number;
}

export interface UseCaseTest {
  id: string;
  use_case: string;
  use_case_title: string;
  kind: string;
  title: string;
  priority: string;
  preconditions: string[];
  steps: { action: string; expected: string }[];
  expected: string;
  data: string;
  /** automated: runs as generated · scaffolded: generated, needs a record to act on (RECORDS) · manual */
  automation: "automated" | "scaffolded" | "manual";
  intents: string[];
}

export interface UseCaseSetBrief {
  id: number;
  name: string;
  filename: string;
  status: "draft" | "committed" | "live" | "covered";
  read_by: "template" | "ai" | "text";
  version_id: number | null;
  summary: UseCaseSummary;
  updated_at: string | null;
  committed_at: string | null;
  tests: { count: number; generated_at: string | null; stale: boolean };
}

export interface UseCaseSet extends UseCaseSetBrief {
  use_cases: UseCase[];
  gaps: { use_case: string; title: string; text: string; kind: string; how: string }[];
  test_cases: UseCaseTest[];
  examples_dropped?: number;
}

export interface UseCaseQuestion {
  id: string;
  prompt: string;
  options: string[];
  values?: string[];
  default?: string | null;
  group: string;
  process?: string;
}

export interface UseCaseDesign {
  group: string;
  process: string;
  mode: "append" | "new";
  changes: boolean;
  steps: { code: string; label: string }[];
  links: { from_label: string; to_label: string }[];
  components: { engine: string; kind: string; title: string; lines: string[]; warnings: string[] }[];
  engineering: { need: string; why: string; effort: string; workaround: string }[];
  assumptions: string[];
  questions: Omit<UseCaseQuestion, "group" | "process">[];
  notes: string[];
}

export interface UseCaseCommit {
  committed: boolean;
  status?: UseCaseSetBrief["status"];
  version?: { id: number; number: number; label?: string; name: string; status: string; workspace?: string | null } | null;
  questions: UseCaseQuestion[];
  activation_error?: string | null;
  assumptions?: string[];
  set: UseCaseSet;
}

const BASE = "/api/process-builder/use-cases";
export type Answers = Record<string, Record<string, string>>;

export async function uploadUseCases(file: File): Promise<UseCaseSet> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return request<UseCaseSet>(BASE, { method: "POST", body: JSON.stringify({ filename: file.name, content_base64: btoa(binary) }) });
}

export const listUseCaseSets = () => request<UseCaseSetBrief[]>(BASE);
export const getUseCaseSet = (id: number) => request<UseCaseSet>(`${BASE}/${id}`);
export const saveUseCaseSet = (id: number, name: string, useCases: UseCase[]) =>
  request<UseCaseSet>(`${BASE}/${id}`, { method: "PUT", body: JSON.stringify({ name, use_cases: useCases.map(({ mapping: _m, ...c }) => c) }) });
export const deleteUseCaseSet = (id: number) => request<void>(`${BASE}/${id}`, { method: "DELETE" });
export const previewUseCases = (id: number, answers: Answers) =>
  request<{ designs: UseCaseDesign[] }>(`${BASE}/${id}/preview`, { method: "POST", body: JSON.stringify({ answers }) });
export const commitUseCases = (id: number, answers: Answers, activate: boolean) =>
  request<UseCaseCommit>(`${BASE}/${id}/commit`, { method: "POST", body: JSON.stringify({ answers, activate }) });
export const generateUseCaseTests = (id: number) => request<UseCaseSet>(`${BASE}/${id}/tests`, { method: "POST" });

async function download(path: string, filename: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text().catch(() => "")}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const downloadUseCaseTemplate = () => download("/api/process-builder/use-case-template", "BRT360-use-case-template.docx");

export function downloadUseCaseTests(set: UseCaseSetBrief, kind: "manual.docx" | "manual.csv" | "automation.py"): Promise<void> {
  const stem = set.name.replace(/[^\w-]+/g, "-").toLowerCase() || "use-cases";
  const name = kind === "automation.py" ? `test_${stem.replace(/-/g, "_")}.py` : `test-cases-${stem}.${kind.split(".")[1]}`;
  return download(`${BASE}/${set.id}/tests/${kind}`, name);
}
