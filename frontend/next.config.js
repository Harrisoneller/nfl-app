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

const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "a.espncdn.com" },
      { protocol: "https", hostname: "sleepercdn.com" },
    ],
  },
};
module.exports = nextConfig;
