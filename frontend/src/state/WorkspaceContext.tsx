import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  DEFAULT_CHART_BG,
  DEFAULT_SYMBOL,
  LINK_CONNECTOR_IDS,
  type ConnectorId,
  type GridLayout,
  type MainWindowState,
  type SavedLayout,
  type SubWindowConfig,
} from "../types/workspace";

type ConnectorSymbolMap = Record<Exclude<ConnectorId, "none">, string>;

interface WorkspaceContextValue {
  // Main Window tab management. Deliberately exposes only {id,label} here —
  // the tab strip doesn't need each window's full grid/sub-window state.
  mainWindows: { id: string; label: string }[];
  activeMainWindowId: string;
  addMainWindow: () => void;
  closeMainWindow: (id: string) => void;
  setActiveMainWindow: (id: string) => void;

  // The ACTIVE Main Window's state, flattened — everything below this line
  // reads/writes whichever window is currently active.
  gridLayout: GridLayout;
  rowHeights: number[];
  colWidths: number[];
  subWindows: SubWindowConfig[];
  infoCollapsed: boolean;
  infoWidthPx: number;

  // GLOBAL — shared across every Main Window. This is what makes "connector 0
  // in Layout 1" and "connector 0 in Layout 2" the same link group.
  connectorSymbols: ConnectorSymbolMap;

  setGridLayout: (layout: GridLayout) => void;
  setRowHeights: (heights: number[]) => void;
  setColWidths: (widths: number[]) => void;
  updateSubWindow: (id: string, patch: Partial<SubWindowConfig>) => void;
  setSubWindowSymbol: (id: string, symbol: string) => void;
  resolvedSymbol: (config: SubWindowConfig) => string;
  setInfoCollapsed: (collapsed: boolean) => void;
  setInfoWidthPx: (width: number) => void;

