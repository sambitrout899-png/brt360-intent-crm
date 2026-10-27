import { useCallback, useEffect, useState } from "react";

/* Data hooks and helpers for the training workspace (kept apart from the
   components in ui.tsx so fast refresh keeps working). */

/** Turns "422 Unprocessable Entity: {...detail...}" into a readable sentence. */
export function readable(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  const json = text.indexOf("{");
  if (json >= 0) {
    try {
      const detail = JSON.parse(text.slice(json)).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) return detail.map((d) => (typeof d === "string" ? d : d.msg ?? JSON.stringify(d))).join(" · ");
      if (detail?.message) return detail.message;
    } catch {
      /* fall through */
    }
  }
  return text;
}

export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const reload = useCallback(() => {
    loader()
      .then((d) => {
        setError("");
        setData(d);
      })
      .catch((e) => setError(readable(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload, setData };
}

/** Runs an action, showing its error or a success line. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState("");
  async function run<T>(fn: () => Promise<T>, success?: string): Promise<T | undefined> {
    setBusy(true);
    setError("");
    setOk("");
    try {
      const out = await fn();
      if (success) setOk(success);
      return out;
    } catch (e) {
      setError(readable(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, ok, run, setError, setOk };
}

/** Local datetime-input value -> ISO with the browser's offset. */
export function toIso(local: string): string {
  return local ? new Date(local).toISOString() : "";
}
