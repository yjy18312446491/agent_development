FROM python:3.9.25-slim-bookworm@sha256:a02e9c5406c416c504d6c9a1a306ff4080c3173f1008d192f953bd20382a2d5c

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt ./
# pytest and HTTPX are used by the host-side tests.
RUN sed -E '/^(pytest|httpx)([[:space:]<=>!~]|$)/d' requirements.txt > /tmp/requirements-runtime.txt \
    && python -m pip install --no-cache-dir -r /tmp/requirements-runtime.txt \
    && python -m pip check \
    && rm /tmp/requirements-runtime.txt

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser \
    && install -d -o appuser -g appuser /data

COPY app/ ./app/
COPY data/synthetic/jobs/ ./data/synthetic/jobs/
COPY data/synthetic/resumes/S001_normal_backend.pdf data/synthetic/resumes/S002_empty.pdf ./data/synthetic/resumes/

USER appuser:appuser
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).close()"]
CMD ["uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "8000"]
