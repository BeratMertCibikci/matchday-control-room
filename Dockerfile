# Single container: FastAPI backend serving the built React frontend.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/mcr backend/mcr
RUN pip install --no-cache-dir ./backend
COPY --from=web /web/dist /app/frontend/dist
ENV MCR_STATIC_DIR=/app/frontend/dist \
    MCR_LLM=offline \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "mcr.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
