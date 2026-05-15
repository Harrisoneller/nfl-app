/**
 * Letter-grade + Elo number display. Casual users see the grade; analytics
 * users see the rating. Color-coded by grade tier.
 */

const GRADE_COLORS: Record<string, string> = {
  "A+": "#22c55e", "A": "#22c55e", "A-": "#34d399",
  "B+": "#84cc16", "B": "#84cc16", "B-": "#a3e635",
  "C+": "#eab308", "C": "#eab308", "C-": "#fbbf24",
  "D": "#f97316", "F": "#ef4444",
};

export function EloBadge({
  rating,
  grade,
  size = "md",
  showRating = true,
}: {
  rating: number;
  grade: string;
  size?: "sm" | "md" | "lg";
  showRating?: boolean;
}) {
  const color = GRADE_COLORS[grade] ?? "#94a3b8";
  const sizeClasses = {
    sm: { box: "px-1.5 py-0.5 text-[10px]", grade: "text-xs", rating: "text-[10px]" },
    md: { box: "px-2 py-1 text-xs", grade: "text-sm font-semibold", rating: "text-[11px]" },
    lg: { box: "px-3 py-2 text-sm", grade: "text-2xl font-bold", rating: "text-xs" },
  }[size];

  return (
    <div
      className={`inline-flex items-center gap-2 rounded border divider ${sizeClasses.box}`}
      style={{ borderColor: color }}
    >
      <span className={sizeClasses.grade} style={{ color }}>{grade}</span>
      {showRating && (
        <span className={`${sizeClasses.rating} text-muted tabular-nums`}>
          Elo {Math.round(rating)}
        </span>
      )}
    </div>
  );
}
