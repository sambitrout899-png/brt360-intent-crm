import { API_BASE, getAccessToken, request } from "../../api/client";

/* ---------- shared shapes (training module, BRD v1.0) ---------- */

export type QuestionType = "single" | "multi" | "true_false" | "likert5" | "rating10" | "short_text" | "long_text" | "nps";
export type Measure = "knowledge" | "confidence" | "outcome" | "feedback";

export type Question = {
  item_key: string;
  section?: string;
  type: QuestionType;
  text: string;
  options: string[];
  correct_options: number[];
  points: number;
  measure: Measure;
  objective: string | null;
  required: boolean;
  help?: string;
};

export type Template = {
  id: number;
  type: "pre" | "post" | "feedback";
  name: string;
  version_label: string;
  family_id: number | null;
  state: "draft" | "published";
  settings: { randomize_questions?: boolean; randomize_options?: boolean; time_limit_min?: number | null; one_attempt?: boolean };
  sections: { key: string; title: string }[];
  questions: Question[];
  in_use?: boolean;
};

export type Module = { title: string; objectives: string[]; duration_min: number; materials: { label: string; url: string }[]; notes: string };
export type Objective = { code: string; text: string };

export type Version = {
  id: number;
  program_id: number;
  version: number;
  version_label: string;
  state: "draft" | "published" | "retired";
  modules: Module[] | null;
  curriculum: string[];
  objectives: Objective[] | null;
  duration_hours: number | null;
  pre_template_id: number | null;
  post_template_id: number | null;
  feedback_template_id: number | null;
  assessments_waived: boolean | null;
  waive_reason: string | null;
  required_certification_id: number | null;
  published_at: string | null;
  sessions: number;
};

export type Program = {
  id: number;
  name: string;
  code: string | null;
  description: string;
  category: string | null;
  audience: string | null;
  delivery_mode: string | null;
  duration_hours: number | null;
  objectives: Objective[] | null;
  status: string | null;
  required_certification_id: number | null;
  versions: Version[];
};

export type Certification = { id: number; name: string; issuer: string; validity_months: number | null; program_ids: number[] };
export type Catalog = { programs: Program[]; templates?: Template[]; certifications?: Certification[] };

export type Location = { id: number; name: string; code: string; address: string; timezone: string; status: string };

export type Me = {
  roles: string[];
  primary: string;
  can: Record<string, string>;
  registrations: { registration_id: number; session_id: number; title: string; starts_at: string; status: string; session_status: string }[];
  scores: { session: string; phase: string; knowledge: number | null; confidence: number | null }[];
  messages: { id: number; event: string; subject: string; body: string; sent_at: string | null }[];
};

export type CalendarItem = {
  id: number;
  title: string;
  status: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  start_local: string;
  location: string;
  virtual: boolean;
  program: string;
  program_id: number | null;
  version: string;
  facilitator_ids: number[];
  capacity: number;
  registered: number;
};

export type Person = { kind: "user" | "participant"; id: number; name: string; email: string; phone: string; organization?: string; erased?: boolean; email_invalid?: boolean };

export type SessionDetail = {
  id: number;
  title: string;
  status: string;
  status_reason: string | null;
  revision_id: number;
  program: string;
  version: string;
  starts_at: string;
  ends_at: string;
  start_local: string;
  end_local: string;
  timezone: string | null;
  location: string;
  location_id: number | null;
  virtual_url: string | null;
  room: string | null;
  capacity: number;
  registration_deadline: string | null;
  waitlist_enabled: boolean | null;
  feedback_mode: string | null;
  feedback_locked: boolean;
  notes: string | null;
  settings: Record<string, unknown> | null;
  modules: Partial<Module>[];
  facilitators: { id: number; name: string; lead: boolean }[];
  instances: { type: string; opens_at: string | null; closes_at: string | null; anonymous: boolean; status: string }[];
  staff: boolean;
  checkin_open: boolean;
  checkin_code?: string;
  checkin_url?: string;
  counts?: Record<string, number>;
  registrations?: {
    id: number;
    status: string;
    source: string | null;
    waitlist_position: number | null;
    person: Person;
    assessments: Record<string, { submitted: boolean; started: boolean }>;
  }[];
  mine: { id: number; status: string } | null;
};

