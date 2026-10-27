import { useRef } from "react";
import type { MapNode } from "../../api/functionMapTypes";
import { GUTTER, H, W, type Band } from "./geometry";

/** The whole map in miniature, with the part on screen outlined - click or
 * drag in it to move there. */
export default function MiniMap({
  width,
  height,
  bands,
  nodes,
  laneTop,
  view,
  size,
  tint,
  onMove,
}: {
  width: number;
  height: number;
  bands: Band[];
  nodes: MapNode[];
  laneTop: Record<string, number>;
  view: { x: number; y: number; k: number };
  size: { w: number; h: number };
  tint: (n: MapNode) => string;
  onMove: (cx: number, cy: number) => void;
}) {
  const box = useRef<SVGSVGElement>(null);
  const s = Math.min(200 / width, 130 / Math.max(height, 1));
  const mw = width * s;
  const mh = height * s;

  function go(e: React.PointerEvent) {
    const r = box.current!.getBoundingClientRect();
    onMove(((e.clientX - r.left) / r.width) * width, ((e.clientY - r.top) / r.height) * height);
  }

  return (
    <div className="absolute bottom-3 right-3 z-20 rounded-lg border border-slate-700 bg-slate-950/90 p-1.5 shadow-xl shadow-black/40" title="Overview - click or drag to move around">
      <svg
        ref={box}
        width={mw}
        height={mh}
        className="block cursor-pointer"
        onPointerDown={(e) => {
          (e.currentTarget as Element).setPointerCapture(e.pointerId);
          go(e);
        }}
        onPointerMove={(e) => e.buttons === 1 && go(e)}
      >
        {bands.map((b, i) => (
          <rect key={b.key} x={0} y={b.top * s} width={mw} height={b.height * s} fill={i % 2 ? "#0f172a" : "#020617"} />
        ))}
        {nodes.map((n) => (
          <rect key={n.code} x={(GUTTER + n.x) * s} y={(laneTop[n.lane] + n.dy) * s} width={Math.max(2, W * s)} height={Math.max(1.5, H * s)} rx={1} fill={tint(n)} />
        ))}
        <rect
          x={(-view.x / view.k) * s}
          y={(-view.y / view.k) * s}
          width={(size.w / view.k) * s}
          height={(size.h / view.k) * s}
          fill="#38bdf81a"
          stroke="#38bdf8"
          strokeWidth={1}
        />
      </svg>
    </div>
  );
}
