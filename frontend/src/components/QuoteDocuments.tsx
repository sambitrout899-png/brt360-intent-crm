import { useCallback, useEffect, useRef, useState } from "react";
import { listQuoteDocuments, quoteDocumentFile, uploadQuoteDocument } from "../api/client";
import type { QuoteDocument, QuoteDocumentKind } from "../api/types";
import { notifyIntentExecuted, useIntentRefresh } from "../workspace";

const ICON: Record<string, string> = { proposal: "📄", purchase_order: "🧾", agreement: "📜", other: "📎" };
const UPLOAD_KINDS: [QuoteDocumentKind, string][] = [
  ["purchase_order", "Customer's PO"],
  ["proposal", "Signed proposal"],
  ["agreement", "Signed agreement"],
  ["other", "Other document"],
];

function size(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Open a quote's PDF in a new tab - or save it. The tab is opened at once
 * (before the file arrives) so the browser doesn't treat it as a pop-up. */
async function openQuoteDocument(doc: QuoteDocument, save = false): Promise<void> {
  const tab = save ? null : window.open("", "_blank");
  try {
    const url = URL.createObjectURL(await quoteDocumentFile(doc.quote_id, doc.id));
    if (tab) tab.location.href = url;
    else {
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.file_name;
      a.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    tab?.close();
    throw e;
  }
}

function useDocuments(quoteId: number, initial?: QuoteDocument[]) {
  const [docs, setDocs] = useState<QuoteDocument[] | null>(initial ?? null);
  const load = useCallback(() => {
    listQuoteDocuments(quoteId).then(setDocs).catch(() => setDocs([]));
  }, [quoteId]);
  useEffect(() => {
    if (!initial) load();
  }, [load, initial]);
  useIntentRefresh(load);
  return { docs, load };
}

/** The quote's PDFs as small chips (Customer 360's record list). */
export function QuoteDocumentChips({ quoteId, documents, tone = "c360" }: { quoteId: number; documents?: QuoteDocument[]; tone?: "c360" | "slate" }) {
  const { docs } = useDocuments(quoteId, documents);
  // the newest of each kind - older versions are in the record panel
  const latest = (docs ?? []).filter((d, i, all) => all.findIndex((x) => x.kind === d.kind) === i);
  if (latest.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {latest.map((d) => (
        <button
          key={d.id}
          onClick={() => openQuoteDocument(d).catch(() => undefined)}
          title={`${d.title} - ${d.file_name} (${size(d.size)}) - open the PDF`}
          className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold hover:brightness-125 ${
            tone === "slate" ? "border-slate-700 text-slate-300 hover:border-sky-500 hover:text-sky-300" : ""
          }`}
          style={tone === "c360" ? { borderColor: "color-mix(in srgb, var(--c-muted) 40%, transparent)", color: "var(--c-text)" } : undefined}
        >
          {ICON[d.kind] ?? "📎"} {d.kind_label}
          {d.version > 1 ? ` v${d.version}` : ""}
        </button>
      ))}
    </span>
  );
}

/** Every PDF of the quote - view, save, attach more (record panel). */
export default function QuoteDocuments({ quoteId, canUpload }: { quoteId: number; canUpload: boolean }) {
  const { docs, load } = useDocuments(quoteId);
  const [kind, setKind] = useState<QuoteDocumentKind>("purchase_order");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  async function attach(file: File) {
    setError("");
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return setError("Only PDF files can be attached to a quote.");
    if (file.size > 10 * 1024 * 1024) return setError("The file is larger than 10 MB.");
    setBusy(true);
    try {
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",").pop() ?? "");
        reader.onerror = () => reject(new Error("The file could not be read."));
        reader.readAsDataURL(file);
      });
      await uploadQuoteDocument(quoteId, { kind, file_name: file.name, content_base64: content });
      load();
      notifyIntentExecuted(); // the quote's buttons and chips elsewhere follow
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+ [^:]*: /, "") : "Could not attach the file.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const view = (d: QuoteDocument, save = false) => openQuoteDocument(d, save).catch((e) => setError(e instanceof Error ? e.message : "Could not open it."));

  return (
    <div>
      <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">Documents</h3>
      <div className="space-y-1">
        {docs === null && <p className="text-xs text-slate-500">Loading…</p>}
        {docs?.length === 0 && (
          <p className="text-xs text-slate-500">No PDFs yet - the proposal appears here once it is generated, then the customer's PO and the agreement.</p>
        )}
        {docs?.map((d) => (
          <div key={d.id} className="flex items-center gap-2 rounded-md bg-slate-950 px-3 py-1.5 text-xs">
            <span>{ICON[d.kind] ?? "📎"}</span>
            <button onClick={() => view(d)} className="min-w-0 flex-1 truncate text-left text-slate-200 hover:text-sky-400" title={d.file_name}>
              {d.title}
            </button>
            <span className="shrink-0 text-slate-500" title={new Date(d.created_at).toLocaleString()}>
              {d.source === "uploaded" ? "attached" : "generated"} {new Date(d.created_at).toLocaleDateString()}
              {d.created_by ? ` · ${d.created_by}` : ""} · {size(d.size)}
            </span>
            <button onClick={() => view(d, true)} className="shrink-0 rounded px-1 text-slate-400 hover:text-sky-400" title="Save the PDF">
              ⤓
            </button>
          </div>
        ))}
      </div>
      {canUpload && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select value={kind} onChange={(e) => setKind(e.target.value as QuoteDocumentKind)}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
            {UPLOAD_KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
          <button
            onClick={() => input.current?.click()}
            disabled={busy}
            className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300 hover:border-sky-500 hover:text-sky-400 disabled:opacity-50"
          >
            {busy ? "Attaching…" : "+ Attach PDF"}
          </button>
          <input ref={input} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(e) => e.target.files?.[0] && attach(e.target.files[0])} />
        </div>
      )}
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
    </div>
  );
}
