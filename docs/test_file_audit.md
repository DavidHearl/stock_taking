# Test File Audit

Date: 2026-06-11

## Active, keep (CI-safe)

- stock_take/tests.py
  - Proper Django `TestCase` suite.
  - Expanded with IT desktop machine endpoint coverage.
- material_generator/tests.py
  - Django test module placeholder.
  - Keep if app is active, but currently empty.

## Legacy/ad-hoc scripts (not CI-safe)

These root-level files are script-style diagnostics and rely on live APIs, credentials, browser automation, or production-like data. They should not be treated as unit tests.

- scripts/legacy_diagnostics/diag_xero_connection.py
- scripts/legacy_diagnostics/diag_appointments_api.py
- scripts/legacy_diagnostics/diag_anthill_api.py
- scripts/legacy_diagnostics/diag_payments_api.py
- scripts/legacy_diagnostics/diag_activity_detail.py
- scripts/legacy_diagnostics/diag_wsdl.py
- scripts/legacy_diagnostics/diag_anthill_payments_screen.py
- scripts/legacy_diagnostics/diag_activity_fields.py
- scripts/legacy_diagnostics/diag_csv_functionality.py
- scripts/legacy_diagnostics/diag_ordering_post.py
- scripts/legacy_diagnostics/diag_api_key.py

## Recommended action

1. Legacy scripts moved into `scripts/legacy_diagnostics/`.
2. Renamed from `test_*.py` to `diag_*.py` so they are not picked up as tests.
3. Keep only deterministic tests in `stock_take/tests.py` (and app-level `tests.py` files).
4. Rebuild key legacy scenarios as proper unit/integration tests with mocks and fixtures.

## New coverage added

Added in `stock_take/tests.py` (`DesktopMachineViewTests`):

- Create desktop machine with editable `vram_gb` and `pflops`.
- Reject negative metric values.
- Require at least one component.
- Update machine: replace components and persist metrics.
- Enforce edit permissions on save endpoint.

## Current blocker when running tests

Running tests currently fails during database setup because migration `0175_it_mobile_device` attempts to create a relation that already exists (`stock_take_mobiledevice`).

This indicates a pre-existing migration/database state issue in the environment, not a failure caused by the new unit tests.
