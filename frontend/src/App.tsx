import { useState } from "react";
import { WorkspaceProvider, useWorkspace } from "./state/WorkspaceContext";
import { GridPicker } from "./components/workspace/GridPicker";
import { LayoutsMenu } from "./components/workspace/LayoutsMenu";
import { MainWindowTabs } from "./components/workspace/MainWindowTabs";
import { SubWindowGrid } from "./components/workspace/SubWindowGrid";
import { InfoTab } from "./components/workspace/InfoTab";

// Minimal hand-rolled routing — the only two shapes this app needs: "/"
// (the full multi-tab workspace) and "/window/:id" (one Main Window,
// popped out into its own browser tab for a second monitor — see
// MainWindowTabs.tsx's pop-out button and WorkspaceContext.tsx's
// lockedMainWindowId). Not worth pulling in a routing library for exactly
// two shapes that only ever get reached via a fresh window.open() — there's
// no in-app client-side navigation between them, so nothing needs
// history/link handling, just a one-time read of the URL at load.
function parsePoppedOutWindowId(): string | null {
  const match = window.location.pathname.match(/^\/window\/([^/]+)\/?$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function FullWorkspaceShell() {
  return (
    <div className="flex h-screen flex-col bg-base-bg">
      <header className="flex items-center justify-between border-b border-base-border px-4 py-2">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold text-text-primary">Trading Workspace</span>
          <span className="rounded bg-base-panel px-2 py-0.5 font-mono text-[11px] text-text-muted">
            Phase 1 — static mock data
          </span>
        </div>
        <div className="flex items-center gap-2">
          <LayoutsMenu />
          <GridPicker />
        </div>
      </header>

      <MainWindowTabs />

      <main className="flex min-h-0 flex-1">
        <section className="min-w-0 flex-1">
          <SubWindowGrid />
        </section>
        <InfoTab />
      </main>
    </div>
  );
}

// The popped-out view for one Main Window (see parsePoppedOutWindowId
// above and MainWindowTabs.tsx's pop-out button). Reuses the exact same
// SubWindowGrid/InfoTab/GridPicker/LayoutsMenu the full workspace uses —
// they all already read/write through useWorkspace()'s "active window"
// concept, and WorkspaceProvider's lockedMainWindowId prop is what pins
// that concept to this one window for this tab's whole lifetime, so
// nothing about those components needed to change to support this.
// Deliberately omits MainWindowTabs — there's nothing to switch between in
// a tab locked to one window — and adds a small link back to "/" instead.
function PoppedOutWindowShell({ windowId }: { windowId: string }) {
  const { mainWindows } = useWorkspace();
  const target = mainWindows.find((w) => w.id === windowId);

  if (!target) {
    // Genuinely possible, not just defensive: the main tab (or another
    // popped-out tab) can close this layout at any time, and this tab
    // finds out live via the same cross-tab sync that keeps its content
    // up to date — showing a clear message beats silently falling back to
    // some other window on a trading app.
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-base-bg font-mono text-sm text-text-muted">
        <p>This layout no longer exists in the main workspace.</p>
        <a href="/" className="rounded border border-base-border px-3 py-1.5 text-text-primary hover:bg-base-panel">
          ⌂ Back to all layouts
        </a>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col bg-base-bg">
      <header className="flex items-center justify-between border-b border-base-border px-4 py-2">
        <div className="flex items-center gap-3">
          <a href="/" title="Back to all layouts" className="font-mono text-sm text-text-muted hover:text-text-primary">
            ⌂
          </a>
          <span className="font-mono text-sm font-semibold text-text-primary">{target.label}</span>
          <span className="rounded bg-base-panel px-2 py-0.5 font-mono text-[11px] text-text-muted">
            Popped out — live-synced with the main workspace
          </span>
        </div>
        <div className="flex items-center gap-2">
          <LayoutsMenu />
          <GridPicker />
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        <section className="min-w-0 flex-1">
          <SubWindowGrid />
        </section>
        <InfoTab />
      </main>
    </div>
  );
}

export default function App() {
  // Read once — this tab's URL doesn't change over its lifetime (a pop-out
  // is always a fresh tab, never client-side navigation into or out of one).
  const [poppedOutId] = useState(parsePoppedOutWindowId);

  return (
    <WorkspaceProvider lockedMainWindowId={poppedOutId ?? undefined}>
      {poppedOutId ? <PoppedOutWindowShell windowId={poppedOutId} /> : <FullWorkspaceShell />}
    </WorkspaceProvider>
  );
}
