import { useEffect, useState } from "react";
import AdminPage from "./pages/AdminPage";
import AuditLogPage from "./pages/AuditLogPage";
import CatalogPage from "./pages/CatalogPage";
import CustomerPage from "./pages/CustomerPage";
import DashboardPage from "./pages/DashboardPage";
import IntentCommandBar from "./pages/IntentCommandBar";
import LoginPage from "./pages/LoginPage";
import OrdersPage from "./pages/OrdersPage";
import PipelinePage from "./pages/PipelinePage";
import PlatformAdminPage from "./pages/PlatformAdminPage";
import TicketsPage from "./pages/TicketsPage";
import { currentUser, getAccessToken, logout } from "./api/client";
import type { CurrentUser } from "./api/types";

type Tab = "dashboard" | "customer" | "pipeline" | "orders" | "tickets" | "catalog" | "intent" | "audit" | "admin";

const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "customer", label: "Customer 360" },
  { id: "pipeline", label: "Sales Pipeline" },
  { id: "orders", label: "Orders & Billing" },
  { id: "tickets", label: "Tickets & Field" },
  { id: "catalog", label: "Catalog" },
  { id: "intent", label: "Intent Command Bar" },
  { id: "audit", label: "Audit Log" },
  { id: "admin", label: "Admin" },
];

function CrmApp({ user, onLoggedOut }: { user: CurrentUser; onLoggedOut: () => void }) {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [intentPrefill, setIntentPrefill] = useState("");

  function requestIntent(text: string) {
    setIntentPrefill(text);
    setTab("intent");
  }

  const tabs = user.role === "admin" ? TABS : TABS.filter((t) => t.id !== "admin");

  return (
    <div className="min-h-screen bg-slate-950">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-4">
          <span className="font-bold text-slate-100">
            BRT 360 <span className="text-sky-500">·</span> Intent CRM
          </span>
          <nav className="flex flex-wrap gap-1">
            {tabs.map((t) => (
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
          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-slate-500">
              {user.name} · {user.role}
            </span>
            <button
              onClick={() => {
                logout();
                onLoggedOut();
              }}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400"
            >
              Log Out
            </button>
          </div>
        </div>
      </header>

      <main className="px-6 py-10">
        {tab === "dashboard" && <DashboardPage />}
        {tab === "customer" && <CustomerPage onRequestIntent={requestIntent} />}
        {tab === "pipeline" && <PipelinePage onRequestIntent={requestIntent} />}
        {tab === "orders" && <OrdersPage onRequestIntent={requestIntent} />}
        {tab === "tickets" && <TicketsPage onRequestIntent={requestIntent} />}
        {tab === "catalog" && <CatalogPage />}
        {tab === "intent" && <IntentCommandBar prefillText={intentPrefill} />}
        {tab === "audit" && <AuditLogPage />}
        {tab === "admin" && user.role === "admin" && <AdminPage />}
      </main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<CurrentUser | null | undefined>(undefined); // undefined = still checking

  function refreshSession() {
    if (!getAccessToken()) {
      setUser(null);
      return;
    }
    currentUser()
      .then(setUser)
      .catch(() => setUser(null));
  }

  useEffect(refreshSession, []);

  if (user === undefined) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-500">Loading…</div>;
  }

  if (user === null) {
    return <LoginPage onLoggedIn={refreshSession} />;
  }

  if (user.role === "platform_admin") {
    return <PlatformAdminPage onLoggedOut={refreshSession} />;
  }

  return <CrmApp user={user} onLoggedOut={refreshSession} />;
}
