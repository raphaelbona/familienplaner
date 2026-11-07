FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
# Optional build secret `build_ca`: a CA bundle for networks that intercept TLS (e.g. antivirus
# SSL scanning). Mounted only for this step, so it never ends up in an image layer.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi \
    && npm ci --legacy-peer-deps
COPY . .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
