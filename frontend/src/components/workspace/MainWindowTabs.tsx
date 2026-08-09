import { useWorkspace } from "../../state/WorkspaceContext";

// Tabs across the top switch between Main Windows (each its own grid + info
// tab). Connector links are global (see types/workspace.ts), so a connector-0
// window here shows the same symbol as a connector-0 window on another tab —
// deliberately, per the confirmed spec.
//
// Each tab also gets a pop-out button (⧉) — opens /window/:id in a real,
// separate browser tab (window.open, not client-side navigation), which is
// what actually lets one Main Window be dragged onto a second monitor;
// switching between virtual tabs inside one browser window never could.
// The popped-out tab live-syncs with this one via WorkspaceContext's
// cross-tab sync (BroadcastChannel) — it's a live mirror, not a one-time
// snapshot, and this tab keeps showing the layout too rather than losing
// it, so either can be closed independently without losing the other's view.
export function MainWindowTabs() {
  const { mainWindows, activeMainWindowId, setActiveMainWindow, addMainWindow, closeMainWindow } = useWorkspace();

  const popOut = (id: string) => {
    window.open(`${window.location.origin}/window/${encodeURIComponent(id)}`, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="flex items-center gap-1 border-b border-base-border bg-base-panel px-2 py-1">
      {mainWindows.map((w) => {
        const active = w.id === activeMainWindowId;
        return (
          <div
            key={w.id}
            onClick={() => setActiveMainWindow(w.id)}
            className={`group flex cursor-pointer items-center gap-1.5 rounded-t px-3 py-1 font-mono text-xs ${
              active
                ? "border border-b-0 border-base-border bg-base-bg text-text-primary"
                : "text-text-muted hover:text-text-primary"
            }`}
          >
            <span>{w.label}</span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                popOut(w.id);
              }}
              className="rounded px-1 text-text-muted opacity-0 hover:bg-base-border hover:text-text-primary group-hover:opacity-100"
              title="Open in a new window (for a second monitor)"
            >
              &#10697;
            </button>
            {mainWindows.length > 1 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  closeMainWindow(w.id);
                }}
                className="rounded px-1 text-text-muted opacity-0 hover:bg-base-border hover:text-text-primary group-hover:opacity-100"
                title="Close layout"
              >
                ×
              </button>
            )}
          </div>
        );
      })}
      <button
        onClick={addMainWindow}
        className="ml-1 rounded px-2 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
        title="Add Main Window"
      >
        + New Layout
      </button>
    </div>
  );
}
