import { useRef, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

function drag(e: ReactPointerEvent, cursor: string, move: (dx: number, dy: number) => void, end: () => void) {
  e.preventDefault();
  const x0 = e.clientX;
  const y0 = e.clientY;
  const previous = document.body.style.cursor;
  document.body.style.cursor = cursor;
  document.body.style.userSelect = "none";
  const onMove = (ev: PointerEvent) => move(ev.clientX - x0, ev.clientY - y0);
  const onUp = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    document.body.style.cursor = previous;
    document.body.style.userSelect = "";
    end();
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

/** The left edge of a panel docked on the right: drag it to make the panel
 * wider or narrower; double-click puts it back. */
export function EdgeHandle({ panel, width, onWidth, min = 380 }: {
  panel: RefObject<HTMLElement | null>; width: number | null; onWidth: (n: number | null) => void; min?: number;
}) {
  const live = useRef<number | null>(width);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      title="Drag to resize · double-click to reset"
      onDoubleClick={() => onWidth(null)}
      onPointerDown={(e) => {
        const start = panel.current?.getBoundingClientRect().width ?? width ?? 576;
        drag(e, "col-resize", (dx) => {
          const next = Math.min(window.innerWidth - 40, Math.max(min, start - dx));
          live.current = next;
          if (panel.current) panel.current.style.width = `${next}px`; // follow the mouse without re-rendering the panel
        }, () => live.current !== null && onWidth(live.current));
      }}
      className="group absolute inset-y-0 left-0 z-10 w-2 -translate-x-1 cursor-col-resize"
    >
      <div className="mx-auto h-full w-0.5 bg-transparent transition group-hover:bg-sky-500/70" />
    </div>
  );
}

/** A bar between two stacked panes: drag it to give the top pane more or
 * less room; double-click lets it size itself again. */
export function SplitHandle({ pane, onHeight, min = 80 }: {
  pane: RefObject<HTMLElement | null>; onHeight: (n: number | null) => void; min?: number;
}) {
  const live = useRef<number | null>(null);
  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      title="Drag to resize · double-click to reset"
      onDoubleClick={() => onHeight(null)}
      onPointerDown={(e) => {
        const start = pane.current?.getBoundingClientRect().height ?? 200;
        drag(e, "row-resize", (_dx, dy) => {
          const next = Math.min(window.innerHeight - 120, Math.max(min, start + dy));
          live.current = next;
          if (pane.current) pane.current.style.height = `${next}px`;
        }, () => live.current !== null && onHeight(live.current));
      }}
      className="group flex h-3 cursor-row-resize items-center"
    >
      <div className="mx-auto h-1 w-12 rounded-full bg-slate-700 transition group-hover:w-24 group-hover:bg-sky-500/70" />
    </div>
  );
}
