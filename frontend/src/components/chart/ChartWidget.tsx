import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineWidth,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, ChartObject } from "../../types/market";
import type { IndicatorPoint } from "../../utils/indicators";
import type { CandleLimit, DailyLevelsConfig, HorizontalLevelInstance, LineStyleOption, Timeframe, TimerConfig, VolumeAvgIndicatorConfig, VolumeBarsConfig } from "../../types/workspace";
import { DEFAULT_GRID_COLOR, createDefaultDailyLevelsConfig, createDefaultVolumeBarsConfig } from "../../types/workspace";
import type { DailyLevelWireShape } from "../../services/api-client";
import { dayAverageVolume, trailingAverageVolume } from "../../utils/volumeAverages";
import { computeHorizontalLevel } from "../../utils/indicators";
import { TimerBadge } from "./TimerBadge";

export interface IndicatorSeries {
  key: string;
  label: string;
  color: string;
  lineWidth?: number; // px — fractional allowed (e.g. 1.5), see the
  // PRICE_INDICATOR_LINE_WIDTH_STEP comment in types/workspace.ts. Defaults
  // to 2 when omitted (legacy callers).
  showPriceLabel?: boolean; // last-value tag near the price axis; defaults to true
  data: IndicatorPoint[];
}

const LINE_STYLE_MAP: Record<LineStyleOption, LineStyle> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
};

interface RectBox {
  label: string;
  color: string;
  borderColor: string;
  topPx: number;
  bottomPx: number;
}

interface ChartWidgetProps {
  symbol: string;
  candles: Candle[];
  overlays: ChartObject[];
  indicators?: IndicatorSeries[];
  horizontalLevels?: HorizontalLevelInstance[];
  // Backend-computed values for the levels above (confirmed decision
  // #58) — see computeHorizontalLevel's own comment in utils/indicators.ts
  // for the lookup convention. Optional: an undefined/missing map means
  // every level falls back to local computation, same as before this prop
  // existed.
  horizontalLevelValues?: Record<string, number | undefined>;
  candleLimit?: CandleLimit;
  backgroundColor?: string;
  gridColor?: string;
  timeframe?: Timeframe;
  timer?: TimerConfig;
  volumeAvg?: VolumeAvgIndicatorConfig;
  volumeBars?: VolumeBarsConfig;
  // Daily Levels (confirmed decisions #59-#61) — deliberately NOT part of
  // horizontalLevels/horizontalLevelValues above: those are a small,
  // FIXED set of named types (PDH, CAM_R1, ...), one HorizontalLevelInstance
  // per type the user opted into. Daily Levels is a variable-COUNT,
  // backend-clustered list with no local fallback (DailyLevelsConfig's own
  // comment) — a genuinely different shape, so it gets its own prop pair
  // rather than being forced into that one.
  dailyLevels?: DailyLevelWireShape[];
  dailyLevelsConfig?: DailyLevelsConfig;
}

const BULL = "#3FB950";
const BEAR = "#F85149";
const SIGNAL = "#E3B341";

// Stable reference for the default params below — an inline object
// literal as a default value would be a fresh reference on every render
// a caller omits the prop, which is harmless here (SubWindow.tsx always
// passes config.volumeBars/config.dailyLevelsConfig explicitly) but costs
// nothing to avoid.
const DEFAULT_VOLUME_BARS = createDefaultVolumeBarsConfig();
const DEFAULT_DAILY_LEVELS_CONFIG = createDefaultDailyLevelsConfig();

function volumeBarColor(candle: Candle, config: VolumeBarsConfig): string {
  return config.colorMode === "one_color" ? config.singleColor : candle.close >= candle.open ? config.upColor : config.downColor;
}

