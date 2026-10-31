# FINN container: FastAPI serves the API and the built PWA. Build context = repository root.
# Used by deploy-cloudrun.ps1 (gcloud run deploy --source .); works on any container host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY frontend/dist /app/frontend/dist
# Pre-warmed cache (AI answers, filing text, prices) so a fresh instance opens instantly; see deploy/make_seed.py.
COPY deploy/seed.db ./data/finn.db

RUN useradd --create-home finn && chown -R finn:finn /app
USER finn

EXPOSE 8080
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log
