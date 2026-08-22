FROM python:3.11-slim

# System packages:
# - chromium: the browser Selenium drives for the VTU scraper
#   (app/vtu_scraper/browser.py). Selenium Manager, bundled with
#   selenium>=4.6, auto-downloads a matching chromedriver at runtime, so
#   we don't pin/install one ourselves.
# - tesseract-ocr: local OCR that solves VTU's CAPTCHA
#   (app/vtu_scraper/captcha.py) - no third-party captcha API.
# - fonts-liberation: without a real font installed, headless Chrome can
#   fail to render page text (including the CAPTCHA image) correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    tesseract-ocr \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Tells app/vtu_scraper/browser.py where to find the browser binary
# (see its CHROME_BIN handling) - unset locally on a dev machine, where
# Selenium Manager finds Chrome on its own.
ENV CHROME_BIN=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render (and most PaaS hosts) inject $PORT at runtime; 8000 is just the
# fallback for a plain `docker run` with no -e PORT set.
ENV PORT=8000
EXPOSE 8000

# --workers 1 is deliberate, not a resource-saving shortcut: background
# scrape-job state (app/scrape_jobs.py) lives in an in-process dict, not
# the database, so a second worker process would never see jobs started
# on the first one and the progress-polling endpoint would 404 on it.
# --threads lets this one worker still handle concurrent requests (e.g.
# polling progress while a scrape runs elsewhere in the same process)
# without needing a second process. If this ever needs to scale past one
# worker, scrape_jobs.py has to move that state into the database first.
CMD gunicorn run:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
