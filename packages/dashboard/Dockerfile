# CamAI dashboard (Next.js 14, standalone output). Build from the REPO ROOT:
#   docker build -f packages/dashboard/Dockerfile \
#     --build-arg CAMAI_API_ORIGIN=https://api.example.com -t camai-dashboard .
#
# CAMAI_API_ORIGIN is the cloud API the /v1/* rewrite proxies to. Next bakes
# rewrites at build time, so it must be passed as a build arg (not just runtime).
FROM node:20-alpine AS deps
WORKDIR /app
COPY packages/dashboard/package.json packages/dashboard/package-lock.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
ARG CAMAI_API_ORIGIN=http://localhost:8000
ENV CAMAI_API_ORIGIN=$CAMAI_API_ORIGIN \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY packages/dashboard/ ./
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
# Standalone server bundle + static assets + public dir.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
