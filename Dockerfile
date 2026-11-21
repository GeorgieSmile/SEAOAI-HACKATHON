FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.js ./
COPY frontend/src/ src/
COPY frontend/public/ public/
RUN npm run typecheck && npm run build

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/*.py backend/
COPY backend/prompts/*.txt backend/prompts/
COPY samples/official-sources.json samples/official-sources.json
COPY --from=frontend /build/frontend/dist frontend/dist
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn backend.hosted:from_env --factory --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1"]
