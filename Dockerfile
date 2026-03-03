# Stage 1: build Vue/Vite frontend
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: run FastAPI, serve built frontend as static files
FROM python:3.13-alpine
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ .
COPY scripts/ ./scripts/
COPY --from=frontend-build /app/api/dist ./dist

RUN printf '*/15 * * * * . /etc/environment && cd /app && python scripts/collect.py >> /proc/1/fd/1 2>&1\n*/15 * * * * . /etc/environment && cd /app && python scripts/collect_weather.py >> /proc/1/fd/1 2>&1\n*/15 * * * * . /etc/environment && cd /app && python scripts/collect_ned.py >> /proc/1/fd/1 2>&1\n' > /etc/crontabs/root

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
