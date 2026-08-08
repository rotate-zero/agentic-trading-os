// Shared across every calculation file in this directory. Kept separate
// from sma.ts/ema.ts/etc. so none of them has to "own" the type the others
// import from.
export interface IndicatorPoint {
  time: number;
  value: number;
}
