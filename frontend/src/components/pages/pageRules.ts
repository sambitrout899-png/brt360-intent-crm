import type { CompiledRule, PageForm, RuleCondition } from "../../api/client";

/** The same checks the server runs on save (crm_core/page_service.py), so
 * the form can say what's wrong while you type - the server still decides. */

const FORMATS: Record<string, [RegExp, string]> = {
  gstin: [/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/, "a valid 15-character GSTIN"],
  pan: [/^[A-Z]{5}[0-9]{4}[A-Z]$/, "a valid PAN (e.g. ABCDE1234F)"],
  cin: [/^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/, "a valid 21-character CIN"],
  pincode: [/^[1-9][0-9]{5}$/, "a valid 6-digit PIN code"],
  mobile: [/^(\+91[ -]?)?[6-9][0-9]{9}$/, "a valid 10-digit mobile number"],
  aadhaar: [/^[2-9][0-9]{3} ?[0-9]{4} ?[0-9]{4}$/, "a valid 12-digit Aadhaar number"],
  email: [/^[^@\s]+@[^@\s]+\.[^@\s]+$/i, "a valid email address"],
  website: [/^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(\/.*)?$/i, "a valid website address"],
  ifsc: [/^[A-Z]{4}0[A-Z0-9]{6}$/, "a valid IFSC code"],
};

export const isBlank = (v: unknown) => v === null || v === undefined || (typeof v === "string" && !v.trim()) || (Array.isArray(v) && v.length === 0);

const num = (v: unknown): number | null => {
  const n = Number(String(v ?? "").replace(/[,₹\s]/g, ""));
  return String(v ?? "").trim() !== "" && Number.isFinite(n) ? n : null;
};

const date = (v: unknown): number | null => {
  const t = Date.parse(String(v ?? "").slice(0, 10));
  return Number.isNaN(t) ? null : t;
};

/** true / false, or null when the field is blank. */
export function test(cond: RuleCondition, values: Record<string, unknown>): boolean | null {
  const actual = values[cond.field];
  const { op, value } = cond;
  if (op === "empty") return isBlank(actual);
  if (op === "not_empty") return !isBlank(actual);
  if (isBlank(actual)) return null;
  const text = String(actual).trim();
  if (["gt", "gte", "lt", "lte"].includes(op)) {
    let a = num(actual);
    let b = num(value);
    if (a === null || b === null) {
      a = date(actual);
      b = date(value);
      if (a === null || b === null) return false;
    }
    return op === "gt" ? a > b : op === "gte" ? a >= b : op === "lt" ? a < b : a <= b;
  }
  if (op === "in" || op === "not_in") {
    const options = (Array.isArray(value) ? value : String(value ?? "").split(",")).map((o) => String(o).trim().toLowerCase());
    const inside = options.includes(text.toLowerCase());
    return op === "in" ? inside : !inside;
  }
  if (op === "matches") {
    const named = FORMATS[String(value).toLowerCase()];
    try {
      return (named ? named[0] : new RegExp(String(value))).test(text);
    } catch {
      return false;
    }
  }
  if (op === "length") {
    const [lo, hi] = Array.isArray(value) ? value : [value, value];
    const n = text.replace(/\s/g, "").length;
    return (num(lo) === null || n >= (num(lo) as number)) && (num(hi) === null || n <= (num(hi) as number));
  }
  if (op === "before_today" || op === "after_today") {
    const d = date(actual);
    const today = new Date(new Date().toISOString().slice(0, 10)).getTime();
    return d !== null && (op === "before_today" ? d < today : d > today);
  }
  const a = num(actual);
  const b = num(value);
  const equal = a !== null && b !== null ? a === b : text.toLowerCase() === String(value ?? "").trim().toLowerCase();
  return op === "eq" ? equal : !equal;
}

function describe(cond: RuleCondition, labels: Record<string, string>): string {
  const label = labels[cond.field] ?? cond.field;
  if (cond.op === "matches") {
    const digits = /^\^\[0-9\]\{(\d+)\}\$$/.exec(String(cond.value));
    if (digits) return `${label} must be ${digits[1]} digits`;
    return `${label} must be ${FORMATS[String(cond.value).toLowerCase()]?.[1] ?? `in the format ${cond.value}`}`;
  }
  if (cond.op === "before_today") return `${label} must be in the past`;
  if (cond.op === "after_today") return `${label} must be in the future`;
  if (cond.op === "length") return `${label} has the wrong length`;
  const words: Record<string, string> = { gt: "more than", gte: "at least", lt: "less than", lte: "at most", in: "one of", not_in: "none of", ne: "other than", eq: "" };
  const shown = Array.isArray(cond.value) ? cond.value.join(", ") : String(cond.value ?? "");
  return `${label} must be ${words[cond.op] ?? cond.op} ${shown}`.replace(/\s+/g, " ").trim();
}

export function evaluate(rule: CompiledRule, values: Record<string, unknown>, labels: Record<string, string>): string[] {
  if (!(rule.when ?? []).every((c) => test(c, values))) return [];
  const out: string[] = [];
  for (const key of rule.require ?? []) if (isBlank(values[key])) out.push(rule.message || `${labels[key] ?? key} is mandatory here`);
  for (const c of rule.check ?? []) if (test(c, values) === false) out.push(rule.message || describe(c, labels));
  return [...new Set(out)];
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE = /^\+?[0-9][0-9 ()-]{5,18}[0-9]$/;

/** Field-by-field problems: mandatory, type and rule errors keyed by field ("" = the form). */
export function checkForm(form: PageForm, values: Record<string, unknown>): Record<string, string[]> {
  const labels: Record<string, string> = {};
  const problems: Record<string, string[]> = {};
  const add = (key: string, msg: string) => (problems[key] = [...(problems[key] ?? []), msg]);
  for (const s of form.sections)
    for (const f of s.fields) {
      labels[f.key] = f.label;
      const v = values[f.key];
      if (f.mandatory && isBlank(v)) add(f.key, "Mandatory");
      else if (!isBlank(v)) {
        if (f.type === "email" && !EMAIL.test(String(v).trim())) add(f.key, "Not an email address");
        if (f.type === "phone" && !PHONE.test(String(v).trim())) add(f.key, "Not a phone number");
        if (f.type === "number" && num(v) === null) add(f.key, "Must be a number");
      }
    }
  for (const r of form.rules) {
    for (const msg of evaluate(r.compiled, values, labels)) {
      const target = (r.compiled.require ?? []).find((k) => msg.startsWith(labels[k] ?? k)) ?? (r.compiled.check ?? []).find((c) => msg.startsWith(labels[c.field] ?? c.field))?.field ?? "";
      add(target, msg);
    }
  }
  return problems;
}
