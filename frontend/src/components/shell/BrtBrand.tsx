/** BlueRose Technologies - the maker's mark on every screen of BRT 360.
 * The trademark keeps its own colours, so it always sits on white: the full
 * logo on a white plate, the swirl emblem on a white tile. */

const base = import.meta.env.BASE_URL;
const BRT_COMPANY = "BlueRose Technologies";
const YEAR = new Date().getFullYear();

/** the copyright line */
export function BrtCopyright({ className = "" }: { className?: string }) {
  return (
    <span className={className}>
      © {YEAR} {BRT_COMPANY}. All rights reserved.
    </span>
  );
}

/** the full BlueRose Technologies logo on a white plate */
export function BrtLogo({ className = "h-8" }: { className?: string }) {
  return (
    <span className="inline-flex items-center rounded-lg bg-white px-2.5 py-1.5 shadow-sm ring-1 ring-black/5">
      <img src={`${base}brt-logo.png`} alt={BRT_COMPANY} className={`w-auto object-contain ${className}`} draggable={false} />
    </span>
  );
}

/** the swirl emblem on a white tile */
export function BrtEmblem({ size = "h-9 w-9", rounded = "rounded-lg" }: { size?: string; rounded?: string }) {
  return (
    <span className={`inline-flex shrink-0 items-center justify-center bg-white p-1 shadow-sm ring-1 ring-black/5 ${rounded} ${size}`}>
      <img src={`${base}brt-emblem.png`} alt={BRT_COMPANY} className="h-full w-full object-contain" draggable={false} />
    </span>
  );
}

/** "Powered by" mark - the full logo, or just the emblem where space is tight */
export function PoweredByBrt({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <span title={`BRT 360 by ${BRT_COMPANY}`}>
        <BrtEmblem size="h-8 w-8" />
      </span>
    );
  }
  return (
    <div className="flex flex-col items-start gap-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Powered by</span>
      <BrtLogo className="h-6" />
    </div>
  );
}

/** the line at the foot of every page */
export function BrtFooter() {
  return (
    <footer className="mt-16 flex flex-wrap items-center justify-between gap-3 border-t border-slate-800/80 pt-6 text-xs text-slate-500">
      <span className="flex items-center gap-2.5">
        <BrtEmblem size="h-6 w-6" rounded="rounded-md" />
        <span>
          <span className="font-semibold text-slate-400">BRT 360</span> · Intent CRM
        </span>
      </span>
      <BrtCopyright />
    </footer>
  );
}
