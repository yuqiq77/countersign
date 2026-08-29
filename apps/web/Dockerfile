FROM node:22-alpine AS build
WORKDIR /repo
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
# NEXT_PUBLIC_* is inlined into the client bundle at BUILD time; a runtime
# environment value would be silently ignored, so it must arrive here.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN pnpm --dir apps/web build

FROM node:22-alpine
WORKDIR /repo
RUN corepack enable
COPY --from=build /repo ./
EXPOSE 3000
CMD ["pnpm", "--dir", "apps/web", "start"]
