import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


# Vendored/minified files we never touch.
SKIP_FILES = {'bootstrap-icons.min.css'}

# 16px == 1rem base; intermediate values snap to this increment first.
STEP = Decimal('0.125')
PX_PER_REM = Decimal('16')

# The font-size scale defined in styles.css :root. Every font-size literal is
# mapped to the nearest of these tokens (ties round up); anything above the top
# step is capped at --fs-4xl. Keep this in sync with the --fs-* tokens.
FS_SCALE = [
	(Decimal('0.5'), '--fs-2xs'),
	(Decimal('0.625'), '--fs-xs'),
	(Decimal('0.75'), '--fs-sm'),
	(Decimal('0.875'), '--fs-md'),
	(Decimal('1'), '--fs-lg'),
	(Decimal('1.125'), '--fs-xl'),
	(Decimal('1.25'), '--fs-2xl'),
	(Decimal('1.5'), '--fs-3xl'),
	(Decimal('2'), '--fs-4xl'),
]

# Matches a font-size with a bare px/rem literal value — NOT `var(--fs-*)`
# (already tokenised) and NOT the `--fs-*:` token definitions themselves.
FONT_SIZE_RE = re.compile(
	r'(font-size\s*:\s*)([0-9]*\.?[0-9]+)(px|rem)\b',
	re.IGNORECASE,
)


def rem_to_token(rem):
	"""Return the --fs-* token whose value is nearest to `rem` (ties round up)."""
	best_dist = None
	best_value = None
	best_name = None
	for value, name in FS_SCALE:
		dist = abs(rem - value)
		if best_dist is None or dist < best_dist or (dist == best_dist and value > best_value):
			best_dist, best_value, best_name = dist, value, name
	return best_name


def snap_font_size(number_str, unit):
	"""Map a px/rem font-size literal to a `var(--fs-*)` scale token."""
	value = Decimal(number_str)
	rem = value / PX_PER_REM if unit.lower() == 'px' else value
	steps = (rem / STEP).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
	snapped = steps * STEP
	return f'var({rem_to_token(snapped)})'


def normalize_font_sizes(text):
	"""Rewrite every px/rem font-size literal to its nearest --fs-* token.

	Returns (new_text, changes) where changes is a list of (old, new) tuples.
	var()/clamp()/calc()/%/em/keyword values and the --fs-* token definitions
	are left untouched because they don't match the regex.
	"""
	changes = []

	def repl(match):
		prefix, number, unit = match.group(1), match.group(2), match.group(3)
		new_value = snap_font_size(number, unit)
		changes.append((f'{number}{unit}', new_value))
		return f'{prefix}{new_value}'

	new_text = FONT_SIZE_RE.sub(repl, text)
	return new_text, changes


def _scan_braces(s):
	"""Return (min_depth, final_depth, has_open) for the top-level braces in s.

	Braces inside /* */ comments and '…'/"…" strings are ignored. min_depth
	going negative means the fragment starts with an unmatched closer.
	"""
	depth = 0
	min_depth = 0
	has_open = False
	quote = None
	in_comment = False
	i = 0
	n = len(s)
	while i < n:
		ch = s[i]
		if in_comment:
			if s[i:i + 2] == '*/':
				in_comment = False
				i += 2
				continue
			i += 1
			continue
		if quote:
			if ch == '\\':
				i += 2
				continue
			if ch == quote:
				quote = None
			i += 1
			continue
		if s[i:i + 2] == '/*':
			in_comment = True
			i += 2
			continue
		if ch in ('"', "'"):
			quote = ch
			i += 1
			continue
		if ch == '{':
			depth += 1
			has_open = True
		elif ch == '}':
			depth -= 1
			min_depth = min(min_depth, depth)
		i += 1
	return min_depth, depth, has_open


def _find_matching_brace(s, open_idx):
	"""Index of the '}' matching the '{' at open_idx, or -1. Skips strings/comments."""
	depth = 0
	quote = None
	in_comment = False
	i = open_idx
	n = len(s)
	while i < n:
		ch = s[i]
		if in_comment:
			if s[i:i + 2] == '*/':
				in_comment = False
				i += 2
				continue
			i += 1
			continue
		if quote:
			if ch == '\\':
				i += 2
				continue
			if ch == quote:
				quote = None
			i += 1
			continue
		if s[i:i + 2] == '/*':
			in_comment = True
			i += 2
			continue
		if ch in ('"', "'"):
			quote = ch
			i += 1
			continue
		if ch == '{':
			depth += 1
		elif ch == '}':
			depth -= 1
			if depth == 0:
				return i
		i += 1
	return -1


