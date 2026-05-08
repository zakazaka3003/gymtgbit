# Frontend (Vite static) — multi-stage: build → nginx alpine.
FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
# VITE_API_BASE_URL пустой — фронт ходит на тот же origin, /api/* проксируется Caddy.
ENV VITE_API_BASE_URL=""
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
