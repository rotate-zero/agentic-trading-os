import { useEffect, useState } from "react";
import { fetchCandles, subscribeSymbol, ApiError } from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

/**
 * Latest price per symbol, for compact multi-symbol views (the
 * watchlist in InfoTab.tsx's GeneralContent). Distinct from
 * useLiveCandles, which tracks a full candle series for exactly one
 * symbol at a time (the chart) — this hook fans a subscribe out across
 * every symbol it's given and listens on market.tick (not
 * market.candle) since a watchlist wants "current price," not a full
 * bar series.
 */
export function useLatestPrices(symbols: string[]): Record<string, number> {
  const [prices, setPrices] = useState<Record<string, number>>({});

  useEffect(() => {
    let cancelled = false;

    for (const symbol of symbols) {
      subscribeSymbol(symbol).catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useLatestPrices: subscribe(${symbol}) failed — ${detail}`);
      });
    }

    Promise.all(
      symbols.map((symbol) =>
        fetchCandles(symbol, 1)
          .then((candles): [string, number | undefined] => [symbol, candles[candles.length - 1]?.close])
          .catch((): [string, number | undefined] => [symbol, undefined]),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      setPrices((prev) => {
        const next = { ...prev };
        for (const [symbol, close] of pairs) {
          if (close !== undefined) next[symbol] = close;
        }
        return next;
      });
    });

    const unsubscribe = workspaceSocket.subscribe("market.tick", (msg: WireMessage) => {
      if (!msg.symbol || !symbols.includes(msg.symbol)) return;
      const price = msg.payload?.price;
      if (typeof price !== "number") return;
      setPrices((prev) => ({ ...prev, [msg.symbol as string]: price }));
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
    // symbols is expected to be a stable/memoized array from the caller —
    // see InfoTab.tsx's useMemo around MOCK_TICKERS.map(...). Depending
    // on .join(",") instead of the array reference avoids re-running this
    // effect (and re-subscribing) on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(",")]);

  return prices;
}
