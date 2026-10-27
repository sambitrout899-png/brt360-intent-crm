import { useId, useState } from "react";
import type { MapNode } from "../../api/functionMapTypes";

import { matchesFunctionPoint, normalize } from "./search";

export default function FunctionPointPicker({ nodes, value, onChange }: {
  nodes: MapNode[];
  value: string;
  onChange: (code: string) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  const [active, setActive] = useState(0);
  const selected = nodes.find((node) => node.code === value);
  const results = nodes.filter((node) => (filter === "all" || node.kind === filter) && matchesFunctionPoint(node, query));
  const choose = (node: MapNode) => {
    onChange(node.code);
    setOpen(false);
    setQuery("");
  };
  return (
    <div className="mt-1" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    }}>
      <input
        role="combobox" aria-label="Search intents and flows" aria-expanded={open}
        aria-controls={`${id}-results`} aria-autocomplete="list"
        aria-activedescendant={open && results[active] ? `${id}-${active}` : undefined}
        value={open ? query : selected ? `${selected.label} (${selected.code})` : ""}
        placeholder="Search intents, flows, phrases…"
        className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
        onFocus={() => { setOpen(true); setActive(0); }}
        onClick={() => setOpen(true)}
        onChange={(event) => { setQuery(event.target.value); setActive(0); setOpen(true); onChange(""); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.stopPropagation(); setOpen(false); }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            const next = Math.max(0, Math.min(results.length - 1, active + (event.key === "ArrowDown" ? 1 : -1)));
            setActive(next);
            document.getElementById(`${id}-${next}`)?.scrollIntoView({ block: "nearest" });
          }
          if (event.key === "Enter" && open && results[active]) { event.preventDefault(); choose(results[active]); }
        }}
      />
      {open && (
        <div className="mt-1 rounded-md border border-slate-700 bg-slate-950 p-1">
          <div className="flex gap-1 p-1" aria-label="Filter function points">
            {[["all", "All"], ["builtin", "Intents"], ["custom", "Flows"]].map(([key, label]) => (
              <button key={key} type="button" aria-pressed={filter === key}
                className={`rounded px-2 py-1 text-xs ${filter === key ? "bg-sky-900 text-sky-200" : "text-slate-400 hover:bg-slate-800"}`}
                onClick={() => { setFilter(key); setActive(0); }}>{label}</button>
            ))}
          </div>
          <p className="px-2 py-1 text-[10px] text-slate-400" role="status">{results.length} matches · names, IDs, descriptions and phrases</p>
          <ul id={`${id}-results`} role="listbox" aria-label="Intents and flows" className="max-h-60 overflow-y-auto">
            {results.map((node, index) => (
              <li key={node.code} id={`${id}-${index}`} role="option" aria-selected={value === node.code}
                className={`cursor-pointer rounded px-2 py-2 text-xs ${active === index ? "bg-sky-950 text-sky-100" : "text-slate-200 hover:bg-slate-800"}`}
                onMouseDown={(event) => event.preventDefault()} onClick={() => choose(node)}>
                <div className="font-medium">{node.icon} {node.label}</div>
                <div className="mt-0.5 text-[10px] text-slate-400">{node.code} · {node.kind === "custom" ? "Flow" : "Intent"}{!node.active ? " · Inactive" : ""}{node.served === false ? " · Segment not served" : ""}</div>
                <div className="mt-1 line-clamp-2 text-[11px] text-slate-400">{[...(node.phrases ?? []), ...(node.examples ?? [])].find((phrase) => query.trim() && normalize(phrase).includes(normalize(query))) || node.description}</div>
              </li>
            ))}
          </ul>
          {!results.length && <p className="p-2 text-xs text-slate-400">No matches. Try fewer words or another phrase.</p>}
        </div>
      )}
    </div>
  );
}
