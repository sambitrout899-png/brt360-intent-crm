import { useState } from "react";
import type { FunctionMap, MapNode } from "../../api/functionMapTypes";
import type { Band } from "./geometry";

/** The map as lists: each business process with its steps in order, and each
 * business area with its function points. Click to jump there. */
export default function Navigator({
  processes,
  processColor,
  bands,
  nodes,
  process,
  selected,
  onProcess,
  onNode,
  onToggleLane,
  onAllLanes,
}: {
  processes: FunctionMap["processes"];
  processColor: Record<string, string>;
  bands: Band[];
  nodes: Record<string, MapNode>;
  process: string;
  selected?: string;
  onProcess: (key: string) => void;
  onNode: (code: string) => void;
  onToggleLane: (key: string) => void;
  onAllLanes: (collapse: boolean) => void;
}) {
  const [tab, setTab] = useState<"process" | "area">("process");
  const [open, setOpen] = useState<string | null>(null);
  const laneLabel = Object.fromEntries(bands.map((b) => [b.key, b.label]));

  const item = (n: MapNode | undefined, code: string, num?: number) => (
    <li key={`${num ?? ""}${code}`}>
      <button
        onClick={() => onNode(code)}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs transition ${
          selected === code ? "bg-sky-500/15 text-sky-200" : "text-slate-300 hover:bg-slate-800"
        } ${n && !n.active ? "opacity-50" : ""}`}
      >
        {num !== undefined && <span className="w-4 shrink-0 text-right text-[10px] font-semibold text-slate-500">{num}</span>}
        <span className="shrink-0">{n?.icon ?? "•"}</span>
        <span className="min-w-0 flex-1 truncate">{n?.label ?? code}</span>
        {num !== undefined && n && <span className="shrink-0 truncate text-[10px] text-slate-600">{laneLabel[n.lane]}</span>}
      </button>
    </li>
  );

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-900/50">
      <div className="flex gap-1 border-b border-slate-800 p-1.5 text-xs font-semibold">
        {(
          [
            ["process", "Processes"],
            ["area", "Business areas"],
          ] as const
        ).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} className={`flex-1 rounded-md px-2 py-1.5 ${tab === k ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"}`}>
            {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {tab === "process" ? (
          <ul className="space-y-0.5">
            {processes.map((p) => {
              const on = process === p.key;
              const expanded = on || open === p.key;
              return (
                <li key={p.key}>
                  <div className={`flex items-center gap-1 rounded-md ${on ? "bg-slate-800" : "hover:bg-slate-800/60"}`}>
                    <button onClick={() => setOpen(expanded && !on ? null : p.key)} className="px-1.5 py-1.5 text-[10px] text-slate-500" aria-label={expanded ? "Hide steps" : "Show steps"}>
                      {expanded ? "▾" : "▸"}
                    </button>
                    <button onClick={() => onProcess(on ? "" : p.key)} className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pr-2 text-left text-xs" title={on ? "Stop highlighting" : "Highlight this process on the map"}>
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: processColor[p.key] }} />
                      <span className={`min-w-0 flex-1 truncate ${on ? "font-semibold text-white" : "text-slate-200"}`}>{p.label}</span>
                      <span className="text-[10px] text-slate-500">{p.steps.length}</span>
                    </button>
                  </div>
                  {expanded && <ol className="mb-1 ml-3 border-l border-slate-800 pl-1">{p.steps.map((code, i) => item(nodes[code], code, i + 1))}</ol>}
                </li>
              );
            })}
            {processes.length === 0 && <li className="px-2 py-3 text-xs text-slate-500">No processes for your modules.</li>}
          </ul>
        ) : (
          <>
            <div className="mb-1 flex gap-2 px-2 text-[11px]">
              <button onClick={() => onAllLanes(true)} className="text-slate-400 hover:text-slate-200">
                Collapse all
              </button>
              <button onClick={() => onAllLanes(false)} className="text-slate-400 hover:text-slate-200">
                Expand all
              </button>
            </div>
            <ul className="space-y-0.5">
              {bands.map((b) => {
                const expanded = open === b.key;
                const members = Object.values(nodes)
                  .filter((n) => n.lane === b.key)
                  .sort((a, c) => a.label.localeCompare(c.label));
                return (
                  <li key={b.key}>
                    <div className="flex items-center gap-1 rounded-md hover:bg-slate-800/60">
                      <button onClick={() => setOpen(expanded ? null : b.key)} className="flex min-w-0 flex-1 items-center gap-2 px-1.5 py-1.5 text-left text-xs">
                        <span className="text-[10px] text-slate-500">{expanded ? "▾" : "▸"}</span>
                        <span className={`min-w-0 flex-1 truncate ${b.collapsed ? "text-slate-500" : "text-slate-200"}`}>{b.label}</span>
                        <span className="text-[10px] text-slate-500">{b.count}</span>
                      </button>
                      <button
                        onClick={() => onToggleLane(b.key)}
                        title={b.collapsed ? "Show this area on the map" : "Fold this area away on the map"}
                        className="rounded px-1.5 py-1 text-[11px] text-slate-500 hover:text-slate-200"
                      >
                        {b.collapsed ? "Show" : "Fold"}
                      </button>
                    </div>
                    {expanded && <ul className="mb-1 ml-3 border-l border-slate-800 pl-1">{members.map((n) => item(n, n.code))}</ul>}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