// Structural diff between two candle arrays, used by the data effect below
// to decide setData()+re-pin (a genuine reset) vs. update() (a live tick/
// candle refresh that must NOT touch zoom/pan — see that effect's comment
// for why this exists). Every branch of useLiveCandles' state update
// either replaces the whole array (symbol switch, backfill fetch) or
// appends exactly one new candle at the end (`[...prev, candle]`), and
// resampleCandles' deterministic index-based bucketing means a resampled
// array either keeps every bar's `time` identical except the last (the new
// 1m candle merged into the still-open bucket) or keeps every existing bar
// identical and gains one more (the new 1m candle opened a fresh bucket) —
// so comparing `time` fields end-to-end is a reliable, cheap way to tell
// "this is the same series, just refreshed" from "this is a different
// dataset" (symbol/timeframe switch, or anything unexpected) without
// needing reference equality, which resample's fresh object allocations
// each call would never give us anyway.
type CandleDiff = "reset" | "update_last" | "append_one";

function diffCandles(prev: Candle[], next: Candle[]): CandleDiff {
  if (prev.length === 0 || next.length === 0) return "reset";
  if (next.length === prev.length) {
    for (let i = 0; i < prev.length - 1; i++) {
      if (prev[i].time !== next[i].time) return "reset";
    }
    return prev[prev.length - 1].time === next[prev.length - 1].time ? "update_last" : "reset";
  }
  if (next.length === prev.length + 1) {
    for (let i = 0; i < prev.length; i++) {
      if (prev[i].time !== next[i].time) return "reset";
    }
    return "append_one";
  }
  return "reset";
}

