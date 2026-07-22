/* Payments → Reconcile tab (customer-anchored).
   Expand/collapse a customer's sales + payments and run the reconcile tools:
   add a payment to a chosen sale, ignore/un-ignore or delete a payment, and
   search Xero for the customer's invoices then link one to the right sale.
   All actions hit the existing sale/customer endpoints; every successful
   mutation reloads so the pooled balances stay accurate. */
(function () {
	'use strict';

	function csrf() {
		return window.PAY_CSRF || '';
	}

	function today() {
		const d = new Date();
		const mm = String(d.getMonth() + 1).padStart(2, '0');
		const dd = String(d.getDate()).padStart(2, '0');
		return d.getFullYear() + '-' + mm + '-' + dd;
	}

	function money(v) {
		const n = parseFloat(v);
		if (isNaN(n)) return v;
		return '£' + n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}

	function readJSON(resp) {
		return resp.json().catch(function () { return {}; }).then(function (data) {
			return { ok: resp.ok, data: data };
		});
	}

	function postJSON(url, payload) {
		return fetch(url, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-CSRFToken': csrf(),
			},
			body: payload ? JSON.stringify(payload) : '{}',
		}).then(readJSON);
	}

	function toggleRow(pk) {
		const row = document.querySelector('.pay-recon-row[data-pk="' + pk + '"]');
		const detail = document.querySelector('.pay-recon-detail[data-detail-pk="' + pk + '"]');
		if (!row || !detail) return;
		if (detail.hasAttribute('hidden')) {
			detail.removeAttribute('hidden');
			row.classList.add('is-open');
		} else {
			detail.setAttribute('hidden', '');
			row.classList.remove('is-open');
		}
	}

	function setBusy(btn, busy) {
		if (!btn) return;
		btn.disabled = busy;
		btn.classList.toggle('is-busy', busy);
	}

	function reloadOr(btn, res, fallbackMsg) {
		if (res.ok && res.data.success) {
			window.location.reload();
		} else {
			setBusy(btn, false);
			const msg = res.data && (res.data.error || (res.data.errors || []).join(', '));
			alert(msg || fallbackMsg);
		}
	}

	// ── Xero search + link (customer-level) ──

	function renderXeroResults(container, linkUrl, data) {
		container.innerHTML = '';
		container.removeAttribute('hidden');
		const invoices = (data && data.invoices) || [];
		const sales = (data && data.sales) || [];
		if (!invoices.length) {
			const empty = document.createElement('div');
			empty.className = 'pay-xero-empty';
			empty.textContent = (data && data.message) || 'No Xero invoices found for this customer.';
			container.appendChild(empty);
			return;
		}
		const head = document.createElement('div');
		head.className = 'pay-recon-sub';
		head.textContent = 'Xero invoices' + (data.customer_name ? ' — ' + data.customer_name : '');
		container.appendChild(head);

		invoices.forEach(function (inv) {
			const row = document.createElement('div');
			row.className = 'pay-xero-row';

			const info = document.createElement('div');
			info.className = 'pay-xero-info';
			const num = document.createElement('span');
			num.className = 'pay-xero-num';
			num.textContent = inv.invoice_number || inv.reference || '(no number)';
			const meta = document.createElement('span');
			meta.className = 'pay-xero-meta';
			meta.textContent = [inv.date || '', money(inv.total), inv.status || ''].filter(Boolean).join(' · ');
			info.appendChild(num);
			info.appendChild(meta);
			row.appendChild(info);

			const action = document.createElement('div');
			action.className = 'pay-xero-action';
			if (inv.linked_sales && inv.linked_sales.length) {
				const tag = document.createElement('span');
				tag.className = 'pay-xero-tag linked';
				tag.textContent = 'On ' + inv.linked_sales.join(', ');
				action.appendChild(tag);
			} else {
				const sel = document.createElement('select');
				sel.className = 'pay-xero-sale';
				sel.setAttribute('aria-label', 'Link to sale');
				sales.forEach(function (s) {
					const opt = document.createElement('option');
					opt.value = s.pk;
					opt.textContent = s.label;
					if (inv.suggested_sale_pk && String(inv.suggested_sale_pk) === String(s.pk)) {
						opt.selected = true;
					}
					sel.appendChild(opt);
				});
				const btn = document.createElement('button');
				btn.type = 'button';
				btn.className = 'btn btn-sm btn-primary';
				btn.textContent = 'Link';
				btn.setAttribute('data-recon-action', 'xero-link');
				btn.setAttribute('data-url', linkUrl);
				btn.setAttribute('data-invoice-id', inv.invoice_id);
				action.appendChild(sel);
				action.appendChild(btn);
			}
			row.appendChild(action);
			container.appendChild(row);
		});
	}

	function searchXero(btn) {
		const container = btn.closest('.pay-recon-tools').querySelector('.pay-xero-results');
		const linkUrl = btn.getAttribute('data-xero-link-url');
		container.removeAttribute('hidden');
		container.innerHTML = '<div class="pay-xero-loading">Searching Xero…</div>';
		setBusy(btn, true);
		fetch(btn.getAttribute('data-xero-search-url'), {
			headers: { 'X-CSRFToken': csrf() },
		}).then(readJSON).then(function (res) {
			setBusy(btn, false);
			if (res.ok && res.data.success) {
				renderXeroResults(container, linkUrl, res.data);
			} else {
				container.innerHTML = '';
				const err = document.createElement('div');
				err.className = 'pay-xero-empty';
				err.textContent = (res.data && res.data.error) || 'Xero search failed.';
				container.appendChild(err);
			}
		}).catch(function () {
			setBusy(btn, false);
			container.innerHTML = '<div class="pay-xero-empty">Network error — could not reach Xero.</div>';
		});
	}

	// ── Action dispatch ──

	function handleAction(btn) {
		const action = btn.getAttribute('data-recon-action');

		if (action === 'search-xero') {
			searchXero(btn);
			return;
		}

		if (action === 'xero-link') {
			const sel = btn.closest('.pay-xero-action').querySelector('.pay-xero-sale');
			const salePk = sel ? sel.value : '';
			if (!salePk) { alert('Pick a sale to link this invoice to.'); return; }
			setBusy(btn, true);
			postJSON(btn.getAttribute('data-url'), { invoice_id: btn.getAttribute('data-invoice-id'), sale_pk: salePk })
				.then(function (res) { reloadOr(btn, res, 'Could not link this invoice.'); })
				.catch(function () { setBusy(btn, false); alert('Network error — invoice not linked.'); });
			return;
		}

		if (action === 'add-adjustment') {
			const wrap = btn.closest('.pay-recon-add');
			const saleSel = wrap.querySelector('.pay-recon-sale-select');
			const opt = saleSel && saleSel.selectedOptions[0];
			const addUrl = opt ? opt.getAttribute('data-add-url') : wrap.getAttribute('data-add-url');
			const showroom = opt ? (opt.getAttribute('data-showroom') || '') : (wrap.getAttribute('data-showroom') || '');
			const typeSel = wrap.querySelector('.pay-recon-type');
			const input = wrap.querySelector('.pay-recon-amount');
			const amount = (input.value || '').trim();
			if (!addUrl) { alert('No sale selected.'); return; }
			if (!amount) { input.focus(); return; }
			setBusy(btn, true);
			postJSON(addUrl, {
				payments: [{
					type: typeSel ? typeSel.value : 'Adjustment',
					date: today(),
					location: showroom,
					user: '',
					amount: amount,
					status: 'Confirmed',
				}],
			}).then(function (res) { reloadOr(btn, res, 'Could not add payment.'); })
				.catch(function () { setBusy(btn, false); alert('Network error — payment not added.'); });
			return;
		}

		// toggle-ignore / delete-manual — both POST to a URL on the button.
		const url = btn.getAttribute('data-url');
		if (!url) return;
		if (action === 'delete-manual' && !window.confirm('Delete this manual payment?')) return;
		setBusy(btn, true);
		postJSON(url, null)
			.then(function (res) { reloadOr(btn, res, 'Action failed.'); })
			.catch(function () { setBusy(btn, false); alert('Network error — action not applied.'); });
	}

	document.addEventListener('click', function (e) {
		const actionBtn = e.target.closest('[data-recon-action]');
		if (actionBtn) {
			e.preventDefault();
			handleAction(actionBtn);
			return;
		}
		// Expand/collapse: ignore clicks on links, buttons, inputs and selects.
		if (e.target.closest('a, button, input, select')) return;
		const row = e.target.closest('.pay-recon-row');
		if (row) {
			toggleRow(row.getAttribute('data-pk'));
		}
	});
})();
