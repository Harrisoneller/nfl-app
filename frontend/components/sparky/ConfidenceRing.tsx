"use client";
import { confidenceColor } from "./format";

/** Conic-gradient confidence ring driven by the CSS --pct variable. */
export function ConfidenceRing({
  score,
  size = 64,
}: {
  score: number;
  size?: number;
}) {
  const color = confidenceColor(score);
  return (
    <div
      className="sparky-ring"
      style={
        {
          "--pct": Math.max(0, Math.min(100, score)),
          width: size,
          height: size,
          background: `conic-gradient(${color} calc(var(--pct) * 1%), rgba(148,163,184,0.18) 0)`,
        } as React.CSSProperties
      }
      role="img"
      aria-label={`Confidence ${score.toFixed(0)} out of 100`}
    >
      <span className="sparky-ring__label">{score.toFixed(0)}</span>
    </div>
  );
}
