import type { RecordButton } from "../api/types";
import { useWorkspace } from "../workspace";

/** A record's buttons (see useRecordButtons), each opening its intent prefilled. */
export function RecordButtonRow({ list, className, buttonClass }: { list: RecordButton[] | undefined; className?: string; buttonClass: string }) {
  const { runIntent } = useWorkspace();
  if (!list || list.length === 0) return null;
  return (
    <div className={className ?? "flex flex-wrap gap-1.5"}>
      {list.map((b) => (
        <button key={`${b.intent}-${b.label}`} type="button" onClick={() => runIntent(b.intent, b.entities)} className={buttonClass}>
          {b.label}
        </button>
      ))}
    </div>
  );
}
