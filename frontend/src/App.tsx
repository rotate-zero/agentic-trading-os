import { WorkspaceProvider } from "./state/WorkspaceContext";
import { GridPicker } from "./components/workspace/GridPicker";
import { LayoutsMenu } from "./components/workspace/LayoutsMenu";
import { MainWindowTabs } from "./components/workspace/MainWindowTabs";
import { SubWindowGrid } from "./components/workspace/SubWindowGrid";
import { InfoTab } from "./components/workspace/InfoTab";

function WorkspaceShell() {
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

export default function App() {
  return (
    <WorkspaceProvider>
      <WorkspaceShell />
    </WorkspaceProvider>
  );
}
