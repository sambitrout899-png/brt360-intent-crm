import { useCallback, useEffect, useState } from "react";
import { recordButtons } from "../api/client";
import type { RecordButton } from "../api/types";
import { useIntentRefresh } from "../workspace";

/** The buttons of several records ("quote:12", "lead:3" ...), worked out on
 * the server from each record's current state - the same list Customer 360
 * and the record panel show. Reloaded after every intent. */
export function useRecordButtons(refs: string[]): Record<string, RecordButton[]> {
  const key = refs.join(",");
  const [buttons, setButtons] = useState<Record<string, RecordButton[]>>({});
  const load = useCallback(() => {
    recordButtons(key ? key.split(",") : [])
      .then(setButtons)
      .catch(() => setButtons({}));
  }, [key]);
  useEffect(load, [load]);
  useIntentRefresh(load);
  return buttons;
}
