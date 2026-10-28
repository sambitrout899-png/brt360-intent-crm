# BRT 360 web image: the built UI served by nginx, which also passes /api to
# the API container - the same layout deploy/brt360.sh sets up on the host.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# the API is on the same origin in production (nginx passes /api on)
ENV VITE_API_BASE=
RUN npx vite build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx_proxy.conf /etc/nginx/brt360_proxy.conf
COPY --from=build /src/dist /usr/share/nginx/html
