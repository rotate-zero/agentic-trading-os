import { useState } from "react";
import { GRID_PRESETS } from "../../types/workspace";
import { useWorkspace } from "../../state/WorkspaceContext";

export function GridPresetPicker() {
  const { preset, setPreset } = useWorkspace();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-base-border px-2 py-1 font-mono text-xs text-text-muted hover:border-signal hover:text-text-primary"
      >
        Grid: {preset.label}
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 grid grid-cols-3 gap-2 rounded-md border border-base-border bg-base-panel p-2 shadow-xl">
          {GRID_PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                setPreset(p);
                setOpen(false);
              }}
              className={`flex flex-col items-center gap-1 rounded p-2 hover:bg-base-bg ${
                p.label === preset.label ? "ring-1 ring-signal" : ""
              }`}
              title={p.label}
            >
              <div
                className="grid h-8 w-10 gap-[2px]"
                style={{ gridTemplateRows: `repeat(${p.rows}, 1fr)`, gridTemplateColumns: `repeat(${p.cols}, 1fr)` }}
              >
                {Array.from({ length: p.rows * p.cols }).map((_, i) => (
                  <div key={i} className="rounded-[1px] bg-base-border" />
                ))}
              </div>
              <span className="font-mono text-[10px] text-text-muted">{p.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