  // No-database save/load — everything lives in localStorage for now. Same
  // JSON shape this produces is what a future workspace_layouts API call
  // would send/receive, so this isn't throwaway work.
  savedLayouts: SavedLayout[];
  saveCurrentLayout: (name: string) => void;
  loadLayout: (id: string) => void;
  deleteLayout: (id: string) => void;
  exportLayouts: () => void;
  importLayouts: (json: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

const SESSION_KEY = "trading-workspace:session";
const SAVED_LAYOUTS_KEY = "trading-workspace:saved-layouts";

// The very first load only — demonstrates linking immediately instead of
// making the person configure it themselves to see it work. Windows 0 and 1
// share connector 0 (same symbol, different timeframe/indicators, exactly the
// 1m+EMA / 5m+SMA example from the spec). Window 2 is deliberately unlinked on
// a different symbol. Window 3 is on a second connector to show groups don't
// cross-talk. Grid changes after this use the generic fallback below.
function makeInitialSubWindows(): SubWindowConfig[] {
  return [
    { id: "sw-0", connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "1m", indicators: ["EMA9"], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: "sw-1", connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "5m", indicators: ["SMA20"], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: "sw-2", connector: "none", symbol: "TSLA", timeframe: "15m", indicators: [], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: "sw-3", connector: 1, symbol: "AAPL", timeframe: "1h", indicators: ["EMA20"], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
  ];
}

// New Main Windows seed their first cell onto connector 0 deliberately — since
// connectors are global, this immediately shows the cross-window linking
// working (it'll already display whatever symbol connector 0 currently holds)
// without the person needing to configure anything to see the feature.
function makeSecondaryMainWindowSubWindows(id: string): SubWindowConfig[] {
  return [
    { id: `${id}-sw-0`, connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "15m", indicators: ["EMA20"], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: `${id}-sw-1`, connector: "none", symbol: "MSFT", timeframe: "1m", indicators: [], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: `${id}-sw-2`, connector: "none", symbol: DEFAULT_SYMBOL, timeframe: "1m", indicators: [], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
    { id: `${id}-sw-3`, connector: "none", symbol: DEFAULT_SYMBOL, timeframe: "1m", indicators: [], candleLimit: "all", backgroundColor: DEFAULT_CHART_BG },
  ];
}

function makeDefaultSubWindows(rows: number, cols: number, prior: SubWindowConfig[]): SubWindowConfig[] {
  const count = rows * cols;
  const out: SubWindowConfig[] = [];
  for (let i = 0; i < count; i++) {
    if (prior[i]) {
      out.push(prior[i]);
    } else {
      out.push({
        id: `sw-${i}-${Date.now()}`,
        connector: "none",
        symbol: DEFAULT_SYMBOL,
        timeframe: "1m",
        indicators: i === 0 ? ["EMA9"] : [],
        candleLimit: "all",
        backgroundColor: DEFAULT_CHART_BG,
      });
    }
  }
  return out;
}

function makeMainWindow(id: string, label: string, subWindows: SubWindowConfig[]): MainWindowState {
  return {
    id,
    label,
    gridLayout: { rows: 2, cols: 2 },
    rowHeights: [0.5, 0.5],
    colWidths: [0.5, 0.5],
    subWindows,
    infoCollapsed: false,
    infoWidthPx: 300,
  };
}

const INITIAL_ID = "mw-1";

function defaultConnectorSymbols(): ConnectorSymbolMap {
  return {
    ...(Object.fromEntries(LINK_CONNECTOR_IDS.map((id) => [id, DEFAULT_SYMBOL])) as ConnectorSymbolMap),
    0: DEFAULT_SYMBOL,
    1: "AAPL",
  };
}

// Plain synchronous localStorage — this is a real app running in the user's
// own browser, not a Claude.ai artifact preview, so browser storage APIs are
// fully available (unlike in-chat artifacts, which can't use them).
interface StoredSession {
  mainWindows: MainWindowState[];
  activeMainWindowId: string;
  connectorSymbols: ConnectorSymbolMap;
}

function loadSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.mainWindows?.length || !parsed?.activeMainWindowId || !parsed?.connectorSymbols) return null;
    return parsed as StoredSession;
  } catch {
    return null; // corrupt/missing storage just falls back to defaults
  }
}

function loadSavedLayouts(): SavedLayout[] {
  try {
    const raw = localStorage.getItem(SAVED_LAYOUTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [mainWindows, setMainWindows] = useState<MainWindowState[]>(
    () => loadSession()?.mainWindows ?? [makeMainWindow(INITIAL_ID, "Layout 1", makeInitialSubWindows())]
  );
  const [activeMainWindowId, setActiveMainWindowId] = useState(
    () => loadSession()?.activeMainWindowId ?? INITIAL_ID
  );
  const [connectorSymbols, setConnectorSymbols] = useState<ConnectorSymbolMap>(
    () => loadSession()?.connectorSymbols ?? defaultConnectorSymbols()
  );
  const [savedLayouts, setSavedLayouts] = useState<SavedLayout[]>(loadSavedLayouts);

  // Auto-save the live session on every change — this is what makes "load the
  // same setup in the future" work even without ever pressing an explicit
  // save button (a page refresh keeps everything, tabs included).
  useEffect(() => {
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify({ mainWindows, activeMainWindowId, connectorSymbols }));
    } catch {
      // ignore quota / private-browsing errors — session just won't persist
    }
  }, [mainWindows, activeMainWindowId, connectorSymbols]);

  useEffect(() => {
    try {
      localStorage.setItem(SAVED_LAYOUTS_KEY, JSON.stringify(savedLayouts));
    } catch {
      // same as above
    }
  }, [savedLayouts]);

  const activeWindow = mainWindows.find((w) => w.id === activeMainWindowId) ?? mainWindows[0];

  const updateActive = (patch: Partial<MainWindowState> | ((w: MainWindowState) => Partial<MainWindowState>)) => {
    setMainWindows((prev) =>
      prev.map((w) => {
        if (w.id !== activeMainWindowId) return w;
        const p = typeof patch === "function" ? patch(w) : patch;
        return { ...w, ...p };
      })
    );
  };

  const setGridLayout = (layout: GridLayout) => {
    updateActive((w) => ({
      gridLayout: layout,
      rowHeights: Array(layout.rows).fill(1 / layout.rows),
      colWidths: Array(layout.cols).fill(1 / layout.cols),
      subWindows: makeDefaultSubWindows(layout.rows, layout.cols, w.subWindows),
    }));
  };

  const setRowHeights = (heights: number[]) => updateActive({ rowHeights: heights });
  const setColWidths = (widths: number[]) => updateActive({ colWidths: widths });

  const updateSubWindow = (id: string, patch: Partial<SubWindowConfig>) => {
    updateActive((w) => ({ subWindows: w.subWindows.map((sw) => (sw.id === id ? { ...sw, ...patch } : sw)) }));
  };

  const setSubWindowSymbol = (id: string, symbol: string) => {
    const target = activeWindow.subWindows.find((sw) => sw.id === id);
    if (!target) return;
    if (target.connector === "none") {
      updateSubWindow(id, { symbol });
    } else {
      const connector = target.connector;
      setConnectorSymbols((prev) => ({ ...prev, [connector]: symbol }));
    }
  };

  const resolvedSymbol = (config: SubWindowConfig) =>
    config.connector === "none" ? config.symbol : connectorSymbols[config.connector];

  const setInfoCollapsed = (collapsed: boolean) => updateActive({ infoCollapsed: collapsed });
  const setInfoWidthPx = (width: number) => updateActive({ infoWidthPx: width });

  const addMainWindow = () => {
    const id = `mw-${Date.now()}`;
    const label = `Layout ${mainWindows.length + 1}`;
    setMainWindows((prev) => [...prev, makeMainWindow(id, label, makeSecondaryMainWindowSubWindows(id))]);
    setActiveMainWindowId(id);
  };

  const closeMainWindow = (id: string) => {
    if (mainWindows.length <= 1) return; // always keep at least one
    const next = mainWindows.filter((w) => w.id !== id);
    setMainWindows(next);
    if (activeMainWindowId === id) setActiveMainWindowId(next[0].id);
  };

  const setActiveMainWindow = (id: string) => setActiveMainWindowId(id);

  // --- Saved layouts (no database yet — localStorage stands in for it) ---

  const saveCurrentLayout = (name: string) => {
    const usedConnectors = new Set(
      activeWindow.subWindows
        .map((sw) => sw.connector)
        .filter((c): c is Exclude<ConnectorId, "none"> => c !== "none")
    );
    const snapshot: Partial<ConnectorSymbolMap> = {};
    usedConnectors.forEach((c) => {
      snapshot[c] = connectorSymbols[c];
    });

    const saved: SavedLayout = {
      id: `layout-${Date.now()}`,
      name,
      savedAt: new Date().toISOString(),
      gridLayout: activeWindow.gridLayout,
      rowHeights: activeWindow.rowHeights,
      colWidths: activeWindow.colWidths,
      subWindows: activeWindow.subWindows,
      connectorSymbolsSnapshot: snapshot,
    };
    setSavedLayouts((prev) => [...prev, saved]);
  };

  const loadLayout = (id: string) => {
    const layout = savedLayouts.find((l) => l.id === id);
    if (!layout) return;
    // Fresh ids on load so a loaded sub-window can never collide with an id
    // already in use elsewhere in the app.
    const freshSubWindows = layout.subWindows.map((sw, i) => ({ ...sw, id: `sw-loaded-${Date.now()}-${i}` }));
    updateActive({
      label: layout.name,
      gridLayout: layout.gridLayout,
      rowHeights: layout.rowHeights,
      colWidths: layout.colWidths,
      subWindows: freshSubWindows,
    });
    // Re-assert the connector symbols this layout was built around — makes
    // "Load" faithfully reproduce what you saved even if those connectors
    // have since been changed by other windows/tabs.
    setConnectorSymbols((prev) => ({ ...prev, ...layout.connectorSymbolsSnapshot }));
  };

  const deleteLayout = (id: string) => setSavedLayouts((prev) => prev.filter((l) => l.id !== id));

  const exportLayouts = () => {
    const blob = new Blob([JSON.stringify(savedLayouts, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "trading-workspace-layouts.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const importLayouts = (json: string) => {
    try {
      const parsed = JSON.parse(json);
      if (!Array.isArray(parsed)) return;
      const withFreshIds = (parsed as SavedLayout[]).map((l, i) => ({
        ...l,
        id: `layout-${Date.now()}-${i}`,
      }));
      setSavedLayouts((prev) => [...prev, ...withFreshIds]);
    } catch {
      // malformed file — silently ignored, nothing to recover
    }
  };

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      mainWindows: mainWindows.map((w) => ({ id: w.id, label: w.label })),
      activeMainWindowId,
      addMainWindow,
      closeMainWindow,
      setActiveMainWindow,

      gridLayout: activeWindow.gridLayout,
      rowHeights: activeWindow.rowHeights,
      colWidths: activeWindow.colWidths,
      subWindows: activeWindow.subWindows,
      infoCollapsed: activeWindow.infoCollapsed,
      infoWidthPx: activeWindow.infoWidthPx,

      connectorSymbols,

      setGridLayout,
      setRowHeights,
      setColWidths,
      updateSubWindow,
      setSubWindowSymbol,
      resolvedSymbol,
      setInfoCollapsed,
      setInfoWidthPx,

      savedLayouts,
      saveCurrentLayout,
      loadLayout,
      deleteLayout,
      exportLayouts,
      importLayouts,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mainWindows, activeMainWindowId, activeWindow, connectorSymbols, savedLayouts]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}
