FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libffi-dev \
    libjpeg-dev \
    libpng-dev \
    wget ca-certificates \
    # Playwright/Chromium runtime dependencies
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libcairo2 libatspi2.0-0 libx11-6 libxext6 libxcb1 libxshmfence1 \
    fonts-liberation libvulkan1 \
&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# DEBUG: show python/pip versions too
RUN python -V && pip -V

RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser into the image
RUN playwright install chromium

COPY . .

# Collect static files into /app/staticfiles.
# DEBUG=0 is REQUIRED here: the static storage backend is chosen by DEBUG in
# settings.py, so without it the build defaults to DEBUG=1 and uses the plain
# (non-manifest) storage — producing no hashed manifest, while runtime (DEBUG=0)
# uses ManifestStaticFilesStorage and serves hashed URLs whose files aren't in
# the image (=> every /static/*.hash.* 404s). --clear wipes any stale committed
# staticfiles/ copied in by "COPY . ." so the manifest is clean and complete.
# SECRET_KEY is needed by Django settings at build time; the real key is set at runtime via env vars.
RUN SECRET_KEY=build-placeholder DEBUG=0 python manage.py collectstatic --noinput --clear

# Search the name from WSGI in the settings file, ie WSGI_APPLICATION = 'stock_taking.wsgi.application'
CMD ["gunicorn","stock_taking.wsgi:application","-w","3","-b",":8000"]

EXPOSE 8000

# Default command is provided by compose (gunicorn)
