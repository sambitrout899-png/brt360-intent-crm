import { useCallback, useState } from "react";

/** A size the user dragged a window or pane to, remembered per browser. */
export function useStoredSize(key: string, initial: number | null): [number | null, (n: number | null) => void] {
  const storageKey = `brt360.size.${key}`;
  const [size, setSize] = useState<number | null>(() => {
    try {
      const saved = Number(localStorage.getItem(storageKey));
      return saved > 0 ? saved : initial;
    } catch {
      return initial;
    }
  });
  const save = useCallback(
    (n: number | null) => {
      setSize(n);
      try {
        if (n === null) localStorage.removeItem(storageKey);
        else localStorage.setItem(storageKey, String(Math.round(n)));
      } catch {
        /* private window - the size lasts for this visit */
      }
    },
    [storageKey],
  );
  return [size, save];
}
