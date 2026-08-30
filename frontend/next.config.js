/** @type {import('next').NextConfig} */
const apiBase = process.env.NEXT_PUBLIC_API_BASE || "";
if (process.env.VERCEL === "1") {
  if (!apiBase || /localhost|127\.0\.0\.1/.test(apiBase)) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE must be your public Railway URL on Vercel " +
        "(Settings → Environment Variables → Production, then redeploy). " +
        `Current value: ${apiBase || "(unset)"}`,
    );
  }
}

// CFB lives in a separate Vercel project (basePath /cfb) and is reached from
// statletics.io via these rewrites. They MUST be `beforeFiles`: Next handles
// RSC/prefetch flights (/cfb?_rsc=…) before vercel.json rewrites, so a missing
// /cfb page in THIS app used to 404 the CFB homepage client navigation and
// log Safari's "Failed to load resource … 404 (cfb)". Document GETs still
// worked because vercel.json caught those. Keep vercel.json in sync.
const CFB_ORIGIN = "https://cfb-app-jet.vercel.app";

const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "a.espncdn.com" },
      { protocol: "https", hostname: "sleepercdn.com" },
    ],
  },
  async rewrites() {
    return {
      beforeFiles: [
        { source: "/cfb", destination: `${CFB_ORIGIN}/cfb` },
        { source: "/cfb/:path*", destination: `${CFB_ORIGIN}/cfb/:path*` },
      ],
    };
  },
};
module.exports = nextConfig;