export function ChartWidget({
  symbol,
  candles,
  overlays,
  indicators = [],
  horizontalLevels = [],
  horizontalLevelValues,
  candleLimit = "all",
  backgroundColor = "#131720",
  gridColor = DEFAULT_GRID_COLOR,
  timeframe,
  timer,
  volumeAvg,
  volumeBars = DEFAULT_VOLUME_BARS,
  dailyLevels = [],
  dailyLevelsConfig = DEFAULT_DAILY_LEVELS_CONFIG,
}: ChartWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lineSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const [rectBoxes, setRectBoxes] = useState<RectBox[]>([]);
  // Previous render's candles/candleLimit — compared against the current
  // render in the data effect below to tell a live update from a real
  // reset. Starts empty/"all" so the very first run is always treated as a
  // reset (correct: there's nothing to diff against yet).
  const prevCandlesRef = useRef<Candle[]>([]);
  const prevCandleLimitRef = useRef<CandleLimit>("all");

  // Chart lifecycle — created once per mount, torn down on unmount. Deliberately
  // NOT keyed on symbol/candles — recreating the chart on every symbol or
  // timeframe switch would reset zoom/crosshair state for no reason. Data
  // updates are handled by the separate effect below instead.
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#131720" },
        textColor: "#7D8590",
        fontFamily: "IBM Plex Mono, ui-monospace, monospace",
      },
      grid: {
        vertLines: { color: DEFAULT_GRID_COLOR },
        horzLines: { color: DEFAULT_GRID_COLOR },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1E2530" },
      timeScale: { borderColor: "#1E2530", timeVisible: true },
      autoSize: true,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: BULL,
      downColor: BEAR,
      borderVisible: false,
      wickUpColor: BULL,
      wickDownColor: BEAR,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      lineSeriesRef.current.clear();
    };
  }, []);

  // Background + grid line color — applied live via applyOptions rather than
  // folded into chart creation, so changing either (or loading a saved layout
  // with different colors) never tears down the chart and loses zoom/pan state.
  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: backgroundColor } },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
    });
  }, [backgroundColor, gridColor]);

  // Candle/volume data updates — runs whenever candles or the candle-count
  // limit change (symbol switch, timeframe switch, the "Candles" stepper,
  // or a live candle arriving/refreshing via useLiveCandles).
  //
  // Bug fix: this used to unconditionally call setData() + either
  // setVisibleLogicalRange() or fitContent() on every single run — so
  // every live candle update silently re-pinned the view, overriding
  // whatever zoom/pan the person had set manually (the reported "zoom
  // keeps changing with each live candle" bug). Now: a genuine reset
  // (symbol/timeframe switch, or the person explicitly changing the
  // candle-count limit) still does the full setData()+re-pin, exactly as
  // before. A live update that's just "the last bar refreshed" or "one new
  // bar appended" — diffCandles' "update_last"/"append_one" — instead uses
  // series.update(), which never touches the time scale's visible range at
  // all, and which Lightweight Charts specifically auto-scrolls to reveal
  // (pushing the oldest bar off the left) ONLY when the person is already
  // viewing the live edge — exactly the "act like a fixed zoom; if no
  // space, push the oldest candle out" behavior that was asked for. If
  // they've scrolled back into history, update() leaves them there.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !volumeSeries || !chart) return;

    const toCandlePoint = (c: Candle) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });
    const toVolumePoint = (c: Candle) => ({
      time: c.time as UTCTimestamp,
      value: c.volume,
      color: volumeBarColor(c, volumeBars),
    });

    // An explicit candle-count change is treated the same as a reset even
    // if the underlying data itself only appended one bar — the person
    // just asked for a different pinned window, so it should apply
    // immediately rather than waiting for the next diffable update.
    const limitChanged = prevCandleLimitRef.current !== candleLimit;
    const diff = limitChanged ? "reset" : diffCandles(prevCandlesRef.current, candles);

    if (diff === "reset") {
      candleSeries.setData(candles.map(toCandlePoint));
      volumeSeries.setData(candles.map(toVolumePoint));

      // "Fixed number of candles, new ones replace old ones" — implemented
      // as a visible-range constraint pinned to the latest N bars, not
      // data deletion. Only applied on a genuine reset now (see comment
      // above) — a live update no longer re-runs this.
      const total = candles.length;
      if (candleLimit !== "all" && candleLimit < total) {
        chart.timeScale().setVisibleLogicalRange({ from: total - candleLimit, to: total + 1 });
      } else {
        chart.timeScale().fitContent();
      }
    } else if (candles.length > 0) {
      const last = candles[candles.length - 1];
      candleSeries.update(toCandlePoint(last));
      volumeSeries.update(toVolumePoint(last));
    }

    prevCandlesRef.current = candles;
    prevCandleLimitRef.current = candleLimit;
    // volumeBars is deliberately excluded — a color/mode/visibility change
    // is handled by its own effect below (recolors without touching data
    // identity or zoom), not by re-running this one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, candleLimit]);

  // Volume bar visibility/color — recolors (or hides) every existing bar
  // immediately when the customization changes, without touching the
  // candle series or the time scale's zoom/pan at all. Kept separate from
  // the data effect above for the same reason the background/grid effect
  // is separate from the data effect: a cosmetic-only change should never
  // be able to disturb zoom, and keeping it in its own effect makes that
  // true by construction rather than by careful conditionals inside one
  // shared effect. Disabling collapses the volume price scale's margins to
  // zero height (not just hiding the bars) so the candles actually reclaim
  // the vertical space rather than leaving an empty strip at the bottom.
  useEffect(() => {
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!volumeSeries || !chart) return;

    volumeSeries.applyOptions({ visible: volumeBars.enabled });
    chart.priceScale("volume").applyOptions({
      scaleMargins: volumeBars.enabled ? { top: 0.82, bottom: 0 } : { top: 1, bottom: 0 },
    });
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: volumeBarColor(c, volumeBars),
      }))
    );
    // candles is deliberately excluded — this effect only needs to re-run
    // when the customization itself changes; it still reads the current
    // render's candles from closure, same pattern as the indicators effect
    // below excluding a dependency it still reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [volumeBars]);

  // Overlay application — re-runs whenever the backend (or mock data) sends new
  // chart objects. This is the exact function that later just gets called from
  // a WebSocket handler instead of a prop change — no rewrite needed.
  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series) return;

    const priceLines: ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[] = [];
    const markers: Parameters<ISeriesApi<"Candlestick">["setMarkers"]>[0] = [];
    const rects: { label: string; color: string; borderColor: string; top: number; bottom: number }[] = [];

    for (const obj of overlays) {
      if (obj.type === "horizontal_line") {
        priceLines.push(
          series.createPriceLine({
            price: obj.price,
            color: obj.color ?? "#58A6FF",
            lineWidth: 2,
            lineStyle: 2, // dashed
            axisLabelVisible: true,
            title: obj.label,
          })
        );
      } else if (obj.type === "marker") {
        markers.push({
          time: obj.time as UTCTimestamp,
          position: obj.position === "BUY" ? "belowBar" : "aboveBar",
          color: SIGNAL,
          shape: obj.position === "BUY" ? "arrowUp" : "arrowDown",
          text: `${obj.position} ${obj.confidence}%`,
        });
      } else if (obj.type === "rectangle") {
        rects.push({
          label: obj.label,
          color: obj.color ?? "rgba(227,179,65,0.12)",
          borderColor: obj.borderColor ?? "rgba(255,255,255,0.25)",
          top: obj.top,
          bottom: obj.bottom,
        });
      }
    }

    series.setMarkers(markers);

    const positionRects = () => {
      setRectBoxes(
        rects.map((r) => ({
          label: r.label,
          color: r.color,
          borderColor: r.borderColor,
          topPx: series.priceToCoordinate(r.top) ?? 0,
          bottomPx: series.priceToCoordinate(r.bottom) ?? 0,
        }))
      );
    };

    positionRects();
    chart.timeScale().subscribeVisibleLogicalRangeChange(positionRects);

    return () => {
      priceLines.forEach((line) => series.removePriceLine(line));
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(positionRects);
    };
  }, [overlays]);

  // Volume average lines — up to 4 horizontal price lines drawn on the volume
  // pane's own price scale (via createPriceLine, same mechanism the
  // horizontal_line overlay above uses on the candle series). Dotted (vs. the
  // overlay lines' dashed) purely so the two families are visually distinct.
  // Re-drawn whenever candles or the line configs change; cleanup removes the
  // previous set first so toggling a line off (or changing its bar count)
  // never leaves a stale line behind.
  useEffect(() => {
    const series = volumeSeriesRef.current;
    if (!series || !volumeAvg?.enabled) return;

    const priceLines = volumeAvg.lines
      .filter((line) => line.enabled)
      .map((line) => {
        const value = line.adjustable ? trailingAverageVolume(candles, line.barCount) : dayAverageVolume(candles);
        return series.createPriceLine({
          price: value,
          color: line.color,
          lineWidth: 1,
          lineStyle: 1, // dotted
          axisLabelVisible: true,
          title: line.label,
        });
      });

    return () => {
      priceLines.forEach((line) => series.removePriceLine(line));
    };
  }, [candles, volumeAvg]);

  // Horizontal level indicators (Previous Day Close/High/Low, Pre-Market
  // High/Low, Camarilla Pivots, VPOC) — drawn on the candle series via
  // createPriceLine, same mechanism as the backend `overlays` block above,
  // but with per-instance color/width/style/label-visibility instead of a
  // fixed style. Recreated on every candles/config change (not diffed via
  // applyOptions like the indicator line series above) — price lines are
  // cheap to recreate and this matches the existing overlays/volumeAvg
  // precedent in this same file.
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    const priceLines = horizontalLevels
      .filter((level) => level.enabled)
      .map((level) => computeHorizontalLevel(candles, level, horizontalLevelValues))
      .filter((resolved): resolved is NonNullable<typeof resolved> => resolved !== undefined)
      .map((resolved) =>
        series.createPriceLine({
          price: resolved.price,
          color: resolved.color,
          lineWidth: Math.min(4, Math.max(1, Math.round(resolved.lineWidth))) as LineWidth,
          lineStyle: LINE_STYLE_MAP[resolved.lineStyle],
          axisLabelVisible: resolved.showPriceLabel,
          title: resolved.label,
        })
      );

    return () => {
      priceLines.forEach((line) => series.removePriceLine(line));
    };
  }, [candles, horizontalLevels, horizontalLevelValues]);

  // Daily Levels (confirmed decisions #59-#62) — one price line per
  // backend-clustered zone, filtered by minStrength AND an optional
  // price range (minPrice/maxPrice — decision #62, added specifically
  // because a symbol's full 180-day price spread crowded out other
  // indicators; a band around the current price is a more direct fix
  // than strength filtering alone), drawn with a SINGLE uniform
  // color/width from dailyLevelsConfig (Saqib's own call: one color for
  // all of them, not a per-level gradient/opacity scheme). Strength is
  // communicated as a short text tag on the line's title instead —
  // "DL-N" (decision #62 shortened this from the original "Daily Level
  // · Strength N") — rather than encoded visually, so it reads as an
  // explicit number without eating pane width. Same createPriceLine
  // mechanism as horizontalLevels above, same recreate-on-every-change
  // posture (price lines are cheap), but its own effect since it's
  // keyed off a genuinely different prop pair, not an instance list.
  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series || !dailyLevelsConfig.enabled) return;

    const priceLines = dailyLevels
      .filter((level) => level.strength >= dailyLevelsConfig.minStrength)
      .filter((level) => dailyLevelsConfig.minPrice == null || level.price >= dailyLevelsConfig.minPrice)
      .filter((level) => dailyLevelsConfig.maxPrice == null || level.price <= dailyLevelsConfig.maxPrice)
      .map((level) =>
        series.createPriceLine({
          price: level.price,
          color: dailyLevelsConfig.color,
          lineWidth: Math.min(4, Math.max(1, Math.round(dailyLevelsConfig.lineWidth))) as LineWidth,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: dailyLevelsConfig.showPriceLabels,
          title: `DL-${level.strength}`,
        })
      );

    return () => {
      priceLines.forEach((line) => series.removePriceLine(line));
    };
  }, [dailyLevels, dailyLevelsConfig]);

  // Indicator line series — created/updated/removed as the sub-window's indicator
  // selection changes. Keyed so toggling one indicator doesn't touch the others.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const activeKeys = new Set(indicators.map((ind) => ind.key));

    for (const [key, series] of lineSeriesRef.current.entries()) {
      if (!activeKeys.has(key)) {
        chart.removeSeries(series);
        lineSeriesRef.current.delete(key);
      }
    }

    for (const ind of indicators) {
      // Lightweight Charts' TS type restricts lineWidth to 1|2|3|4, but the
      // installed v4.2.3's Line-series renderer passes it straight into the
      // canvas 2D context's lineWidth (a float property) with no
      // rounding — verified by reading the shipped source, not assumed. So
      // a fractional value like 1.5 genuinely renders as an intermediate
      // thickness; this cast is deliberate, not a type-safety shortcut. See
      // PRICE_INDICATOR_LINE_WIDTH_STEP in types/workspace.ts for the full
      // explanation and the caveat about this being version-coupled.
      const lineWidth = Math.min(4, Math.max(1, ind.lineWidth ?? 2)) as unknown as LineWidth;
      const showPriceLabel = ind.showPriceLabel ?? true;
      let series = lineSeriesRef.current.get(ind.key);
      if (!series) {
        series = chart.addLineSeries({
          color: ind.color,
          lineWidth,
          title: ind.label,
          priceLineVisible: false,
          lastValueVisible: showPriceLabel,
        });
        lineSeriesRef.current.set(ind.key, series);
      } else {
        // Color/thickness/label/price-label-visibility are all live-editable
        // per instance after the series already exists — applied on every
        // render rather than only at creation, or a color pick / thickness
        // step / checkbox toggle wouldn't show until the series was torn
        // down and recreated.
        series.applyOptions({ color: ind.color, lineWidth, title: ind.label, lastValueVisible: showPriceLabel });
      }
      series.setData(ind.data.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indicators, candles]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {timeframe && timer && <TimerBadge timeframe={timeframe} timer={timer} />}
      {rectBoxes.map((box, i) => (
        <div
          key={i}
          className="pointer-events-none absolute left-0 right-16 border-t border-b border-dashed"
          style={{
            top: Math.min(box.topPx, box.bottomPx),
            height: Math.abs(box.bottomPx - box.topPx),
            backgroundColor: box.color,
            borderColor: box.borderColor,
          }}
        >
          <span className="absolute left-2 top-1 font-mono text-[10px] uppercase tracking-wide text-text-primary/70">
            {box.label}
          </span>
        </div>
      ))}
    </div>
  );
}
