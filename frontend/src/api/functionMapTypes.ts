/** Admin -> Functional Map (backend: orchestration/function_map.py). */

export interface MapLane {
  key: string;
  label: string;
}

export interface MapNode {
  code: string;
  label: string;
  default_label: string;
  icon: string;
  lane: string;
  default_lane: string;
  description: string;
  phrases?: string[];
  examples?: string[];
  target: string | null;
  segment: string | null;
  /** false when the tenant doesn't sell to this segment (B2B / B2C) */
  served?: boolean;
  active: boolean;
  kind: "builtin" | "custom";
  flow_id?: number | null;
  in_vanilla: boolean;
  x: number;
  /** offset from the top of its lane */
  dy: number;
}

/** A link's condition: field is entities.<input>, result.<output> or
 * record.<field> of the step the link leaves; "otherwise" = when none of that
 * step's other conditions holds. */
export interface MapCondition {
  field?: string;
  operator: string;
  value?: string;
}

export interface MapEdge {
  source: string;
  target: string;
  /** next = offered as the next step; auto = runs by itself (a Flow Studio automation) */
  kind: "next" | "auto";
  why?: string;
  /** offered / run only when this holds; none = always */
  when?: MapCondition | null;
  flow_id?: number;
  flow_name?: string;
  active?: boolean;
  /** an automation drawn on the map (others are changed in Flow Studio) */
  by_map?: boolean;
}

export interface MapProcess {
  key: string;
  label: string;
  steps: string[];
}

export interface MapDifference {
  kind: string;
  code: string;
  text: string;
}

export interface FunctionMap {
  lanes: MapLane[];
  nodes: MapNode[];
  edges: MapEdge[];
  processes: MapProcess[];
  vanilla: boolean;
  saved_at?: string | null;
  saved_by?: string | null;
  customized?: boolean;
  differences?: MapDifference[];
  /** set when this is one of the tenant's versions */
  version?: MapVersion;
  /** what BRT released since the version was last saved, merged in */
  release_notes?: string[];
}

/** One of the tenant's versions of the map: drafts are worked on, the active
 * one drives the business, retired ones are kept to activate again. */
export interface MapVersion {
  id: number;
  number: number;
  name: string;
  notes: string;
  status: "draft" | "active" | "retired";
  based_on: string;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  activated_at: string | null;
  activated_by: string | null;
}

export interface MapVersions {
  versions: MapVersion[];
  active_id: number | null;
  /** no version active - the tenant runs BRT's original map */
  on_original: boolean;
}

export interface MapChange {
  kind: string;
  engine: string;
  text: string;
  warning?: string | null;
  code?: string;
  source?: string;
  target?: string;
}

export interface MapPlan {
  changes: MapChange[];
  problems: { text: string; code?: string; source?: string; target?: string }[];
  layout_only: boolean;
}

export interface MapDraft {
  nodes: Pick<MapNode, "code" | "label" | "lane" | "active" | "x" | "dy">[];
  edges: Pick<MapEdge, "source" | "target" | "kind" | "why" | "when">[];
}
