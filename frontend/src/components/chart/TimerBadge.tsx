import { useEffect, useState } from "react";
import { currentBarProgressPct } from "../../utils/timerProgress";
import type { Timeframe, TimerConfig } from "../../types/workspace";

// Fixed frame chrome — deliberately NOT tied to the sub-window's own
// backgroundColor/gridColor, since the whole point is staying legible no
// matter what chart background the user picks.
const BADGE_BG = "#3A3F4B";
const BADGE_BORDER = "#000000";
const TRACK_COLOR = "rgba(255,255,255,0.18)";

export function TimerBadge({ timeframe, timer }: { timeframe: Timeframe; timer: TimerConfig }) {
  const [pct, setPct] = useState(() => currentBarProgressPct(timeframe));

  useEffect(() => {
    if (!timer.enabled) return;
    setPct(currentBarProgressPct(timeframe)); // jump immediately on timeframe change, don't wait a tick
    const id = setInterval(() => setPct(currentBarProgressPct(timeframe)), 1000);
    return () => clearInterval(id);
  }, [timeframe, timer.enabled]);

  if (!timer.enabled) return null;

  return (
    <div
      className="pointer-events-none absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-md"
      style={{ backgroundColor: BADGE_BG, border: `1.5px solid ${BADGE_BORDER}` }}
      title={`${timeframe} bar progress: ${Math.round(pct)}%`}
    >
      <div
        className="h-4 w-4 rounded-full"
        style={{
          // conic-gradient starts at 12 o'clock and sweeps clockwise by
          // default — exactly the "radar" behavior asked for, driven purely
          // by `pct` with no separate rotation transform needed.
          background: `conic-gradient(${timer.color} ${pct}%, ${TRACK_COLOR} ${pct}% 100%)`,
        }}
      />
    </div>
  );
}