def _emit_rules(fragment, indent, out):
	"""Emit the rules/declarations in a balanced fragment as multi-line output.

	Recurses into nested blocks (@media/@keyframes/@supports). Declarations get
	one line each with a trailing ';'. Appends lines (no newline) to `out`.
	"""
	s = fragment
	n = len(s)
	i = 0
	while i < n:
		if s[i].isspace():
			i += 1
			continue
		# Scan the prelude up to the terminating '{' or ';' at paren-depth 0.
		j = i
		paren = 0
		quote = None
		in_comment = False
		brace_pos = -1
		semi_pos = -1
		while j < n:
			ch = s[j]
			if in_comment:
				if s[j:j + 2] == '*/':
					in_comment = False
					j += 2
					continue
				j += 1
				continue
			if quote:
				if ch == '\\':
					j += 2
					continue
				if ch == quote:
					quote = None
				j += 1
				continue
			if s[j:j + 2] == '/*':
				in_comment = True
				j += 2
				continue
			if ch in ('"', "'"):
				quote = ch
				j += 1
				continue
			if ch == '(':
				paren += 1
			elif ch == ')':
				paren -= 1
			elif ch == '{' and paren == 0:
				brace_pos = j
				break
			elif ch == ';' and paren == 0:
				semi_pos = j
				break
			j += 1

		if brace_pos == -1 and semi_pos == -1:
			# Trailing content with no terminator (e.g. a comment, or a final
			# declaration missing its semicolon).
			rest = s[i:].strip()
			if rest:
				if not rest.startswith('/*') and ':' in rest:
					out.append(f'{indent}{rest};')
				else:
					out.append(f'{indent}{rest}')
			break

		if semi_pos != -1 and (brace_pos == -1 or semi_pos < brace_pos):
			decl = s[i:semi_pos].strip()
			if decl:
				out.append(f'{indent}{decl};')
			i = semi_pos + 1
			continue

		# A rule: prelude { body }.
		prelude = s[i:brace_pos].strip()
		close = _find_matching_brace(s, brace_pos)
		if close == -1:
			rest = s[i:].strip()
			if rest:
				out.append(f'{indent}{rest}')
			break
		body = s[brace_pos + 1:close]
		out.append(f'{indent}{prelude} {{')
		_emit_rules(body, indent + '\t', out)
		out.append(f'{indent}}}')
		i = close + 1


def process_line(line):
	"""Reformat one physical line if it packs a rule (or rules) onto a single line.

	Returns the replacement text (possibly multiple lines) or None to leave the
	line untouched. Handles leaf rules, nested at-rule wrappers, multiple rules
	on one line, and a stray leading '}' glued to the next selector.
	"""
	indent = line[:len(line) - len(line.lstrip())]
	body = line[len(indent):]

	# Peel any leading closing braces glued to the front (e.g. `}.card {`).
	closers = 0
	rest = body
	while rest.startswith('}'):
		closers += 1
		rest = rest[1:].lstrip()

	min_depth, depth, has_open = _scan_braces(rest)
	is_one_liner = has_open and depth == 0 and min_depth == 0

	# Nothing to do: no glued closer and not a self-contained one-line rule.
	if closers == 0 and not is_one_liner:
		return None

	out = []
	for _ in range(closers):
		out.append(f'{indent}}}')
	if rest == '':
		pass
	elif is_one_liner:
		_emit_rules(rest, indent, out)
	else:
		# Remainder is a block opener (`.foo {`) or a fragment we won't touch.
		out.append(f'{indent}{rest.rstrip()}')
	replacement = '\n'.join(out)
	# Idempotent: a plain closing-brace line reconstructs to itself — no change.
	if replacement == line:
		return None
	return replacement


def _advance_comment_state(line, in_comment):
	"""Return whether we're inside a /* */ comment at the end of `line`.

	Strings are honoured so a '/*' inside a quoted value isn't a comment.
	Quotes don't carry across lines in CSS, so quote state resets each call.
	"""
	i = 0
	n = len(line)
	quote = None
	while i < n:
		if in_comment:
			if line[i:i + 2] == '*/':
				in_comment = False
				i += 2
				continue
			i += 1
			continue
		if quote:
			if line[i] == '\\':
				i += 2
				continue
			if line[i] == quote:
				quote = None
			i += 1
			continue
		ch = line[i]
		if line[i:i + 2] == '/*':
			in_comment = True
			i += 2
			continue
		if ch in ('"', "'"):
			quote = ch
			i += 1
			continue
		i += 1
	return in_comment


