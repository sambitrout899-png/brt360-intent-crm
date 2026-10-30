import { rememberIntentLabels } from "../intentLabels";
import type {
  C360Connector,
  C360ConnectorDraft,
  C360ConnectorInput,
  C360Layout,
  C360MappingSuggestion,
  C360Plan,
  C360Studio,
  C360TestResult,
  C360View,
  CustomerNetwork,
} from "./c360Types";
import type { FunctionMap, MapChange, MapDraft, MapPlan, MapSimulation, MapVersions } from "./functionMapTypes";
import type { Conflict, ConflictCondition, ConflictList, ScratchDraft } from "./conflictTypes";
import type { FulfillmentCatalog, FulfillmentDraft, FulfillmentOrderSummary, FulfillmentOrderView, FulfillmentPattern, FulfillmentPatternInput, FulfillmentPreview } from "./types";
import type {
  AgentPlan,
  AgentRun,
  AgentStep,
  ProcessBlueprint,
  BrdRead,
  ProcessBuild,
  ProcessInput,
  ProcessPlacement,
  AssistantJournalEntry,
  AssistantUnderstanding,
  AccessPolicy,
  ApprovalLogEntry,
  Branding,
  Broadcast,
  Notices,
  RecordButton,
  TenantUpdate,
  AdminIntentCatalog,
  IntentCatalog,
  IntentCreateInput,
  IntentCreateResult,
  IntentDef,
  IntentUpdateInput,
  PreparedIntent,
  RecentIntent,
  RecordOption,
  AccessCatalog,
  AiProvider,
  AiSettings,
  AiTestResult,
  LocalModelStatus,
  AppRole,
  ApprovalChainTemplate,
  ApprovalRequest,
  DesignOp,
  DesignPlan,
  PriceQuote,
  ProductDesign,
  ProductDesignSummary,
  ApprovalRule,
  ApprovalRuleInput,
  ApprovalRuleOptions,
  ApprovalPolicy,
  PriceList,
  PriceListInput,
  ProcessRole,
  Assignees,
  AssignmentRule,
  CustomFieldDefinition,
  Department,
  EntityType,
  Flow,
  FlowCatalog,
  FlowFieldCatalog,
  FlowGenerateResult,
  FlowGraph,
  FlowInput,
  FlowReview,
  FlowSimulateResult,
  IntentContext,
  MyAccess,
  LifecycleState,
  LifecycleTransition,
  MyWorkItem,
  Position,
  RecordLifecycle,
  StateModelConfig,
  AttributeConstraintRule,
  AuditLogDetail,
  AuditLogEntry,
  CurrentUser,
  DashboardSummary,
  FeasibilityCheckType,
  IdentityProvider,
  IntentAssist,
  IntentResponse,
  Lead,
  Offering,
  OrchestrationPolicy,
  Opportunity,
  Order,
  Party,
  Party360,
  BillingAccount,
  PonrPolicy,
  ProductAttributeDefinition,
  ProductAttributeValue,
  ProductSpecification,
  Quote,
  QuoteDocument,
  QuoteDocumentKind,
  QuoteDocumentTemplate,
  SlaClock,
  SlaPolicy,
  SlaSweepResult,
  Technician,
  Tenant,
  TestRun,
  TestRunSummary,
  TroubleTicket,
  User,
  UserAccess,
  WorkOrder,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

const TOKEN_STORAGE_KEY = "brt360_access_token";

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setAccessToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // Private browsing or storage disabled - the user will just need to
    // log in again next load, which is a tolerable degradation.
  }
}

export class UnauthorizedError extends Error {}

// Which of the user's positions they are working as, and how wide a data
// view they want - sent on every request; the server validates both
// against what the user actually holds (unknown position -> primary,
// too-wide scope -> clamped), so these are preferences, not permissions.
const WORK_CONTEXT_KEY = "brt360_work_context";
let workContext: { positionId: number | null; scope: string | null } = (() => {
  try {
    return JSON.parse(localStorage.getItem(WORK_CONTEXT_KEY) ?? "") ?? { positionId: null, scope: null };
  } catch {
    return { positionId: null, scope: null };
  }
})();

export function getWorkContext() {
  return workContext;
}

export function setWorkContext(next: { positionId: number | null; scope: string | null }): void {
  workContext = next;
  try {
    localStorage.setItem(WORK_CONTEXT_KEY, JSON.stringify(next));
  } catch {
    // per-session only when storage is unavailable
  }
}

// The administrator's workspace (change control, /api/admin/change): set, the
// Studio's saves are drafts there - nothing live changes until a release.
const WORKSPACE_KEY = "brt360_workspace";
let workspaceId: number | null = (() => {
  try {
    return Number(localStorage.getItem(WORKSPACE_KEY)) || null;
  } catch {
    return null;
  }
})();
const workspaceListeners = new Set<(id: number | null) => void>();

export function getWorkspace(): number | null {
  return workspaceId;
}

export function setWorkspace(id: number | null): void {
  workspaceId = id;
  try {
    if (id) localStorage.setItem(WORKSPACE_KEY, String(id));
    else localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    // per-session only when storage is unavailable
  }
  workspaceListeners.forEach((fn) => fn(id));
}

/** Told when the workspace changes - screens reload what they show. */
export function onWorkspaceChange(fn: (id: number | null) => void): () => void {
  workspaceListeners.add(fn);
  return () => workspaceListeners.delete(fn);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (workContext.positionId) headers["X-Position-Id"] = String(workContext.positionId);
  if (workContext.scope) headers["X-Data-Scope"] = workContext.scope;
  if (workspaceId && path.startsWith("/api/admin/flows")) headers["X-BRT-Workspace"] = String(workspaceId);
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
  });
  if (res.status === 401) {
    setAccessToken(null);
    throw new UnauthorizedError("Session expired - please log in again.");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

/** What sign-in needs next: nothing (signed in), a code from the
 * authenticator app, or setting two-step sign-in up first (the company
 * requires it). stepToken is good for that one step, for a few minutes. */
export type LoginStep = { kind: "done" } | { kind: "code"; stepToken: string } | { kind: "setup"; stepToken: string };

async function signInCall(path: string, payload: unknown, token?: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(payload),
  });
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "";
    throw new Error(res.status === 401 && path === "/api/auth/login" ? "Incorrect email or password." : detail || "Sign-in failed.");
  }
  return body;
}

function signedIn(accessToken: string): void {
  setAccessToken(accessToken);
  setWorkContext({ positionId: null, scope: null }); // every sign-in opens in the primary position
}

export async function login(email: string, password: string): Promise<LoginStep> {
  const body = await signInCall("/api/auth/login", { email, password });
  if (typeof body.access_token === "string") {
    signedIn(body.access_token);
    return { kind: "done" };
  }
  return { kind: body.mfa_setup_required ? "setup" : "code", stepToken: String(body.step_token) };
}

export async function loginWithCode(stepToken: string, code: string): Promise<void> {
  const body = await signInCall("/api/auth/login/mfa", { step_token: stepToken, code });
  signedIn(String(body.access_token));
}

export interface MfaStatus {
  enabled: boolean;
  required: boolean;
  recovery_codes_left: number;
}

export interface MfaSetup {
  secret: string;
  otpauth_uri: string;
  qr_data_url: string;
}

export function mfaStatus(): Promise<MfaStatus> {
  return request<MfaStatus>("/api/auth/mfa");
}

/** stepToken: setting it up during sign-in; without it, for the signed-in user. */
export async function mfaSetup(stepToken?: string): Promise<MfaSetup> {
  if (stepToken) return (await signInCall("/api/auth/mfa/setup", {}, stepToken)) as unknown as MfaSetup;
  return request<MfaSetup>("/api/auth/mfa/setup", { method: "POST" });
}

/** Confirms the first code; returns the recovery codes (shown once). During
 * sign-in it also completes the sign-in. */
