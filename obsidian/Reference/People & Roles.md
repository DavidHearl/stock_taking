---
tags: [reference, auth, roles]
---

# People & Roles

How access control works — see also [[Middleware & Auth]].

## Models
- `Role` — named role with a permission set
- `PagePermission` — page-level access control
- `UserProfile` — extends Django `User` (role, location, settings)
- `UserSiteRole` — per-site role assignment

## Access patterns
- **Role-based access control (RBAC)** enforced by `RolePermissionMiddleware`.
- **Franchise users** are restricted to their own tickets and claims.
- **Impersonation** lets admins act as another user (`ImpersonationMiddleware`).
- Permission gates in templates use `role_perms.<area>.can_edit` and `is_role_admin`.

## Locations
Tied to contract-number prefixes (see [[Configuration]]): BFS, DUB, NTG, WYE, MDE.

## Related
- [[Data Models]]
- [[Middleware & Auth]]
