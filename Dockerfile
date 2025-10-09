FROM python:3.10-slim
WORKDIR /app

# system deps for psycopg2, Pillow, lxml, etc.
RUN apt-get update  apt-get install -y --no-install-recommends \
    build-essential libpq-dev libxml2-dev libxslt1-dev zlib1g-dev libffi-dev \
     rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip  pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Default command is provided by compose (gunicorn)
