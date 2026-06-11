from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from django.core.management.base import BaseCommand
from django.test.runner import DiscoverRunner


class Command(BaseCommand):
    help = 'Run Django unit tests and print a compact summary table.'

    def add_arguments(self, parser):
        parser.add_argument(
            'labels',
            nargs='*',
            help='Optional test labels, e.g. stock_take or stock_take.tests.DesktopMachineViewTests',
        )
        parser.add_argument(
            '--keepdb',
            action='store_true',
            help='Preserve the test database between runs.',
        )
        parser.add_argument(
            '--noinput',
            action='store_true',
            help='Suppress interactive prompts.',
        )
        parser.add_argument(
            '--failfast',
            action='store_true',
            help='Stop after the first failure or error.',
        )

    def handle(self, *args, **options):
        labels = options['labels']
        command_verbosity = options['verbosity']
        runner = DiscoverRunner(
            verbosity=0,
            interactive=not options['noinput'],
            keepdb=options['keepdb'],
            failfast=options['failfast'],
        )

        suite = runner.build_suite(labels)
        suite_count = suite.countTestCases()
        output_buffer = StringIO()
        failures = 1
        result = None

        try:
            with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
                runner.setup_test_environment()
                old_config = runner.setup_databases()
                try:
                    result = runner.run_suite(suite)
                    failures = runner.suite_result(suite, result)
                finally:
                    runner.teardown_databases(old_config)
                    runner.teardown_test_environment()
        finally:
            captured_output = output_buffer.getvalue().strip()

        if captured_output and (failures or command_verbosity > 1):
            self.stdout.write(captured_output)
            self.stdout.write('')

        self.stdout.write('')
        self.stdout.write(self._format_table(result, suite_count, labels))

        failed_tests = self._collect_failed_tests(result)
        if failed_tests:
            self.stdout.write('')
            self.stdout.write(self._format_failures_table(failed_tests))

        if failures:
            raise SystemExit(1)

    def _format_table(self, result, discovered_count, labels):
        failures = len(getattr(result, 'failures', []))
        errors = len(getattr(result, 'errors', []))
        skipped = len(getattr(result, 'skipped', []))
        expected_failures = len(getattr(result, 'expectedFailures', []))
        unexpected_successes = len(getattr(result, 'unexpectedSuccesses', []))
        tests_run = getattr(result, 'testsRun', 0)
        passed = max(
            tests_run - failures - errors - skipped - expected_failures,
            0,
        )
        label_text = ', '.join(labels) if labels else 'all discovered tests'

        headers = ['Scope', 'Discovered', 'Run', 'Passed', 'Failures', 'Errors', 'Skipped']
        rows = [[
            label_text,
            str(discovered_count),
            str(tests_run),
            str(passed),
            str(failures),
            str(errors),
            str(skipped),
        ]]

        if expected_failures or unexpected_successes:
            headers.extend(['Expected Failures', 'Unexpected Successes'])
            rows[0].extend([str(expected_failures), str(unexpected_successes)])

        return self._draw_table(headers, rows)

    def _format_failures_table(self, failed_tests):
        headers = ['Status', 'Test']
        rows = [[status, test_id] for status, test_id in failed_tests]
        return self._draw_table(headers, rows)

    def _collect_failed_tests(self, result):
        failed = []
        for test, _traceback in getattr(result, 'failures', []):
            failed.append(('FAIL', test.id()))
        for test, _traceback in getattr(result, 'errors', []):
            failed.append(('ERROR', test.id()))
        return failed

    def _draw_table(self, headers, rows):
        widths = [len(header) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))

        def format_row(row):
            return '| ' + ' | '.join(cell.ljust(widths[index]) for index, cell in enumerate(row)) + ' |'

        separator = '+-' + '-+-'.join('-' * width for width in widths) + '-+'
        lines = [separator, format_row(headers), separator]
        lines.extend(format_row(row) for row in rows)
        lines.append(separator)
        return '\n'.join(lines)