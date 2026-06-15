const CONTACT_EMAIL = "harrison@ellercollective.com";
const MAILTO = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(
  "Reaching out about Statletics NFL",
)}&body=${encodeURIComponent("Hi Harrison,\n\n")}`;

/**
 * Global site footer — attribution + a direct line for business inquiries.
 */
export function Footer() {
  return (
    <footer className="max-w-7xl mx-auto px-4 pt-8 pb-28 md:pb-8">
      <div className="border-t divider pt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm">
            <span className="text-muted">Built by</span>{" "}
            <span className="font-semibold text-text">Harrison Eller</span>
          </p>
          <p className="text-xs text-muted mt-0.5">
            Open to business engagements — feel free to reach out.
          </p>
        </div>

        <a
          href={MAILTO}
          className="glass-pill inline-flex items-center gap-2 self-start sm:self-auto text-sm text-team-primary hover:text-text px-3 py-1.5"
          aria-label={`Email Harrison Eller at ${CONTACT_EMAIL}`}
        >
          <MailIcon />
          <span>Get in touch</span>
        </a>
      </div>

      <p className="text-[11px] text-muted/80 mt-4">
        Statletics NFL · data via ESPN, Sleeper, nfl-data-py, The Odds API, Open-Meteo
      </p>
    </footer>
  );
}

function MailIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 7 8.5 6 8.5-6" />
    </svg>
  );
}
