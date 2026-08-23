import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { broadcastSync, subscribeSync } from "./crossTabSync";
import {
  DEFAULT_CHART_BG,
  DEFAULT_CHART_STYLE,
  DEFAULT_GRID_COLOR,
  DEFAULT_SYMBOL,
  LINK_CONNECTOR_IDS,
  PRICE_INDICATOR_DEFAULT_LINE_WIDTH,
  createDefaultTimerConfig,
  createDefaultVolumeAvgConfig,
  createDefaultVolumeBarsConfig,
  createDefaultDailyLevelsConfig,
  createPriceIndicatorInstance,
  type ConnectorId,
  type GridLayout,
  type MainWindowState,
  type PriceIndicatorInstance,
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
  featureEngineCollapsed: boolean;
  featureEngineWidthPx: number;
  featureEnginePanelSymbol: string;

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
  setFeatureEngineCollapsed: (collapsed: boolean) => void;
  setFeatureEngineWidthPx: (width: number) => void;
  setFeatureEnginePanelSymbol: (symbol: string) => void;

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
    { id: "sw-0", connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "1m", liveTick: false, priceIndicators: [createPriceIndicatorInstance("EMA", 0, 9)], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    // 9/20/50 SMA on the 5m window — the exact motivating example for the
    // instance-based SMA system, shown live instead of making the person
    // configure it themselves to see it work (same rationale as the rest of
    // this function's comment above).
    { id: "sw-1", connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "5m", liveTick: false, priceIndicators: [createPriceIndicatorInstance("SMA", 0, 9), createPriceIndicatorInstance("SMA", 1, 20), createPriceIndicatorInstance("SMA", 2, 50)], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    { id: "sw-2", connector: "none", symbol: "TSLA", timeframe: "15m", liveTick: false, priceIndicators: [], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    { id: "sw-3", connector: 1, symbol: "AAPL", timeframe: "1h", liveTick: false, priceIndicators: [createPriceIndicatorInstance("EMA", 0, 20)], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
  ];
}

// New Main Windows seed their first cell onto connector 0 deliberately — since
// connectors are global, this immediately shows the cross-window linking
// working (it'll already display whatever symbol connector 0 currently holds)
// without the person needing to configure anything to see the feature.
function makeSecondaryMainWindowSubWindows(id: string): SubWindowConfig[] {
  return [
    { id: `${id}-sw-0`, connector: 0, symbol: DEFAULT_SYMBOL, timeframe: "15m", liveTick: false, priceIndicators: [createPriceIndicatorInstance("EMA", 0, 20)], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    { id: `${id}-sw-1`, connector: "none", symbol: "MSFT", timeframe: "1m", liveTick: false, priceIndicators: [], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    { id: `${id}-sw-2`, connector: "none", symbol: DEFAULT_SYMBOL, timeframe: "1m", liveTick: false, priceIndicators: [], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
    { id: `${id}-sw-3`, connector: "none", symbol: DEFAULT_SYMBOL, timeframe: "1m", liveTick: false, priceIndicators: [], horizontalLevels: [], candleLimit: "all", chartStyle: DEFAULT_CHART_STYLE, backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR, timer: createDefaultTimerConfig(), volumeAvg: createDefaultVolumeAvgConfig(), volumeBars: createDefaultVolumeBarsConfig(), dailyLevelsConfig: createDefaultDailyLevelsConfig() },
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
        timeframe: "1m", liveTick: false,
        priceIndicators: i === 0 ? [createPriceIndicatorInstance("EMA", 0, 9)] : [],
        horizontalLevels: [],
        candleLimit: "all",
        chartStyle: DEFAULT_CHART_STYLE,
        backgroundColor: DEFAULT_CHART_BG,
        gridColor: DEFAULT_GRID_COLOR,
        timer: createDefaultTimerConfig(),
        volumeAvg: createDefaultVolumeAvgConfig(),
        volumeBars: createDefaultVolumeBarsConfig(),
        dailyLevelsConfig: createDefaultDailyLevelsConfig(),
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
    featureEngineCollapsed: true, // starts collapsed — a second sidebar shouldn't grab space by default
    featureEngineWidthPx: 300,
    featureEnginePanelSymbol: DEFAULT_SYMBOL,
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

// Back-fills fields that didn't exist on SubWindowConfig at various earlier
// points (gridColor, timer, volumeAvg, priceIndicators, horizontalLevels,
// and priceIndicators[].showPriceLabel) — anything persisted to localStorage
// by an earlier version of the app won't have them, so reading them without
// this would throw at render time (e.g. `config.timer.enabled` on
// `undefined`).
//
// Also migrates the old fixed `indicators: IndicatorType[]` field (now
// removed from SubWindowConfig entirely) into real PriceIndicatorInstance
// entries: unlike SMA20/SMA50 (dropped, not migrated, in the previous pass
// — see git history / confirmed-decisions.md #40 — because the old fixed
// system carried no per-instance color/thickness for them to carry over),
// EMA9 and EMA20 map cleanly onto the new EMA overlay type with a known
// period and the exact same color the old fixed system used, so this is a
// real, faithful migration rather than a drop. "SMA20"/"SMA50" (if somehow
// still present from an even older session) are still dropped for the same
// reason as before.
const LEGACY_EMA_COLORS: Record<"EMA9" | "EMA20", string> = { EMA9: "#E3B341", EMA20: "#F778BA" };

function normalizeSubWindow(sw: SubWindowConfig & { indicators?: string[] }): SubWindowConfig {
  const migratedFromLegacyIndicators: PriceIndicatorInstance[] = (sw.indicators ?? [])
    .filter((i): i is "EMA9" | "EMA20" => i === "EMA9" || i === "EMA20")
    .map((i, idx) => ({
      id: `migrated-${i.toLowerCase()}-${Date.now().toString(36)}-${idx}`,
      type: "EMA" as const,
      enabled: true,
      period: i === "EMA9" ? 9 : 20,
      color: LEGACY_EMA_COLORS[i],
      lineWidth: PRICE_INDICATOR_DEFAULT_LINE_WIDTH,
      showPriceLabel: true,
    }));

  const { indicators: _legacyIndicators, ...rest } = sw;

  return {
    ...rest,
    priceIndicators: [
      // Cast to Partial here: the TYPE says showPriceLabel is always present,
      // but a session persisted before this field existed won't actually
      // have it at runtime — the fallback below is for that real case, not
      // a type-checking exercise.
      ...(sw.priceIndicators ?? []).map((p) => ({ showPriceLabel: true, ...(p as Partial<PriceIndicatorInstance>) }) as PriceIndicatorInstance),
      ...migratedFromLegacyIndicators,
    ],
    horizontalLevels: sw.horizontalLevels ?? [],
    // Back-fill for sessions persisted before chartStyle existed (decision
    // #73) — candlestick is what every prior saved layout was already
    // rendering unconditionally, so this is the only backfill value that
    // doesn't silently change an old layout's appearance.
    chartStyle: sw.chartStyle ?? DEFAULT_CHART_STYLE,
    gridColor: sw.gridColor ?? DEFAULT_GRID_COLOR,
    timer: sw.timer ?? createDefaultTimerConfig(),
    volumeAvg: sw.volumeAvg ?? createDefaultVolumeAvgConfig(),
    // Back-fill for sessions persisted before volumeBars existed — defaults
    // preserve the previous hardcoded look (two-color, same green/red as
    // the candles) rather than an unconfigured/empty state.
    volumeBars: sw.volumeBars ?? createDefaultVolumeBarsConfig(),
    // Back-fill for sessions persisted before Daily Levels existed
    // (decision #61) — defaults to disabled, same "opt-in" convention
    // createDefaultDailyLevelsConfig() itself already uses, so an old
    // saved layout doesn't suddenly show a new indicator nobody asked for.
    dailyLevelsConfig: sw.dailyLevelsConfig ?? createDefaultDailyLevelsConfig(),
  };
}

function normalizeMainWindow(w: MainWindowState): MainWindowState {
  return { ...w, subWindows: w.subWindows.map(normalizeSubWindow) };
}

function loadSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.mainWindows?.length || !parsed?.activeMainWindowId || !parsed?.connectorSymbols) return null;
    return { ...parsed, mainWindows: (parsed.mainWindows as MainWindowState[]).map(normalizeMainWindow) } as StoredSession;
  } catch {
    return null; // corrupt/missing storage just falls back to defaults
  }
}

function loadSavedLayouts(): SavedLayout[] {
  try {
    const raw = localStorage.getItem(SAVED_LAYOUTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return (parsed as SavedLayout[]).map((l) => ({ ...l, subWindows: l.subWindows.map(normalizeSubWindow) }));
  } catch {
    return [];
  }
}

/**
 * lockedMainWindowId: only set for a popped-out `/window/:id` tab (see
 * App.tsx). When present, this tab's `activeMainWindowId` is pinned to it
 * for the tab's whole lifetime — an incoming cross-tab sync updates this
 * tab's copy of `mainWindows` (so edits made elsewhere still show up here)
 * but never overrides which window THIS tab is looking at, since a
 * popped-out tab exists specifically to keep showing one particular window
 * on one particular monitor regardless of what the main tab's active tab
 * happens to be at any given moment.
 */
export function WorkspaceProvider({
  children,
  lockedMainWindowId,
}: {
  children: ReactNode;
  lockedMainWindowId?: string;
}) {
  const [mainWindows, setMainWindows] = useState<MainWindowState[]>(
    () => loadSession()?.mainWindows ?? [makeMainWindow(INITIAL_ID, "Layout 1", makeInitialSubWindows())]
  );
  const [activeMainWindowId, setActiveMainWindowId] = useState(
    () => lockedMainWindowId ?? loadSession()?.activeMainWindowId ?? INITIAL_ID
  );
  const [connectorSymbols, setConnectorSymbols] = useState<ConnectorSymbolMap>(
    () => loadSession()?.connectorSymbols ?? defaultConnectorSymbols()
  );
  const [savedLayouts, setSavedLayouts] = useState<SavedLayout[]>(loadSavedLayouts);

  // Last JSON this tab either wrote itself or just applied from an
  // incoming cross-tab sync message — lets the persistence effects below
  // tell "a genuine local change happened, go persist+broadcast it" apart
  // from "this state update IS the thing we just received," which would
  // otherwise write+broadcast right back and bounce forever between two
  // open tabs. See crossTabSync.ts's module doc for the fuller picture.
  const lastSyncedSessionRef = useRef<string>("");
  const lastSyncedLayoutsRef = useRef<string>("");

  // Auto-save the live session on every change — this is what makes "load the
  // same setup in the future" work even without ever pressing an explicit
  // save button (a page refresh keeps everything, tabs included), and now
  // also what every other open tab of this app picks up live via
  // broadcastSync (see crossTabSync.ts and the subscribeSync effect below).
  useEffect(() => {
    const json = JSON.stringify({ mainWindows, activeMainWindowId, connectorSymbols });
    if (json === lastSyncedSessionRef.current) return; // this IS what we just synced in — don't re-broadcast it
    try {
      localStorage.setItem(SESSION_KEY, json);
      lastSyncedSessionRef.current = json;
      broadcastSync("session");
    } catch {
      // ignore quota / private-browsing errors — session just won't persist
    }
  }, [mainWindows, activeMainWindowId, connectorSymbols]);

  useEffect(() => {
    const json = JSON.stringify(savedLayouts);
    if (json === lastSyncedLayoutsRef.current) return;
    try {
      localStorage.setItem(SAVED_LAYOUTS_KEY, json);
      lastSyncedLayoutsRef.current = json;
      broadcastSync("saved-layouts");
    } catch {
      // same as above
    }
  }, [savedLayouts]);

  // Cross-tab live sync — the other half of the multi-monitor pop-out.
  // Another tab (main workspace or another popped-out window) writing a
  // change broadcasts a lightweight "go re-read localStorage" ping (see
  // crossTabSync.ts); this re-runs the exact same load*() parsing used on
  // initial mount and applies the result. lockedMainWindowId tabs
  // deliberately skip applying the incoming activeMainWindowId — see this
  // function's own doc comment above for why. No-ops entirely if
  // BroadcastChannel isn't available (subscribeSync returns a no-op
  // unsubscribe in that case) — same single-tab-only behavior as before
  // this feature existed.
  useEffect(() => {
    return subscribeSync((msg) => {
      if (msg.kind === "session") {
        const loaded = loadSession();
        if (!loaded) return;
        const json = JSON.stringify({
          mainWindows: loaded.mainWindows,
          activeMainWindowId: lockedMainWindowId ?? loaded.activeMainWindowId,
          connectorSymbols: loaded.connectorSymbols,
        });
        lastSyncedSessionRef.current = json; // set BEFORE the state updates below, so the persistence effect above sees a match and skips re-writing/re-broadcasting
        setMainWindows(loaded.mainWindows);
        if (!lockedMainWindowId) setActiveMainWindowId(loaded.activeMainWindowId);
        setConnectorSymbols(loaded.connectorSymbols);
      } else if (msg.kind === "saved-layouts") {
        const loaded = loadSavedLayouts();
        lastSyncedLayoutsRef.current = JSON.stringify(loaded);
        setSavedLayouts(loaded);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  const setFeatureEngineCollapsed = (collapsed: boolean) => updateActive({ featureEngineCollapsed: collapsed });
  const setFeatureEngineWidthPx = (width: number) => updateActive({ featureEngineWidthPx: width });
  const setFeatureEnginePanelSymbol = (symbol: string) => updateActive({ featureEnginePanelSymbol: symbol });

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

  // Locked tabs (a popped-out /window/:id) never switch which window is
  // active — there's no tab strip rendered for them to click in the first
  // place (see App.tsx's PoppedOutWindowShell), but guarding the setter
  // itself too means that stays true even if something else ever calls it.
  const setActiveMainWindow = (id: string) => {
    if (lockedMainWindowId) return;
    setActiveMainWindowId(id);
  };

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
        subWindows: l.subWindows.map(normalizeSubWindow),
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
      featureEngineCollapsed: activeWindow.featureEngineCollapsed,
      featureEngineWidthPx: activeWindow.featureEngineWidthPx,
      featureEnginePanelSymbol: activeWindow.featureEnginePanelSymbol,

      connectorSymbols,

      setGridLayout,
      setRowHeights,
      setColWidths,
      updateSubWindow,
      setSubWindowSymbol,
      resolvedSymbol,
      setInfoCollapsed,
      setInfoWidthPx,
      setFeatureEngineCollapsed,
      setFeatureEngineWidthPx,
      setFeatureEnginePanelSymbol,

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
