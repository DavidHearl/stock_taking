/*
 * Shared report rendering for standalone report pages.
 *
 * Renders the same data the dashboard report modals show, but into an on-page
 * container. Static files cannot use Django {% url %} tags, so every page passes
 * its endpoint URLs and context in via a config object.
 *
 * Each page sets `window.REPORT_CFG = {...}` before loading this file, then calls
 * the matching DashReports.init* function on DOMContentLoaded.
 */
(function () {
    'use strict';

    const fmt = n => '\u00a3' + Math.round(n).toLocaleString();
    const fmtDec = n => '\u00a3' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    function setLoading(bodyEl) {
        bodyEl.innerHTML = '<p style="padding:12px;color:var(--text-secondary)">Loading&hellip;</p>';
    }
    function setError(bodyEl) {
        bodyEl.innerHTML = '<p style="padding:12px;color:var(--danger-color)">Failed to load report.</p>';
    }
    function setEmpty(bodyEl, msg) {
        bodyEl.innerHTML = '<p style="padding:12px;color:var(--text-secondary)">' + msg + '</p>';
    }

    /* ============================ Fit-sales reports (Week / Monthly) ============================ */
    function renderFitRows(data, noun) {
        let html = '<table class="dash-report-table"><thead><tr>';
        html += '<th>Customer</th><th>Sale #</th><th>Order Date</th><th>Fit Date</th><th>Designer</th><th class="text-right">Sale Value</th>';
        html += '</tr></thead><tbody>';
        data.rows.forEach(r => {
            html += '<tr>' +
                '<td><a href="/sale/' + r.pk + '/" target="_blank" class="dash-report-link">' + r.customer + '</a></td>' +
                '<td>' + (r.sale_number || '-') + '</td>' +
                '<td>' + (r.order_date || '-') + '</td>' +
                '<td>' + (r.fit_date || '-') + '</td>' +
                '<td>' + r.designer + '</td>' +
                '<td class="text-right">' + fmt(r.sale_value) + '</td>' +
                '</tr>';
        });
        const grandTotal = data.rows.reduce((s, r) => s + r.sale_value, 0);
        html += '<tr class="dash-report-year-sep">' +
            '<td colspan="5"><strong>' + data.count + ' ' + noun + (data.count !== 1 ? 's' : '') + '</strong>' +
            '<span class="dash-report-year-total">' + fmt(grandTotal) + ' total</span></td>' +
            '<td class="text-right" style="font-weight:600;">' + fmt(grandTotal) + '</td>' +
            '</tr>';
        html += '</tbody></table>';
        return html;
    }

    function initWeek(cfg) {
        const body = document.getElementById(cfg.bodyId);
        const meta = document.getElementById(cfg.metaId);
        setLoading(body);
        fetch(cfg.reportUrl)
            .then(r => r.json())
            .then(data => {
                if (!data.success || !data.rows.length) {
                    if (meta) meta.textContent = '';
                    setEmpty(body, 'No fits scheduled this week.');
                    return;
                }
                if (meta) {
                    meta.innerHTML = data.count + ' fit' + (data.count !== 1 ? 's' : '') +
                        ' &nbsp;|&nbsp; ' + fmt(data.total) +
                        ' &nbsp;|&nbsp; ' + data.week_start + ' \u2013 ' + data.week_end;
                }
                body.innerHTML = renderFitRows(data, 'fit');
            })
            .catch(() => setError(body));
    }

    function initMonthly(cfg) {
        const body = document.getElementById(cfg.bodyId);
        const meta = document.getElementById(cfg.metaId);
        const titleEl = cfg.titleId ? document.getElementById(cfg.titleId) : null;

        function load() {
            setLoading(body);
            const params = '?year=' + cfg.getYear() + '&month=' + cfg.getMonth();
            if (cfg.pdfBtnId && cfg.pdfUrlBase) {
                document.getElementById(cfg.pdfBtnId).href = cfg.pdfUrlBase + params;
            }
            fetch(cfg.reportUrlBase + params)
                .then(r => r.json())
                .then(data => {
                    if (!data.success) { setError(body); return; }
                    if (titleEl) titleEl.textContent = 'Monthly Fits \u2014 ' + data.month_label;
                    if (!data.rows.length) {
                        if (meta) meta.textContent = '';
                        setEmpty(body, 'No fits found for ' + data.month_label + '.');
                        return;
                    }
                    if (meta) {
                        meta.innerHTML = data.count + ' fit' + (data.count !== 1 ? 's' : '') + ' &nbsp;|&nbsp; ' + fmt(data.total);
                    }
                    body.innerHTML = renderFitRows(data, 'fit');
                })
                .catch(() => setError(body));
        }
        cfg.onChange = load;
        load();
    }

    /* ============================ Sales After Date ============================ */
    function initSalesAfter(cfg) {
        const body = document.getElementById(cfg.bodyId);
        const meta = document.getElementById(cfg.metaId);

        function load() {
            setLoading(body);
            const date = cfg.getDate();
            const dateParam = '?date=' + encodeURIComponent(date);
            if (cfg.pdfBtnId && cfg.pdfUrlBase) {
                document.getElementById(cfg.pdfBtnId).href = cfg.pdfUrlBase + dateParam;
            }
            fetch(cfg.reportUrlBase + dateParam)
                .then(r => r.json())
                .then(data => {
                    if (!data.success || !data.rows.length) {
                        if (meta) meta.textContent = '';
                        setEmpty(body, 'No orders found from this date.');
                        return;
                    }
                    if (meta) {
                        meta.innerHTML = data.count + ' order' + (data.count !== 1 ? 's' : '') +
                            ' &nbsp;|&nbsp; ' + fmt(data.total) + ' &nbsp;|&nbsp; from ' + data.cutoff;
                    }
                    let html = '<table class="dash-report-table"><thead><tr>';
                    html += '<th>Customer</th><th>Sale #</th><th>Order Date</th><th>Fit Date</th><th>Designer</th><th class="text-right">Sale Value</th><th class="text-right">Paid</th><th class="text-right">Remaining</th>';
                    html += '</tr></thead><tbody>';
                    data.rows.forEach(r => {
                        const saleLink = r.sale_number
                            ? '<a href="/sale/' + r.pk + '/" target="_blank" class="dash-report-link">' + r.sale_number + '</a>'
                            : '-';
                        const remainingStyle = r.remaining > 0 ? ' style="color:var(--danger-color);font-weight:600;"' : '';
                        html += '<tr>' +
                            '<td><a href="/sale/' + r.pk + '/" target="_blank" class="dash-report-link">' + r.customer + '</a></td>' +
                            '<td>' + saleLink + '</td>' +
                            '<td>' + (r.order_date || '-') + '</td>' +
                            '<td>' + (r.fit_date || '-') + '</td>' +
                            '<td>' + r.designer + '</td>' +
                            '<td class="text-right">' + fmt(r.sale_value) + '</td>' +
                            '<td class="text-right">' + fmt(r.paid) + '</td>' +
                            '<td class="text-right"' + remainingStyle + '>' + fmt(r.remaining) + '</td>' +
                            '</tr>';
                    });
                    const grandTotal = data.rows.reduce((s, r) => s + r.sale_value, 0);
                    const grandPaid = data.rows.reduce((s, r) => s + r.paid, 0);
                    const grandRemaining = data.rows.reduce((s, r) => s + r.remaining, 0);
                    html += '<tr class="dash-report-year-sep">' +
                        '<td colspan="5"><strong>' + data.count + ' order' + (data.count !== 1 ? 's' : '') + '</strong>' +
                        '<span class="dash-report-year-total">' + fmt(grandTotal) + ' total</span></td>' +
                        '<td class="text-right" style="font-weight:600;">' + fmt(grandTotal) + '</td>' +
                        '<td class="text-right" style="font-weight:600;">' + fmt(grandPaid) + '</td>' +
                        '<td class="text-right" style="font-weight:600;color:var(--danger-color);">' + fmt(grandRemaining) + '</td>' +
                        '</tr>';
                    html += '</tbody></table>';
                    body.innerHTML = html;
                })
                .catch(() => setError(body));
        }
        cfg.onChange = load;
        load();
    }

    /* ============================ Average Sale Value ============================ */
    function initAvg(cfg) {
        const body = document.getElementById(cfg.bodyId);
        const meta = document.getElementById(cfg.metaId);
        setLoading(body);
        fetch(cfg.reportUrl)
            .then(r => r.json())
            .then(data => {
                if (!data.success || !data.rows.length) {
                    if (meta) meta.textContent = '';
                    setEmpty(body, 'No data available.');
                    return;
                }
                if (meta) {
                    meta.innerHTML = data.period +
                        ' &nbsp;|&nbsp; ' + data.grand_count.toLocaleString() + ' fits' +
                        ' &nbsp;|&nbsp; ' + fmt(data.grand_total) + ' total';
                }
                let html = '<table class="dash-report-table"><thead><tr>';
                html += '<th>Month</th><th class="text-center">Fits</th><th class="text-right">Total Value</th><th class="text-right">Avg Sale Value</th>';
                html += '</tr></thead><tbody>';
                data.rows.forEach(r => {
                    html += '<tr>' +
                        '<td>' + r.month + '</td>' +
                        '<td class="text-center">' + r.count.toLocaleString() + '</td>' +
                        '<td class="text-right">' + fmt(r.total) + '</td>' +
                        '<td class="text-right">' + fmt(r.avg) + '</td>' +
                        '</tr>';
                });
                const avgPerSale = data.grand_count ? Math.round(data.grand_total / data.grand_count) : 0;
                html += '<tr class="dash-report-year-sep">' +
                    '<td><strong>Total / Avg</strong></td>' +
                    '<td class="text-center" style="font-weight:600;">' + data.grand_count.toLocaleString() + '</td>' +
                    '<td class="text-right" style="font-weight:600;">' + fmt(data.grand_total) + '</td>' +
                    '<td class="text-right" style="font-weight:600;">' + fmt(avgPerSale) + '</td>' +
                    '</tr>';
                html += '</tbody></table>';
                html += '<div style="margin-top:16px;padding:12px 16px;background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;display:flex;gap:32px;flex-wrap:wrap;">' +
                    '<div><div style="font-size:0.75rem;color:var(--text-secondary);">Daily Avg Revenue (total \u00f7 365)</div>' +
                    '<div style="font-size:1.25rem;font-weight:700;">' + fmt(data.daily_avg) + '</div></div>' +
                    '<div><div style="font-size:0.75rem;color:var(--text-secondary);">Avg Sale Value (total \u00f7 fits)</div>' +
                    '<div style="font-size:1.25rem;font-weight:700;">' + fmt(avgPerSale) + '</div></div>' +
                    '<div><div style="font-size:0.75rem;color:var(--text-secondary);">Period</div>' +
                    '<div style="font-size:0.85rem;font-weight:500;">' + data.period + '</div></div>' +
                    '</div>';
                body.innerHTML = html;
            })
            .catch(() => setError(body));
    }

    /* ============================ Stock Report (tabs) ============================ */
    let _stockHistoryLoaded = false;
    let _stockLoadedDate = null;

    function initStock(cfg) {
        DashReports._stockCfg = cfg;
        cfg.onChange = function () { _stockLoadedDate = null; _stockHistoryLoaded = false; loadStock(cfg); };
        loadStock(cfg);
    }

    function switchStockTab(tab) {
        const cfg = DashReports._stockCfg;
        const reportBody = document.getElementById(cfg.reportBodyId);
        const changesBody = document.getElementById(cfg.changesBodyId);
        const historyBody = document.getElementById(cfg.historyBodyId);
        const tabs = {
            report: document.getElementById(cfg.tabReportId),
            changes: document.getElementById(cfg.tabChangesId),
            history: document.getElementById(cfg.tabHistoryId),
        };
        const pdfBtn = document.getElementById(cfg.pdfBtnId);
        const meta = document.getElementById(cfg.metaId);

        [reportBody, changesBody, historyBody].forEach(b => b.style.display = 'none');
        Object.values(tabs).forEach(b => b.classList.remove('active'));
        if (pdfBtn) pdfBtn.style.display = 'none';

        if (tab === 'report') {
            reportBody.style.display = '';
            tabs.report.classList.add('active');
            if (pdfBtn) pdfBtn.style.display = '';
        } else if (tab === 'changes') {
            changesBody.style.display = '';
            tabs.changes.classList.add('active');
        } else {
            historyBody.style.display = '';
            tabs.history.classList.add('active');
            if (!_stockHistoryLoaded) loadStockHistory(cfg);
        }
    }

    function loadStock(cfg) {
        const stockDate = cfg.getDate();
        if (cfg.pdfBtnId && cfg.pdfUrlBase) {
            document.getElementById(cfg.pdfBtnId).href = cfg.pdfUrlBase + '?date=' + stockDate;
        }
        if (_stockLoadedDate === stockDate) return;
        _stockLoadedDate = stockDate;

        const reportBody = document.getElementById(cfg.reportBodyId);
        const changesBody = document.getElementById(cfg.changesBodyId);
        const meta = document.getElementById(cfg.metaId);
        setLoading(reportBody);
        setLoading(changesBody);
        if (meta) meta.textContent = '';

        fetch(cfg.reportUrlBase + '?date=' + stockDate)
            .then(r => r.json())
            .then(data => {
                if (!data.success) {
                    reportBody.innerHTML = '<p style="padding:12px;color:var(--danger-color)">Failed to load stock data.</p>';
                    changesBody.innerHTML = '';
                    return;
                }
                const dateLabel = data.as_of_date || 'Today';
                if (meta) {
                    meta.innerHTML = dateLabel + ' &nbsp;|&nbsp; ' + data.stock_count.toLocaleString() + ' items &nbsp;|&nbsp; ' + fmt(data.total_value);
                }

                // Changes tab
                let changesHtml = '';
                if (data.recent_changes.length) {
                    changesHtml += '<table class="dash-report-table"><thead><tr>' +
                        '<th>SKU</th><th>Material</th><th class="text-center">Type</th>' +
                        '<th class="text-right">Qty Change</th><th class="text-right">Value Change</th><th class="text-right">Date</th>' +
                        '</tr></thead><tbody>';
                    data.recent_changes.forEach(c => {
                        const qtyColor = c.change_amount > 0 ? 'var(--success-color)' : 'var(--danger-color)';
                        const valColor = c.value_change > 0 ? 'var(--success-color)' : 'var(--danger-color)';
                        const qtySign = c.change_amount > 0 ? '+' : '';
                        const valSign = c.value_change > 0 ? '+' : '';
                        changesHtml += '<tr>' +
                            '<td style="font-weight:600;">' + c.sku + '</td>' +
                            '<td>' + c.name + '</td>' +
                            '<td class="text-center"><span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.7rem;font-weight:600;background:var(--bg-tertiary);border:1px solid var(--border-color);">' + c.change_type + '</span></td>' +
                            '<td class="text-right" style="font-weight:600;color:' + qtyColor + ';">' + qtySign + c.change_amount.toLocaleString() + '</td>' +
                            '<td class="text-right" style="color:' + valColor + ';">' + valSign + fmt(Math.abs(c.value_change)) + '</td>' +
                            '<td class="text-right" style="color:var(--text-secondary);white-space:nowrap;">' + c.date + '</td>' +
                            '</tr>';
                    });
                    changesHtml += '</tbody></table>';
                } else {
                    changesHtml = '<p style="padding:16px;color:var(--text-secondary);">No stock changes in the past 5 days.</p>';
                }
                changesBody.innerHTML = changesHtml;

                // Report tab
                const grandQty = data.current_stock.reduce((s, i) => s + i.quantity, 0);
                let reportHtml = '<table class="dash-report-table"><thead><tr>' +
                    '<th>SKU</th><th>Material</th><th>Category</th><th>Location</th>' +
                    '<th class="text-right">Qty</th><th class="text-right">Unit Cost</th><th class="text-right">Total Value</th>' +
                    '</tr></thead><tbody>';
                data.current_stock.forEach(i => {
                    reportHtml += '<tr>' +
                        '<td style="font-weight:600;">' + i.sku + '</td>' +
                        '<td>' + i.name + '</td>' +
                        '<td style="color:var(--text-secondary);">' + (i.category || '&mdash;') + '</td>' +
                        '<td style="color:var(--text-secondary);">' + (i.location || '&mdash;') + '</td>' +
                        '<td class="text-right">' + i.quantity.toLocaleString() + '</td>' +
                        '<td class="text-right" style="color:var(--text-secondary);">\u00a3' + i.unit_cost.toFixed(2) + '</td>' +
                        '<td class="text-right" style="font-weight:600;">' + fmt(i.total_value) + '</td>' +
                        '</tr>';
                });
                reportHtml += '<tr style="background:var(--bg-tertiary);font-weight:700;border-top:2px solid var(--border-color);">' +
                    '<td colspan="4">' + data.stock_count.toLocaleString() + ' items &nbsp;&bull;&nbsp; ' + grandQty.toLocaleString() + ' units total</td>' +
                    '<td class="text-right">' + grandQty.toLocaleString() + '</td>' +
                    '<td></td>' +
                    '<td class="text-right">' + fmt(data.total_value) + '</td>' +
                    '</tr>';
                reportHtml += '</tbody></table>';
                reportBody.innerHTML = reportHtml;
            })
            .catch(() => setError(reportBody));
    }

    function loadStockHistory(cfg) {
        const body = document.getElementById(cfg.historyBodyId);
        setLoading(body);
        fetch(cfg.historyUrl)
            .then(r => r.json())
            .then(data => {
                if (!data.success) {
                    body.innerHTML = '<p style="padding:12px;color:var(--danger-color)">Failed to load data.</p>';
                    return;
                }
                _stockHistoryLoaded = true;
                const months = data.months;
                const canvasId = 'monthlyStockChart';
                let html = '<canvas id="' + canvasId + '" style="width:100%;max-height:260px;margin-bottom:16px;"></canvas>';
                html += '<div style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden;">';
                html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;padding:6px 16px;background:var(--bg-card);border-bottom:2px solid var(--border-color);font-size:0.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text-secondary);">' +
                    '<span>Month</span><span style="text-align:right;">Stock Value</span><span style="text-align:right;">Change</span></div>';
                months.forEach((row, idx) => {
                    const prev = idx > 0 ? months[idx - 1].value : null;
                    let changeHtml = '<span style="color:var(--text-secondary);">&mdash;</span>';
                    if (prev !== null) {
                        const diff = row.value - prev;
                        const pct = prev > 0 ? ((diff / prev) * 100).toFixed(1) : '&mdash;';
                        const color = diff >= 0 ? 'var(--success-color)' : 'var(--danger-color)';
                        const sign = diff >= 0 ? '+' : '';
                        changeHtml = '<span style="color:' + color + ';">' + sign + fmt(diff) + ' (' + sign + pct + '%)</span>';
                    }
                    const border = idx > 0 ? 'border-top:1px solid var(--border-color);' : '';
                    html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;padding:6px 16px;' + border + '">' +
                        '<span style="font-weight:600;">' + row.label + '</span>' +
                        '<span style="text-align:right;">' + fmt(row.value) + '</span>' +
                        '<span style="text-align:right;">' + changeHtml + '</span>' +
                        '</div>';
                });
                html += '</div>';
                body.innerHTML = html;

                if (typeof Chart !== 'undefined') {
                    const labels = months.map(m => m.label);
                    const values = months.map(m => m.value);
                    const ctx = document.getElementById(canvasId).getContext('2d');
                    new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Stock Value',
                                data: values,
                                borderColor: 'var(--primary-color)',
                                backgroundColor: 'rgba(41,232,235,0.1)',
                                fill: true,
                                tension: 0.3,
                            }],
                        },
                        options: {
                            responsive: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                y: { ticks: { callback: v => '\u00a3' + Math.round(v).toLocaleString() } },
                            },
                        },
                    });
                }
            })
            .catch(() => setError(body));
    }

    /* ============================ Outstanding Payments ============================ */
    let _osCfg = null;
    let _osHidePayments = false;
    let _osPeriod = 'all';

    function initOutstanding(cfg) {
        _osCfg = cfg;
        DashReports._osCfg = cfg;
        loadOutstanding();
    }

    function setOutstandingPeriod(period, btn) {
        _osPeriod = period;
        document.querySelectorAll('.dash-report-period-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        loadOutstanding();
    }

    function toggleOutstandingHidePaid(btn) {
        _osHidePayments = !_osHidePayments;
        if (btn) {
            btn.classList.toggle('active', _osHidePayments);
            btn.innerHTML = '<i class="bi bi-funnel"></i> Hide Paid';
        }
        filterOutstandingRows();
    }

    function loadOutstanding() {
        const cfg = _osCfg;
        const body = document.getElementById(cfg.bodyId);
        const metaEl = document.getElementById(cfg.metaId);
        setLoading(body);
        if (metaEl) metaEl.textContent = '';

        let params = [];
        if (cfg.location) params.push('location=' + encodeURIComponent(cfg.location));
        if (_osPeriod !== 'all') params.push('period=' + _osPeriod);
        const queryStr = params.length ? '?' + params.join('&') : '';
        if (cfg.pdfBtnId && cfg.pdfUrlBase) {
            document.getElementById(cfg.pdfBtnId).href = cfg.pdfUrlBase + queryStr;
        }

        fetch(cfg.reportUrlBase + queryStr)
            .then(r => r.json())
            .then(data => {
                if (!data.success || !data.rows.length) {
                    if (metaEl) metaEl.textContent = '';
                    setEmpty(body, 'No outstanding balances found.');
                    return;
                }
                const totalOutstanding = data.rows.reduce((s, r) => s + r.total_outstanding, 0);
                const locLabel = cfg.location || 'All Locations';
                if (metaEl) {
                    metaEl.innerHTML = data.count + ' customer' + (data.count !== 1 ? 's' : '') + ' &nbsp;|&nbsp; ' + fmt(totalOutstanding) + ' &nbsp;|&nbsp; ' + locLabel;
                }

                const COL_COUNT = 6;
                let html = '<table class="dash-report-table"><thead><tr>';
                html += '<th style="width:24px"></th><th>Customer</th><th>Sales</th><th class="text-right">Sale Value</th><th class="text-right">Paid</th><th class="text-right">Outstanding</th>';
                html += '</tr></thead><tbody>';

                let custIdx = 0;
                data.rows.forEach(cust => {
                    const rid = 'os-cust-' + custIdx++;
                    const hasSales = cust.sales && cust.sales.length > 0;
                    const hasPayments = cust.sales.some(s => s.payments && s.payments.length > 0);
                    const chevron = hasSales
                        ? '<i class="bi bi-chevron-down os-row-chevron" id="icon-' + rid + '"></i>'
                        : '<span style="display:inline-block;width:14px;"></span>';
                    const custLink = cust.customer_id
                        ? '<a href="/customer/' + cust.customer_id + '/" target="_blank" class="dash-report-link">' + cust.customer + '</a>'
                        : cust.customer;
                    const manageLink = cust.customer_id
                        ? ' <a href="/customer/' + cust.customer_id + '/manage-payments/" target="_blank" class="os-manage-link" title="Manage payments"><i class="bi bi-sliders2"></i></a>'
                        : '';

                    html += '<tr class="os-main-row' + (hasSales ? ' os-expandable' : '') + '" data-has-payments="' + (hasPayments ? 1 : 0) + '" data-outstanding="' + cust.total_outstanding + '" data-rid="' + rid + '" onclick="DashReports.toggleOutstandingDetail(\'' + rid + '\', event)">';
                    html += '<td class="text-center" style="padding-left:8px;padding-right:0;">' + chevron + '</td>';
                    html += '<td>' + custLink + manageLink + '</td>';
                    html += '<td>' + cust.sales.length + ' sale' + (cust.sales.length !== 1 ? 's' : '') + '</td>';
                    html += '<td class="text-right">' + fmt(cust.total_sale_value) + '</td>';
                    html += '<td class="text-right">' + fmt(cust.total_paid) + '</td>';
                    html += '<td class="text-right" style="color:var(--danger-color);font-weight:600">' + fmt(cust.total_outstanding) + '</td>';
                    html += '</tr>';

                    html += '<tr class="os-detail-row" id="' + rid + '" style="display:none;"><td colspan="' + COL_COUNT + '">';
                    html += '<div class="os-detail-content">';
                    if (hasSales) {
                        html += '<table class="os-sales-table"><thead><tr>';
                        html += '<th>Sale #</th><th>Contract</th><th>Fit Date</th><th class="text-right">Sale Value</th><th class="text-right">Paid</th><th class="text-right">Outstanding</th><th class="text-center" style="width:36px"></th>';
                        html += '</tr></thead><tbody>';
                        cust.sales.forEach(s => {
                            html += '<tr>';
                            html += '<td>' + (s.sale_number ? '<a href="/sale/' + s.pk + '/" target="_blank" class="dash-report-link">' + s.sale_number + '</a>' : '-') + '</td>';
                            html += '<td>' + (s.contract || '-') + '</td>';
                            html += '<td>' + (s.fit_date || '<span style="color:var(--danger-color);font-style:italic;">No fit date</span>') + '</td>';
                            html += '<td class="text-right">' + fmt(s.sale_value) + '</td>';
                            html += '<td class="text-right">' + fmt(s.paid) + '</td>';
                            html += '<td class="text-right" style="color:var(--danger-color);font-weight:600">' + fmt(s.outstanding) + '</td>';
                            html += '<td class="text-center"><button class="os-xero-btn" onclick="DashReports.checkXeroSingle(' + s.pk + ', this, event)" title="Check Xero for payments"><i class="bi bi-arrow-repeat"></i></button></td>';
                            html += '</tr>';
                            if (s.payments && s.payments.length > 0) {
                                html += '<tr class="os-payment-sub"><td colspan="7"><table class="os-payments-table"><thead><tr><th>Date</th><th>Type</th><th>Source</th><th>Invoice</th><th>Status</th><th class="text-right">Amount</th></tr></thead><tbody>';
                                s.payments.forEach(p => {
                                    html += '<tr><td>' + (p.date || '-') + '</td><td>' + p.type + '</td><td>' + p.source + '</td><td>' + (p.invoice || '-') + '</td><td>' + (p.invoice_status || '-') + '</td><td class="text-right">' + fmtDec(p.amount) + '</td></tr>';
                                });
                                html += '</tbody></table></td></tr>';
                            }
                        });
                        html += '</tbody></table>';
                    }
                    html += '</div></td></tr>';
                });
                html += '</tbody></table>';

                if (data.overpaid_rows && data.overpaid_rows.length > 0) {
                    const totalOverpaid = data.overpaid_rows.reduce((s, r) => s + r.total_overpaid, 0);
                    html += '<div style="margin-top:18px;padding:12px 16px;background:rgba(255,165,0,0.08);border:1px solid rgba(255,165,0,0.3);border-radius:8px;">';
                    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;"><i class="bi bi-exclamation-triangle-fill" style="color:var(--warning-color);font-size:1rem;"></i><strong style="color:var(--warning-color);">Overpaid Customers</strong><span style="font-size:0.78rem;color:var(--text-secondary);">' + data.overpaid_rows.length + ' customer' + (data.overpaid_rows.length !== 1 ? 's' : '') + ' &mdash; ' + fmt(totalOverpaid) + ' overpaid</span></div>';
                    html += '<table class="dash-report-table" style="margin:0;"><thead><tr>';
                    html += '<th style="width:24px"></th><th>Customer</th><th>Sales</th><th class="text-right">Sale Value</th><th class="text-right">Paid</th><th class="text-right">Overpaid</th>';
                    html += '</tr></thead><tbody>';
                    let opIdx = 0;
                    data.overpaid_rows.forEach(cust => {
                        const oprid = 'op-cust-' + opIdx++;
                        const hasSales = cust.sales && cust.sales.length > 0;
                        const chevron = hasSales
                            ? '<i class="bi bi-chevron-down os-row-chevron" id="icon-' + oprid + '"></i>'
                            : '<span style="display:inline-block;width:14px;"></span>';
                        const custLink = cust.customer_id
                            ? '<a href="/customer/' + cust.customer_id + '/" target="_blank" class="dash-report-link">' + cust.customer + '</a>'
                            : cust.customer;
                        html += '<tr class="os-main-row' + (hasSales ? ' os-expandable' : '') + '" data-rid="' + oprid + '" onclick="DashReports.toggleOutstandingDetail(\'' + oprid + '\', event)">';
                        html += '<td class="text-center" style="padding-left:8px;padding-right:0;">' + chevron + '</td>';
                        html += '<td>' + custLink + '</td>';
                        html += '<td>' + cust.sales.length + ' sale' + (cust.sales.length !== 1 ? 's' : '') + '</td>';
                        html += '<td class="text-right">' + fmt(cust.total_sale_value) + '</td>';
                        html += '<td class="text-right">' + fmt(cust.total_paid) + '</td>';
                        html += '<td class="text-right" style="color:var(--warning-color);font-weight:600">+' + fmt(cust.total_overpaid) + '</td>';
                        html += '</tr>';
                        html += '<tr class="os-detail-row" id="' + oprid + '" style="display:none;"><td colspan="6">';
                        html += '<div class="os-detail-content">';
                        if (hasSales) {
                            html += '<table class="os-sales-table"><thead><tr><th>Sale #</th><th>Contract</th><th>Fit Date</th><th class="text-right">Sale Value</th><th class="text-right">Paid</th><th class="text-right">Overpaid</th></tr></thead><tbody>';
                            cust.sales.forEach(s => {
                                html += '<tr>';
                                html += '<td>' + (s.sale_number ? '<a href="/sale/' + s.pk + '/" target="_blank" class="dash-report-link">' + s.sale_number + '</a>' : '-') + '</td>';
                                html += '<td>' + (s.contract || '-') + '</td>';
                                html += '<td>' + (s.fit_date || '-') + '</td>';
                                html += '<td class="text-right">' + fmt(s.sale_value) + '</td>';
                                html += '<td class="text-right">' + fmt(s.paid) + '</td>';
                                html += '<td class="text-right" style="color:var(--warning-color);font-weight:600">+' + fmt(s.overpaid) + '</td>';
                                html += '</tr>';
                                if (s.payments && s.payments.length > 0) {
                                    html += '<tr class="os-payment-sub"><td colspan="6"><table class="os-payments-table"><thead><tr><th>Date</th><th>Type</th><th>Source</th><th>Invoice</th><th>Status</th><th class="text-right">Amount</th></tr></thead><tbody>';
                                    s.payments.forEach(p => {
                                        html += '<tr><td>' + (p.date || '-') + '</td><td>' + p.type + '</td><td>' + p.source + '</td><td>' + (p.invoice || '-') + '</td><td>' + (p.invoice_status || '-') + '</td><td class="text-right">' + fmtDec(p.amount) + '</td></tr>';
                                    });
                                    html += '</tbody></table></td></tr>';
                                }
                            });
                            html += '</tbody></table>';
                        }
                        html += '</div></td></tr>';
                    });
                    html += '</tbody></table></div>';
                }

                body.innerHTML = html;
                filterOutstandingRows();
            })
            .catch(() => setError(body));
    }

    function toggleOutstandingDetail(rid, event) {
        if (event && event.target && (event.target.closest('a') || event.target.closest('button'))) return;
        const detailRow = document.getElementById(rid);
        const icon = document.getElementById('icon-' + rid);
        if (!detailRow || !icon) return;
        const isOpen = detailRow.style.display !== 'none';
        detailRow.style.display = isOpen ? 'none' : 'table-row';
        const mainRow = document.querySelector('tr[data-rid="' + rid + '"]');
        if (mainRow) mainRow.classList.toggle('os-open', !isOpen);
    }

    function checkXeroSingle(salePk, btnEl, event) {
        if (event) { event.stopPropagation(); event.preventDefault(); }
        const cfg = _osCfg;
        const originalHtml = btnEl.innerHTML;
        btnEl.disabled = true;
        btnEl.innerHTML = '<i class="bi bi-hourglass-split"></i>';
        const url = cfg.xeroSingleUrl + '?sale_pk=' + salePk;
        fetch(url)
            .then(r => r.json())
            .then(data => {
                btnEl.disabled = false;
                if (!data.success) {
                    btnEl.innerHTML = '<i class="bi bi-x-circle" style="color:var(--danger-color);"></i>';
                    btnEl.title = data.error || 'Error';
                    setTimeout(() => { btnEl.innerHTML = originalHtml; btnEl.title = 'Check Xero for payments'; }, 3000);
                    return;
                }
                if (data.found && (data.payments_created > 0 || data.payments_updated > 0)) {
                    btnEl.innerHTML = '<i class="bi bi-check-circle" style="color:var(--success-color);"></i>';
                    btnEl.title = data.message;
                    setTimeout(() => loadOutstanding(), 1500);
                } else {
                    btnEl.innerHTML = '<i class="bi bi-dash-circle" style="color:var(--text-secondary);"></i>';
                    btnEl.title = data.message || 'No invoices found';
                    setTimeout(() => { btnEl.innerHTML = originalHtml; btnEl.title = 'Check Xero for payments'; }, 3000);
                }
            })
            .catch(() => {
                btnEl.disabled = false;
                btnEl.innerHTML = '<i class="bi bi-x-circle" style="color:var(--danger-color);"></i>';
                setTimeout(() => { btnEl.innerHTML = originalHtml; }, 3000);
            });
    }

    function _recalcOutstandingMeta() {
        const cfg = _osCfg;
        let totalVisible = 0, totalOutstandingVisible = 0;
        document.querySelectorAll('#' + cfg.bodyId + ' tr.os-main-row[data-outstanding]').forEach(tr => {
            if (tr.style.display !== 'none') {
                totalVisible++;
                totalOutstandingVisible += parseFloat(tr.dataset.outstanding || 0);
            }
        });
        const meta = document.getElementById(cfg.metaId);
        if (meta) {
            const locLabel = cfg.location || 'All Locations';
            meta.innerHTML = totalVisible + ' customer' + (totalVisible !== 1 ? 's' : '') + ' &nbsp;|&nbsp; ' + fmt(totalOutstandingVisible) + ' &nbsp;|&nbsp; ' + locLabel;
        }
    }

    function filterOutstandingRows() {
        const cfg = _osCfg;
        const searchEl = document.getElementById(cfg.searchId);
        const q = (searchEl ? searchEl.value : '').toLowerCase().trim();
        document.querySelectorAll('#' + cfg.bodyId + ' tr.os-main-row[data-outstanding]').forEach(tr => {
            const cells = tr.querySelectorAll('td');
            const name = cells[1] ? cells[1].textContent.toLowerCase() : '';
            const matchesSearch = !q || name.includes(q);
            const matchesPaid = !_osHidePayments || tr.dataset.hasPayments !== '1';
            const show = matchesSearch && matchesPaid;
            tr.style.display = show ? '' : 'none';
            const rid = tr.dataset.rid;
            if (rid) {
                const detailRow = document.getElementById(rid);
                if (detailRow && !show) detailRow.style.display = 'none';
            }
        });
        _recalcOutstandingMeta();
    }

    function checkXeroPayments(btn) {
        const cfg = _osCfg;
        const logPanel = document.getElementById(cfg.xeroLogId);
        const originalHtml = btn.innerHTML;
        let abortController = new AbortController();
        let hadUpdates = false;

        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';
        btn.classList.add('active');
        btn.onclick = function () {
            abortController.abort();
            appendLog('Cancelled by user.', 'warn');
            resetBtn();
        };

        logPanel.style.display = 'block';
        logPanel.innerHTML = '';

        function appendLog(text, level) {
            const line = document.createElement('div');
            if (level === 'found') { line.style.color = 'var(--success-color)'; line.style.fontWeight = '600'; }
            else if (level === 'error') { line.style.color = 'var(--danger-color)'; }
            else if (level === 'warn') { line.style.color = 'var(--warning-color)'; }
            else if (level === 'skip') { line.style.color = 'var(--text-secondary, var(--text-secondary))'; }
            else { line.style.color = 'var(--text-primary, var(--text-primary))'; }
            line.textContent = text;
            logPanel.appendChild(line);
            logPanel.scrollTop = logPanel.scrollHeight;
        }

        function resetBtn() {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
            btn.classList.remove('active');
            btn.onclick = function () { checkXeroPayments(btn); };
            if (hadUpdates) loadOutstanding();
        }

        let params = [];
        if (cfg.location) params.push('location=' + encodeURIComponent(cfg.location));
        if (_osPeriod !== 'all') params.push('period=' + _osPeriod);
        const queryStr = params.length ? '?' + params.join('&') : '';

        function processLine(line) {
            if (!line.trim()) return;
            let msg;
            try { msg = JSON.parse(line); } catch (e) { return; }

            if (msg.type === 'start') {
                appendLog('Starting Xero check for ' + msg.total + ' outstanding sale' + (msg.total !== 1 ? 's' : '') + '...', 'info');
            } else if (msg.type === 'checking') {
                appendLog('[' + msg.index + '/' + msg.total + '] Checking ' + msg.customer + ' (' + msg.contract + ')...', 'info');
            } else if (msg.type === 'result') {
                if (msg.status === 'found') {
                    appendLog('[' + msg.index + '/' + msg.total + '] \u2713 ' + msg.customer + ': ' + msg.message, 'found');
                    hadUpdates = true;
                } else if (msg.status === 'error') {
                    appendLog('[' + msg.index + '/' + msg.total + '] \u2717 ' + msg.customer + ': ' + msg.message, 'error');
                } else {
                    appendLog('[' + msg.index + '/' + msg.total + '] \u2014 ' + msg.customer + ': ' + msg.message, 'skip');
                }
            } else if (msg.type === 'done') {
                const s = msg.stats;
                appendLog('-'.repeat(50), 'info');
                appendLog('Done. ' + s.total + ' checked | ' + s.invoices_found + ' invoices found | ' + s.payments_created + ' created | ' + s.payments_updated + ' updated | ' + s.no_invoice + ' no invoice | ' + s.errors + ' errors', 'info');
                resetBtn();
            }
        }

        fetch(cfg.xeroCheckUrl + queryStr, { signal: abortController.signal })
            .then(response => {
                const ct = response.headers.get('content-type') || '';
                if (ct.includes('application/json')) {
                    return response.json().then(data => {
                        if (!data.success) appendLog('Error: ' + (data.error || 'Unknown error'), 'error');
                        else if (data.message) appendLog(data.message, 'warn');
                        resetBtn();
                    });
                }
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                function pump() {
                    return reader.read().then(({ done, value }) => {
                        if (done) {
                            if (buffer.trim()) processLine(buffer);
                            resetBtn();
                            return;
                        }
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop();
                        lines.forEach(processLine);
                        return pump();
                    });
                }
                return pump();
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                appendLog('Connection error: ' + err.message, 'error');
                resetBtn();
            });
    }

    window.DashReports = {
        initWeek, initMonthly, initSalesAfter, initAvg,
        initStock, switchStockTab,
        initOutstanding, setOutstandingPeriod, toggleOutstandingHidePaid,
        toggleOutstandingDetail, checkXeroSingle, filterOutstandingRows, checkXeroPayments,
    };
})();
