import { createContext, useContext, useEffect, useRef } from "react";
import type { EntityType } from "./api/types";

/**
 * App-wide workspace services every page can reach without prop drilling:
 * invoke an intent in place (the Intent Drawer slides over whatever page
 * you are on) and open any record's lifecycle panel (state, owner, custom
 * fields, available actions).
 */
export interface Workspace {
  role: string;
  requestIntent: (text: string) => void;
  /** Open the drawer straight on one intent's form, prefilled. */
  runIntent: (code: string, entities?: Record<string, unknown>) => void;
  /** Whether this user may run the intent (switched on, allowed for their
   * roles) - pages hide the buttons of intents they can't. True until the
   * catalog has loaded; the server checks again on every run. */
  canRun: (code: string) => boolean;
  openRecord: (entityType: EntityType, entityId: number) => void;
  /** Open the "About this record" pop-up: everything about any record. */
  openAbout?: (entityType: EntityType, entityId: number) => void;
  /** A page says which record it is showing - the floating "About this record" button follows it. */
  setCurrentRecord?: (record: { entityType: EntityType; entityId: number; title?: string } | null) => void;
  /** Admins: open the Configure 360 studio (optionally with a request for its agent). */
  openConfigure360?: (opts?: { instruction?: string; partyId?: number; tab?: "ai" | "layout" | "look" | "sources" }) => void;
  /** Admins: open Admin > Product Modeller on a product. */
  openProductModeller?: (product?: string) => void;
}

export const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceContext");
  return ctx;
}

const INTENT_EXECUTED_EVENT = "brt360:intent-executed";

export function notifyIntentExecuted(): void {
  window.dispatchEvent(new Event(INTENT_EXECUTED_EVENT));
}

/** Re-runs `reload` whenever an intent executes anywhere in the app, so a
 * page reflects changes made from the drawer without a manual refresh. */
export function useIntentRefresh(reload: () => void): void {
  const ref = useRef(reload);
  useEffect(() => {
    ref.current = reload;
  });
  useEffect(() => {
    const handler = () => ref.current();
    window.addEventListener(INTENT_EXECUTED_EVENT, handler);
    return () => window.removeEventListener(INTENT_EXECUTED_EVENT, handler);
  }, []);
}
