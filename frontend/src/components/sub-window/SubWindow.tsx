import { useMemo } from "react";
import { ChartWidget } from "../chart/ChartWidget";
import { SubWindowMenu } from "./SubWindowMenu";
import { useLiveCandles } from "../../hooks/useLiveCandles";
import { generateMockOverlays } from "../../mocks/chartObjects";
import { resampleCandles } from "../../utils/resample";
import { computeIndicator, computePriceIndicator } from "../../utils/indicators";
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

  // Legacy fixed-preset indicators (EMA9/EMA20) and the new instance-based
  // price indicators (SMA today) are two separate config lists but merge
  // into one flat series array for ChartWidget, which doesn't need to know
  // the two systems exist. Keyed by IndicatorType string vs. instance.id
  // respectively — both are unique within a sub-window, so no collision risk
  // combining them into one Map inside ChartWidget.
  const legacyIndicators = useMemo(
    () => config.indicators.map((type) => ({ key: type, ...computeIndicator(candles, type) })),
    [config.indicators, candles]
  );
  const priceIndicators = useMemo(
    () =>
      config.priceIndicators
        .filter((inst) => inst.enabled)
        .map((inst) => ({ key: inst.id, ...computePriceIndicator(candles, inst) })),
    [config.priceIndicators, candles]
  );
  const indicators = useMemo(
    () => [...legacyIndicators, ...priceIndicators],
    [legacyIndicators, priceIndicators]
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
          candleLimit={config.candleLimit}
          backgroundColor={config.backgroundColor}
          gridColor={config.gridColor}
          timeframe={config.timeframe}
          timer={config.timer}
          volumeAvg={config.volumeAvg}
        />
      </div>
    </div>
  );
}