def expand_single_line_rules(text):
	"""Expand every single-line/packed rule to the standard multi-line layout.

	Returns (new_text, expanded_count, skipped_count). skipped_count flags
	lines whose braces don't balance cleanly (kept as-is for manual review).
	Lines that open inside a multi-line /* */ comment are left untouched so
	brace-like text inside comments is never mistaken for a rule.
	"""
	lines = text.split('\n')
	out_lines = []
	expanded = 0
	skipped = 0
	in_comment = False
	for line in lines:
		if in_comment:
			# Comment interior — never reformat; just track when it closes.
			out_lines.append(line)
			in_comment = _advance_comment_state(line, True)
			continue

		replacement = process_line(line)
		in_comment = _advance_comment_state(line, False)
		if replacement is not None:
			out_lines.append(replacement)
			expanded += 1
			continue
		# A line holding both braces that we still didn't reformat is an odd,
		# unbalanced case worth a manual look.
		if '{' in line and '}' in line:
			skipped += 1
		out_lines.append(line)

	return '\n'.join(out_lines), expanded, skipped


class Command(BaseCommand):
	help = (
		'Lint the project CSS: expand single-line rules to the standard '
		'multi-line layout, and map every px/rem font-size to the nearest '
		'--fs-* scale token from styles.css (16px == 1rem, ties round up, '
		'capped at --fs-4xl). Reports violations by default; pass --fix to '
		'rewrite the files in place.'
	)

	def add_arguments(self, parser):
		parser.add_argument(
			'files',
			nargs='*',
			help='Specific CSS files to lint (default: all of static/css).',
		)
		parser.add_argument(
			'--fix',
			action='store_true',
			help='Rewrite files in place (default is report-only).',
		)

	def handle(self, *args, **options):
		fix = options['fix']
		explicit = options['files']

		css_dir = Path(settings.BASE_DIR) / 'static' / 'css'
		if explicit:
			targets = [Path(f) for f in explicit]
		else:
			targets = sorted(
				p for p in css_dir.glob('*.css') if p.name not in SKIP_FILES
			)

		total_expanded = 0
		total_skipped = 0
		total_font = 0
		files_changed = 0
		font_change_counts = {}

		for path in targets:
			if not path.exists():
				self.stderr.write(self.style.WARNING(f'skip (missing): {path}'))
				continue
			if path.name in SKIP_FILES:
				continue

			# Read/normalise line endings ourselves so Windows text-mode I/O
			# never silently flips LF<->CRLF and blows up the diff.
			raw_bytes = path.read_bytes()
			raw = raw_bytes.decode('utf-8')
			uses_crlf = '\r\n' in raw
			original = raw.replace('\r\n', '\n')

			text, font_changes = normalize_font_sizes(original)
			text, expanded, skipped = expand_single_line_rules(text)

			total_expanded += expanded
			total_skipped += skipped
			total_font += len(font_changes)
			for old, new in font_changes:
				font_change_counts[(old, new)] = font_change_counts.get((old, new), 0) + 1

			changed = text != original
			if not changed:
				continue
			files_changed += 1

			label = f'{path.name}: {expanded} rule(s) expanded, {len(font_changes)} font-size(s) tokenised'
			if skipped:
				label += f', {skipped} packed line(s) skipped'

			if fix:
				out = text.replace('\n', '\r\n') if uses_crlf else text
				path.write_bytes(out.encode('utf-8'))
				self.stdout.write(self.style.SUCCESS(f'fixed  {label}'))
			else:
				self.stdout.write(f'would fix  {label}')

		self.stdout.write('')
		verb = 'Fixed' if fix else 'Would fix'
		self.stdout.write(self.style.MIGRATE_HEADING(
			f'{verb} {files_changed} file(s): '
			f'{total_expanded} single-line rules expanded, '
			f'{total_font} font-sizes tokenised'
			+ (f', {total_skipped} packed lines need manual review.' if total_skipped else '.')
		))

		if font_change_counts:
			self.stdout.write('')
			self.stdout.write(self.style.MIGRATE_HEADING('Font-size remaps (old -> token, count):'))
			for (old, new), count in sorted(
				font_change_counts.items(), key=lambda kv: (-kv[1], kv[0])
			):
				self.stdout.write(f'  {old:>10} -> {new:<14} x{count}')

		if not fix and files_changed:
			self.stdout.write('')
			self.stdout.write(self.style.WARNING('Report only — re-run with --fix to apply.'))
