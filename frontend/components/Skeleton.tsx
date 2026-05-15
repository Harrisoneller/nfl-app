/**
 * Reusable skeleton primitives. Animated shimmer via tailwind's animate-pulse;
 * keep them roughly the same shape as the real content so layout doesn't jump.
 */

export function SkeletonLine({ width = "100%", className = "" }: { width?: string; className?: string }) {
  return (
    <div
      className={`h-3 bg-bg/60 rounded animate-pulse ${className}`}
      style={{ width }}
    />
  );
}

export function SkeletonBlock({ height = 200, className = "" }: { height?: number; className?: string }) {
  return (
    <div
      className={`rounded bg-bg/60 animate-pulse ${className}`}
      style={{ height }}
    />
  );
}

export function SkeletonRadar({ height = 320 }: { height?: number }) {
  return (
    <div className="flex items-center justify-center" style={{ height }}>
      <div className="rounded-full bg-bg/60 animate-pulse" style={{ width: height * 0.7, height: height * 0.7 }} />
    </div>
  );
}

export function SkeletonRow() {
  return (
    <div className="flex items-center gap-3 py-2">
      <SkeletonLine width="20%" />
      <SkeletonLine width="60%" />
      <SkeletonLine width="15%" />
    </div>
  );
}

export function SkeletonTable({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-1">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}
