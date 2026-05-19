/**
 * Stadium + analytics scene for the welcome hero (lower panel).
 */
export function StadiumHeroBackdrop() {
  const gid = "stadium-hero";

  return (
    <div className="welcome-hero__scene" aria-hidden>
      <svg
        className="welcome-hero__svg"
        viewBox="0 0 1200 360"
        preserveAspectRatio="xMidYMax meet"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id={`${gid}-sky`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1e3a5f" />
            <stop offset="100%" stopColor="#0f172a" />
          </linearGradient>
          <linearGradient id={`${gid}-turf`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#34d399" />
            <stop offset="100%" stopColor="#15803d" />
          </linearGradient>
          <radialGradient id={`${gid}-lightL`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fde047" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#fde047" stopOpacity="0" />
          </radialGradient>
          <radialGradient id={`${gid}-lightR`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fde047" stopOpacity="0.85" />
            <stop offset="100%" stopColor="#fde047" stopOpacity="0" />
          </radialGradient>
        </defs>

        <rect width="1200" height="360" fill={`url(#${gid}-sky)`} />

        {/* Upper deck */}
        <ellipse cx="600" cy="200" rx="560" ry="120" fill="#1e293b" />
        <ellipse cx="600" cy="215" rx="500" ry="95" fill="none" stroke="#475569" strokeWidth="2" />

        {/* Side stands */}
        <path d="M0 120 Q180 60 280 160 L280 360 L0 360 Z" fill="#334155" />
        <path d="M1200 120 Q1020 60 920 160 L920 360 L1200 360 Z" fill="#334155" />

        {/* Floodlights */}
        <ellipse cx="70" cy="40" rx="55" ry="38" fill={`url(#${gid}-lightL)`} />
        <ellipse cx="1130" cy="40" rx="55" ry="38" fill={`url(#${gid}-lightR)`} />
        <rect x="62" y="35" width="8" height="120" fill="#64748b" rx="2" />
        <rect x="1130" y="35" width="8" height="120" fill="#64748b" rx="2" />

        {/* Turf */}
        <path d="M260 360 L940 360 L820 210 L380 210 Z" fill={`url(#${gid}-turf)`} />
        {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => {
          const t = i / 8;
          const y = 210 + t * 150;
          const x1 = 380 + t * 55;
          const x2 = 820 - t * 55;
          return (
            <line
              key={i}
              x1={x1}
              y1={y}
              x2={x2}
              y2={y}
              stroke="#ffffff"
              strokeOpacity={i === 4 ? 0.9 : 0.35}
              strokeWidth={i === 4 ? 2.5 : 1.2}
            />
          );
        })}
        <line x1="600" y1="210" x2="600" y2="360" stroke="#ffffff" strokeOpacity="0.5" strokeWidth="2" />

        {/* Analytics grid — visible cyan overlay */}
        <g stroke="#22d3ee" strokeWidth="1" fill="none" opacity="0.55">
          {Array.from({ length: 7 }).map((_, i) => (
            <line key={`h${i}`} x1="340" y1={225 + i * 22} x2="860" y2={225 + i * 22} />
          ))}
          {Array.from({ length: 9 }).map((_, i) => (
            <line key={`v${i}`} x1={400 + i * 55} y1="218" x2={430 + i * 42} y2="355" />
          ))}
        </g>

        {/* Data arcs */}
        <path
          d="M80 50 Q300 130 420 200"
          fill="none"
          stroke="#38bdf8"
          strokeWidth="2"
          strokeOpacity="0.7"
          strokeDasharray="8 6"
          className="welcome-hero__dash"
        />
        <path
          d="M1120 50 Q900 130 780 200"
          fill="none"
          stroke="#4ade80"
          strokeWidth="2"
          strokeOpacity="0.7"
          strokeDasharray="8 6"
          className="welcome-hero__dash welcome-hero__dash--reverse"
        />

        {/* Yard markers as data nodes */}
        {[
          [420, 280],
          [520, 300],
          [600, 310],
          [680, 300],
          [780, 280],
        ].map(([cx, cy], i) => (
          <g key={i}>
            <circle cx={cx} cy={cy} r="5" fill="#22d3ee" opacity="0.9" />
            <circle cx={cx} cy={cy} r="10" fill="none" stroke="#22d3ee" strokeOpacity="0.4" />
          </g>
        ))}
      </svg>

      <div className="welcome-hero__chip welcome-hero__chip--1">EPA +0.14</div>
      <div className="welcome-hero__chip welcome-hero__chip--2">Elo 1684</div>
      <div className="welcome-hero__chip welcome-hero__chip--3">67% WP</div>
      <div className="welcome-hero__chip welcome-hero__chip--4">Spread -3.5</div>

      <div className="welcome-hero__scene-fade" />
    </div>
  );
}
