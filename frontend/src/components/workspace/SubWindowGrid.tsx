import { useRef } from "react";
import { useWorkspace } from "../../state/WorkspaceContext";
import { SubWindow } from "../sub-window/SubWindow";

function cumulative(fractions: number[]): number[] {
  const out: number[] = [];
  let sum = 0;
  for (const f of fractions) {
    sum += f;
    out.push(sum);
  }
  return out;
}

const MIN_TRACK = 0.08;

export function SubWindowGrid() {
  const { rowHeights, colWidths, subWindows, setRowHeights, setColWidths } = useWorkspace();
  const containerRef = useRef<HTMLDivElement>(null);

  const rowBoundaries = cumulative(rowHeights).slice(0, -1);
  const colBoundaries = cumulative(colWidths).slice(0, -1);

  const onRowHandleDown = (i: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const startY = e.clientY;
    const start = [...rowHeights];

    const onMove = (ev: PointerEvent) => {
      const deltaFrac = (ev.clientY - startY) / rect.height;
      const next = [...start];
      let a = next[i] + deltaFrac;
      let b = next[i + 1] - deltaFrac;
      if (a < MIN_TRACK) {
        b -= MIN_TRACK - a;
        a = MIN_TRACK;
      }
      if (b < MIN_TRACK) {
        a -= MIN_TRACK - b;
        b = MIN_TRACK;
      }
      next[i] = a;
      next[i + 1] = b;
      setRowHeights(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const onColHandleDown = (i: number) => (e: React.PointerEvent) => {
    e.preventDefault();
    const rect = containerRef.current!.getBoundingClientRect();
    const startX = e.clientX;
    const start = [...colWidths];

    const onMove = (ev: PointerEvent) => {
      const deltaFrac = (ev.clientX - startX) / rect.width;
      const next = [...start];
      let a = next[i] + deltaFrac;
      let b = next[i + 1] - deltaFrac;
      if (a < MIN_TRACK) {
        b -= MIN_TRACK - a;
        a = MIN_TRACK;
      }
      if (b < MIN_TRACK) {
        a -= MIN_TRACK - b;
        b = MIN_TRACK;
      }
      next[i] = a;
      next[i + 1] = b;
      setColWidths(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div ref={containerRef} className="relative h-full w-full">
      <div
        className="grid h-full w-full"
        style={{
          gridTemplateRows: rowHeights.map((h) => `${h}fr`).join(" "),
          gridTemplateColumns: colWidths.map((w) => `${w}fr`).join(" "),
        }}
      >
        {subWindows.map((sw) => (
          <div key={sw.id} className="min-h-0 min-w-0 overflow-hidden border border-base-border">
            <SubWindow config={sw} />
          </div>
        ))}
      </div>

      {/* Row handles — shared across the whole grid width, per the confirmed uniform-grid model */}
      {rowBoundaries.map((b, i) => (
        <div
          key={`row-${i}`}
          onPointerDown={onRowHandleDown(i)}
          className="absolute left-0 right-0 z-10 h-[6px] -translate-y-1/2 cursor-row-resize hover:bg-signal/30"
          style={{ top: `${b * 100}%` }}
        />
      ))}
      {/* Column handles — also shared across all rows, per the same rule */}
      {colBoundaries.map((b, i) => (
        <div
          key={`col-${i}`}
          onPointerDown={onColHandleDown(i)}
          className="absolute top-0 bottom-0 z-10 w-[6px] -translate-x-1/2 cursor-col-resize hover:bg-signal/30"
          style={{ left: `${b * 100}%` }}
        />
      ))}
    </div>
  );
}
