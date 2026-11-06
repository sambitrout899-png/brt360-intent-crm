/** A mapping as a person writes it, one line per field:
 *
 *    CustomerName => name
 *    customer.mail => email | lower
 *    kind => segment | map:C=B2C,E=B2B | default:B2B
 *    = source | value:SAP                  (a fixed value)
 *    gstin => tax_id | required
 *
 * Plain renames stay the simple {source: target} form; anything more becomes a list of rules (backend app/hub/mapping.py).
 */

export interface MappingRule {
  source?: string;
  target?: string;
  transform?: string;
  default?: string;
  value?: string;
  map?: Record<string, string>;
  required?: boolean;
}

export type Mapping = Record<string, string> | MappingRule[];

const TRANSFORMS = new Set(["trim", "upper", "lower", "title", "digits", "number", "integer", "boolean", "date", "string"]);

export function parseMapping(text: string): Mapping {
  const rules: MappingRule[] = [];
  let simple = true;
  text.split("\n").forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const [head, ...options] = line.split("|").map((s) => s.trim());
    const fixed = head.match(/^=\s*(.+)$/);
    const pair = head.split(/=>|->/).map((s) => s.trim());
    if (!fixed && pair.length !== 2) throw new Error(`Line ${index + 1}: write it as source => target`);
    const rule: MappingRule = fixed ? { target: fixed[1] } : { source: pair[0], target: pair[1] };
    if (!rule.target) throw new Error(`Line ${index + 1}: which field does it go to?`);
    for (const option of options) {
      const [word, ...rest] = option.split(":");
      const value = rest.join(":").trim();
      if (TRANSFORMS.has(word)) rule.transform = word;
      else if (word === "required") rule.required = true;
      else if (word === "default") rule.default = value;
      else if (word === "value") rule.value = value;
      else if (word === "map") rule.map = Object.fromEntries(value.split(",").map((p) => p.split("=").map((s) => s.trim()) as [string, string]));
      else throw new Error(`Line ${index + 1}: "${option}" - use a transform (${[...TRANSFORMS].join(", ")}), required, default:, value: or map:`);
    }
    if (options.length || fixed || /[.[]/.test(pair[0] ?? "")) simple = false;
    rules.push(rule);
  });
  return simple ? Object.fromEntries(rules.map((r) => [r.source as string, r.target as string])) : rules;
}

export function mappingText(mapping: Mapping | null | undefined): string {
  if (!mapping) return "";
  if (!Array.isArray(mapping)) return Object.entries(mapping).map(([k, v]) => `${k} => ${v}`).join("\n");
  return mapping
    .map((r) => {
      const parts = [r.source ? `${r.source} => ${r.target ?? r.source}` : `= ${r.target}`];
      if (r.transform) parts.push(r.transform);
      if (r.map) parts.push(`map:${Object.entries(r.map).map(([k, v]) => `${k}=${v}`).join(",")}`);
      if (r.default) parts.push(`default:${r.default}`);
      if (r.value !== undefined) parts.push(`value:${r.value}`);
      if (r.required) parts.push("required");
      return parts.join(" | ");
    })
    .join("\n");
}

export function mappedCount(mapping: Mapping | null | undefined): number {
  return !mapping ? 0 : Array.isArray(mapping) ? mapping.length : Object.keys(mapping).length;
}
