# =============================================================================
# Next.js storefront and admin console.
#
# Three stages: install dependencies, build, then a runtime image containing
# only what is needed to serve. The final image is ~150MB rather than the ~1GB
# a naive single-stage build produces.
# =============================================================================

# --- Stage 1: dependencies ---------------------------------------------------
FROM node:22-alpine AS deps

WORKDIR /app

# Only the manifests, so this layer is cached until a dependency actually
# changes. Editing a component then rebuilds in seconds instead of re-running
# a full npm install.
COPY package.json package-lock.json ./

# `npm ci` installs exactly what the lock file pins - no resolution, no drift
# between your machine and the image. It requires the lock file to be in sync
# with package.json and fails loudly when it is not, which is the point.
RUN npm ci

# --- Stage 2: build ----------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined into the JavaScript bundle at BUILD time,
# not read at runtime. They must therefore be build args, and changing one
# requires a rebuild rather than a restart.
#
# Everything behind this prefix is public by definition - it ships to every
# visitor's browser. Never put a secret here. The Stripe *publishable* key is
# designed for exactly this; the secret key lives only in the payments service.
ARG NEXT_PUBLIC_API_URL=http://localhost:8080
ARG NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=""
ARG NEXT_PUBLIC_SITE_NAME=Atelier

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY \
    NEXT_PUBLIC_SITE_NAME=$NEXT_PUBLIC_SITE_NAME \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build

# --- Stage 3: runtime --------------------------------------------------------
FROM node:22-alpine AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Unprivileged user. If an attacker achieves code execution they land without
# write access to the application they are running.
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs

# `output: "standalone"` in next.config.ts emits a self-contained server with
# only the modules actually imported, so node_modules is not copied at all.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

# The standalone output's own server. `next start` is not available here -
# the Next CLI is deliberately not in this image.
CMD ["node", "server.js"]
