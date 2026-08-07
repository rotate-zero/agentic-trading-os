import { useEffect, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, ChartObject } from "../../types/market";
import type { IndicatorPoint } from "../../utils/indicators";
import type { CandleLimit, Timeframe, TimerConfig, VolumeAvgIndicatorConfig } from "../../types/workspace";
import { DEFAULT_GRID_COLOR } from "../../types/workspace";
import { dayAverageVolume, trailingAverageVolume } from "../../utils/volumeAverages";
import { TimerBadge } from "./TimerBadge";

export interface IndicatorSeries {
  key: string;
  label: string;
  color: string;
  lineWidth?: number; // px, 1-4 — defaults to 2 when omitted (legacy EMA9/EMA20 callers)
  data: IndicatorPoint[];
}

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
  candleLimit?: CandleLimit;
  backgroundColor?: string;
  gridColor?: string;
  timeframe?: Timeframe;
  timer?: TimerConfig;
  volumeAvg?: VolumeAvgIndicatorConfig;
}

const BULL = "#3FB950";
const BEAR = "#F85149";
const SIGNAL = "#E3B341";

export function ChartWidget({
  symbol,
  candles,
  overlays,
  indicators = [],
  candleLimit = "all",
  backgroundColor = "#131720",
  gridColor = DEFAULT_GRID_COLOR,
  timeframe,
  timer,
  volumeAvg,
}: ChartWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lineSeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const [rectBoxes, setRectBoxes] = useState<RectBox[]>([]);

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

  // Candle/volume data updates — runs whenever candles change for any reason
  // (symbol switch, timeframe switch, or eventually a live WebSocket push).
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !volumeSeries || !chart) return;

    candleSeries.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "rgba(63,185,80,0.5)" : "rgba(248,81,73,0.5)",
      }))
    );

    // "Fixed number of candles, new ones replace old ones" — implemented as a
    // visible-range constraint pinned to the latest N bars, not data deletion.
    // Re-running whenever candles OR the limit changes means it re-pins on
    // every update, which is exactly what "stays pinned as new candles arrive"
    // needs once this is fed by a live feed instead of static mock data.
    const total = candles.length;
    if (candleLimit !== "all" && candleLimit < total) {
      chart.timeScale().setVisibleLogicalRange({ from: total - candleLimit, to: total + 1 });
    } else {
      chart.timeScale().fitContent();
    }
  }, [candles, candleLimit]);

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
      // Lightweight Charts only accepts 1|2|3|4 for lineWidth — clamp rather
      // than trust the caller, since PriceIndicatorInstance's lineWidth is
      // user-typed-adjacent (a stepper, but still worth defending here too).
      const lineWidth = Math.min(4, Math.max(1, Math.round(ind.lineWidth ?? 2))) as 1 | 2 | 3 | 4;
      let series = lineSeriesRef.current.get(ind.key);
      if (!series) {
        series = chart.addLineSeries({
          color: ind.color,
          lineWidth,
          title: ind.label,
          priceLineVisible: false,
          lastValueVisible: true,
        });
        lineSeriesRef.current.set(ind.key, series);
      } else {
        // Color/thickness/label are live-editable per instance (the new SMA
        // system) after the series already exists — applied on every render
        // rather than only at creation, or a color pick / thickness step
        // wouldn't show until the series was torn down and recreated.
        series.applyOptions({ color: ind.color, lineWidth, title: ind.label });
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
