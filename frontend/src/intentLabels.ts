/** Intent codes (INT-04, FLOW-3) are internal; people see the action's
 * name. The names are learned whenever the intent catalog is loaded, so
 * tenant renames are respected; anything still unknown reads "that action". */

const labels = new Map<string, string>();

export function rememberIntentLabels(intents: { code: string; label: string }[]): void {
  for (const i of intents) labels.set(i.code, i.label);
}

export function intentLabel(code: string | null | undefined): string {
  if (!code) return "";
  return labels.get(code) ?? (code === "ALL" ? "Every action" : "that action");
}

/** Replaces intent codes inside a message with the actions' names. */
export function humanizeCodes(text: string | null | undefined): string {
  return (text ?? "")
    .replace(/\b(INT-\d{2}(?:\/\d{2})?|FLOW-\d+)\b/g, (code) => `“${intentLabel(code.split("/")[0])}”`)
    .replace(/“([^”]+)”\s+step sequence/g, "“$1” steps");
}
