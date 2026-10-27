/** Conflicts (backend: orchestration/conflicts.py) and build-from-scratch (orchestration/scratch.py). */

export interface ConflictCondition {
  field: string;
  operator: string;
  value?: unknown;
}

export interface ConflictRule {
  order: number[];
  mode: "all" | "first";
  conditions: Record<string, ConflictCondition>;
  by?: string | null;
  at?: string;
}

export interface ConflictMember {
  type: "flow" | "action";
  id: number;
  name: string;
  code?: string;
  entity_type?: string | null;
  condition?: ConflictCondition | null;
}

export interface Conflict {
  key: string;
  /** automations: flows on one trigger · actions: custom actions on one phrase · steps: steps of this plan */
  type: "automations" | "actions" | "steps";
  severity: "high" | "medium" | "low";
  resolved: boolean;
  title: string;
  trigger?: string;
  reasons: string[];
  kinds: string[];
  members?: ConflictMember[];
  rule?: ConflictRule | null;
  ask: string;
  /** plan-step conflicts: the steps involved and the one to drop */
  steps?: number[];
  drop?: number;
  flow_id?: number;
}

export interface ConflictList {
  conflicts: Conflict[];
  open: number;
  rules: Record<string, ConflictRule>;
  operators: string[];
}

export interface ScratchField {
  key: string;
  label: string;
  type: string;
  required: boolean;
  values: string[];
  source: string;
  placeholder: string;
  help: string;
}

export interface ScratchDraft {
  request: string;
  label: string;
  description: string;
  icon: string;
  category: string;
  entity_type: string | null;
  phrases: string[];
  fields: ScratchField[];
  graph: Record<string, unknown>;
  source: string;
  steps: string[];
  warnings: string[];
  creates: string[];
}
