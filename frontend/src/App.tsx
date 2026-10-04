import { useState } from "react";
import AuditLogPage from "./pages/AuditLogPage";
import CatalogPage from "./pages/CatalogPage";
import IntentCommandBar from "./pages/IntentCommandBar";

type Tab = "intent" | "catalog" | "audit";

const TABS: { id: Tab; label: string }[] = [
  { id: "intent", label: "Intent Command Bar" },
  { id: "catalog", label: "Catalog" },
  { id: "audit", label: "Audit Log" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("intent");

  return (
    <div className="min-h-screen bg-slate-950">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-4">
          <span className="font-bold text-slate-100">
            BRT 360 <span className="text-sky-500">·</span> Intent CRM
          </span>
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                  tab === t.id ? "bg-sky-600 text-white" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="px-6 py-10">
        {tab === "intent" && <IntentCommandBar />}
        {tab === "catalog" && <CatalogPage />}
        {tab === "audit" && <AuditLogPage />}
      </main>
    </div>
  );
}
