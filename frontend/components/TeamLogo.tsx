"use client";
import { useEffect, useState } from "react";

const ESPN_LOGO = (id: string) =>
  `https://a.espncdn.com/i/teamlogos/nfl/500/${id.toLowerCase()}.png`;

/**
 * Team logo with graceful fallback. ESPN's CDN serves the standard team
 * logos at a stable URL pattern; we use that directly so we don't bake
 * the logo URL into our DB row (which is already there but optional).
 */
export function TeamLogo({
  teamId,
  size = 28,
  className = "",
}: {
  teamId: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  // Reset on teamId change
  useEffect(() => setFailed(false), [teamId]);

  if (failed || !teamId) {
    return (
      <div
        className={`inline-flex items-center justify-center rounded-full bg-bg border divider text-[10px] font-semibold text-muted ${className}`}
        style={{ width: size, height: size }}
      >
        {teamId?.slice(0, 3) ?? "—"}
      </div>
    );
  }
  return (
    <img
      src={ESPN_LOGO(teamId)}
      alt={teamId}
      width={size}
      height={size}
      onError={() => setFailed(true)}
      className={`inline-block ${className}`}
      style={{ width: size, height: size }}
      loading="lazy"
    />
  );
}
