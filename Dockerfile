FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# DEBUG: show python/pip versions too
RUN python -V && pip -V

RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Search the name from WSGI in the settings file, ie WSGI_APPLICATION = 'stock_taking.wsgi.application'
CMD ["gunicorn","stock_taking.wsgi:application","-w","3","-b",":8000"]

EXPOSE 8000

# Default command is provided by compose (gunicorn)
