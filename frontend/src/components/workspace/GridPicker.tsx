import { useState } from "react";
import { MAX_GRID_DIM } from "../../types/workspace";
import { useWorkspace } from "../../state/WorkspaceContext";

// Excel/PowerPoint-style hover grid: hovering cell (r,c) previews an (r+1) x
// (c+1) selection (e.g. hovering the very first cell = 1x1 = sub-window takes
// the whole Main Window). Click commits. Fully free 1-8 x 1-8, not a fixed list.
export function GridPicker() {
  const { gridLayout, setGridLayout } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);

  const cells = Array.from({ length: MAX_GRID_DIM }).flatMap((_, r) =>
    Array.from({ length: MAX_GRID_DIM }).map((_, c) => ({ r, c }))
  );

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-base-border px-2 py-1 font-mono text-xs text-text-muted hover:border-signal hover:text-text-primary"
      >
        Grid: {gridLayout.rows}x{gridLayout.cols}
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 rounded-md border border-base-border bg-base-panel p-3 shadow-xl"
          onMouseLeave={() => setHover(null)}
        >
          <div className="mb-2 text-center font-mono text-xs text-text-muted">
            {hover ? `${hover.row + 1} x ${hover.col + 1}` : "Select grid size"}
          </div>
          <div
            className="grid gap-[3px]"
            style={{ gridTemplateColumns: `repeat(${MAX_GRID_DIM}, 14px)` }}
          >
            {cells.map(({ r, c }) => {
              const active = hover !== null && r <= hover.row && c <= hover.col;
              return (
                <div
                  key={`${r}-${c}`}
                  onMouseEnter={() => setHover({ row: r, col: c })}
                  onClick={() => {
                    setGridLayout({ rows: r + 1, cols: c + 1 });
                    setOpen(false);
                    setHover(null);
                  }}
                  className={`h-[14px] w-[14px] cursor-pointer rounded-[2px] border ${
                    active ? "border-signal bg-signal/40" : "border-base-border bg-base-bg"
                  }`}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
