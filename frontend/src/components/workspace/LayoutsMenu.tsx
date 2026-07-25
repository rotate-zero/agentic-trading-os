import { useRef, useState, type ChangeEvent } from "react";
import { useWorkspace } from "../../state/WorkspaceContext";

export function LayoutsMenu() {
  const { savedLayouts, saveCurrentLayout, loadLayout, deleteLayout, exportLayouts, importLayouts } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSave = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    saveCurrentLayout(trimmed);
    setName("");
  };

  const handleImportFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => importLayouts(String(reader.result ?? ""));
    reader.readAsText(file);
    e.target.value = ""; // allow importing the same file again later
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-base-border px-2 py-1 font-mono text-xs text-text-muted hover:border-signal hover:text-text-primary"
      >
        Layouts
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-64 rounded-md border border-base-border bg-base-panel p-3 shadow-xl">
          <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-text-muted">
            Save current layout
          </div>
          <div className="mb-3 flex gap-1">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSave()}
              placeholder="Layout name"
              className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-2 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal"
            />
            <button
              onClick={handleSave}
              disabled={!name.trim()}
              className="rounded bg-signal/20 px-2 py-1 font-mono text-xs text-signal hover:bg-signal/30 disabled:opacity-40"
            >
              Save
            </button>
          </div>

          <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-text-muted">Saved</div>
          <div className="mb-3 max-h-40 overflow-y-auto">
            {savedLayouts.length === 0 && (
              <div className="px-1 py-2 font-mono text-[11px] text-text-muted">No saved layouts yet.</div>
            )}
            {savedLayouts.map((l) => (
              <div key={l.id} className="flex items-center justify-between gap-1 rounded px-1 py-1 hover:bg-base-bg">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-xs text-text-primary">{l.name}</div>
                  <div className="font-mono text-[10px] text-text-muted">
                    {new Date(l.savedAt).toLocaleString()}
                  </div>
                </div>
                <button
                  onClick={() => {
                    loadLayout(l.id);
                    setOpen(false);
                  }}
                  className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-signal hover:bg-signal/20"
                >
                  Load
                </button>
                <button
                  onClick={() => deleteLayout(l.id)}
                  className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:bg-bear/20 hover:text-bear"
                >
                  &times;
                </button>
              </div>
            ))}
          </div>

          <div className="flex gap-1 border-t border-base-border pt-2">
            <button
              onClick={exportLayouts}
              disabled={savedLayouts.length === 0}
              className="flex-1 rounded border border-base-border px-2 py-1 font-mono text-[11px] text-text-muted hover:text-text-primary disabled:opacity-40"
            >
              Export
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex-1 rounded border border-base-border px-2 py-1 font-mono text-[11px] text-text-muted hover:text-text-primary"
            >
              Import
            </button>
            <input ref={fileInputRef} type="file" accept="application/json" onChange={handleImportFile} className="hidden" />
          </div>
        </div>
      )}
    </div>
  );
}
