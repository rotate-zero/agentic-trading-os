import { useMemo } from "react";
import { ChartWidget } from "../chart/ChartWidget";
import { SubWindowMenu } from "./SubWindowMenu";
import { useLiveCandles } from "../../hooks/useLiveCandles";
import { generateMockOverlays } from "../../mocks/chartObjects";
import { resampleCandles } from "../../utils/resample";
import { computePriceIndicator } from "../../utils/indicators";
import { useWorkspace } from "../../state/WorkspaceContext";
import type { SubWindowConfig } from "../../types/workspace";

export function SubWindow({ config }: { config: SubWindowConfig }) {
  const { resolvedSymbol } = useWorkspace();
  const symbol = resolvedSymbol(config);

  const oneMinCandles = useLiveCandles(symbol);
  const candles = useMemo(
    () => resampleCandles(oneMinCandles, config.timeframe),
    [oneMinCandles, config.timeframe]
  );
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
