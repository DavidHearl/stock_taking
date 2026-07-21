"""Canonical parser for the free-text date strings held on legacy CharFields.

Several models (most notably PurchaseOrder's issue/approved/expected/received
dates) store dates as strings in whatever format the originating system sent.
The shapes actually present in the data are:

	2026-07-15T09:14:22+01:00           ISO 8601, with offset and optional
	2026-07-15T09:14:22.1234567+01:00   fractional seconds (1-7 digits)
	2026-07-15                          ISO date only
	21-07-2026                          UK day-first, dash separated
	21/07/2026                          UK day-first, slash separated

Day-first and ISO are told apart by which end holds the 4-digit year, so there
is no ambiguity to guess at. Anything else returns None rather than a wrong date.
"""
import re
from datetime import date, datetime

# Leading date portion, before any 'T'/space time component.
_ISO_RE = re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})')
_UK_RE = re.compile(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})')


def parse_date_str(value):
	"""Parse a stored date string (any of the formats above) to a `date`.

	Returns None when the value is empty or in an unrecognised format. Accepts
	real date/datetime objects too, so callers don't have to special-case them.
	"""
	if not value:
		return None
	# datetime subclasses date, so it has to be narrowed first.
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value

	text = str(value).strip()
	if not text:
		return None

	match = _ISO_RE.match(text)
	if match:
		year, month, day = (int(g) for g in match.groups())
	else:
		match = _UK_RE.match(text)
		if not match:
			return None
		day, month, year = (int(g) for g in match.groups())

	try:
		return date(year, month, day)
	except ValueError:
		# Real-looking but impossible (e.g. 31-02-2026) — treat as unparseable.
		return None


def date_str_to_iso(value):
	"""Parse a stored date string and return 'YYYY-MM-DD', or '' if unparseable."""
	parsed = parse_date_str(value)
	return parsed.isoformat() if parsed else ''