export type Dashboard = {
  session: { id: number; title: string; status: string };
  template_based: boolean;
  note?: string;
  funnel: { invited: number; pre_done: number; attended: number; post_done: number };
  paired_n: number;
  post_only: number;
  late_pre_excluded: number;
  aggregate_only: boolean;
  pass_mark: number;
  knowledge?: { pre: { mean: number | null; n: number }; post: { mean: number | null; n: number }; change: number | null };
  confidence?: { pre: { mean: number | null; n: number }; post: { mean: number | null; n: number }; change: number | null };
  gain?: { mean: number | null; bands: Record<string, number> };
  objectives?: {
    code: string;
    text: string;
    measured: boolean;
    status: string | null;
    pre: number | null;
    post: number | null;
    change: number | null;
    confidence_pre: number | null;
    confidence_post: number | null;
    achieved: number | null;
    achieved_of: number | null;
  }[];
  outcome_rate?: number | null;
  significance?: Record<string, { test: string; n: number; p_value: number; cohens_d: number | null; label: string; significant: boolean } | null>;
  flags?: { flag: string; objective: string | null; detail: string }[];
  feedback?: {
    anonymous: boolean;
    n: number;
    suppressed: boolean;
    message?: string;
    rating?: number | null;
    questions?: { item_key: string; text: string; type: string; n: number; mean?: number | null; nps?: number; distribution?: { label: string; count: number }[]; comments?: string[] }[];
  } | null;
  participants?: { name: string; k_pre: number | null; k_post: number | null; dk: number | null; c_pre: number | null; c_post: number | null; dc: number | null; g: number | null; band: string | null }[];
};

export type CompareGroup = {
  key: string;
  label: string;
  n: number;
  sessions: number;
  knowledge_pre: number | null;
  knowledge_post: number | null;
  knowledge_change: number | null;
  gain: number | null;
  confidence_pre: number | null;
  confidence_post: number | null;
  confidence_change: number | null;
  small_group: boolean;
};

export type DirectoryUser = {
  id: number;
  name: string;
  email: string;
  active: boolean;
  roles: string[];
  department_id: number | null;
  location: string;
  phone: string;
  scope_location_ids: number[];
  scope_department_ids: number[];
  profile: Record<string, unknown>;
};
export type DirectoryParticipant = {
  id: number;
  name: string;
  email: string;
  phone: string;
  employee_id: string;
  organization: string;
  status: string;
  email_invalid: boolean;
  unsubscribed: boolean;
  erased_at: string | null;
};

export type Facilitator = {
  id: number;
  name: string;
  email: string;
  profile: Record<string, unknown>;
  certificates: { id: number; name: string; issuer: string | null; expires_at: string; status: string; certification_id: number | null; evidence_url: string | null }[];
};

export type Scorecard = {
  facilitator: { id: number; name: string };
  sessions_delivered: number;
  sessions_assigned: number;
  average_feedback: number | null;
  feedback_n: number;
  average_knowledge_gain: number | null;
  latest_qa_rating: number | null;
  certifications: { id: number; name: string; expires_at: string; status: string }[];
  below_threshold: boolean;
};

export type NotifyLog = { id: number; event: string; channel: string; recipient: string; scheduled_at: string; sent_at: string | null; status: string; attempts: number; reason: string | null; registration_id: number | null };
export type NotifyTemplate = { event: string; subject: string; body_text: string; body_html: string; custom: boolean };
export type AuditRow = { at: string; actor: string; action: string; entity: string; entity_id: string; before: unknown; after: unknown; ip: string };
export type ImportReport = { rows: { row: number; email?: string; ok: boolean; errors?: string[]; status?: string }[]; valid?: number; invalid?: number; created?: number; registered?: number; committed?: boolean };

export type Overview = {
  roles: string[];
  pages: string[];
  alerts: { page: string; tone: "warn" | "info"; text: string; session_id?: number; link?: string | null }[];
  sessions: {
    next_7_days: number;
    in_progress: number;
    drafts: number;
    awaiting_attendance: number;
    waitlisted: number;
    seats_filled: number | null;
    i_facilitate: number;
    upcoming: { id: number; title: string; starts_at: string; status: string; capacity: number; registered: number; facilitating: boolean }[];
  };
  mine: { upcoming: number; completed: number; open_assessments: { session: string; phase: string; closes_at: string; link: string | null }[] };
  programs?: { programs: number; published: number; drafts: number; retired: number };
  assessments?: { templates: number; drafts: number; by_type: Record<string, number>; questions: number };
  people?: { users: number; roles: Record<string, number>; participants: number; erased: number; bounced: number; unsubscribed: number };
  facilitators?: { facilitators: number; valid: number; expiring: number; expired: number; revoked: number };
  outcomes?: { paired_participants: number; sessions_with_results: number; knowledge_change: number | null; confidence_change: number | null; gain: number | null };
  messages?: { sent_7d: number; opened_7d: number; planned: number; failed: number; skipped_7d: number; recent_failures: { event: string; recipient: string; error: string | null; session_id: number | null }[] };
};

