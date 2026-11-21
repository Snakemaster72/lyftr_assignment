# syntax=docker/dockerfile:1
# Stages: builder -> base -> test (CI only) ; base -> runtime (last, so it's the default build target).

# ---- builder: install pinned dependencies into a virtualenv ------------------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- base: slim Python + the venv + the app (no build tools) -----------------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    # Use LiteLLM's bundled model cost map instead of fetching it from GitHub at import.
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    DATABASE_URL=sqlite:////data/app.db
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app

# ---- test: base + pytest, so CI runs the suite against the real runtime deps --
FROM base AS test
RUN pip install --no-cache-dir pytest==9.2.0
COPY pyproject.toml ./
COPY tests ./tests
CMD ["pytest", "-q"]

# ---- runtime ------------------------------------------------------------------
FROM base AS runtime
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
# The entrypoint fixes /data ownership if a volume is mounted root-owned, then drops to `app`.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]
