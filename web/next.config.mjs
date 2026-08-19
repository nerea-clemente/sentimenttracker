// Static export targeting GitHub Pages, matching the mediatracker setup.
// basePath/assetPrefix are set when DEPLOY_BASE_PATH is provided by CI.
const basePath = process.env.DEPLOY_BASE_PATH || "";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath,
  assetPrefix: basePath || undefined,
  reactStrictMode: true,
  env: {
    // "static" reads the committed snapshot (GitHub Pages). Anything else fetches the Python API,
    // which is what `make api` + `make web` gives you locally, with live data and write access.
    NEXT_PUBLIC_DATA_MODE: process.env.NEXT_PUBLIC_DATA_MODE ?? "live",
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000",
    // Files under web/public are served beneath basePath on Pages; links to them need the prefix.
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default nextConfig;
