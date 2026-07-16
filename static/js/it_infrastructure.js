/* ============================================================
   it_infrastructure.js  –  UniFi rack planner (mockup)
   Client-side prototype. State persists to localStorage only;
   swap for a server model once the concept is approved.
   ============================================================ */
(function () {
	'use strict';

	var STORAGE_KEY = 'atlas_infra_v2';
	var U_PX = 40; // pixel height of one rack unit

	// Branch locations — mirrors the laptop asset register set.
	var LOCATIONS = ['Belfast', 'Dublin', 'Nottingham', 'Wyedean', 'Midlands'];

	// ── UniFi equipment catalog (approx. GBP list prices + faceplate hints) ──
	var CATALOG = [
		{ key: 'udm-pro',            name: 'Dream Machine Pro',          model: 'UDM-Pro',            category: 'gateway', uh: 1, cost: 379,  power: 33,  ports: '8× GbE + 2× SFP+',        face: 'gateway',     fp: { ports: 8, sfp: 2, screen: true } },
		{ key: 'udm-pro-max',        name: 'Dream Machine Pro Max',      model: 'UDM-Pro-Max',        category: 'gateway', uh: 1, cost: 579,  power: 33,  ports: '8× 2.5G + 2× 10G SFP+',   face: 'gateway',     fp: { ports: 8, sfp: 2, screen: true } },
		{ key: 'efg',                name: 'Enterprise Fortress Gateway',model: 'EFG',                category: 'gateway', uh: 1, cost: 1999, power: 50,  ports: '2× 25G SFP28',            face: 'gateway',     fp: { ports: 2, sfp: 2, screen: true } },
		{ key: 'usw-pro-48-poe',     name: 'Switch Pro 48 PoE',          model: 'USW-Pro-48-PoE',     category: 'switch',  uh: 1, cost: 1099, power: 600, ports: '48× GbE PoE + 4× SFP+',   face: 'switch',      fp: { ports: 48, sfp: 4 } },
		{ key: 'usw-pro-24-poe',     name: 'Switch Pro 24 PoE',          model: 'USW-Pro-24-PoE',     category: 'switch',  uh: 1, cost: 679,  power: 400, ports: '24× GbE PoE + 2× SFP+',   face: 'switch',      fp: { ports: 24, sfp: 2 } },
		{ key: 'usw-pro-max-24-poe', name: 'Switch Pro Max 24 PoE',      model: 'USW-Pro-Max-24-PoE', category: 'switch',  uh: 1, cost: 729,  power: 400, ports: '24× 2.5G PoE + 2× 10G',   face: 'switch',      fp: { ports: 24, sfp: 2 } },
		{ key: 'usw-24-poe',         name: 'Switch 24 PoE',              model: 'USW-24-PoE',         category: 'switch',  uh: 1, cost: 379,  power: 95,  ports: '24× GbE (16 PoE) + 2× SFP',face: 'switch',      fp: { ports: 24, sfp: 2 } },
		{ key: 'usw-aggregation',    name: 'Switch Aggregation',         model: 'USW-Aggregation',    category: 'switch',  uh: 1, cost: 279,  power: 18,  ports: '28× 10G SFP+',            face: 'aggregation', fp: { sfp: 28 } },
		{ key: 'usw-pro-aggregation',name: 'Switch Pro Aggregation',     model: 'USW-Pro-Aggregation',category: 'switch',  uh: 1, cost: 1399, power: 60,  ports: '28× 10G + 4× 25G SFP28',  face: 'aggregation', fp: { sfp: 32 } },
		{ key: 'unvr-pro',           name: 'Network Video Recorder Pro', model: 'UNVR-Pro',           category: 'storage', uh: 2, cost: 679,  power: 60,  ports: '7-bay HDD, 10G SFP+',     face: 'storage',     fp: { bays: 7, screen: true } },
		{ key: 'unvr',               name: 'Network Video Recorder',     model: 'UNVR',               category: 'storage', uh: 1, cost: 379,  power: 40,  ports: '4-bay HDD',               face: 'storage',     fp: { bays: 4 } },
		{ key: 'usp-pdu-pro',        name: 'SmartPower PDU Pro',         model: 'USP-PDU-Pro',        category: 'power',   uh: 1, cost: 389,  power: 0,   ports: '24 outlets (metered)',    face: 'pdu',         fp: { outlets: 8 } },
		{ key: 'patch-24',           name: 'Patch Panel 24-port',        model: 'Keystone 24',        category: 'patch',   uh: 1, cost: 22,   power: 0,   ports: '24× RJ45',                face: 'patch',       fp: { ports: 24 } },
		{ key: 'cable-mgmt',         name: 'Cable Management',           model: '1U brush panel',     category: 'blank',   uh: 1, cost: 12,   power: 0,   ports: '—',                       face: 'cable',       fp: {} },
		{ key: 'blank',              name: 'Blanking Panel',             model: '1U',                 category: 'blank',   uh: 1, cost: 5,    power: 0,   ports: '—',                       face: 'blank',       fp: {} },
		{ key: 'custom',             name: 'Custom Equipment…',          model: '',                   category: 'switch',  uh: 1, cost: 0,    power: 0,   ports: '',                        face: 'switch',      fp: { ports: 24, sfp: 2 }, custom: true }
	];

	var CATEGORIES = [
		{ key: 'gateway', label: 'Gateway' },
		{ key: 'switch',  label: 'Switch' },
		{ key: 'power',   label: 'Power' },
		{ key: 'storage', label: 'Storage / NVR' },
		{ key: 'patch',   label: 'Patch panel' },
		{ key: 'blank',   label: 'Blank / cable' }
	];

	// Cable colour per source category (SVG stroke via CSS var)
	var CABLE_COLOR = {
		gateway: 'var(--primary-color)', switch: 'var(--info-color)',
		storage: 'var(--purple-color)',  power: 'var(--warning-color)',
		patch: 'var(--success-color)',   blank: 'var(--text-muted)'
	};

	// ── State ──
	var state = { racks: [] };
	var selected = null;      // { rackId, deviceId }
	var activeLocation = LOCATIONS[0];
	var showCabling = false;
	var editingRackId = null;
	var drag = null;          // { rackId, deviceId, uh }
	var uid = 0;
	function nextId(prefix) { uid += 1; return prefix + '-' + uid + '-' + (state.racks.length + 1) * 7; }

	function seed() {
		function dev(key, u, uplink) {
			var c = CATALOG.find(function (x) { return x.key === key; });
			return {
				id: 'dev-' + key, catalog: key, name: c.name, model: c.model,
				category: c.category, uh: c.uh, cost: c.cost, power: c.power,
				ports: c.ports, notes: '', u: u, uplink: uplink || null
			};
		}
		return {
			racks: [{
				id: 'rack-seed', name: 'Belfast Comms Room', location: 'Belfast', height: 12,
				devices: [
					dev('udm-pro', 12),
					dev('usw-pro-24-poe', 11, 'dev-udm-pro'),
					dev('usw-24-poe', 10, 'dev-usw-pro-24-poe'),
					dev('patch-24', 9, 'dev-usw-24-poe'),
					dev('unvr-pro', 6, 'dev-usw-pro-24-poe'),
					dev('usp-pdu-pro', 1)
				]
			}]
		};
	}

	function load() {
		try {
			var raw = localStorage.getItem(STORAGE_KEY);
			if (raw) { state = JSON.parse(raw); }
			else { state = seed(); save(); }
		} catch (e) { state = seed(); save(); }
		var withRacks = LOCATIONS.filter(function (l) { return state.racks.some(function (r) { return r.location === l; }); });
		if (withRacks.length) { activeLocation = withRacks[0]; }
	}

	function save() {
		try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
	}

	// ── Helpers ──
	function esc(s) {
		return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
			return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
		});
	}
	function money(n) { return '£' + Number(n || 0).toLocaleString('en-GB'); }
	function getRack(id) { return state.racks.find(function (r) { return r.id === id; }); }
	function getDevice(rack, id) { return rack.devices.find(function (d) { return d.id === id; }); }
	function racksIn(loc) { return state.racks.filter(function (r) { return r.location === loc; }); }

	function occupied(rack, exceptId) {
		var set = {};
		rack.devices.forEach(function (d) {
			if (d.id === exceptId) { return; }
			for (var i = 0; i < d.uh; i++) { set[d.u + i] = true; }
		});
		return set;
	}
	function fits(rack, u, uh, exceptId) {
		if (u < 1 || u + uh - 1 > rack.height) { return false; }
		var occ = occupied(rack, exceptId);
		for (var i = 0; i < uh; i++) { if (occ[u + i]) { return false; } }
		return true;
	}
	function lowestFree(rack, uh) {
		for (var u = 1; u <= rack.height - uh + 1; u++) { if (fits(rack, u, uh)) { return u; } }
		return null;
	}

	// ── DOM refs ──
	var racksEl = document.getElementById('infraRacks');
	var detailEl = document.getElementById('infraDetail');
	var catalogEl = document.getElementById('infraCatalog');
	var summaryEl = document.getElementById('infraSummary');
	var navEl = document.getElementById('infraLocationNav');
	var locTitleEl = document.getElementById('infraLocTitle');

	function renderAll() { renderNav(); renderRacks(); renderSummary(); renderDetail(); }

	// ── Location nav ──
	// Standard .tab-nav-pills-vertical[data-pill-slider] / .tab-button layout.
	// Buttons are built once and then refreshed in place so the sliding pill
	// thumb (and the observers initPillSliders wires onto them) survive
	// re-renders; a tab click toggles .active in place, which the thumb follows.
	function navBtnHtml(loc) {
		var racks = racksIn(loc);
		var devices = racks.reduce(function (s, r) { return s + r.devices.length; }, 0);
		return '<span class="infra-loc-tab-name"><i class="bi bi-geo-alt"></i> ' + esc(loc) + '</span>' +
			'<span class="infra-loc-tab-sub">' + racks.length + ' rack' + (racks.length === 1 ? '' : 's') +
				' · ' + devices + ' device' + (devices === 1 ? '' : 's') + '</span>';
	}

	function renderNav() {
		var buttons = navEl.querySelectorAll('.tab-button');
		var sameSet = buttons.length === LOCATIONS.length &&
			LOCATIONS.every(function (loc, i) { return buttons[i].dataset.location === loc; });

		if (sameSet) {
			// Refresh counts + active state in place — keep the thumb + observers.
			LOCATIONS.forEach(function (loc, i) {
				buttons[i].innerHTML = navBtnHtml(loc);
				buttons[i].classList.toggle('active', loc === activeLocation);
			});
			locTitleEl.textContent = activeLocation;
			return;
		}

		// Location set changed — rebuild, then (re)init the pill slider.
		navEl.innerHTML = '';
		navEl._pillThumb = null;
		LOCATIONS.forEach(function (loc) {
			var btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'tab-button infra-loc-tab' + (loc === activeLocation ? ' active' : '');
			btn.dataset.location = loc;
			btn.innerHTML = navBtnHtml(loc);
			btn.addEventListener('click', function () { selectLocation(loc); });
			navEl.appendChild(btn);
		});
		locTitleEl.textContent = activeLocation;
		if (window.initPillSliders) window.initPillSliders(navEl);
	}

	function selectLocation(loc) {
		activeLocation = loc;
		navEl.querySelectorAll('.tab-button').forEach(function (b) {
			b.classList.toggle('active', b.dataset.location === loc);
		});
		locTitleEl.textContent = activeLocation;
		renderRacks();
	}

	// ── Racks ──
	function renderRacks() {
		racksEl.innerHTML = '';
		var racks = racksIn(activeLocation);
		if (!racks.length) {
			racksEl.innerHTML = '<div class="it-placeholder"><i class="bi bi-hdd-network"></i>' +
				'<p>No racks in ' + esc(activeLocation) + ' yet. Add one to start planning.</p></div>';
			return;
		}
		racks.forEach(function (rack) { racksEl.appendChild(buildRack(rack)); });
		drawAllCables();
	}

	function buildRack(rack) {
		var used = rack.devices.reduce(function (s, d) { return s + d.uh; }, 0);
		var cost = rack.devices.reduce(function (s, d) { return s + Number(d.cost || 0); }, 0);
		var power = rack.devices.reduce(function (s, d) { return s + Number(d.power || 0); }, 0);

		var el = document.createElement('div');
		el.className = 'infra-rack' + (showCabling ? ' cabling-on' : '');
		el.dataset.rackId = rack.id;
		el.innerHTML =
			'<div class="infra-rack-head">' +
				'<div>' +
					'<h2 class="infra-rack-name">' + esc(rack.name) + '</h2>' +
					'<p class="infra-rack-meta">' + used + '/' + rack.height + 'U used · ' +
						power.toLocaleString('en-GB') + ' W · ' + money(cost) + '</p>' +
				'</div>' +
				'<div class="infra-rack-actions">' +
					'<button class="btn-icon" data-rack-edit title="Edit rack"><i class="bi bi-pencil"></i></button>' +
					'<button class="btn-icon" data-rack-delete title="Delete rack"><i class="bi bi-trash"></i></button>' +
				'</div>' +
			'</div>';

		var cab = document.createElement('div');
		cab.className = 'infra-cabinet';

		var unum = document.createElement('div');
		unum.className = 'infra-unum';
		for (var n = rack.height; n >= 1; n--) {
			var u = document.createElement('div');
			u.className = 'infra-unum-u';
			u.textContent = n;
			unum.appendChild(u);
		}

		var bay = document.createElement('div');
		bay.className = 'infra-bay';

		var uu = rack.height;
		while (uu >= 1) {
			var dev = rack.devices.find(function (d) { return (d.u + d.uh - 1) === uu; });
			if (dev) { bay.appendChild(buildDevice(rack, dev)); uu -= dev.uh; }
			else { bay.appendChild(buildEmpty()); uu -= 1; }
		}

		// Cabling SVG overlay (drawn after layout)
		var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
		svg.setAttribute('class', 'infra-cables');
		bay.appendChild(svg);

		// Drop preview band
		var preview = document.createElement('div');
		preview.className = 'infra-drop-preview';
		bay.appendChild(preview);

		wireRackDnD(rack, bay, preview);

		cab.appendChild(unum);
		cab.appendChild(bay);
		el.appendChild(cab);

		el.querySelector('[data-rack-edit]').addEventListener('click', function () { openRackModal(rack.id); });
		el.querySelector('[data-rack-delete]').addEventListener('click', function () { deleteRack(rack.id); });
		return el;
	}

	function buildEmpty() {
		var el = document.createElement('div');
		el.className = 'infra-slot-empty';
		return el;
	}

	function buildDevice(rack, d) {
		var el = document.createElement('div');
		el.className = 'infra-device';
		el.style.height = (d.uh * U_PX) + 'px';
		el.setAttribute('draggable', 'true');
		el.dataset.deviceId = d.id;
		if (selected && selected.rackId === rack.id && selected.deviceId === d.id) {
			el.classList.add('infra-selected');
		}
		el.innerHTML =
			'<div class="infra-device-name">' + esc(d.name || 'Unnamed') + '</div>' +
			'<div class="infra-device-sub">' +
				'<span>' + esc(d.model || '') + '</span>' +
				(d.uh > 1 ? '<span class="infra-device-uh">' + d.uh + 'U</span>' : '') +
				'<span class="infra-cost">' + money(d.cost) + '</span>' +
			'</div>';
		el.title = (d.name || '') + (d.ports ? ' — ' + d.ports : '');

		el.addEventListener('click', function () { selectDevice(rack.id, d.id); });
		el.addEventListener('dragstart', function (ev) {
			drag = { rackId: rack.id, deviceId: d.id, uh: d.uh };
			ev.dataTransfer.effectAllowed = 'move';
			try { ev.dataTransfer.setData('text/plain', d.id); } catch (e) { /* IE guard */ }
			setTimeout(function () { el.classList.add('infra-dragging'); }, 0);
		});
		el.addEventListener('dragend', function () { el.classList.remove('infra-dragging'); drag = null; });
		return el;
	}

	// ── Cabling overlay ──
	function drawAllCables() {
		if (!showCabling) { return; }
		Array.prototype.forEach.call(racksEl.querySelectorAll('.infra-rack'), function (rackEl) {
			var rack = getRack(rackEl.dataset.rackId);
			if (rack) { drawCables(rack, rackEl.querySelector('.infra-bay'), rackEl.querySelector('.infra-cables')); }
		});
	}

	function centerY(rack, d) {
		var topRow = rack.height - (d.u + d.uh - 1);
		return topRow * U_PX + (d.uh * U_PX) / 2;
	}

	function drawCables(rack, bay, svg) {
		while (svg.firstChild) { svg.removeChild(svg.firstChild); }
		var w = bay.clientWidth, h = bay.clientHeight;
		if (!w || !h) { return; }
		svg.setAttribute('width', w);
		svg.setAttribute('height', h);
		svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);

		var idx = 0;
		rack.devices.forEach(function (d) {
			if (!d.uplink) { return; }
			var target = getDevice(rack, d.uplink);
			if (!target) { return; }
			var sy = centerY(rack, d);
			var ty = centerY(rack, target);
			var channel = w - 6 - (idx % 4) * 4;   // stagger channels so lines don't overlap
			var sx = w - 14;
			var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
			path.setAttribute('d', 'M ' + sx + ' ' + sy + ' H ' + channel + ' V ' + ty + ' H ' + sx + '');
			path.style.stroke = CABLE_COLOR[d.category] || 'var(--text-muted)';
			path.setAttribute('stroke-linecap', 'round');
			path.setAttribute('stroke-linejoin', 'round');
			svg.appendChild(path);

			[[sx, sy], [sx, ty]].forEach(function (pt) {
				var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
				dot.setAttribute('cx', pt[0]); dot.setAttribute('cy', pt[1]); dot.setAttribute('r', 2);
				dot.style.fill = CABLE_COLOR[d.category] || 'var(--text-muted)';
				svg.appendChild(dot);
			});
			idx += 1;
		});
	}

	// ── Drag & drop (container-level, pointer-snapped) ──
	function wireRackDnD(rack, bay, preview) {
		function targetBottomU(ev) {
			var rect = bay.getBoundingClientRect();
			var row = Math.floor((ev.clientY - rect.top) / U_PX);
			if (row < 0) { row = 0; }
			if (row > rack.height - 1) { row = rack.height - 1; }
			var topU = rack.height - row;
			return topU - drag.uh + 1;
		}
		bay.addEventListener('dragover', function (ev) {
			if (!drag) { return; }
			ev.preventDefault();
			var bottomU = targetBottomU(ev);
			var except = drag.rackId === rack.id ? drag.deviceId : null;
			var ok = fits(rack, bottomU, drag.uh, except);
			ev.dataTransfer.dropEffect = ok ? 'move' : 'none';
			var topRow = rack.height - (bottomU + drag.uh - 1);
			preview.style.display = 'block';
			preview.style.top = (topRow * U_PX) + 'px';
			preview.style.height = (drag.uh * U_PX) + 'px';
			preview.classList.toggle('ok', ok);
			preview.classList.toggle('bad', !ok);
		});
		bay.addEventListener('dragleave', function (ev) {
			if (ev.target === bay) { preview.style.display = 'none'; }
		});
		bay.addEventListener('drop', function (ev) {
			if (!drag) { return; }
			ev.preventDefault();
			preview.style.display = 'none';
			moveDevice(drag.rackId, drag.deviceId, rack.id, targetBottomU(ev));
		});
	}

	// ── Summary ──
	function renderSummary() {
		var devices = 0, units = 0, power = 0, cost = 0;
		state.racks.forEach(function (r) {
			r.devices.forEach(function (d) {
				devices += 1; units += Number(d.uh || 0);
				power += Number(d.power || 0); cost += Number(d.cost || 0);
			});
		});
		var sep = '<span class="infra-summary-sep">·</span>';
		summaryEl.innerHTML =
			'<span><strong>' + state.racks.length + '</strong> racks</span>' + sep +
			'<span><strong>' + devices + '</strong> devices</span>' + sep +
			'<span><strong>' + units + '</strong> U</span>' + sep +
			'<span><strong>' + power.toLocaleString('en-GB') + '</strong> W</span>' + sep +
			'<span class="infra-summary-cost">total <strong>' + money(cost) + '</strong></span>';
	}

	// ── Detail panel ──
	function renderDetail() {
		if (!selected) {
			detailEl.innerHTML = '<div class="infra-detail-empty">Select a device in the rack to view and edit its details.</div>';
			return;
		}
		var rack = getRack(selected.rackId);
		var d = rack && getDevice(rack, selected.deviceId);
		if (!d) { selected = null; renderDetail(); return; }

		var catOptions = CATEGORIES.map(function (c) {
			return '<option value="' + c.key + '"' + (c.key === d.category ? ' selected' : '') + '>' + esc(c.label) + '</option>';
		}).join('');
		var uplinkOptions = '<option value="">— None —</option>' + rack.devices
			.filter(function (x) { return x.id !== d.id; })
			.map(function (x) {
				return '<option value="' + x.id + '"' + (x.id === d.uplink ? ' selected' : '') + '>' + esc(x.name) + '</option>';
			}).join('');

		detailEl.innerHTML =
			'<div class="infra-field"><label>Name</label>' +
				'<input type="text" class="form-control" data-f="name" value="' + esc(d.name) + '"></div>' +
			'<div class="infra-field"><label>Model</label>' +
				'<input type="text" class="form-control" data-f="model" value="' + esc(d.model) + '"></div>' +
			'<div class="infra-field"><label>Category</label>' +
				'<select class="form-control" data-f="category">' + catOptions + '</select></div>' +
			'<div class="infra-field"><label>Height</label>' +
				'<input type="number" class="form-control" data-f="uh" min="1" max="' + rack.height + '" value="' + d.uh + '"></div>' +
			'<div class="infra-field"><label>Cost £</label>' +
				'<input type="number" class="form-control" data-f="cost" min="0" step="1" value="' + Number(d.cost || 0) + '"></div>' +
			'<div class="infra-field"><label>Power W</label>' +
				'<input type="number" class="form-control" data-f="power" min="0" step="1" value="' + Number(d.power || 0) + '"></div>' +
			'<div class="infra-field"><label>Ports</label>' +
				'<input type="text" class="form-control" data-f="ports" value="' + esc(d.ports) + '"></div>' +
			'<div class="infra-field"><label>Uplink</label>' +
				'<select class="form-control" data-f="uplink">' + uplinkOptions + '</select></div>' +
			'<div class="infra-field infra-field-top"><label>Notes</label>' +
				'<textarea class="form-control" data-f="notes" rows="2">' + esc(d.notes) + '</textarea></div>' +
			'<div class="infra-move-row">' +
				'<span class="infra-move-label">Position</span>' +
				'<span class="infra-move-pos">U' + d.u + '</span>' +
				'<button class="btn-icon" data-move="up" title="Move up"><i class="bi bi-arrow-up"></i></button>' +
				'<button class="btn-icon" data-move="down" title="Move down"><i class="bi bi-arrow-down"></i></button></div>' +
			'<div class="infra-detail-actions">' +
				'<button class="btn btn-danger" data-del><i class="bi bi-trash"></i> Remove</button>' +
			'</div>';

		Array.prototype.forEach.call(detailEl.querySelectorAll('[data-f]'), function (inp) {
			var evt = (inp.tagName === 'SELECT') ? 'change' : 'input';
			inp.addEventListener(evt, function () { updateField(d, rack, inp.dataset.f, inp.value); });
		});
		detailEl.querySelector('[data-del]').addEventListener('click', function () { deleteDevice(rack.id, d.id); });
		detailEl.querySelector('[data-move="up"]').addEventListener('click', function () { nudge(rack, d, 1); });
		detailEl.querySelector('[data-move="down"]').addEventListener('click', function () { nudge(rack, d, -1); });
	}

	function updateField(d, rack, field, value) {
		if (field === 'uh') {
			var uh = Math.max(1, parseInt(value, 10) || 1);
			if (fits(rack, d.u, uh, d.id)) { d.uh = uh; } else { return; }
			save(); renderRacks(); renderSummary();
			return;
		}
		if (field === 'cost' || field === 'power') {
			d[field] = Math.max(0, parseFloat(value) || 0);
			save(); renderRacks(); renderSummary();
			return;
		}
		if (field === 'uplink') { d.uplink = value || null; save(); renderRacks(); return; }
		d[field] = value;
		save();
		if (field === 'name' || field === 'model' || field === 'category') { renderRacks(); }
	}

	function nudge(rack, d, delta) {
		if (fits(rack, d.u + delta, d.uh, d.id)) { d.u += delta; save(); renderRacks(); renderDetail(); }
	}

	function selectDevice(rackId, deviceId) {
		selected = { rackId: rackId, deviceId: deviceId };
		renderRacks(); renderDetail();
	}

	function deleteDevice(rackId, deviceId) {
		var rack = getRack(rackId);
		if (!rack) { return; }
		rack.devices = rack.devices.filter(function (d) { return d.id !== deviceId; });
		rack.devices.forEach(function (d) { if (d.uplink === deviceId) { d.uplink = null; } });
		if (selected && selected.deviceId === deviceId) { selected = null; }
		save(); renderAll();
	}

	function moveDevice(fromRackId, deviceId, toRackId, u) {
		var from = getRack(fromRackId), to = getRack(toRackId);
		if (!from || !to) { return; }
		var d = getDevice(from, deviceId);
		if (!d) { return; }
		if (!fits(to, u, d.uh, fromRackId === toRackId ? deviceId : null)) { return; }
		if (from !== to) {
			from.devices = from.devices.filter(function (x) { return x.id !== deviceId; });
			d.uplink = null; // uplink references don't cross racks
			to.devices.push(d);
		}
		d.u = u;
		selected = { rackId: toRackId, deviceId: deviceId };
		save(); renderAll();
	}

	// ── Catalog ──
	function renderCatalog() {
		catalogEl.innerHTML = '';
		CATALOG.forEach(function (c) {
			var el = document.createElement('div');
			el.className = 'infra-catalog-item';
			el.innerHTML =
				'<div class="infra-catalog-copy">' +
					'<div class="infra-catalog-name">' + esc(c.name) + '</div>' +
					'<div class="infra-catalog-spec">' + esc(c.model || 'Custom') + ' · ' + c.uh + 'U</div>' +
				'</div>' +
				'<div class="infra-catalog-cost">' + (c.custom ? '+' : money(c.cost)) + '</div>';
			el.addEventListener('click', function () { addFromCatalog(c); });
			catalogEl.appendChild(el);
		});
	}

	function addFromCatalog(c) {
		var here = racksIn(activeLocation);
		var rack = (selected && getRack(selected.rackId) && getRack(selected.rackId).location === activeLocation)
			? getRack(selected.rackId) : here[0];
		if (!rack) { openRackModal(null); return; }
		var u = lowestFree(rack, c.uh);
		if (u == null) {
			alert('No free space for a ' + c.uh + 'U device in "' + rack.name + '". Free up units or add another rack.');
			return;
		}
		var d = {
			id: nextId('dev'), catalog: c.key,
			name: c.custom ? 'New Equipment' : c.name, model: c.model,
			category: c.category, uh: c.uh, cost: c.cost, power: c.power,
			ports: c.ports, notes: '', u: u, uplink: null
		};
		rack.devices.push(d);
		selected = { rackId: rack.id, deviceId: d.id };
		save(); renderAll();
	}

	// ── Rack modal ──
	var modal = document.getElementById('infraRackModal');
	var fName = document.getElementById('infraRackName');
	var fLoc = document.getElementById('infraRackLocation');
	var fHeight = document.getElementById('infraRackHeight');

	fLoc.innerHTML = LOCATIONS.map(function (l) { return '<option value="' + esc(l) + '">' + esc(l) + '</option>'; }).join('');

	function openRackModal(rackId) {
		editingRackId = rackId;
		var rack = rackId ? getRack(rackId) : null;
		document.getElementById('infraRackModalTitle').textContent = rack ? 'Edit Rack' : 'Add Rack';
		fName.value = rack ? rack.name : '';
		fLoc.value = rack ? rack.location : activeLocation;
		fHeight.value = rack ? rack.height : 12;
		modal.classList.add('open');
		fName.focus();
	}
	function closeRackModal() { modal.classList.remove('open'); editingRackId = null; }

	function saveRack() {
		var name = fName.value.trim() || 'Untitled Rack';
		var loc = fLoc.value;
		var height = Math.min(48, Math.max(1, parseInt(fHeight.value, 10) || 12));
		if (editingRackId) {
			var rack = getRack(editingRackId);
			if (rack) {
				var maxU = rack.devices.reduce(function (m, d) { return Math.max(m, d.u + d.uh - 1); }, 0);
				rack.name = name; rack.location = loc; rack.height = Math.max(height, maxU);
			}
		} else {
			state.racks.push({ id: nextId('rack'), name: name, location: loc, height: height, devices: [] });
		}
		activeLocation = loc;
		save(); closeRackModal(); renderAll();
	}

	function deleteRack(rackId) {
		var rack = getRack(rackId);
		if (!rack) { return; }
		if (!confirm('Delete rack "' + rack.name + '" and all ' + rack.devices.length + ' devices in it?')) { return; }
		state.racks = state.racks.filter(function (r) { return r.id !== rackId; });
		if (selected && selected.rackId === rackId) { selected = null; }
		save(); renderAll();
	}

	// ── Wire up ──
	document.getElementById('infraAddRackBtn').addEventListener('click', function () { openRackModal(null); });
	document.getElementById('infraAddRackHereBtn').addEventListener('click', function () { openRackModal(null); });
	document.getElementById('infraCablingToggle').addEventListener('change', function () {
		showCabling = this.checked; renderRacks();
	});
	document.getElementById('infraRackSave').addEventListener('click', saveRack);
	Array.prototype.forEach.call(modal.querySelectorAll('[data-infra-close]'), function (b) {
		b.addEventListener('click', closeRackModal);
	});
	modal.addEventListener('click', function (ev) { if (ev.target === modal) { closeRackModal(); } });
	fName.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { saveRack(); } });
	window.addEventListener('resize', function () { drawAllCables(); });

	load();
	renderCatalog();
	renderAll();
}());
