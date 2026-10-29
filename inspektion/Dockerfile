# Basis fest auf Debian/Python setzen: Chromium (Playwright) läuft nicht auf dem
# Alpine-basierten HA-Standardimage. Das vom Supervisor übergebene BUILD_FROM wird
# deshalb bewusst nicht verwendet.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Europe/Berlin

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache

COPY app ./app

CMD ["python", "-m", "app.main"]
