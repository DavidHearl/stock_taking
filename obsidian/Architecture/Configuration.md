---
tags: [architecture, config]
---

# Configuration

Key settings from `stock_taking/settings.py`.

- **Time zone:** Europe/Dublin
- **Secrets:** loaded from `.env` via `python-dotenv` (SECRET_KEY, DB URL, Xero OAuth, MS Graph, Spaces creds)
- **CSRF:** custom `csrf_failure` view for login diagnostics
- **Logging:** structured logging to console; debug-toolbar noise filtered
- **Email:** Office 365 SMTP (`smtp.office365.com`) + custom adapters
- **Storage:** S3 (DigitalOcean Spaces) for media, WhiteNoise for static
- **Caching:** file-based cache in system temp dir, ~24h TTL

## Location filtering
Contract-number prefixes map to locations:
| Prefix | Location |
|---|---|
| BFS | Belfast |
| DUB | Dublin |
| NTG | Nottingham |
| WYE | Wye |
| MDE | (Midlands/other) |

## Database indexes (highlights)
- `StockItem`: `(tracking_type, quantity)`, `(category, tracking_type)`
- `db_index` on many FKs and frequently-filtered fields

## Related
- [[Deployment]]
- [[Tech Stack]]
- [[Middleware & Auth]]
