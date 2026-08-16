import { useMemo } from "react";
import { ChartWidget } from "../chart/ChartWidget";
import { SubWindowMenu } from "./SubWindowMenu";
import { useLiveCandles } from "../../hooks/useLiveCandles";
import { useFeatureEngineSeries } from "../../hooks/useFeatureEngineSeries";
import { generateMockOverlays } from "../../mocks/chartObjects";
import { computePriceIndicator } from "../../utils/indicators";
import { useWorkspace } from "../../state/WorkspaceContext";
import type { SubWindowConfig } from "../../types/workspace";

export function SubWindow({ config }: { config: SubWindowConfig }) {
  const { resolvedSymbol } = useWorkspace();
  const symbol = resolvedSymbol(config);

  // Requests config.timeframe directly from the backend (see
  // useLiveCandles' own docstring) rather than always pulling 1m and
  // resampling client-side — resample.ts's index-based bucketing is no
  // longer wired into the live render path (see that file's own header
  // note; candle_aggregator.py is now the single source of truth for
  // 5m/15m/1h, Polygon for 1d).
  const candles = useLiveCandles(symbol, config.timeframe);
  const overlays = useMemo(() => generateMockOverlays(candles), [candles]);

  // Backend-computed SMA/EMA/VWAP (confirmed decision #54, Stage 1 of the
  // chart migration) — a symbol+timeframe-scoped series lookup passed
  // into computePriceIndicator below, which uses it when available and
  // falls back to local computation otherwise (see that function's own
  // comment). Fetched unconditionally, even when config.priceIndicators
  // is empty or every instance is disabled — cheap (one request + one WS
  // subscription per sub-window, not per instance) and avoids a second
  // effect that only sometimes runs.
  const { series: featureSeries } = useFeatureEngineSeries(symbol, config.timeframe);

  // Overlay indicators (SMA/EMA/VWAP instances) computed into flat series for
  // ChartWidget. Keyed by instance.id, which is unique within a sub-window.
  const indicators = useMemo(
    () =>
      config.priceIndicators
        .filter((inst) => inst.enabled)
        .map((inst) => ({ key: inst.id, ...computePriceIndicator(candles, inst, featureSeries) })),
    [config.priceIndicators, candles, featureSeries]
  );

  return (
    <div className="flex h-full w-full flex-col bg-base-panel">
      <SubWindowMenu config={config} displaySymbol={symbol} />
      <div className="min-h-0 flex-1">
        <ChartWidget
          symbol={symbol}
          candles={candles}
          overlays={overlays}
          indicators={indicators}
          horizontalLevels={config.horizontalLevels}
          candleLimit={config.candleLimit}
          backgroundColor={config.backgroundColor}
          gridColor={config.gridColor}
          timeframe={config.timeframe}
          timer={config.timer}
          volumeAvg={config.volumeAvg}
          volumeBars={config.volumeBars}
        />
      </div>
    </div>
  );
}