export type Insights = {
  activity: { week: string; sessions: number; registered: number; attended: number; future: boolean }[];
  session_status: Record<string, number>;
  registration_status: Record<string, number>;
  funnel: { step: string; value: number }[];
  completion: Record<"pre" | "post" | "feedback", { invited: number; done: number; rate: number | null }>;
  attendance_rate: number | null;
  targets: { pre: number; post: number };
  sources: Record<string, number>;
  analyst: boolean;
  impact?: {
    n: number;
    knowledge: { pre: number | null; post: number | null };
    confidence: { pre: number | null; post: number | null };
    gain: number | null;
    bands: Record<"Decline" | "Low" | "Medium" | "High", number>;
    pass_mark: number;
  };
  programs?: { program: string; n: number; knowledge_change: number | null; gain: number | null }[];
  feedback?: { rating: number | null; n: number; distribution: number[] };
  facilitators?: { name: string; sessions: number; knowledge_change: number | null; rating: number | null }[];
  certificates?: Record<"Valid" | "Expiring" | "Expired" | "Revoked", number>;
  messages?: { day: string; sent: number; skipped: number; failed: number }[];
};

/* ---------- calls ---------- */

const T = "/api/training";
const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

export const tr = {
  me: () => request<Me>(`${T}/me`),
  overview: () => request<Overview>(`${T}/overview`),
  insights: () => request<Insights>(`${T}/insights`),
  settings: () => request<{ version: number; settings: Record<string, unknown> }>(`${T}/settings`),
  saveSettings: (settings: Record<string, unknown>) => request<{ version: number; settings: Record<string, unknown> }>(`${T}/settings`, json("PUT", { settings })),
  locations: () => request<Location[]>(`${T}/locations`),
  addLocation: (body: Partial<Location>) => request<Location>(`${T}/locations`, json("POST", body)),
  editLocation: (id: number, body: Partial<Location>) => request<Location>(`${T}/locations/${id}`, json("PATCH", body)),
  people: () => request<{ users: DirectoryUser[]; participants: DirectoryParticipant[] }>(`${T}/people`),
  addPerson: (body: Record<string, string>) => request<DirectoryParticipant>(`${T}/people`, json("POST", body)),
  importPeople: (csv: string, commit: boolean) => request<ImportReport>(`${T}/people/import`, json("POST", { csv, commit })),
  erasePerson: (id: number) => request<DirectoryParticipant>(`${T}/people/${id}/erase`, json("POST")),
  setAccess: (userId: number, body: Record<string, unknown>) => request(`${T}/members/${userId}/access`, json("PUT", body)),
  catalog: () => request<Catalog>(`${T}/catalog`),
  addProgram: (body: Record<string, unknown>) => request<{ program: Program; version: Version }>(`${T}/catalog/programs`, json("POST", body)),
  editProgram: (id: number, body: Record<string, unknown>) => request<Program>(`${T}/catalog/programs/${id}`, json("PATCH", body)),
  newVersion: (programId: number, baseId: number | null, bump: "minor" | "major") =>
    request<Version>(`${T}/catalog/programs/${programId}/versions`, json("POST", { base_id: baseId, bump })),
  editVersion: (id: number, body: Record<string, unknown>) => request<Version>(`${T}/versions/${id}`, json("PATCH", body)),
  checkVersion: (id: number) => request<{ problems: string[] }>(`${T}/versions/${id}/check`),
  publishVersion: (id: number) => request<Version>(`${T}/versions/${id}/publish`, json("POST")),
  retireVersion: (id: number) => request<Version>(`${T}/versions/${id}/retire`, json("POST")),
  diff: (a: number, b: number) =>
    request<{ from: string; to: string; added: string[]; removed: string[]; changed: { title: string; fields: string[] }[]; objectives: { added: string[]; removed: string[]; changed: string[] } }>(
      `${T}/versions/${a}/diff/${b}`,
    ),
  template: (id: number) => request<Template>(`${T}/templates/${id}`),
  saveTemplate: (body: Partial<Template>, id?: number) => request<Template>(id ? `${T}/templates/${id}` : `${T}/templates`, json(id ? "PATCH" : "POST", body)),
  cloneTemplate: (id: number) => request<Template>(`${T}/templates/${id}/clone`, json("POST")),
  publishTemplate: (id: number) => request<Template>(`${T}/templates/${id}/publish`, json("POST")),
  addCertification: (body: Record<string, unknown>) => request<Certification>(`${T}/certifications`, json("POST", body)),
  addFacilitatorCert: (body: Record<string, unknown>) => request(`${T}/facilitator-certificates`, json("POST", body)),
  revokeCert: (id: number, reason: string) => request(`${T}/facilitator-certificates/${id}/revoke`, json("POST", { reason })),
  facilitators: () => request<Facilitator[]>(`${T}/facilitators`),
  scorecard: (id: number) => request<Scorecard>(`${T}/facilitators/${id}/scorecard`),
  qaReviews: (facilitatorId?: number) =>
    request<{ rubric: string[]; reviews: { id: number; session_id: number; facilitator_id: number; overall_rating: number; scores: Record<string, number>; comments: string; actions: string; review_date: string }[] }>(
      `${T}/qa-reviews${facilitatorId ? `?facilitator_id=${facilitatorId}` : ""}`,
    ),
  addQaReview: (body: Record<string, unknown>) => request(`${T}/qa-reviews`, json("POST", body)),
  calendar: (q: Record<string, string | number | undefined>) => {
    const qs = Object.entries(q)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join("&");
    return request<CalendarItem[]>(`${T}/calendar${qs ? `?${qs}` : ""}`);
  },
  schedule: (body: Record<string, unknown>) => request<{ id: number }>(`${T}/schedule`, json("POST", body)),
  session: (id: number) => request<SessionDetail>(`${T}/sessions/${id}/detail`),
  editSession: (id: number, body: Record<string, unknown>) => request(`${T}/sessions/${id}`, json("PATCH", body)),
  transition: (id: number, to: string, reason = "") => request(`${T}/sessions/${id}/transition`, json("POST", { to, reason })),
  enrol: (id: number, body: Record<string, unknown>) => request<{ id: number; status: string }>(`${T}/sessions/${id}/enrol`, json("POST", body)),
  enrolBulk: (id: number, csv: string) => request<ImportReport>(`${T}/sessions/${id}/enrol/bulk`, json("POST", { csv })),
  confirm: (registrationId: number) => request(`${T}/registrations/${registrationId}/confirm`, json("POST")),
  cancelRegistration: (registrationId: number) => request(`${T}/registrations/${registrationId}/cancel`, json("POST")),
  mark: (id: number, items: { registration_id: number; status: string }[]) => request(`${T}/sessions/${id}/attendance`, json("POST", { items })),
  markUpload: (id: number, csv: string) => request<ImportReport>(`${T}/sessions/${id}/attendance/upload`, json("POST", { csv })),
  regenerateQr: (id: number) => request<{ checkin_code: string }>(`${T}/sessions/${id}/qr/regenerate`, json("POST")),
  dashboard: (id: number) => request<Dashboard>(`${T}/analytics/sessions/${id}`),
  recompute: (id: number) => request(`${T}/analytics/sessions/${id}/recompute`, json("POST")),
  compare: (groupBy: string, start?: string, end?: string) =>
    request<{ group_by: string; groups: CompareGroup[]; hidden_small_groups: number; threshold: number }>(
      `${T}/analytics/compare?group_by=${groupBy}${start ? `&start=${encodeURIComponent(start)}` : ""}${end ? `&end=${encodeURIComponent(end)}` : ""}`,
    ),
  notifyTemplates: () => request<{ fields: string[]; events: NotifyTemplate[] }>(`${T}/notification-templates`),
  saveNotifyTemplate: (event: string, body: Partial<NotifyTemplate>) => request<NotifyTemplate>(`${T}/notification-templates/${event}`, json("PUT", body)),
  previewNotify: (event: string, body: Partial<NotifyTemplate>) =>
    request<{ subject: string; body_text: string; body_html: string }>(`${T}/notification-templates/${event}/preview`, json("POST", body)),
  notifyLog: (id: number) => request<NotifyLog[]>(`${T}/sessions/${id}/notifications`),
  resend: (registrationIds: number[], phase: string, regenerate: boolean) =>
    request<{ resent: { registration_id: number; status: string }[] }>(`${T}/notifications/resend`, json("POST", { registration_ids: registrationIds, phase, regenerate })),
  tick: () => request<{ transitions: number; sent: number; purged: number }>(`${T}/automation/tick`, json("POST")),
  audit: () => request<AuditRow[]>(`${T}/audit-log`),
};

