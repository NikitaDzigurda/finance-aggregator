FROM node:22.17.1-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22.17.1-alpine AS runtime

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=5173 \
    FINANCE_FRONTEND_PROXY_TARGET=http://api:8000

WORKDIR /app

COPY --from=build /app/dist ./dist
COPY scripts/serve.mjs ./scripts/serve.mjs

USER node

EXPOSE 5173

CMD ["node", "scripts/serve.mjs"]
