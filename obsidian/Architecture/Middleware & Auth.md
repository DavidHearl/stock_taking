---
tags: [architecture, auth, security]
---

# Middleware & Auth

## Middleware stack
1. `SecurityMiddleware` — CSRF checks, X-Frame-Options
2. `WhiteNoise` — static file compression & caching
3. `SessionMiddleware`
4. `CsrfViewMiddleware`
5. **`ImpersonationMiddleware`** (custom) — admins can impersonate users for support
6. **`RolePermissionMiddleware`** (custom) — row/page-level access control
7. **`ActivityLoggingMiddleware`** (custom) — audit trail → `ActivityLog`
8. Allauth (account / OAuth)
9. Debug Toolbar (dev only)

## Authentication & authorisation
- Django built-in `User` + `UserProfile` extension
- Role-based access control (RBAC) via `Role` + `PagePermission` models
- Per-site assignment via `UserSiteRole`
- Franchise users restricted to their own tickets / claims
- Impersonation available for support staff

## Notes
- CSRF failures use a custom failure view for login diagnostics (see [[Configuration]]).
- See repo memory `login-403-csrf` for past CSRF/login issues.

## Related
- [[People & Roles]]
- [[Configuration]]
