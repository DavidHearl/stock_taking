"""Match website enquiries against existing Atlas customers.

Read-only enrichment: given a set of :class:`WebsiteEnquiry` rows, work out
which of them correspond to a customer we already hold, so the enquiries list
can flag repeat/known customers instead of treating every enquiry as a cold lead.

Matching is tiered by confidence (highest first):

    1. ``email``          — exact, case-insensitive email match.
    2. ``phone``          — normalised phone match (last 9 significant digits,
                            so ``07700 900000`` and ``+44 7700 900000`` collide).
    3. ``name_postcode``  — last name + postcode, where a postcode can be
                            extracted from the enquiry's free-text address.

All lookups are built in a handful of queries and resolved in memory, so the
cost is O(customers + enquiries) regardless of how many enquiries are shown.
"""

import logging
import re

from django.db.models import Count

from ..models import Customer, Order

logger = logging.getLogger(__name__)

# UK postcode (e.g. BT1 1AA, SW1A 2AA) and ROI Eircode (e.g. D02 AF30).
_UK_POSTCODE_RE = re.compile(r'[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}', re.IGNORECASE)
_EIRCODE_RE = re.compile(r'[AC-FHKNPRTV-Y]\d{2}\s*[0-9AC-FHKNPRTV-Y]{4}', re.IGNORECASE)

# Human-facing labels + relative confidence for each match tier.
TIER_META = {
	'email': {'label': 'Existing customer', 'confidence': 'high', 'basis': 'email'},
	'phone': {'label': 'Likely match', 'confidence': 'medium', 'basis': 'phone'},
	'name_postcode': {'label': 'Possible match', 'confidence': 'low', 'basis': 'name & postcode'},
}


def _norm_email(value):
	return (value or '').strip().lower()


def _norm_phone(value):
	"""Reduce a phone number to its last 9 significant digits (or '' if too short)."""
	digits = re.sub(r'\D', '', value or '')
	if len(digits) < 9:
		return ''
	return digits[-9:]


def _norm_postcode(value):
	return re.sub(r'\s+', '', (value or '')).upper()


def _extract_postcode(text):
	"""Pull the first UK postcode or ROI Eircode out of a free-text address."""
	if not text:
		return ''
	match = _UK_POSTCODE_RE.search(text) or _EIRCODE_RE.search(text)
	return _norm_postcode(match.group(0)) if match else ''


def _last_name_for(enquiry):
	"""Best-effort last name: the structured field, else the final token of `name`."""
	if enquiry.last_name:
		return enquiry.last_name.strip().lower()
	parts = [p for p in (enquiry.name or '').split() if p]
	return parts[-1].lower() if len(parts) > 1 else ''


def _build_customer_indexes():
	"""Return (email_map, phone_map, name_postcode_map) keyed for fast lookup.

	Each map value is a list of customer ids, so ambiguous matches (more than one
	customer sharing an email/phone) can be surfaced rather than silently picking one.
	"""
	email_map = {}
	phone_map = {}
	name_postcode_map = {}

	rows = Customer.objects.filter(is_active=True).values(
		'id', 'email', 'phone', 'last_name', 'postcode',
	)
	for row in rows:
		email = _norm_email(row['email'])
		if email:
			email_map.setdefault(email, []).append(row['id'])

		phone = _norm_phone(row['phone'])
		if phone:
			phone_map.setdefault(phone, []).append(row['id'])

		last_name = (row['last_name'] or '').strip().lower()
		postcode = _norm_postcode(row['postcode'])
		if last_name and postcode:
			name_postcode_map.setdefault((last_name, postcode), []).append(row['id'])

	return email_map, phone_map, name_postcode_map


def find_customer_matches(enquiries):
	"""Map each enquiry to its best customer match (or ``None``).

	:param enquiries: iterable of :class:`WebsiteEnquiry` instances.
	:returns: ``{enquiry_id: match_dict}`` where ``match_dict`` is::

		{
			'tier': 'email' | 'phone' | 'name_postcode',
			'label': 'Existing customer',
			'confidence': 'high' | 'medium' | 'low',
			'customer_id': 42,
			'customer_name': 'Jane Smith',
			'order_count': 2,
			'ambiguous': False,   # True when >1 customer shared the matched key
		}

	Enquiries with no match are absent from the dict.
	"""
	enquiries = list(enquiries)
	if not enquiries:
		return {}

	email_map, phone_map, name_postcode_map = _build_customer_indexes()

	# First pass: resolve each enquiry to a customer id + tier, collecting the
	# set of matched customer ids so order counts + names load in one query each.
	raw_matches = {}
	matched_ids = set()

	for enq in enquiries:
		customer_ids = None
		tier = None

		email = _norm_email(enq.email)
		if email and email in email_map:
			customer_ids, tier = email_map[email], 'email'

		if customer_ids is None:
			phone = _norm_phone(enq.phone)
			if phone and phone in phone_map:
				customer_ids, tier = phone_map[phone], 'phone'

		if customer_ids is None:
			last_name = _last_name_for(enq)
			postcode = _extract_postcode(enq.address)
			key = (last_name, postcode)
			if last_name and postcode and key in name_postcode_map:
				customer_ids, tier = name_postcode_map[key], 'name_postcode'

		if customer_ids:
			chosen = customer_ids[0]
			raw_matches[enq.pk] = {'customer_id': chosen, 'tier': tier, 'ambiguous': len(customer_ids) > 1}
			matched_ids.add(chosen)

	if not matched_ids:
		return {}

	names = dict(
		Customer.objects.filter(id__in=matched_ids).values_list('id', 'name')
	)
	order_counts = {
		row['customer']: row['n']
		for row in Order.objects.filter(customer_id__in=matched_ids)
		.values('customer').annotate(n=Count('id'))
	}

	result = {}
	for enq_pk, match in raw_matches.items():
		meta = TIER_META[match['tier']]
		result[enq_pk] = {
			'tier': match['tier'],
			'label': meta['label'],
			'confidence': meta['confidence'],
			'basis': meta['basis'],
			'customer_id': match['customer_id'],
			'customer_name': names.get(match['customer_id']) or f"Customer #{match['customer_id']}",
			'order_count': order_counts.get(match['customer_id'], 0),
			'ambiguous': match['ambiguous'],
		}
	return result
