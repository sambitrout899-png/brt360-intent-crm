/** Number, money, date and status formatting for the Customer 360 page. */

export type Fmt = "money" | "number" | "percent" | null | undefined;

/** Two-letter avatar text for a customer name. */
export const initials = (name: string) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

export function formatValue(value: unknown, fmt: Fmt, compact = false, unit?: string): string {
  const n = typeof value === "number" ? value : Number(value);
  if (value === null || value === undefined || value === "" || Number.isNaN(n)) return value == null ? "—" : String(value);
  if (fmt === "percent") return `${n.toFixed(n % 1 === 0 ? 0 : 1)}%`;
  if (fmt === "money") {
    if (compact) {
      const abs = Math.abs(n);
      if (abs >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
      if (abs >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
      if (abs >= 1e3) return `₹${(n / 1e3).toFixed(1)}K`;
    }
    return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
  }
  const s = compact && Math.abs(n) >= 1e5 ? `${(n / 1e5).toFixed(1)} L` : n.toLocaleString("en-IN", { maximumFractionDigits: 1 });
  return unit ? `${s} ${unit}` : s;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function monthLabel(label: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(label);
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1].slice(2)}` : label;
}

export function formatCell(value: unknown, type: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (type === "money") return formatValue(value, "money");
  if (type === "percent") return formatValue(value, "percent");
  if (type === "date" && typeof value === "string") {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
  }
  return String(value).replace(/_/g, " ");
}

const GOOD = /^(paid|active|up|won|resolved|closed|in_service|approved|complete|completed|verified|healthy|ok|provisioned)$/i;
const BAD = /^(overdue|down|critical|faulty|lost|rejected|failed|cancelled|terminated|unpaid|past_due|breached)$/i;
const WARN = /^(due|degraded|high|pending|pending_approval|open|diagnosing|in_progress|scheduled|draft|suspended|pending_cancellation|medium)$/i;

export function statusTone(value: unknown): "good" | "bad" | "warn" | "neutral" {
  const s = String(value ?? "");
  if (GOOD.test(s)) return "good";
  if (BAD.test(s)) return "bad";
  if (WARN.test(s)) return "warn";
  return "neutral";
}

export const TONE_COLORS = { good: "#34d399", bad: "#f87171", warn: "#fbbf24", neutral: "" };

const PALETTE = ["#38bdf8", "#a78bfa", "#34d399", "#f59e0b", "#f472b6", "#60a5fa", "#fb7185", "#2dd4bf", "#94a3b8"];

export function seriesColor(label: string, index: number, accent: string): string {
  const tone = statusTone(label);
  if (tone !== "neutral") return TONE_COLORS[tone];
  return index === 0 ? accent : PALETTE[(index - 1) % PALETTE.length];
}
