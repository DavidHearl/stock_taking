---
tags: [architecture, deployment, devops]
---

# Deployment

## Production
- **Host:** DigitalOcean App Platform (GitHub CI/CD)
- **URL:** `https://atlas-gxbq5.ondigitalocean.app`
- **Reverse proxy / SSL:** Traefik
- **Database:** PostgreSQL managed service
- **Static files:** `collectstatic` at build, served by WhiteNoise
- **Media:** DigitalOcean Spaces (S3) — see [[Integrations]]
- **Secrets:** environment variables

## Local development
- Docker Compose for local PostgreSQL (`docker-compose.yml`)
- `Dockerfile` builds Python 3.12 slim + Playwright Chromium deps
- Django management commands for sync scripts & imports
- Debug toolbar enabled when `DEBUG=True`

## Common commands
```powershell
py manage.py runserver
py manage.py migrate
py manage.py collectstatic
```

## Related
- [[Tech Stack]]
- [[Configuration]]
