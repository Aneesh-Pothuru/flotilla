FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLOTILLA_HOST=0.0.0.0 \
    FLOTILLA_PORT=8765 \
    FLOTILLA_LEDGER=/var/lib/flotilla/flotilla.sqlite \
    FLOTILLA_REPORTS_DIR=/var/lib/flotilla/reports

RUN groupadd --system flotilla \
    && useradd --system --gid flotilla --home-dir /var/lib/flotilla flotilla \
    && mkdir -p /var/lib/flotilla/reports \
    && chown -R flotilla:flotilla /var/lib/flotilla

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

USER flotilla
VOLUME ["/var/lib/flotilla"]
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8765/readyz',timeout=2))['ready']"]

ENTRYPOINT ["flotilla"]
CMD ["serve", "--budget", "12"]

