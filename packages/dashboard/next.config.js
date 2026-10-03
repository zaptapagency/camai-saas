/** @type {import('next').NextConfig} */
// The dashboard is a pure client of the cloud API. In dev we reverse-proxy the
// API under the same origin via rewrites so the browser makes same-origin calls
// (no CORS) and `NEXT_PUBLIC_CAMAI_API_BASE` can stay empty. Point CAMAI_API_ORIGIN
// at a deployed cloud for staging/prod; defaults to the local uvicorn.
const API_ORIGIN = process.env.CAMAI_API_ORIGIN || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // Lean container output (server.js + minimal node_modules) for the Docker image.
  output: "standalone",
  async rewrites() {
    return [
      { source: "/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` },
    ];
  },
};

module.exports = nextConfig;
