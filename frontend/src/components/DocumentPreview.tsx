import { useEffect, useState } from "react";
import { previewQuoteDocument, quoteDocumentFile } from "../api/client";
import { useWorkspace } from "../workspace";

/** A quote's PDF, shown right in the page: the proposal / agreement as the
 * tenant's template writes it for this quote (before it is generated), or a
 * document already attached to the quote. */
export default function DocumentPreview({
  quoteId,
  kind = "proposal",
  documentId,
  title,
  height = 460,
}: {
  quoteId: number;
  kind?: "proposal" | "agreement";
  /** a stored document; without it, a preview of what would be generated */
  documentId?: number;
  title?: string;
  height?: number;
}) {
  const { role } = useWorkspace();
  // what was loaded, for which request - a new quote / document shows "Writing…" until its PDF arrives
  const want = `${quoteId}|${kind}|${documentId ?? ""}`;
  const [loaded, setLoaded] = useState<{ key: string; url?: string; error?: string }>({ key: "" });

  useEffect(() => {
    let live = true;
    let made: string | null = null;
    (documentId ? quoteDocumentFile(quoteId, documentId) : previewQuoteDocument(quoteId, kind))
      .then((blob) => {
        made = URL.createObjectURL(blob);
        if (live) setLoaded({ key: want, url: made });
      })
      .catch((e) => live && setLoaded({ key: want, error: e instanceof Error ? e.message.replace(/^\d+ [^:]*: /, "") : "Could not load the PDF" }));
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [quoteId, kind, documentId, want]);
  const url = loaded.key === want ? loaded.url ?? null : null;
  const error = loaded.key === want ? loaded.error ?? "" : "";

  const label = title ?? (documentId ? "Attached to the quote" : `${kind === "agreement" ? "Agreement" : "Proposal"} preview - written from your template for quote #${quoteId}`);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-700 bg-slate-950">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2 text-xs">
        <span>📄</span>
        <span className="font-semibold text-slate-200">{label}</span>
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="ml-auto font-semibold text-sky-400 hover:text-sky-300">
            Open full size ↗
          </a>
        )}
      </div>
      {error ? (
        <p className="p-3 text-xs text-red-400">{error}</p>
      ) : url ? (
        // drag the bottom-right corner to see more of the page
        <div className="resize-y overflow-hidden" style={{ height, minHeight: 200 }}>
          <iframe title={label} src={`${url}#toolbar=0&view=FitH`} className="block h-full w-full bg-white" />
        </div>
      ) : (
        <p className="p-3 text-xs text-slate-500">Writing the PDF…</p>
      )}
      {!documentId && (
        <p className="border-t border-slate-800 px-3 py-1.5 text-[11px] text-slate-500">
          Nothing is saved until you run it - then this PDF is generated and attached to the quote.
          {role === "admin" ? " The wording is yours to change in Admin › Quote Documents." : ""}
        </p>
      )}
    </div>
  );
}