export async function mfaEnable(code: string, stepToken?: string): Promise<string[]> {
  if (stepToken) {
    const body = await signInCall("/api/auth/mfa/enable", { code }, stepToken);
    signedIn(String(body.access_token));
    return body.recovery_codes as string[];
  }
  return (await request<{ recovery_codes: string[] }>("/api/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) })).recovery_codes;
}

export function mfaDisable(password: string, code: string): Promise<void> {
  return request<void>("/api/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password, code }) });
}

export async function mfaNewRecoveryCodes(code: string): Promise<string[]> {
  return (await request<{ recovery_codes: string[] }>("/api/auth/mfa/recovery-codes", { method: "POST", body: JSON.stringify({ code }) })).recovery_codes;
}

export function logout(): void {
  setAccessToken(null);
  setWorkContext({ positionId: null, scope: null });
  try {
    // the next sign-in shows its "approvals waiting" notice again
    Object.keys(sessionStorage).filter((k) => k.startsWith("brt360.approvals-shown.")).forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* storage unavailable */
  }
}

export function currentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/api/auth/me");
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request<void>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function listUsers(): Promise<User[]> {
  return request<User[]>("/api/admin/users");
}

export function createUser(payload: { email: string; password: string; name: string; role: string }): Promise<User> {
  return request<User>("/api/admin/users", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateUser(id: number): Promise<User> {
  return request<User>(`/api/admin/users/${id}/deactivate`, { method: "PATCH" });
}

export function unlockUser(id: number): Promise<User> {
  return request<User>(`/api/admin/users/${id}/unlock`, { method: "POST" });
}

export function resetUserMfa(id: number): Promise<User> {
  return request<User>(`/api/admin/users/${id}/reset-mfa`, { method: "POST" });
}

export interface SecurityPolicy {
  mfa_required: boolean;
  idle_timeout_minutes: number | null;
}

export function getSecurityPolicy(): Promise<SecurityPolicy> {
  return request<SecurityPolicy>("/api/admin/security");
}

/** Only the fields given change. */
export function setSecurityPolicy(policy: Partial<SecurityPolicy> & { mfa_required: boolean }): Promise<SecurityPolicy> {
  return request<SecurityPolicy>("/api/admin/security", { method: "PUT", body: JSON.stringify(policy) });
}

export function listTenants(): Promise<Tenant[]> {
  return request<Tenant[]>("/api/platform/tenants");
}

export function createTenant(payload: {
  licensed_modules?: string[];
  name: string;
  slug: string;
  vertical: string;
  admin_email: string;
  admin_password: string;
  admin_name: string;
}): Promise<Tenant> {
  return request<Tenant>("/api/platform/tenants", { method: "POST", body: JSON.stringify(payload) });
}

export function updateTenant(id: number, payload: TenantUpdate): Promise<Tenant> {
  return request<Tenant>(`/api/platform/tenants/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

/** The signed-in user's company name, logo and accent colour. */
export function getBranding(): Promise<Branding> {
  return request<Branding>("/api/auth/branding");
}

export function triggerTestRun(): Promise<TestRun> {
  return request<TestRun>("/api/admin/test-runs", { method: "POST" });
}

export function listTestRuns(): Promise<TestRunSummary[]> {
  return request<TestRunSummary[]>("/api/admin/test-runs");
}

export function getTestRun(id: number): Promise<TestRun> {
  return request<TestRun>(`/api/admin/test-runs/${id}`);
}

export function submitIntent(text: string, context?: IntentContext): Promise<IntentResponse> {
  return request<IntentResponse>("/api/intents", {
    method: "POST",
    body: JSON.stringify({ text, context }),
  });
}

/** An in-page form's intent: exact type + entities, no NLU parsing. `text`
 * is the human-readable statement recorded in the audit log. */
export function submitStructuredIntent(text: string, intentType: string, entities: Record<string, unknown>): Promise<IntentResponse> {
  return request<IntentResponse>("/api/intents", {
    method: "POST",
    body: JSON.stringify({ text, structured: { intent_type: intentType, entities } }),
  });
}

/** Runs the intent for real, then takes everything back - returns what it would do (`impact`). */
export function rehearseStructuredIntent(text: string, intentType: string, entities: Record<string, unknown>): Promise<IntentResponse> {
  return request<IntentResponse>("/api/intents/rehearse", {
    method: "POST",
    body: JSON.stringify({ text, structured: { intent_type: intentType, entities } }),
  });
}

// ---------- the agent ----------

type AgentStepIn = { intent_type: string | null; entities: Record<string, unknown>; text: string };

export function agentPlan(goal: string, context?: IntentContext): Promise<AgentPlan> {
  return request<AgentPlan>("/api/agent/plan", { method: "POST", body: JSON.stringify({ goal, context }) });
}

export function agentAnnotate(steps: AgentStepIn[], context?: IntentContext): Promise<AgentStep[]> {
  return request<AgentStep[]>("/api/agent/annotate", { method: "POST", body: JSON.stringify({ steps, context }) });
}

/** rehearse: everything is taken back; execute: all steps are kept together, or none. */
export function agentRun(goal: string, steps: AgentStepIn[], mode: "rehearse" | "execute", context?: IntentContext): Promise<AgentRun> {
  return request<AgentRun>("/api/agent/run", { method: "POST", body: JSON.stringify({ goal, steps, mode, context }) });
}

/** Ask anything - one box for every request, routed to an action, the agent or the process builder. */
export function assistantUnderstand(text: string, context?: IntentContext, autonomous = false): Promise<AssistantUnderstanding> {
  return request<AssistantUnderstanding>("/api/assistant/understand", { method: "POST", body: JSON.stringify({ text, context, autonomous }) });
}

export function assistantJournal(everyone = false): Promise<AssistantJournalEntry[]> {
  return request<AssistantJournalEntry[]>(`/api/assistant/journal${everyone ? "?everyone=true" : ""}`);
}

export function assistantOutcome(journalId: number, status: string, summary = ""): Promise<void> {
  return request<void>(`/api/assistant/journal/${journalId}/outcome`, { method: "POST", body: JSON.stringify({ status, summary: summary.slice(0, 500) }) });
}

/** Functional Map - BRT's original map and the map in use are read-only; a
 * tenant changes its map by activating one of its versions (drafts until then). */
export function functionMapVanilla(): Promise<FunctionMap> {
  return request<FunctionMap>("/api/admin/function-map/vanilla");
}

export function functionMapMine(): Promise<FunctionMap & MapVersions> {
  return request<FunctionMap & MapVersions>("/api/admin/function-map");
}

export function functionMapVersions(): Promise<MapVersions> {
  return request<MapVersions>("/api/admin/function-map/versions");
}

/** The next revision of a version (v3 -> v3.1): a draft to work on, keeping its number. */
export function functionMapRevise(id: number): Promise<FunctionMap> {
  return request<FunctionMap>(`/api/admin/function-map/versions/${id}/revise`, { method: "POST" });
}

/** A new draft copied from BRT's original, the map in use or another version (its id). */
export function functionMapNewVersion(source: "original" | "active" | number, name = ""): Promise<FunctionMap> {
  return request<FunctionMap>("/api/admin/function-map/versions", { method: "POST", body: JSON.stringify({ source, name }) });
}

export function functionMapVersion(id: number): Promise<FunctionMap> {
  return request<FunctionMap>(`/api/admin/function-map/versions/${id}`);
}

export function functionMapSaveVersion(id: number, body: Partial<MapDraft> & { name?: string; notes?: string }): Promise<FunctionMap> {
  return request<FunctionMap>(`/api/admin/function-map/versions/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export function functionMapDeleteVersion(id: number): Promise<void> {
  return request<void>(`/api/admin/function-map/versions/${id}`, { method: "DELETE" });
}

/** What activating a version (or "original": BRT's map) would change - nothing is changed. */
export function functionMapPlanActivation(id: number | "original"): Promise<MapPlan> {
  const path = id === "original" ? "original" : `versions/${id}`;
  return request<MapPlan>(`/api/admin/function-map/${path}/plan`, { method: "POST" });
}

/** The version played through - its staged processes built, their flows run on sample records - then rolled back. */
export function functionMapSimulate(id: number): Promise<MapSimulation> {
  return request<MapSimulation>(`/api/admin/function-map/versions/${id}/simulate`, { method: "POST" });
}

export function functionMapActivate(id: number | "original"): Promise<{ applied: MapChange[]; problems: MapPlan["problems"]; map: FunctionMap } & MapVersions> {
  const path = id === "original" ? "original" : `versions/${id}`;
  return request(`/api/admin/function-map/${path}/activate`, { method: "POST" });
}

/** Conflicts - automations / actions that pull against each other, and the admin's priority and conditions. */
export function conflictsList(): Promise<ConflictList> {
  return request<ConflictList>("/api/admin/conflicts");
}

export function conflictResolve(input: {
  key: string;
  order?: number[];
  mode?: "all" | "first";
  conditions?: Record<string, ConflictCondition | null>;
  winner?: number;
}): Promise<{ conflicts: Conflict[] }> {
  return request("/api/admin/conflicts/resolve", { method: "POST", body: JSON.stringify(input) });
}

export function conflictClear(key: string): Promise<{ conflicts: Conflict[] }> {
  return request(`/api/admin/conflicts/rules/${key.split(":").map(encodeURIComponent).join(":")}`, { method: "DELETE" });
}

/** Build from scratch - a new action for a request nothing covers (admins; built only on consent). */
export function scratchDraft(requestText: string): Promise<ScratchDraft> {
  return request<ScratchDraft>("/api/assistant/scratch/draft", { method: "POST", body: JSON.stringify({ request: requestText }) });
}

export function scratchBuild(spec: ScratchDraft, journalId?: number): Promise<{ intent: IntentDef; code: string; flow_id: number }> {
  return request("/api/assistant/scratch/build", { method: "POST", body: JSON.stringify({ spec, journal_id: journalId ?? null }) });
}

/** Process Builder - a process in plain English, built from the existing engines. */
export function processRecipes(): Promise<{ id: number; name: string; request: string }[]> {
  return request("/api/process-builder/recipes");
}

export function processDesign(input: ProcessInput): Promise<ProcessBlueprint> {
  return request<ProcessBlueprint>("/api/process-builder/design", { method: "POST", body: JSON.stringify(input) });
}

/** execute goes live through the Functional Map: a new version of the map in use, with the process placed in it, activated. */
export function processBuild(input: ProcessInput, mode: "rehearse" | "simulate" | "execute", placement?: ProcessPlacement): Promise<ProcessBuild> {
  return request<ProcessBuild>("/api/process-builder/build", { method: "POST", body: JSON.stringify({ ...input, mode, placement }) });
}

/** Puts the designed process into a draft version of the map (a new one from the map in use, or versionId) - nothing is built yet. */
export function processStage(input: ProcessInput, placement: ProcessPlacement, versionId?: number): Promise<FunctionMap> {
  return request<FunctionMap>("/api/process-builder/stage", { method: "POST", body: JSON.stringify({ ...input, placement, version_id: versionId }) });
}

export function processPlacements(): Promise<{ processes: { key: string; label: string; steps: number; tenant: boolean }[] }> {
  return request("/api/process-builder/placements");
}

/** A BRD (.docx, .txt, .md) read into the builder's request - nothing is designed or saved. */
export async function processReadBrd(file: File): Promise<BrdRead> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return request<BrdRead>("/api/process-builder/brd", { method: "POST", body: JSON.stringify({ filename: file.name, content_base64: btoa(binary) }) });
}

export async function downloadBrdTemplate(): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}/api/process-builder/brd-template`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text().catch(() => "")}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "BRT360-process-BRD-template.docx";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function assistIntent(text: string, context?: IntentContext, signal?: AbortSignal): Promise<IntentAssist> {
  return request<IntentAssist>("/api/intents/assist", {
    method: "POST",
    body: JSON.stringify({ text, context }),
    signal,
  });
}

// ---------- intent catalog / command bar copilot ----------

export function intentCatalog(): Promise<IntentCatalog> {
  return request<IntentCatalog>("/api/intents/catalog").then((c) => {
    rememberIntentLabels(c.intents);
    return c;
  });
}

export function recentIntents(): Promise<RecentIntent[]> {
  return request<RecentIntent[]>("/api/intents/recent");
}

/** Prefill, options, record suggestions and pre-checks for one intent's
 * form - never executes anything. */
export function prepareIntent(
  intentType: string,
  entities: Record<string, unknown>,
  text = "",
  context?: IntentContext
): Promise<PreparedIntent> {
  return request<PreparedIntent>("/api/intents/prepare", {
    method: "POST",
    body: JSON.stringify({ intent_type: intentType, entities, text, context }),
  });
}

export function lookupRecords(recordType: string, q: string, intentType?: string, field?: string): Promise<RecordOption[]> {
  const params = new URLSearchParams({ record_type: recordType, q });
  if (intentType) params.set("intent_type", intentType);
  if (field) params.set("field", field);
  return request<RecordOption[]>(`/api/intents/lookup?${params}`);
}

export function adminIntents(): Promise<AdminIntentCatalog> {
  return request<AdminIntentCatalog>("/api/admin/intents").then((c) => {
    rememberIntentLabels(c.intents);
    return c;
  });
}

export function createIntent(payload: IntentCreateInput): Promise<IntentCreateResult> {
  return request<IntentCreateResult>("/api/admin/intents", { method: "POST", body: JSON.stringify(payload) });
}

export function updateIntent(code: string, payload: IntentUpdateInput): Promise<IntentDef> {
  return request<IntentDef>(`/api/admin/intents/${encodeURIComponent(code)}`, { method: "PUT", body: JSON.stringify(payload) });
}

/** Give or take away access to many intents for many roles at once. */
export function bulkIntentRoles(payload: { codes: string[]; role_ids: number[]; mode: "add" | "remove" | "set" | "everyone" }): Promise<{ changed: string[]; skipped: { code: string; reason: string }[] }> {
  return request(`/api/admin/intents/bulk-roles`, { method: "POST", body: JSON.stringify(payload) });
}

export function resetIntent(code: string): Promise<IntentDef> {
  return request<IntentDef>(`/api/admin/intents/${encodeURIComponent(code)}/reset`, { method: "POST" });
}

export function deleteIntent(code: string): Promise<void> {
  return request<void>(`/api/admin/intents/${encodeURIComponent(code)}`, { method: "DELETE" });
}

export function listCatalog(): Promise<Offering[]> {
  return request<Offering[]>("/api/catalog");
}

export function offeringVersions(id: number): Promise<Offering[]> {
  return request<Offering[]>(`/api/catalog/${id}/versions`);
}

export function listAudit(): Promise<AuditLogEntry[]> {
  return request<AuditLogEntry[]>("/api/audit");
}

export function auditDetail(id: number): Promise<AuditLogDetail> {
  return request<AuditLogDetail>(`/api/audit/${id}`);
}

export function dashboardSummary(): Promise<DashboardSummary> {
  return request<DashboardSummary>("/api/dashboard/summary");
}

export function listParties(q?: string): Promise<Party[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return request<Party[]>(`/api/parties${qs}`);
}

export function party360(id: number): Promise<Party360> {
  return request<Party360>(`/api/parties/${id}/360`);
}

export function listLeads(): Promise<Lead[]> {
  return request<Lead[]>("/api/leads");
}

export function leadDetail(id: number): Promise<Lead> {
  return request<Lead>(`/api/leads/${id}`);
}

export function listOpportunities(stage?: string): Promise<Opportunity[]> {
  const qs = stage ? `?stage=${encodeURIComponent(stage)}` : "";
  return request<Opportunity[]>(`/api/opportunities${qs}`);
}

export function opportunityDetail(id: number): Promise<Opportunity> {
  return request<Opportunity>(`/api/opportunities/${id}`);
}

export function listQuotes(status?: string): Promise<Quote[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<Quote[]>(`/api/quotes${qs}`);
}

export function quoteDetail(id: number): Promise<Quote> {
  return request<Quote>(`/api/quotes/${id}`);
}

export function listOrders(status?: string): Promise<Order[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<Order[]>(`/api/orders${qs}`);
}

export function orderDetail(id: number): Promise<Order> {
  return request<Order>(`/api/orders/${id}`);
}

export function listBillingAccounts(): Promise<BillingAccount[]> {
  return request<BillingAccount[]>("/api/billing-accounts");
}

export function listTickets(status?: string, severity?: string): Promise<TroubleTicket[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (severity) params.set("severity", severity);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return request<TroubleTicket[]>(`/api/tickets${qs}`);
}

export function ticketDetail(id: number): Promise<TroubleTicket> {
  return request<TroubleTicket>(`/api/tickets/${id}`);
}

export function listWorkOrders(status?: string): Promise<WorkOrder[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<WorkOrder[]>(`/api/work-orders${qs}`);
}

export function listTechnicians(): Promise<Technician[]> {
  return request<Technician[]>("/api/technicians");
}

export function listApprovalChains(): Promise<ApprovalChainTemplate[]> {
  return request<ApprovalChainTemplate[]>("/api/admin/approval-chains");
}

export function createApprovalChain(payload: {
  name: string;
  segment: string | null;
  min_discount_pct: number;
  max_discount_pct: number;
  tiers: { approver_tier: string }[];
}): Promise<ApprovalChainTemplate> {
  return request<ApprovalChainTemplate>("/api/admin/approval-chains", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deactivateApprovalChain(id: number): Promise<ApprovalChainTemplate> {
  return request<ApprovalChainTemplate>(`/api/admin/approval-chains/${id}/deactivate`, { method: "PATCH" });
}

export function deleteApprovalChain(id: number): Promise<void> {
  return request<void>(`/api/admin/approval-chains/${id}`, { method: "DELETE" });
}

export function listSlaPolicies(): Promise<SlaPolicy[]> {
  return request<SlaPolicy[]>("/api/admin/sla-policies");
}

export function createSlaPolicy(payload: { clock_type: string; duration_hours: number; on_breach_action: string }): Promise<SlaPolicy> {
  return request<SlaPolicy>("/api/admin/sla-policies", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateSlaPolicy(id: number): Promise<SlaPolicy> {
  return request<SlaPolicy>(`/api/admin/sla-policies/${id}/deactivate`, { method: "PATCH" });
}

export function deleteSlaPolicy(id: number): Promise<void> {
  return request<void>(`/api/admin/sla-policies/${id}`, { method: "DELETE" });
}

export function listSlaClocks(status?: string): Promise<SlaClock[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<SlaClock[]>(`/api/admin/sla-clocks${qs}`);
}

export function processDueSlaClocks(): Promise<SlaSweepResult> {
  return request<SlaSweepResult>("/api/admin/sla/process-due", { method: "POST" });
}

export function listProductSpecifications(): Promise<ProductSpecification[]> {
  return request<ProductSpecification[]>("/api/admin/product-specifications");
}

export function listAttributeDefinitions(): Promise<ProductAttributeDefinition[]> {
  return request<ProductAttributeDefinition[]>("/api/admin/attribute-definitions");
}

export function createAttributeDefinition(payload: {
  name: string;
  data_type: string;
  unit: string | null;
  allowed_values: string | null;
}): Promise<ProductAttributeDefinition> {
  return request<ProductAttributeDefinition>("/api/admin/attribute-definitions", { method: "POST", body: JSON.stringify(payload) });
}

export function deleteAttributeDefinition(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-definitions/${id}`, { method: "DELETE" });
}

export function listAttributeValues(productSpecificationId?: number): Promise<ProductAttributeValue[]> {
  const qs = productSpecificationId != null ? `?product_specification_id=${productSpecificationId}` : "";
  return request<ProductAttributeValue[]>(`/api/admin/attribute-values${qs}`);
}

export function setAttributeValue(payload: {
  product_specification_id: number;
  attribute_definition_id: number;
  value: string;
}): Promise<ProductAttributeValue> {
  return request<ProductAttributeValue>("/api/admin/attribute-values", { method: "PUT", body: JSON.stringify(payload) });
}

export function listAttributeConstraintRules(): Promise<AttributeConstraintRule[]> {
  return request<AttributeConstraintRule[]>("/api/admin/attribute-constraint-rules");
}

export function createAttributeConstraintRule(payload: {
  if_product_spec_id: number;
  if_attribute_id: number;
  operator: string;
  threshold: string;
  rule_type: string;
  then_product_spec_id: number;
}): Promise<AttributeConstraintRule> {
  return request<AttributeConstraintRule>("/api/admin/attribute-constraint-rules", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateAttributeConstraintRule(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-constraint-rules/${id}/deactivate`, { method: "PATCH" });
}

export function deleteAttributeConstraintRule(id: number): Promise<void> {
  return request<void>(`/api/admin/attribute-constraint-rules/${id}`, { method: "DELETE" });
}

export function listIdentityProviders(): Promise<IdentityProvider[]> {
  return request<IdentityProvider[]>("/api/admin/identity-providers");
}

export function createIdentityProvider(name: string): Promise<IdentityProvider> {
  return request<IdentityProvider>("/api/admin/identity-providers", { method: "POST", body: JSON.stringify({ name }) });
}

export function deactivateIdentityProvider(id: number): Promise<IdentityProvider> {
  return request<IdentityProvider>(`/api/admin/identity-providers/${id}/deactivate`, { method: "PATCH" });
}

export function deleteIdentityProvider(id: number): Promise<void> {
  return request<void>(`/api/admin/identity-providers/${id}`, { method: "DELETE" });
}

export function listPonrPolicies(): Promise<PonrPolicy[]> {
  return request<PonrPolicy[]>("/api/admin/ponr-policies");
}

export function createPonrPolicy(payload: { entity_type: string; ponr_statuses: string }): Promise<PonrPolicy> {
  return request<PonrPolicy>("/api/admin/ponr-policies", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivatePonrPolicy(id: number): Promise<PonrPolicy> {
  return request<PonrPolicy>(`/api/admin/ponr-policies/${id}/deactivate`, { method: "PATCH" });
}

export function deletePonrPolicy(id: number): Promise<void> {
  return request<void>(`/api/admin/ponr-policies/${id}`, { method: "DELETE" });
}

export function listFeasibilityCheckTypes(): Promise<FeasibilityCheckType[]> {
  return request<FeasibilityCheckType[]>("/api/admin/feasibility-check-types");
}

export function createFeasibilityCheckType(payload: { name: string; resource_type: string }): Promise<FeasibilityCheckType> {
  return request<FeasibilityCheckType>("/api/admin/feasibility-check-types", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateFeasibilityCheckType(id: number): Promise<FeasibilityCheckType> {
  return request<FeasibilityCheckType>(`/api/admin/feasibility-check-types/${id}/deactivate`, { method: "PATCH" });
}

export function deleteFeasibilityCheckType(id: number): Promise<void> {
  return request<void>(`/api/admin/feasibility-check-types/${id}`, { method: "DELETE" });
}

export function listOrchestrationPolicies(): Promise<OrchestrationPolicy[]> {
  return request<OrchestrationPolicy[]>("/api/admin/orchestration-policies");
}

export function createOrchestrationPolicy(payload: { intent_type: string; mode: string }): Promise<OrchestrationPolicy> {
  return request<OrchestrationPolicy>("/api/admin/orchestration-policies", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateOrchestrationPolicy(id: number): Promise<OrchestrationPolicy> {
  return request<OrchestrationPolicy>(`/api/admin/orchestration-policies/${id}/deactivate`, { method: "PATCH" });
}

export function deleteOrchestrationPolicy(id: number): Promise<void> {
  return request<void>(`/api/admin/orchestration-policies/${id}`, { method: "DELETE" });
}

export function getAiSettings(): Promise<AiSettings> {
  return request<AiSettings>("/api/admin/ai-settings");
}

export function updateAiSettings(payload: {
  api_key: string | null;
  model: string;
  is_active: boolean;
  provider?: AiProvider;
  local_model?: string;
  local_live_assist?: boolean;
}): Promise<AiSettings> {
  return request<AiSettings>("/api/admin/ai-settings", { method: "PUT", body: JSON.stringify(payload) });
}

export function localModelStatus(): Promise<LocalModelStatus> {
  return request<LocalModelStatus>("/api/admin/ai-settings/local-status");
}

export function testAi(payload: { provider: AiProvider; local_model?: string; text?: string }): Promise<AiTestResult> {
  return request<AiTestResult>("/api/admin/ai-settings/test", { method: "POST", body: JSON.stringify(payload) });
}

/** Fine-tuning file for the own model - fetched with the session token, saved by the browser. */
export async function downloadTrainingData(): Promise<number> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}/api/admin/ai-settings/training-data`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "brt360_training_data.jsonl";
  a.click();
  URL.revokeObjectURL(url);
  return Number(res.headers.get("X-Example-Count") ?? 0);
}

export function clearAiSettings(): Promise<void> {
  return request<void>("/api/admin/ai-settings", { method: "DELETE" });
}

// ---------- Phase 7: record lifecycle (all roles) ----------

export function recordLifecycle(entityType: EntityType, id: number): Promise<RecordLifecycle> {
  return request<RecordLifecycle>(`/api/records/${entityType}/${id}`);
}

export function listAssignees(): Promise<Assignees> {
  return request<Assignees>("/api/records/assignees");
}

export function myWork(): Promise<MyWorkItem[]> {
  return request<MyWorkItem[]>("/api/records/my-work");
}

// ---------- Phase 7: admin - state models, positions, assignment, custom fields ----------

export function listStateModels(): Promise<StateModelConfig[]> {
  return request<StateModelConfig[]>("/api/admin/state-models");
}

export function saveStateModel(
  entityType: EntityType,
  payload: { name: string; states: LifecycleState[]; transitions: LifecycleTransition[] }
): Promise<StateModelConfig> {
  return request<StateModelConfig>(`/api/admin/state-models/${entityType}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function resetStateModel(entityType: EntityType): Promise<StateModelConfig> {
  return request<StateModelConfig>(`/api/admin/state-models/${entityType}`, { method: "DELETE" });
}

export function listPositions(): Promise<Position[]> {
  return request<Position[]>("/api/admin/positions");
}

export function createPosition(payload: {
  name: string;
  parent_position_id: number | null;
  department_id?: number | null;
  data_scope?: string;
  user_ids: number[];
}): Promise<Position> {
  return request<Position>("/api/admin/positions", { method: "POST", body: JSON.stringify(payload) });
}

export function updatePosition(
  id: number,
  payload: { name: string; parent_position_id: number | null; department_id: number | null; data_scope: string }
): Promise<Position> {
  return request<Position>(`/api/admin/positions/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

// ---------- Org structure, roles & access ----------

export function myAccess(): Promise<MyAccess> {
  return request<MyAccess>("/api/access/me");
}

export function accessCatalog(): Promise<AccessCatalog> {
  return request<AccessCatalog>("/api/admin/access-catalog");
}

export function listDepartments(): Promise<Department[]> {
  return request<Department[]>("/api/admin/departments");
}

export function createDepartment(payload: { name: string; parent_department_id: number | null }): Promise<Department> {
  return request<Department>("/api/admin/departments", { method: "POST", body: JSON.stringify(payload) });
}

export function updateDepartment(id: number, payload: { name: string; parent_department_id: number | null }): Promise<Department> {
  return request<Department>(`/api/admin/departments/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function deleteDepartment(id: number): Promise<void> {
  return request<void>(`/api/admin/departments/${id}`, { method: "DELETE" });
}

export function listRoles(): Promise<AppRole[]> {
  return request<AppRole[]>("/api/admin/roles");
}

export function createRole(payload: { name: string; description: string; pages: string[] }): Promise<AppRole> {
  return request<AppRole>("/api/admin/roles", { method: "POST", body: JSON.stringify(payload) });
}

export function updateRole(id: number, payload: { name: string; description: string; pages: string[] }): Promise<AppRole> {
  return request<AppRole>(`/api/admin/roles/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function deleteRole(id: number): Promise<void> {
  return request<void>(`/api/admin/roles/${id}`, { method: "DELETE" });
}

export function listUserAccess(): Promise<UserAccess[]> {
  return request<UserAccess[]>("/api/admin/user-access");
}

export function getAccessPolicy(): Promise<AccessPolicy> {
  return request<AccessPolicy>("/api/admin/access-policy");
}

export function setAccessPolicy(payload: Partial<AccessPolicy>): Promise<AccessPolicy> {
  return request<AccessPolicy>("/api/admin/access-policy", { method: "PUT", body: JSON.stringify(payload) });
}

export function approvalLog(): Promise<ApprovalLogEntry[]> {
  return request<ApprovalLogEntry[]>("/api/approvals/log");
}

export function partyRecordActions(partyId: number): Promise<Record<string, RecordButton[]>> {
  return request<Record<string, RecordButton[]>>(`/api/records/actions/party/${partyId}`);
}

export function recordButtons(refs: string[]): Promise<Record<string, RecordButton[]>> {
  if (refs.length === 0) return Promise.resolve({});
  return request<Record<string, RecordButton[]>>("/api/records/actions", { method: "POST", body: JSON.stringify({ refs: refs.slice(0, 500) }) });
}

// The screens' own polling: doesn't count as the user being active (the idle timeout, app/auth.py).
const BACKGROUND = { headers: { "X-BRT-Background": "1" } };

export function myNotices(place: string, partyId?: number): Promise<Notices> {
  return request<Notices>(`/api/notices?place=${encodeURIComponent(place)}${partyId ? `&party_id=${partyId}` : ""}`, BACKGROUND);
}

/** Message boxes a flow sent me (Flow Studio: Notify, channel "popup") that I haven't dismissed. */
export interface PopupMessage {
  id: number;
  title: string;
  message: string;
  level: "info" | "success" | "warning" | "urgent";
  entity_type: string | null;
  entity_id: number | null;
  at: string;
}

export function myPopups(): Promise<PopupMessage[]> {
  return request<PopupMessage[]>("/api/notices/popups", BACKGROUND);
}

export function popupsSeen(upTo: number): Promise<void> {
  return request<void>("/api/notices/popups/seen", { method: "POST", body: JSON.stringify({ up_to: upTo }) });
}

export function listBroadcasts(): Promise<Broadcast[]> {
  return request<Broadcast[]>("/api/admin/broadcasts");
}

export function addBroadcast(payload: { text: string; level: string; place: string; ends_at: string | null }): Promise<Broadcast> {
  return request<Broadcast>("/api/admin/broadcasts", { method: "POST", body: JSON.stringify(payload) });
}

export function removeBroadcast(id: number): Promise<void> {
  return request<void>(`/api/admin/broadcasts/${id}`, { method: "DELETE" });
}

export function setUserAccess(
  userId: number,
  payload: { role_ids: number[]; position_ids: number[]; primary_position_id: number | null }
): Promise<UserAccess> {
  return request<UserAccess>(`/api/admin/users/${userId}/access`, { method: "PUT", body: JSON.stringify(payload) });
}

export function setPositionMembers(id: number, userIds: number[]): Promise<Position> {
  return request<Position>(`/api/admin/positions/${id}/members`, { method: "PUT", body: JSON.stringify({ user_ids: userIds }) });
}

export function deletePosition(id: number): Promise<void> {
  return request<void>(`/api/admin/positions/${id}`, { method: "DELETE" });
}

export function listAssignmentRules(): Promise<AssignmentRule[]> {
  return request<AssignmentRule[]>("/api/admin/assignment-rules");
}

export function createAssignmentRule(payload: {
  name: string;
  entity_type: string;
  on_state: string | null;
  strategy: string;
  position_id: number | null;
  user_id: number | null;
  department_id?: number | null;
  priority: number;
}): Promise<AssignmentRule> {
  return request<AssignmentRule>("/api/admin/assignment-rules", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateAssignmentRule(id: number): Promise<AssignmentRule> {
  return request<AssignmentRule>(`/api/admin/assignment-rules/${id}/deactivate`, { method: "PATCH" });
}

export function deleteAssignmentRule(id: number): Promise<void> {
  return request<void>(`/api/admin/assignment-rules/${id}`, { method: "DELETE" });
}

export function listCustomFields(entityType?: string): Promise<CustomFieldDefinition[]> {
  const qs = entityType ? `?entity_type=${encodeURIComponent(entityType)}` : "";
  return request<CustomFieldDefinition[]>(`/api/admin/custom-fields${qs}`);
}

export function createCustomField(payload: {
  entity_type: string;
  key: string;
  label: string;
  data_type: string;
  allowed_values: string | null;
  required: boolean;
  default_value: string | null;
}): Promise<CustomFieldDefinition> {
  return request<CustomFieldDefinition>("/api/admin/custom-fields", { method: "POST", body: JSON.stringify(payload) });
}

export function deactivateCustomField(id: number): Promise<CustomFieldDefinition> {
  return request<CustomFieldDefinition>(`/api/admin/custom-fields/${id}/deactivate`, { method: "PATCH" });
}

export function deleteCustomField(id: number): Promise<void> {
  return request<void>(`/api/admin/custom-fields/${id}`, { method: "DELETE" });
}

// ---------- Phase 7: AI Flow Studio ----------

export function flowCatalog(): Promise<FlowCatalog> {
  return request<FlowCatalog>("/api/admin/flows/catalog");
}

export function listFlows(): Promise<Flow[]> {
  return request<Flow[]>("/api/admin/flows");
}

export function createFlow(payload: FlowInput): Promise<Flow> {
  return request<Flow>("/api/admin/flows", { method: "POST", body: JSON.stringify(payload) });
}

export function updateFlow(id: number, payload: FlowInput): Promise<Flow> {
  return request<Flow>(`/api/admin/flows/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function activateFlow(id: number): Promise<Flow> {
  return request<Flow>(`/api/admin/flows/${id}/activate`, { method: "POST" });
}

export function deactivateFlow(id: number): Promise<Flow> {
  return request<Flow>(`/api/admin/flows/${id}/deactivate`, { method: "POST" });
}

export function deleteFlow(id: number): Promise<void> {
  return request<void>(`/api/admin/flows/${id}`, { method: "DELETE" });
}

export interface FlowTriggerInfo {
  entity_type: string | null;
  trigger_type: string;
  trigger_intent_type: string | null;
  required_fields: string | null;
}

export function generateFlow(prompt: string, trigger: FlowTriggerInfo): Promise<FlowGenerateResult & {
  trigger?: { trigger_type: "state_enter" | "intent"; entity_type: string | null; trigger_state?: string; trigger_intent_type?: string; timing?: string; summary: string } | null;
}> {
  return request<FlowGenerateResult>("/api/admin/flows/ai-generate", {
    method: "POST",
    body: JSON.stringify({ prompt, ...trigger }),
  });
}

export function flowFieldCatalog(trigger: FlowTriggerInfo): Promise<FlowFieldCatalog> {
  const params = new URLSearchParams({ trigger_type: trigger.trigger_type });
  if (trigger.entity_type) params.set("entity_type", trigger.entity_type);
  if (trigger.trigger_intent_type) params.set("trigger_intent_type", trigger.trigger_intent_type);
  if (trigger.required_fields) params.set("required_fields", trigger.required_fields);
  return request<FlowFieldCatalog>(`/api/admin/flows/field-catalog?${params}`);
}

export function reviewFlow(trigger: FlowTriggerInfo, graph: FlowGraph): Promise<FlowReview> {
  return request<FlowReview>("/api/admin/flows/review", { method: "POST", body: JSON.stringify({ ...trigger, graph }) });
}

export function simulateFlow(id: number, entityId: number | null, entities: Record<string, unknown>, result: Record<string, unknown> = {}): Promise<FlowSimulateResult> {
  return request<FlowSimulateResult>(`/api/admin/flows/${id}/simulate`, {
    method: "POST",
    body: JSON.stringify({ entity_id: entityId, entities, result }),
  });
}

// ---------- Customer 360 (Configure 360) ----------

export function c360PartyView(partyId: number): Promise<C360View> {
  return request<C360View>(`/api/c360/parties/${partyId}/view`);
}

/** The customer's network from Network Inventory - services, their paths and resources, what is at risk. */
export function c360PartyNetwork(partyId: number): Promise<CustomerNetwork> {
  return request<CustomerNetwork>(`/api/c360/parties/${partyId}/network`);
}

export function c360Studio(): Promise<C360Studio> {
  return request<C360Studio>("/api/admin/c360");
}

export function c360Agent(instruction: string, layout?: C360Layout): Promise<C360Plan> {
  return request<C360Plan>("/api/admin/c360/agent", { method: "POST", body: JSON.stringify({ instruction, layout }) });
}

export function c360Preview(partyId: number, layout: C360Layout, connectors: C360ConnectorDraft[]): Promise<C360View> {
  return request<C360View>(`/api/admin/c360/preview/${partyId}`, { method: "POST", body: JSON.stringify({ layout, connectors }) });
}

export function c360Save(
  layout: C360Layout,
  summary: string,
  connectors: C360ConnectorDraft[]
): Promise<{ layout: C360Layout; version: number; connectors_created: string[] }> {
  return request("/api/admin/c360/layout", { method: "PUT", body: JSON.stringify({ layout, summary, connectors }) });
}

export function c360Undo(): Promise<{ layout: C360Layout; version: number }> {
  return request("/api/admin/c360/undo", { method: "POST" });
}

export function c360Restore(version: number): Promise<{ layout: C360Layout; version: number }> {
  return request(`/api/admin/c360/restore/${version}`, { method: "POST" });
}

export function c360CreateConnector(payload: C360ConnectorInput): Promise<C360Connector> {
  return request<C360Connector>("/api/admin/c360/connectors", { method: "POST", body: JSON.stringify(payload) });
}

export function c360UpdateConnector(id: number, payload: C360ConnectorInput): Promise<C360Connector> {
  return request<C360Connector>(`/api/admin/c360/connectors/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function c360DeleteConnector(id: number): Promise<void> {
  return request<void>(`/api/admin/c360/connectors/${id}`, { method: "DELETE" });
}

export function c360TestConnector(id: number, dataset?: string, partyId?: number): Promise<C360TestResult> {
  return request<C360TestResult>(`/api/admin/c360/connectors/${id}/test`, {
    method: "POST",
    body: JSON.stringify({ dataset, party_id: partyId }),
  });
}

export function c360MapFields(dataset: string, sample: string): Promise<C360MappingSuggestion> {
  return request<C360MappingSuggestion>("/api/admin/c360/connectors/map", { method: "POST", body: JSON.stringify({ dataset, sample }) });
}
export interface InventorySyncSettings {
  enabled: boolean; interval_minutes: number; connected: boolean;
  last_success_at: string | null; last_error: string | null;
}
export function getInventorySync() { return request<InventorySyncSettings>("/api/admin/inventory-sync"); }
export function saveInventorySync(enabled: boolean, interval_minutes: number) {
  return request<InventorySyncSettings>("/api/admin/inventory-sync", {method: "PUT", body: JSON.stringify({enabled, interval_minutes})});
}
export function runInventorySync(sample = false) {
  return request<{created: number; updated: number; deactivated: number}>(`/api/admin/inventory-sync/run?sample=${sample}`, {method: "POST"});
}

// ---------------------------------------------------------------- approvals (two kinds) + Sales-to-Order setup

export function listApprovalRules(): Promise<ApprovalRule[]> {
  return request<ApprovalRule[]>("/api/admin/approval-rules");
}

export function approvalRuleOptions(): Promise<ApprovalRuleOptions> {
  return request<ApprovalRuleOptions>("/api/admin/approval-rules/options");
}

export function createApprovalRule(payload: ApprovalRuleInput): Promise<ApprovalRule> {
  return request<ApprovalRule>("/api/admin/approval-rules", { method: "POST", body: JSON.stringify(payload) });
}

export function updateApprovalRule(id: number, payload: ApprovalRuleInput): Promise<ApprovalRule> {
  return request<ApprovalRule>(`/api/admin/approval-rules/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function setApprovalRuleActive(id: number, is_active: boolean): Promise<ApprovalRule> {
  return request<ApprovalRule>(`/api/admin/approval-rules/${id}/active`, { method: "PATCH", body: JSON.stringify({ is_active }) });
}

export function approvalPolicy(): Promise<ApprovalPolicy> {
  return request<ApprovalPolicy>("/api/admin/approval-policy");
}

export function saveApprovalPolicy(body: Partial<ApprovalPolicy>): Promise<ApprovalPolicy> {
  return request<ApprovalPolicy>("/api/admin/approval-policy", { method: "PUT", body: JSON.stringify(body) });
}

/** Every approval under way (administrators), with why nobody can decide it when that is so. */
export function approvalsWaiting(): Promise<ApprovalRequest[]> {
  return request<ApprovalRequest[]>("/api/admin/approvals/waiting");
}

export function routeApproval(id: number, positionId: number): Promise<ApprovalRequest & { change: string }> {
  return request(`/api/admin/approvals/${id}/route`, { method: "POST", body: JSON.stringify({ position_id: positionId }) });
}

export function applyApprovalPreset(key: string, positionIds?: number[]): Promise<ApprovalRule> {
  return request<ApprovalRule>(`/api/admin/approval-rules/presets/${key}`, { method: "POST", ...(positionIds ? { body: JSON.stringify({ position_ids: positionIds }) } : {}) });
}

export function listPriceLists(): Promise<PriceList[]> {
  return request<PriceList[]>("/api/admin/price-lists");
}

export function createPriceList(payload: PriceListInput): Promise<PriceList> {
  return request<PriceList>("/api/admin/price-lists", { method: "POST", body: JSON.stringify(payload) });
}

export function updatePriceList(id: number, payload: PriceListInput): Promise<PriceList> {
  return request<PriceList>(`/api/admin/price-lists/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function listProcessRoles(): Promise<ProcessRole[]> {
  return request<ProcessRole[]>("/api/admin/process-roles");
}

export function setProcessRole(roleKey: string, position_id: number | null): Promise<ProcessRole[]> {
  return request<ProcessRole[]>(`/api/admin/process-roles/${roleKey}`, { method: "PUT", body: JSON.stringify({ position_id }) });
}

// ---- The PDFs of a quote (proposal, PO, agreement) and their templates

export function listQuoteDocuments(quoteId: number): Promise<QuoteDocument[]> {
  return request<QuoteDocument[]>(`/api/quotes/${quoteId}/documents`);
}

export function uploadQuoteDocument(quoteId: number, body: { kind: QuoteDocumentKind; file_name: string; content_base64: string; note?: string }): Promise<QuoteDocument> {
  return request<QuoteDocument>(`/api/quotes/${quoteId}/documents`, { method: "POST", body: JSON.stringify(body) });
}

/** A PDF fetched with the session (the link itself needs the sign-in header) - for viewing or saving. */
async function fetchPdf(path: string): Promise<Blob> {
  const token = getAccessToken();
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  if (workContext.positionId) headers["X-Position-Id"] = String(workContext.positionId);
  if (workContext.scope) headers["X-Data-Scope"] = workContext.scope;
  const res = await fetch(`${API_BASE}${path}`, { headers });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text().catch(() => "")}`);
  return res.blob();
}

export function quoteDocumentFile(quoteId: number, documentId: number): Promise<Blob> {
  return fetchPdf(`/api/quotes/${quoteId}/documents/${documentId}/file`);
}

/** The proposal / agreement as it would be generated for this quote now (nothing saved). */
export function previewQuoteDocument(quoteId: number, kind: "proposal" | "agreement"): Promise<Blob> {
  return fetchPdf(`/api/quotes/${quoteId}/documents/preview/${kind}`);
}

export function listQuoteDocumentTemplates(): Promise<{ templates: QuoteDocumentTemplate[]; placeholders: string[] }> {
  return request("/api/admin/quote-document-templates");
}

export function saveQuoteDocumentTemplate(kind: string, body: Partial<Pick<QuoteDocumentTemplate, "enabled" | "title" | "intro" | "terms" | "closing">> & { reset?: boolean }): Promise<QuoteDocumentTemplate> {
  return request<QuoteDocumentTemplate>(`/api/admin/quote-document-templates/${kind}`, { method: "PUT", body: JSON.stringify(body) });
}

export function previewQuoteDocumentTemplate(kind: string, quoteId?: number): Promise<Blob> {
  return fetchPdf(`/api/admin/quote-document-templates/${kind}/preview${quoteId ? `?quote_id=${quoteId}` : ""}`);
}

export function myApprovals(): Promise<ApprovalRequest[]> {
  return request<ApprovalRequest[]>("/api/approvals");
}

export function recordApprovals(entityType: string, entityId: number): Promise<ApprovalRequest[]> {
  return request<ApprovalRequest[]>(`/api/approvals/${entityType}/${entityId}`);
}

// ---------------------------------------------------------------- product configuration

const enc = encodeURIComponent;

export function productDesign(offeringName: string): Promise<ProductDesign> {
  return request<ProductDesign>(`/api/catalog/configuration/${enc(offeringName)}`);
}

export function priceSelection(
  offeringName: string,
  body: { pricing_attributes?: Record<string, unknown>; opportunity_id?: number; quote_id?: number; party_id?: number; price_list?: string; quantity?: number; discount_pct?: number }
): Promise<PriceQuote> {
  return request<PriceQuote>(`/api/catalog/configuration/${enc(offeringName)}/price`, { method: "POST", body: JSON.stringify(body) });
}

export function listProductDesigns(): Promise<ProductDesignSummary[]> {
  return request<ProductDesignSummary[]>("/api/admin/product-config");
}

export function planProductDesign(offeringName: string, instruction: string): Promise<DesignPlan> {
  return request<DesignPlan>(`/api/admin/product-config/${enc(offeringName)}/plan`, { method: "POST", body: JSON.stringify({ instruction }) });
}

export function applyProductDesign(offeringName: string, ops: DesignOp[]): Promise<{ changes: string[]; design: ProductDesign }> {
  return request<{ changes: string[]; design: ProductDesign }>(`/api/admin/product-config/${enc(offeringName)}/apply`, {
    method: "POST",
    body: JSON.stringify({ ops }),
  });
}

export function suggestDesign(offeringName: string): Promise<DesignPlan> {
  return request<DesignPlan>(`/api/catalog/configuration/${enc(offeringName)}/suggest`, { method: "POST" });
}

export function draftDesign(offeringName: string, instruction: string, draft: DesignOp[] = []): Promise<DesignPlan> {
  return request<DesignPlan>(`/api/catalog/configuration/${enc(offeringName)}/draft`, {
    method: "POST",
    body: JSON.stringify({ instruction, draft }),
  });
}

// ---------------------------------------------------------------- Page Designer

export type PageFieldType = "text" | "number" | "boolean" | "date" | "picklist" | "email" | "phone" | "textarea";

/** A field on a page - or, with `kind`, a button (runs an intent or a Flow
 * Studio flow) or a text block. */
export interface PlacedField {
  key: string;
  kind?: "field" | "button" | "text";
  id?: string;
  intent?: string;
  style?: "primary" | "default" | "danger";
  text?: string;
  mandatory?: boolean;
  hidden?: boolean;
  readonly?: boolean;
  label?: string;
  help?: string;
  span?: "full";
}

export const isElement = (f: { kind?: string }) => f.kind === "button" || f.kind === "text";

export interface PageSection {
  title: string;
  columns?: number;
  fields: PlacedField[];
}

export interface PageLayout {
  sections: PageSection[];
  excluded?: string[];
  /** Only these roles get the page; empty = everyone. */
  role_ids?: number[];
  /** A Flow Studio flow (FLOW-<id>) started when the page is opened / after it is saved. */
  events?: { on_open?: string; after_save?: string };
}

export interface DesignedPage {
  page_key: string;
  entity_type: string;
  segment: string | null;
  name: string;
  description: string;
  layout: PageLayout;
  is_default: boolean;
  customized: boolean;
  is_active: boolean;
  id: number | null;
  brt_id: string | null;
  role_ids?: number[];
  events?: { on_open?: string; after_save?: string };
}

export interface CatalogField {
  key: string;
  label: string;
  data_type: PageFieldType;
  values: string[];
  section: string;
  segment: string | null;
  help: string;
  core: boolean;
  system: boolean;
  id: number | null;
  is_active: boolean;
}

export interface FormField {
  key: string;
  label: string;
  type: PageFieldType;
  values: string[];
  mandatory: boolean;
  readonly: boolean;
  help: string;
  core: boolean;
  value: unknown;
  span?: "full" | null;
  kind?: "field" | "button" | "text";
  intent?: string;
  style?: string;
  text?: string;
}

export interface RuleCondition {
  field: string;
  op: string;
  value?: unknown;
}

export interface CompiledRule {
  when?: RuleCondition[];
  require?: string[];
  check?: RuleCondition[];
  message?: string;
}

export interface PageForm {
  page_key: string;
  name: string;
  entity_type: string;
  segment: string | null;
  sections: { title: string; columns: number; fields: FormField[] }[];
  rules: { id: number; name: string; compiled: CompiledRule; message: string }[];
  events?: { on_open?: string; after_save?: string };
  is_default?: boolean;
}

export interface PageRule {
  id: number;
  name: string;
  entity_type: string;
  segment: string | null;
  kind: "simple" | "flow";
  source_text: string;
  compiled: CompiledRule;
  message: string;
  flow_id: number | null;
  flow_name: string | null;
  is_active: boolean;
  explanation: string;
  brt_id: string | null;
}

export interface PageOp {
  op: string;
  key?: string;
  label?: string;
  data_type?: string;
  values?: string[];
  section?: string | null;
  mandatory?: boolean;
  value?: boolean;
  title?: string;
  to?: string;
}

const pk = encodeURIComponent;

export function pageForm(entity: string, opts: { segment?: string | null; pageKey?: string | null; recordId?: number | null; partyId?: number | null } = {}): Promise<PageForm> {
  const q = new URLSearchParams();
  if (opts.partyId) q.set("party_id", String(opts.partyId));
  if (opts.segment) q.set("segment", opts.segment);
  if (opts.pageKey) q.set("page_key", opts.pageKey);
  if (opts.recordId) q.set("record_id", String(opts.recordId));
  return request<PageForm>(`/api/pages/${pk(entity)}/form?${q.toString()}`);
}

export function validatePage(entity: string, values: Record<string, unknown>, segment?: string | null, partial = false): Promise<{ ok: boolean; errors: string[] }> {
  return request(`/api/pages/${pk(entity)}/validate${segment ? `?segment=${pk(segment)}` : ""}`, {
    method: "POST",
    body: JSON.stringify({ values, partial }),
  });
}

export function segmentOfferings(opts: { segment?: string | null; partyId?: number | null }): Promise<string[]> {
  const q = new URLSearchParams();
  if (opts.segment) q.set("segment", opts.segment);
  if (opts.partyId) q.set("party_id", String(opts.partyId));
  return request<string[]>(`/api/pages/offerings?${q.toString()}`);
}

export function leadProducts(leadId: number): Promise<{ offering_name: string; quantity: number; notes: string | null; brt_id: string }[]> {
  return request(`/api/pages/lead/${leadId}/products`);
}

export function listPages(): Promise<{
  pages: DesignedPage[]; entities: { key: string; label: string }[]; data_types: string[]; segments: string[];
  runnable?: { code: string; label: string; flow: boolean }[]; roles?: { id: number; name: string }[]; events?: string[];
}> {
  return request("/api/admin/pages");
}

export function copyPage(pageKey: string, name: string): Promise<DesignedPage> {
  return request<DesignedPage>(`/api/admin/pages/${pk(pageKey)}/copy`, { method: "POST", body: JSON.stringify({ name }) });
}

export function setPageActive(pageKey: string, isActive: boolean): Promise<DesignedPage> {
  return request<DesignedPage>(`/api/admin/pages/${pk(pageKey)}/active`, { method: "PATCH", body: JSON.stringify({ is_active: isActive }) });
}

export function pageCatalog(entity: string, segment?: string | null): Promise<CatalogField[]> {
  return request<CatalogField[]>(`/api/admin/pages/catalog/${pk(entity)}${segment ? `?segment=${pk(segment)}` : ""}`);
}

export function savePage(pageKey: string, layout: PageLayout, name?: string, description?: string): Promise<DesignedPage> {
  return request<DesignedPage>(`/api/admin/pages/${pk(pageKey)}`, { method: "PUT", body: JSON.stringify({ layout, name, description }) });
}

export function resetPage(pageKey: string): Promise<void> {
  return request<void>(`/api/admin/pages/${pk(pageKey)}`, { method: "DELETE" });
}

export function createPage(body: { entity_type: string; segment?: string | null; name: string; description?: string; instruction?: string }): Promise<DesignedPage> {
  return request<DesignedPage>("/api/admin/pages", { method: "POST", body: JSON.stringify(body) });
}

export function planPage(pageKey: string, instruction: string): Promise<{ ops: PageOp[]; engine: string; reply: string; preview: string[] }> {
  return request(`/api/admin/pages/${pk(pageKey)}/plan`, { method: "POST", body: JSON.stringify({ instruction }) });
}

export function applyPageOps(pageKey: string, ops: PageOp[]): Promise<DesignedPage> {
  return request<DesignedPage>(`/api/admin/pages/${pk(pageKey)}/apply`, { method: "POST", body: JSON.stringify({ ops }) });
}

export function addPageField(body: { entity_type: string; label: string; key?: string; data_type: string; values?: string[]; segment?: string | null; section?: string; help?: string }): Promise<CatalogField> {
  return request<CatalogField>("/api/admin/pages/fields", { method: "POST", body: JSON.stringify(body) });
}

export function updatePageField(id: number, body: { label?: string; values?: string[]; section?: string; help?: string; segment?: string; is_active?: boolean }): Promise<CatalogField> {
  return request<CatalogField>(`/api/admin/pages/fields/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function listPageRules(entity?: string): Promise<{ rules: PageRule[]; flows: { id: number; name: string; entity_type: string | null; is_active: boolean }[]; formats: Record<string, string> }> {
  return request(`/api/admin/pages/rules/all${entity ? `?entity_type=${pk(entity)}` : ""}`);
}

export function compilePageRule(body: { entity_type: string; text: string; segment?: string | null; values?: Record<string, unknown> }): Promise<{ compiled: CompiledRule; engine: string; explanation: string; test?: string[] }> {
  return request("/api/admin/pages/rules/compile", { method: "POST", body: JSON.stringify(body) });
}

export function savePageRule(body: { entity_type: string; text?: string; segment?: string | null; name?: string; message?: string; flow_id?: number | null }, id?: number): Promise<PageRule> {
  return request<PageRule>(id ? `/api/admin/pages/rules/${id}` : "/api/admin/pages/rules", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
}

export function togglePageRule(id: number, active: boolean): Promise<PageRule> {
  return request<PageRule>(`/api/admin/pages/rules/${id}/active?active=${active}`, { method: "PATCH" });
}

export function deletePageRule(id: number): Promise<void> {
  return request<void>(`/api/admin/pages/rules/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------- BIM - BRT Integration Manager

export interface BimEntity {
  key: string;
  label: string;
  prefix: string;
  columns: { name: string; required: boolean; help: string }[];
  custom_fields: boolean;
}

export interface BimRowResult {
  row: number;
  brt_id: string | null;
  status: "inserted" | "updated" | "failed";
  errors: string[];
}

export interface BimJob {
  id: number;
  brt_id: string;
  entity: string;
  channel: "batch" | "api";
  file_name: string | null;
  status: string;
  dry_run: boolean;
  total: number;
  inserted: number;
  updated: number;
  failed: number;
  errors: { row: number; brt_id: string | null; errors: string[] }[];
  started_at: string;
  finished_at: string | null;
  interface_id: number | null;
  results?: BimRowResult[];
}

export interface BimInterface {
  id: number;
  brt_id: string;
  code: string;
  name: string;
  entity: string;
  mode: "upsert" | "insert";
  mapping: Record<string, string>;
  description: string;
  is_active: boolean;
}

export function bimEntities(): Promise<BimEntity[]> {
  return request<BimEntity[]>("/api/bim/entities");
}

export function reserveBrtIds(entity: string, count: number): Promise<{ entity: string; brt_ids: string[] }> {
  return request("/api/bim/brt-ids", { method: "POST", body: JSON.stringify({ entity, count }) });
}

export async function bimTemplate(entity: string, rows: number): Promise<string> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}/api/bim/template/${pk(entity)}?rows=${rows}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text().catch(() => "")}`);
  return res.text();
}

export function bimLoad(body: { entity?: string; interface_id?: number | null; content: string; format: "csv" | "json"; file_name?: string; dry_run?: boolean; apply_page_rules?: boolean }): Promise<BimJob> {
  return request<BimJob>("/api/bim/load", { method: "POST", body: JSON.stringify(body) });
}

/** One load's progress - a large load runs in the background (status queued / running). */
export function bimJob(id: number): Promise<BimJob> {
  return request<BimJob>(`/api/bim/jobs/${id}`);
}

export function bimJobs(): Promise<BimJob[]> {
  return request<BimJob[]>("/api/bim/jobs");
}

export function bimInterfaces(): Promise<BimInterface[]> {
  return request<BimInterface[]>("/api/bim/interfaces");
}

export function saveBimInterface(body: Omit<BimInterface, "id" | "brt_id">, id?: number): Promise<BimInterface> {
  return request<BimInterface>(id ? `/api/bim/interfaces/${id}` : "/api/bim/interfaces", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
}

export function bimRecord(code: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/bim/records/${pk(code)}`);
}

// ---------------------------------------------------------------- Product modeller (hierarchies, import/export, rules)

export interface ModelAttribute {
  id: number;
  name: string;
  kind: "pricing" | "technical";
  affects_price: boolean;
  affects_provisioning: boolean;
  component: string | null;
  component_id: number | null;
  min: number | null;
  max: number | null;
  reference_type: string | null;
  description: string | null;
  data_type: string;
  unit: string | null;
  values: string[];
  required: boolean;
  default: string | null;
  price_impacts: { value: string; type: "flat" | "percent" | "per_unit"; amount: number }[];
}

export interface ModelComponent {
  id: number;
  name: string;
  parent: string | null;
  path: string[];
  description: string;
  min_qty: number;
  max_qty: number;
  default_qty: number;
  mandatory: boolean;
  charge_type: "recurring" | "one_time" | null;
  price: number;
  affects_provisioning: boolean;
  slot?: { type: "products" | "class"; class: string | null; products: { name: string; price: number }[]; listed: string[]; default: string | null } | null;
  attributes: ModelAttribute[];
  children: ModelComponent[];
}

export interface ProductRuleOut {
  id: number;
  brt_id: string;
  offering_name: string;
  rule_type: "eligibility" | "compatibility" | "constraint" | "price";
  source_text: string;
  definition: Record<string, unknown>;
  message: string;
  is_active: boolean;
  explanation: string;
}

export interface ProductTree {
  name: string;
  segment: string;
  status: string;
  price: number;
  brt_id: string;
  attributes: ModelAttribute[];
  children: ModelComponent[];
  rules: ProductRuleOut[];
}

export interface ModelProduct {
  name: string;
  segment: string;
  status: string;
  price: number;
  version: number;
  brt_id: string;
  attributes: number;
  children: number;
  rules: number;
}

const mp = encodeURIComponent;

export function modelProducts(): Promise<ModelProduct[]> {
  return request<ModelProduct[]>("/api/admin/product-model");
}

export function modelTree(name: string): Promise<ProductTree> {
  return request<ProductTree>(`/api/admin/product-model/${mp(name)}`);
}

export function newModelProduct(body: { name: string; segment: string; price: number }): Promise<ProductTree> {
  return request<ProductTree>("/api/admin/product-model", { method: "POST", body: JSON.stringify(body) });
}

export function publishProduct(name: string): Promise<ProductTree> {
  return request<ProductTree>(`/api/admin/product-model/${mp(name)}/publish`, { method: "POST" });
}

export function saveModelComponent(name: string, body: Record<string, unknown>): Promise<{ change: string; tree: ProductTree }> {
  return request(`/api/admin/product-model/${mp(name)}/components`, { method: "POST", body: JSON.stringify(body) });
}

export function deleteModelComponent(name: string, component: string): Promise<{ change: string; tree: ProductTree }> {
  return request(`/api/admin/product-model/${mp(name)}/components/${mp(component)}`, { method: "DELETE" });
}

export function saveModelAttribute(name: string, body: Record<string, unknown>): Promise<{ change: string; tree: ProductTree }> {
  return request(`/api/admin/product-model/${mp(name)}/attributes`, { method: "POST", body: JSON.stringify(body) });
}

export function deleteModelAttribute(name: string, attribute: string): Promise<{ change: string; tree: ProductTree }> {
  return request(`/api/admin/product-model/${mp(name)}/attributes/${mp(attribute)}`, { method: "DELETE" });
}

export function importProducts(fileName: string, contentBase64: string, dryRun: boolean): Promise<{
  dry_run: boolean; preview?: string[]; operations?: number; changes?: string[]; created?: string[];
  errors: { row: string | number | null; element: string | null; error: string }[];
}> {
  return request("/api/admin/product-model/import/file", { method: "POST", body: JSON.stringify({ file_name: fileName, content_base64: contentBase64, dry_run: dryRun }) });
}

export async function exportProducts(opts: { names?: string[]; format: "xlsx" | "csv"; template?: boolean }): Promise<{ blob: Blob; fileName: string }> {
  const token = getAccessToken();
  const q = new URLSearchParams({ format: opts.format });
  if (opts.names?.length) q.set("names", opts.names.join(","));
  if (opts.template) q.set("template", "true");
  const res = await fetch(`${API_BASE}/api/admin/product-model/export/file?${q.toString()}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text().catch(() => "")}`);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const fileName = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `products.${opts.format}`;
  return { blob: await res.blob(), fileName };
}

export function compileProductRule(name: string, ruleType: string, text: string): Promise<{ definition: Record<string, unknown>; engine: string; explanation: string }> {
  return request(`/api/admin/product-model/${mp(name)}/rules/compile`, { method: "POST", body: JSON.stringify({ rule_type: ruleType, text }) });
}

export function saveProductRule(name: string, body: { rule_type: string; text: string; message?: string }): Promise<ProductRuleOut> {
  return request(`/api/admin/product-model/${mp(name)}/rules`, { method: "POST", body: JSON.stringify(body) });
}

// ---- price formulas (backend crm_core/price_formula.py)
export interface FormulaVocabulary {
  base: number;
  attributes: { name: string; values: string[]; unit: string | null; min: number | null; max: number | null; default: string | null; component: string | null }[];
  technical: string[];
  components: { name: string; max_qty: number; parent: string | null }[];
  functions: string[];
  variables: Record<string, string>;
  examples: string[];
}

export interface FormulaCheck {
  expression: string;
  label: string;
  charge: "recurring" | "one_time";
  uses: string[];
  explanation: string;
  preview: { picks: Record<string, string | number>; amount: number }[];
  amount?: number;
  engine: "formula" | "ai" | "rules";
  text: string;
}

export function formulaVocabulary(name: string): Promise<FormulaVocabulary> {
  return request(`/api/admin/product-model/${mp(name)}/price-formulas/vocabulary`);
}

export function checkFormula(name: string, body: { text: string; label?: string | null; charge?: string | null; picks?: Record<string, string>; rule_id?: number | null; use_ai?: boolean }): Promise<FormulaCheck> {
  return request(`/api/admin/product-model/${mp(name)}/price-formulas/check`, { method: "POST", body: JSON.stringify(body) });
}

export function saveFormula(name: string, body: { expression: string; label?: string | null; charge: string; described_as?: string; rule_id?: number | null }): Promise<ProductRuleOut> {
  return request(`/api/admin/product-model/${mp(name)}/price-formulas`, { method: "POST", body: JSON.stringify(body) });
}

export function toggleProductRule(id: number, active: boolean): Promise<ProductRuleOut> {
  return request(`/api/admin/product-model/rules/${id}/active?active=${active}`, { method: "PATCH" });
}

export function deleteProductRule(id: number): Promise<void> {
  return request<void>(`/api/admin/product-model/rules/${id}`, { method: "DELETE" });
}

export function customerEligibility(partyId: number, names: string[] = []): Promise<{ eligible: string[]; not_eligible: Record<string, string>; owns: string[] }> {
  return request(`/api/catalog/eligibility?party_id=${partyId}&names=${mp(names.join(","))}`);
}

// ---------------------------------------------------------------- Configurator engine + model lifecycle

export interface CfgValue { value: string; state: "selected" | "available" | "excluded"; reason?: string; price?: number }
export interface CfgVariable {
  name: string; label: string; kind: "attribute" | "component" | "slot"; component: string | null; required: boolean; unit: string | null;
  numeric: boolean; scope: string; help: string; values: CfgValue[]; picked: boolean; value?: string; range?: { min: number; max: number } | null;
}
export interface CfgResult {
  product: string; version: number | null; mode: string; picks: Record<string, string>; problems: string[];
  auto: { name: string; label: string; value: string; reason: string }[];
  conflict: { picks: { name: string; label: string; value: string }[]; rules: { id: string; label: string }[]; message: string;
              repairs: { label: string; changes: Record<string, string>; monthly: number; changes_count: number }[] } | null;
  variables: CfgVariable[]; instance: Record<string, string>; valid: boolean; complete: boolean; missing: string[]; messages: string[];
  resources: Record<string, { provided: number; used: number }>; price: { base: number; monthly: number; one_time: number; lines: { label: string; amount: number; one_time?: boolean }[]; source?: string };
  customer_errors?: string[]; proposal?: { picks: Record<string, string>; engine: string; reason: string; max_monthly: number | null }; dropped?: string[];
}
export interface CfgContext { opportunity_id?: number; quote_id?: number; party_id?: number; price_list?: string; workspace?: boolean }

const cp = encodeURIComponent;

export function configuratorSession(product: string, picks: Record<string, unknown>, ctx: CfgContext = {}, opts: { objective?: string; max_monthly?: number; signal?: AbortSignal } = {}): Promise<CfgResult> {
  return request<CfgResult>(`/api/configurator/${cp(product)}/session`, {
    method: "POST", signal: opts.signal, body: JSON.stringify({ picks, ...ctx, objective: opts.objective, max_monthly: opts.max_monthly }),
  });
}

export function configuratorAi(product: string, text: string, picks: Record<string, unknown>, ctx: CfgContext = {}): Promise<CfgResult> {
  return request<CfgResult>(`/api/configurator/${cp(product)}/ai`, { method: "POST", body: JSON.stringify({ text, picks, ...ctx }) });
}

export interface ModelTestOut { id: number; name: string; picks: Record<string, string>; expect: "valid" | "invalid"; expect_price: number | null; passed?: boolean; problems?: string[] }
export interface ModelVersionOut { id: number; version: number; status: string; valid_from: string; notes: string; current: boolean; report: Record<string, unknown> }
export interface ReleaseCheck { tests: ModelTestOut[]; qa: { level: string; text: string }[]; regression: Record<string, unknown>; blockers: string[]; warnings: string[]; ready: boolean }
export interface ProductClassOut { id: number; name: string; parent: string | null; description: string; members: string[]; all_members: string[]; attributes: ModelAttribute[] }

export function modelTests(product: string): Promise<ModelTestOut[]> {
  return request(`/api/admin/product-model/${cp(product)}/tests`);
}
export function addModelTest(product: string, body: { name: string; picks: Record<string, unknown>; expect: string; expect_price?: number | null }): Promise<ModelTestOut> {
  return request(`/api/admin/product-model/${cp(product)}/tests`, { method: "POST", body: JSON.stringify(body) });
}
export function deleteModelTest(id: number): Promise<void> {
  return request<void>(`/api/admin/product-model/tests/${id}`, { method: "DELETE" });
}
export function releaseCheck(product: string): Promise<ReleaseCheck> {
  return request(`/api/admin/product-model/${cp(product)}/release-check`);
}
export function releaseModel(product: string, body: { valid_from?: string | null; notes?: string }): Promise<ModelVersionOut> {
  return request(`/api/admin/product-model/${cp(product)}/release`, { method: "POST", body: JSON.stringify(body) });
}
export function modelVersions(product: string): Promise<ModelVersionOut[]> {
  return request(`/api/admin/product-model/${cp(product)}/versions`);
}
export function retireModelVersion(id: number): Promise<{ id: number; status: string }> {
  return request(`/api/admin/product-model/versions/${id}/retire`, { method: "POST" });
}
export function modelJson(product: string, version?: number): Promise<Record<string, unknown>> {
  return request(`/api/admin/product-model/${cp(product)}/model.json${version ? `?version=${version}` : ""}`);
}
export function loadModelJson(product: string, body: Record<string, unknown>): Promise<{ changes: string[] }> {
  return request(`/api/admin/product-model/${cp(product)}/model.json`, { method: "POST", body: JSON.stringify(body) });
}
export function setSlot(product: string, component: string, body: { domain_type: string; products?: string[]; class_name?: string | null; default?: string | null }): Promise<{ change: string; tree: ProductTree }> {
  return request(`/api/admin/product-model/${cp(product)}/components/${cp(component)}/slot`, { method: "PUT", body: JSON.stringify(body) });
}
export function productClasses(): Promise<ProductClassOut[]> {
  return request("/api/admin/product-classes");
}
export function saveProductClass(body: { name: string; parent?: string | null; description?: string; members: string[] }): Promise<ProductClassOut> {
  return request("/api/admin/product-classes", { method: "POST", body: JSON.stringify(body) });
}
export function saveClassAttribute(className: string, body: { name: string; values: string[]; affects_price: boolean; affects_provisioning: boolean; required?: boolean }): Promise<ProductClassOut> {
  return request(`/api/admin/product-classes/${cp(className)}/attributes`, { method: "POST", body: JSON.stringify(body) });
}

// ---------------------------------------------------------------- Product hub (one picture of each product)

export interface ProductPicture {
  name: string; status: string; segment: string; price: number; brt_id: string; catalog_version: number; composition: string;
  versions: { id: number; brt_id: string; version: number; status: string; price: number; segment: string; composition: string; replacement_offering_id: number | null; effective_date: string }[];
  launch_eligibility: { segment: string; channel: string; region: string }[];
  model: {
    status: "not_modelled" | "workspace" | "released" | "released_changed"; label: string; released_version: number | null; changed: boolean;
    pricing_attributes: number; technical_attributes: number; attributes: number; children: number; slots: number;
    rules: { eligibility: number; compatibility: number; constraint: number }; attribute_rules: number; tests: number; classes: string[];
  };
  prices: { price_list: string | null; price: number; source: string; default?: boolean }[];
  tree?: ProductTree;
  rules?: ProductRuleOut[];
}

export interface AttributeUsage {
  id: number; brt_id: string; name: string; data_type: string; unit: string | null; values: string[];
  products: { offering_name: string; kind: string; values: string[]; own_values: boolean; unit: string | null; required: boolean }[];
  classes: string[]; spec_values: number; composition_rules: number; unused_values: string[];
}

export function catalogProducts(): Promise<ProductPicture[]> {
  return request<ProductPicture[]>("/api/catalog/products");
}
export function catalogProduct(name: string): Promise<ProductPicture> {
  return request<ProductPicture>(`/api/catalog/products/${encodeURIComponent(name)}`);
}
export function ruleFromLaunch(name: string): Promise<ProductRuleOut> {
  return request<ProductRuleOut>(`/api/admin/product-model/${encodeURIComponent(name)}/rules/from-launch`, { method: "POST" });
}
export function attributeUsage(): Promise<AttributeUsage[]> {
  return request<AttributeUsage[]>("/api/admin/attribute-definitions/usage");
}
export function updateAttributeDefinition(id: number, body: { name?: string; unit?: string | null; values?: string[] }): Promise<{ id: number; name: string }> {
  return request(`/api/admin/attribute-definitions/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}
export function tidyAttributeDefinition(id: number): Promise<{ removed: string[] }> {
  return request(`/api/admin/attribute-definitions/${id}/tidy`, { method: "POST" });
}

// ---------------------------------------------------------------- Dashboard insights

export interface DashKpi { key: string; label: string; value: number | null; format: "money" | "count" | "pct" | "days"; delta: number | null; hint: string; lower_is_better?: boolean; delta_points?: boolean }
export interface DashAttention { title: string; count: number; severity: "critical" | "high" | "medium" | "low"; entity_type: string; intent: string | null; records: { id: number; label: string; age_days: number | null }[] }
export interface DashboardInsights {
  period_days: number; as_of: string; scope: string; kpis: DashKpi[];
  mrr_trend: { month: string; mrr: number; new: number }[];
  funnel: { label: string; count: number; conversion: number | null }[];
  pipeline_by_stage: { stage: string; label: string; count: number; value: number }[];
  revenue_by_product: { name: string; mrr: number; share: number }[];
  revenue_by_segment: { name: string; mrr: number; share: number }[];
  attention: DashAttention[];
  activity: { date: string; executed: number; failed: number; review: number }[];
  top_intents: { code: string; label: string; count: number }[];
  leaderboard: { user: string; mrr: number; orders: number; intents: number }[];
  tickets_by_severity: Record<string, number>; open_work_orders: number;
  insights: { tone: "good" | "bad" | "warn" | "info"; icon: string; text: string }[];
}

export function dashboardInsights(days = 30): Promise<DashboardInsights> {
  return request<DashboardInsights>(`/api/dashboard/insights?days=${days}`);
}

// ---------------------------------------------------------------- Customer 360 overview

export interface CustomerRow {
  id: number; name: string; brt_id: string; party_type: string; status: string; segment: string | null;
  mrr: number; services: number; open_deals: number; open_tickets: number; urgent_tickets: number; since: string;
}
export interface CustomerOverview {
  party: { id: number; name: string };
  facts: { status: string; segment: string | null; party_type: string; industry: string | null; brt_id: string; since: string; owner: string | null;
           owner_position: string | null; price_list: string | null; kyc: string | null; email: string | null; phone: string | null };
  numbers: { mrr: number; services: number; orders: number; pipeline: number; open_deals: number; open_tickets: number; balance: number | null; tenure_days: number };
  journey: { steps: { label: string; at: string | null; reached: boolean }[]; current: string };
  next_actions: { code: string; label: string; why: string; entities: Record<string, unknown> }[];
  services: { order_id: number; product: string; monthly: number; status: string; since: string; brt_id: string }[];
  timeline: { at: string; icon: string; title: string; detail: string; entity_type: string; entity_id: number }[];
}

export function customersList(q?: string): Promise<CustomerRow[]> {
  return request<CustomerRow[]>(`/api/customers${q ? `?q=${encodeURIComponent(q)}` : ""}`);
}
export function customerOverview(id: number): Promise<CustomerOverview> {
  return request<CustomerOverview>(`/api/customers/${id}/overview`);
}

// ---------------------------------------------------------------- Audit trail

export interface AuditRecordRef { entity_type: string; id: number; change: string | null; label: string }
export interface AuditItem {
  id: number; brt_id: string; at: string; code: string | null; label: string; icon: string; status: "done" | "review" | "failed" | string; raw_status: string;
  user: { id: number; name: string; email: string } | null; channel: string; confidence: number; text: string; outcome: string;
  records: AuditRecordRef[]; duration_ms: number | null; overridden: boolean;
}
export interface AuditFeed {
  total: number; items: AuditItem[];
  stats: { total: number; done: number; review: number; failed: number; automation: number | null; avg_ms: number | null };
  facets: { actions: { code: string | null; label: string; icon: string; count: number }[]; users: { id: number; name: string; count: number }[]; channels: { channel: string; count: number }[] };
}
export interface AuditStory extends AuditItem {
  description: string; completed_at: string | null; engine: string;
  understood: { key: string; label: string; value: unknown; complex: boolean }[];
  checks: { label: string; ok: boolean; value: unknown }[];
  rationale: string;
  steps: { n: number; action: string; status: string; error: string | null; detail: { key: string; label: string; value: unknown; complex: boolean }[] }[];
  events: { type: string; label: string; at: string; payload: Record<string, unknown> }[];
  override: unknown; rerun: { code: string; entities: Record<string, unknown> } | null;
  raw: { entities: unknown; context: unknown; plan: unknown };
}

export function auditFeed(f: { q?: string; status?: string; code?: string; user_id?: number; channel?: string; days?: number; record?: string; limit?: number; offset?: number }): Promise<AuditFeed> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return request<AuditFeed>(`/api/audit/feed?${p.toString()}`);
}
export function auditStory(id: number): Promise<AuditStory> {
  return request<AuditStory>(`/api/audit/${id}/story`);
}

// ---------------------------------------------------------------- inheritance (library -> class -> product) and product instances

export interface InheritedAttribute {
  name: string; kind: "pricing" | "technical"; values: string[]; library_values: string[]; class_values: string[] | null;
  source: "own" | "class" | "override"; from: string | null; narrowed: boolean; required: boolean; default: string | null;
  price_impacts: { value: string; type: string; amount: number }[];
}
export interface Inheritance { offering_name: string; class: string | null; chain: string[]; attributes: InheritedAttribute[] }
export interface ProductInstanceInfo {
  id: number; brt_id: string; context_type: "opportunity" | "quote" | "order"; context_id: number; offering_name: string;
  model_version: number | null; taken_at: string; source_instance_id: number | null; stale: boolean;
  configuration: { pricing?: Record<string, unknown>; technical?: Record<string, unknown>; quantity?: number };
  price: { configured?: number; one_time?: number }; attributes: number; components: number; inherited_from: string[];
}

export function productInheritance(name: string): Promise<Inheritance> {
  return request(`/api/admin/product-model/${encodeURIComponent(name)}/inheritance`);
}
export function bindProductClass(name: string, className: string | null): Promise<Inheritance> {
  return request(`/api/admin/product-model/${encodeURIComponent(name)}/class`, { method: "PUT", body: JSON.stringify({ class_name: className }) });
}
export function productInstances(contextType: string, contextId: number): Promise<ProductInstanceInfo[]> {
  return request(`/api/configurator/instances?context_type=${contextType}&context_id=${contextId}`);
}
export function refreshProductInstance(id: number): Promise<ProductInstanceInfo> {
  return request(`/api/configurator/instances/${id}/refresh`, { method: "POST" });
}

export interface NotOfferedProduct { name: string; reason: string; fix: "publish" | "segment" | "rule" }
export function notOfferedProducts(partyId?: number, segment?: string): Promise<NotOfferedProduct[]> {
  const p = new URLSearchParams();
  if (partyId) p.set("party_id", String(partyId));
  if (segment) p.set("segment", segment);
  return request(`/api/catalog/not-offered?${p.toString()}`);
}

// ---------------------------------------------------------------- field service
/** Field Service API (/api/field-service/*). Errors carry the server's own sentence. */
export function fsApi<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown): Promise<T> {
  return apiCall<T>(`/api/field-service${path}`, method, body);
}

/** Network Inventory API (/api/inventory/*) - see backend crm_core/inventory_service.py. */
export function invApi<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown): Promise<T> {
  return apiCall<T>(`/api/inventory${path}`, method, body);
}

/** Order Management API (/api/order-management/*, admin setup under /api/admin/order-management/*). */
export function omApi<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown, admin = false): Promise<T> {
  return apiCall<T>(`/api/${admin ? "admin/" : ""}order-management${path}`, method, body);
}

/** Communications API (/api/comms/*, admin setup under /api/admin/comms/*). */
export function commsApi<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown, admin = false): Promise<T> {
  return apiCall<T>(`/api/${admin ? "admin/" : ""}comms${path}`, method, body);
}

async function apiCall<T>(url: string, method: string, body?: unknown): Promise<T> {
  try {
    return await request<T>(url, { method, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    const text = e instanceof Error ? e.message : String(e);
    const m = text.match(/^\d{3} [^:]*: (.*)$/s);
    if (m) {
      try {
        const detail = JSON.parse(m[1]).detail;
        if (detail) throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      } catch (inner) {
        if (inner instanceof Error && !(inner instanceof SyntaxError)) throw inner;
      }
    }
    throw e;
  }
}

// ---------- Fulfillment (COM / SOM / TOM order orchestration) ----------

export function fulfillmentCatalog(): Promise<FulfillmentCatalog> {
  return request<FulfillmentCatalog>("/api/admin/fulfillment/catalog");
}

export function listFulfillmentPatterns(): Promise<FulfillmentPattern[]> {
  return request<FulfillmentPattern[]>("/api/admin/fulfillment/patterns");
}

export function createFulfillmentPattern(payload: FulfillmentPatternInput): Promise<FulfillmentPattern> {
  return request<FulfillmentPattern>("/api/admin/fulfillment/patterns", { method: "POST", body: JSON.stringify(payload) });
}

/** Drafts a pattern from a plain-English description (the tenant's model, else the built-in reader) - nothing is saved. */
export function generateFulfillmentPattern(description: string, source_pattern?: FulfillmentPatternInput): Promise<FulfillmentDraft> {
  return request<FulfillmentDraft>("/api/admin/fulfillment/patterns/generate", { method: "POST", body: JSON.stringify({ description, source_pattern }) });
}

export function createFulfillmentPatternFromTemplate(name?: string): Promise<FulfillmentPattern> {
  return request<FulfillmentPattern>("/api/admin/fulfillment/patterns/template", { method: "POST", body: JSON.stringify(name ? { name } : {}) });
}

export function updateFulfillmentPattern(id: number, payload: FulfillmentPatternInput): Promise<FulfillmentPattern> {
  return request<FulfillmentPattern>(`/api/admin/fulfillment/patterns/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function setFulfillmentPatternActive(id: number, active: boolean): Promise<FulfillmentPattern> {
  return request<FulfillmentPattern>(`/api/admin/fulfillment/patterns/${id}/${active ? "activate" : "deactivate"}`, { method: "POST" });
}

export function deleteFulfillmentPattern(id: number): Promise<void> {
  return request<void>(`/api/admin/fulfillment/patterns/${id}`, { method: "DELETE" });
}

export function previewFulfillmentPattern(id: number, orderId: number | null): Promise<FulfillmentPreview> {
  return request<FulfillmentPreview>(`/api/admin/fulfillment/patterns/${id}/preview`, { method: "POST", body: JSON.stringify(orderId ? { order_id: orderId } : {}) });
}

export function listFulfillmentOrders(status?: string): Promise<FulfillmentOrderSummary[]> {
  return request<FulfillmentOrderSummary[]>(`/api/fulfillment/orders${status ? `?status=${encodeURIComponent(status)}` : ""}`);
}

export function getFulfillmentOrder(id: number): Promise<FulfillmentOrderView> {
  return request<FulfillmentOrderView>(`/api/fulfillment/orders/${id}`);
}

export function submitOrderToFulfillment(orderId: number, patternId?: number): Promise<FulfillmentOrderView> {
  return request<FulfillmentOrderView>("/api/fulfillment/orders/submit", { method: "POST", body: JSON.stringify({ order_id: orderId, ...(patternId ? { pattern_id: patternId } : {}) }) });
}

export function resolveFulfillmentTask(taskId: number, action: "retry" | "skip" | "complete", note = "", response?: Record<string, unknown>): Promise<FulfillmentOrderView> {
  return request<FulfillmentOrderView>(`/api/fulfillment/tasks/${taskId}/resolve`, { method: "POST", body: JSON.stringify({ action, note, ...(response ? { response } : {}) }) });
}

export function postInventoryResponse(payload: { request_id: string; status: "completed" | "failed"; resources?: { type: string; identifier: string; kind?: string }[]; message?: string }): Promise<FulfillmentOrderView> {
  return request<FulfillmentOrderView>("/api/fulfillment/inventory/callback", { method: "POST", body: JSON.stringify(payload) });
}

// ---------------------------------------------------------------- waiting flow runs (Flow Studio: Wait, Wait for approval)

export interface FlowRunView {
  id: number;
  flow_id: number;
  flow_name: string;
  status: string; // waiting | completed | handled | continued | rejected | failed | cancelled
  wait: { kind: "timer"; until: string | null } | { kind: "approval"; approver: string; subject: string; expires: string | null };
  entity_type: string | null;
  entity_id: number | null;
  subject: string;
  message: string;
  decision: string | null;
  decision_note: string;
  created_at: string;
  decided_at: string | null;
  error: { code: string; message: string } | null;
}

/** Approval requests flows are waiting on me for. */
export function myFlowApprovals(): Promise<FlowRunView[]> {
  return request<FlowRunView[]>("/api/flow-approvals", BACKGROUND);
}

export function decideFlowApproval(id: number, approve: boolean, note: string): Promise<FlowRunView> {
  return request<FlowRunView>(`/api/flow-approvals/${id}/decide`, { method: "POST", body: JSON.stringify({ approve, note }) });
}

export function listFlowRuns(status = "waiting"): Promise<FlowRunView[]> {
  return request<FlowRunView[]>(`/api/admin/flow-runs?status=${encodeURIComponent(status)}`);
}

export function cancelFlowRun(id: number): Promise<FlowRunView> {
  return request<FlowRunView>(`/api/admin/flow-runs/${id}/cancel`, { method: "POST" });
}
