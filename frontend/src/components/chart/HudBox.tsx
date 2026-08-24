import type { HudConfig, HudVariableKey } from "../../types/workspace";
import { resolveHudLine, hexWithOpacity } from "../../utils/hud";

// Floating, multi-line text readout pinned to the top of the chart pane —
// same "fixed frame chrome over the candles" overlay mechanism as
// TimerBadge.tsx (absolute + pointer-events-none inside ChartWidget's
// relative wrapper), but user-styled (background/text color + opacity)
// rather than fixed chrome, since the whole point here is blending into
// whatever chart background the person has chosen, not staying legible
// against it. Defaults to the top-LEFT corner specifically because
// TimerBadge already owns top-right — align: "right" is still offered
// (config.align) for anyone who'd rather have them overlap or has the
// timer badge turned off.
export function HudBox({
  config,
  values,
}: {
  config: HudConfig;
  values: Partial<Record<HudVariableKey, number>>;
}) {
  if (!config.enabled) return null;

  const lines = config.lines.map((line) => resolveHudLine(line, values)).filter((text) => text.length > 0);
  if (lines.length === 0) return null;

  return (
    <div
      className={`pointer-events-none absolute top-2 z-10 max-w-[70%] rounded-md px-2 py-1 font-mono text-[11px] leading-[1.5] ${
        config.align === "right" ? "right-2 text-right" : "left-2 text-left"
      }`}
      style={{
        backgroundColor: hexWithOpacity(config.backgroundColor, config.backgroundOpacity),
        color: hexWithOpacity(config.textColor, config.textOpacity),
      }}
    >
      {lines.map((text, idx) => (
        <div key={idx} className="whitespace-nowrap">
          {text}
        </div>
      ))}
    </div>
  );
}
