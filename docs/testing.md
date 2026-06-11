# Testing Guide

This project uses Django's built-in test runner for unit tests.

## Simplest way

If you just want a clean pass/fail summary table, run:

```bash
source virtual_environment/bin/activate
python manage.py test_summary --keepdb --noinput
```

Example output:

```text
+----------------------+------------+-----+--------+----------+--------+---------+
| Scope                | Discovered | Run | Passed | Failures | Errors | Skipped |
+----------------------+------------+-----+--------+----------+--------+---------+
| all discovered tests | 82         | 82  | 82     | 0        | 0      | 0       |
+----------------------+------------+-----+--------+----------+--------+---------+
```

This is the easiest command to use when you want a quick answer to:

- How many tests ran?
- How many passed?
- Did anything fail?

## Unit tests only

Legacy root-level diagnostic scripts were moved out of Django test discovery to:

- scripts/legacy_diagnostics/

These are manual diagnostics, not unit tests.

## 1) Activate environment

```bash
source virtual_environment/bin/activate
```

## 2) Run unit tests

```bash
# Clean summary table (recommended)
python manage.py test_summary --keepdb --noinput

# Full Django test runner output
python manage.py test --keepdb --noinput --verbosity 2

# One app
python manage.py test_summary stock_take --keepdb --noinput

# One test class
python manage.py test_summary stock_take.tests.DesktopMachineViewTests --keepdb --noinput

# One test method
python manage.py test stock_take.tests.DesktopMachineViewTests.test_create_desktop_machine_with_metrics --keepdb --noinput
```

## 3) How to read pass/fail counts

With the summary command, the main numbers are shown in a table:

- Discovered: tests found by Django
- Run: tests actually executed
- Passed: successful tests
- Failures: assertion failures
- Errors: crashes/exceptions during tests
- Skipped: intentionally skipped tests

If a run fails, the command also prints a second table listing the failing test names.

If you use the standard Django runner instead, the totals look like this:

```text
Ran 82 tests in 50.457s

OK
```

If tests fail, Django prints `FAILED` and a summary of failures/errors.

## 4) Where the unit tests live

Current Django unit test files in this repo:

- [stock_take/tests.py](stock_take/tests.py) - main application unit tests
- [material_generator/tests.py](material_generator/tests.py) - material generator tests

The simple summary command itself lives here:

- [stock_take/management/commands/test_summary.py](stock_take/management/commands/test_summary.py)

Legacy diagnostics that are not part of the unit suite live here:

- [scripts/legacy_diagnostics](scripts/legacy_diagnostics)

## 5) How to access and edit the tests

In VS Code:

1. Press Cmd+P.
2. Type one of these paths:
	- [stock_take/tests.py](stock_take/tests.py)
	- [material_generator/tests.py](material_generator/tests.py)
	- [stock_take/management/commands/test_summary.py](stock_take/management/commands/test_summary.py)
3. Edit the file and save.
4. Re-run the summary command to see the new totals.

From the terminal, you can also run tests directly against those files by label:

```bash
python manage.py test_summary stock_take --keepdb --noinput
python manage.py test_summary material_generator --keepdb --noinput
```

## 6) Recommended local options

Use these options to avoid interactive prompts and reduce local PostgreSQL teardown issues:

```bash
python manage.py test_summary --keepdb --noinput
```

Use the full Django runner only when you want detailed per-test output:

```bash
python manage.py test --keepdb --noinput --verbosity 2
```

## 7) Troubleshooting

### Error: `column stock_take_desktopmachine.vram_gb does not exist`

This means your database schema is behind the code.

```bash
python manage.py migrate stock_take
```

To verify migration state:

```bash
python manage.py showmigrations stock_take | tail -n 10
```

`0216_desktop_machine_metrics` must be marked with `[X]`.

### Error: `cannot drop the currently open database`

Another connection is still attached to the test DB during teardown.

Use:

```bash
python manage.py test_summary --keepdb --noinput
```

Then close any other sessions/tools connected to the test DB if needed.

### Interactive prompt error during test DB setup

If you see errors around test DB create/delete prompts (for example `ValueError: I/O operation on closed file`), run with:

```bash
python manage.py test_summary --keepdb --noinput
```
