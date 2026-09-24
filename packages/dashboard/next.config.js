/** @type {import('next').NextConfig} */
// The dashboard is a pure client of the cloud API; it holds no server secrets and
// talks to the API base configured at build time via NEXT_PUBLIC_CAMAI_API_BASE.
const nextConfig = {
  reactStrictMode: true,
};

module.exports = nextConfig;
