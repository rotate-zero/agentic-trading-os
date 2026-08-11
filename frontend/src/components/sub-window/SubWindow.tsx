import { useMemo } from "react";
import { ChartWidget } from "../chart/ChartWidget";
import { SubWindowMenu } from "./SubWindowMenu";
import { useLiveCandles } from "../../hooks/useLiveCandles";
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

  // Overlay indicators (SMA/EMA/VWAP instances) computed into flat series for
  // ChartWidget. Keyed by instance.id, which is unique within a sub-window.
  const indicators = useMemo(
    () =>
      config.priceIndicators
        .filter((inst) => inst.enabled)
        .map((inst) => ({ key: inst.id, ...computePriceIndicator(candles, inst) })),
    [config.priceIndicators, candles]
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
