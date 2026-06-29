---
tags: [architecture, tech-stack]
---

# Tech Stack

## Backend
- **Framework:** Django 6.0
- **Language:** Python 3.12
- **Database:** PostgreSQL 12+ (via `psycopg2`, configured with `dj_database_url`)
- **WSGI server:** Gunicorn 23.0 (3 workers)

## Frontend
- **Templating:** Django templates
- **JavaScript:** HTMX, Plotly (interactive charts)
- **CSS:** Bootstrap + Bootstrap Icons; custom CSS in `static/css/`
- **Static files:** WhiteNoise (compression & caching)

## Key dependencies
| Area | Libraries |
|---|---|
| HTTP / API | `requests`, `httpx`, `boto3` |
| Data processing | `pandas`, `numpy`, `openpyxl`, `pdfplumber`, `reportlab` |
| Fuzzy matching | `RapidFuzz`, `Levenshtein` (PO reconciliation) |
| Auth | `django-allauth` (OAuth, account mgmt) |
| Admin / dev | `django-debug-toolbar`, `django-widget-tweaks`, `django-storages` |
| Browser automation | `playwright` (Chromium) — Anthill scraping & doc generation |
| Config | `python-dotenv`, `django-environ` |

## Caching
- File-based cache stored in system temp dir (kept outside OneDrive to avoid sync conflicts).
- TTL ~24 hours.

## Containerisation
- Python 3.12 slim image
- Playwright Chromium runtime deps installed
- `collectstatic` at build time
- Entrypoint: `gunicorn stock_taking.wsgi:application -w3 -b:8000`

## Related
- [[Deployment]]
- [[Configuration]]
- [[Project Overview]]