/** Downloads a protected file (export / audit CSV / QR) with the session's token. */
export async function download(path: string, filename: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text().catch(() => "")}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A protected image as an object URL (for the session QR). */
export async function imageUrl(path: string): Promise<string> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}

/* ---------- public participant calls (no login) ---------- */

async function pub<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api/p${path}`, { credentials: "include", headers: { "Content-Type": "application/json" }, ...init });
  const text = await res.text();
  const body = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail = body?.detail;
    const err = new Error(typeof detail === "string" ? detail : detail?.message ?? `${res.status} ${res.statusText}`) as Error & {
      fields?: Record<string, string>;
      status?: number;
    };
    err.fields = typeof detail === "object" ? detail?.fields : undefined;
    err.status = res.status;
    throw err;
  }
  return body as T;
}

export type PlayerQuestion = {
  item_key: string;
  section: string;
  type: QuestionType;
  text: string;
  options: { i: number; label: string }[];
  required: boolean;
  help: string;
  measure: Measure;
};
export type PlayerForm = {
  status: "open" | "closed" | "submitted" | "not_yet_open";
  session: string;
  type: "pre" | "post" | "feedback";
  anonymous: boolean;
  opens_at: string | null;
  closes_at: string | null;
  name?: string;
  sections?: { key: string; title: string }[];
  questions?: PlayerQuestion[];
  draft?: Record<string, unknown> | null;
  consent_needed?: boolean;
  scores?: Record<string, { knowledge: number | null; confidence: number | null }> | null;
};
export type Landing = {
  session: string;
  starts_at: string;
  status: string;
  checkin_open: boolean;
  phases: { type: string; anonymous: boolean }[];
  anonymous_form?: { name: string; questions: PlayerQuestion[] };
};

export const pubApi = {
  form: (token: string) => pub<PlayerForm>(`/a/${encodeURIComponent(token)}`),
  save: (token: string, answers: Record<string, unknown>) => pub<{ saved: boolean }>(`/a/${encodeURIComponent(token)}/answers`, json("PUT", { answers })),
  submit: (token: string, answers: Record<string, unknown>, consent: boolean) =>
    pub<{ submitted: boolean; already?: boolean; anonymous?: boolean; scores?: PlayerForm["scores"] }>(`/a/${encodeURIComponent(token)}/submit`, json("POST", { answers, consent })),
  landing: (code: string) => pub<Landing>(`/q/${encodeURIComponent(code)}`),
  otp: (code: string, email: string) => pub<{ sent: boolean; message: string }>(`/q/${encodeURIComponent(code)}/otp`, json("POST", { email })),
  identify: (code: string, body: { email: string; otp?: string; phone_last4?: string }) =>
    pub<{ token: string; type: string }>(`/q/${encodeURIComponent(code)}/identify`, json("POST", body)),
  checkin: (code: string, body: { email: string; otp?: string; phone_last4?: string }) =>
    pub<{ checked_in: boolean; session: string; late: boolean }>(`/checkin/${encodeURIComponent(code)}`, json("POST", body)),
  anonymous: (code: string, answers: Record<string, unknown>) => pub<{ submitted: boolean; already?: boolean }>(`/q/${encodeURIComponent(code)}/anonymous`, json("POST", { answers })),
  registration: (code: string) => pub<{ session: string; starts_at: string; start_local: string | null; open: boolean }>(`/register/${encodeURIComponent(code)}`),
  register: (code: string, body: Record<string, unknown>) => pub<{ status: string; waitlist_position: number | null }>(`/register/${encodeURIComponent(code)}`, json("POST", body)),
  unsubscribe: (token: string) => pub<{ unsubscribed: boolean }>(`/u/${encodeURIComponent(token)}`, json("POST")),
};

export const fmt = (iso: string | null | undefined) => (iso ? new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).toLocaleString() : "—");
export const num = (v: number | null | undefined, suffix = "") => (v === null || v === undefined ? "—" : `${v}${suffix}`);
