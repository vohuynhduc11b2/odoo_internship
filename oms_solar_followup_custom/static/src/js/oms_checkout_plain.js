(function () {
    'use strict';

    let checkoutEventsBound = false;
    let outstationSaveTimer = null;

    function ready(fn) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fn);
        } else {
            fn();
        }
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    async function jsonRpc(url, params) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: params || {}, id: Date.now()}),
        });
        const data = await resp.json();
        if (data.error) {
            throw new Error((data.error.data && data.error.data.message) || data.error.message || 'JSON-RPC Error');
        }
        return data.result;
    }


    const OUTSTATION_RE = /giao hàng ngo(?:ại|ài) tỉnh/i;
    const DELIVERY_METHOD_RE = /lấy hàng tại kho|giao hàng tiêu chuẩn|giao hàng ngo(?:ại|ài) tỉnh/i;

    async function fetchCheckoutState() {
        try {
            const state = await jsonRpc('/oms_solar_followup/checkout_state', {});
            if (state && state.ok) {
                window.__omsCheckoutState = state;
                return state;
            }
        } catch (error) {
            console.error('OMS checkout state refresh failed', error);
        }
        return window.__omsCheckoutState || null;
    }

    function textOf(el) {
        return ((el && el.textContent) || '').replace(/\s+/g, ' ').trim();
    }

    function isCheckout() {
        return /\/shop\/checkout/.test(window.location.pathname || '');
    }

    function checkoutScope() {
        return document.querySelector('#shop_checkout')
            || document.querySelector('#wrapwrap main')
            || document.querySelector('main')
            || document.querySelector('#wrapwrap')
            || document.body;
    }

    function scopedNodes(selector) {
        const scope = checkoutScope();
        return Array.from((scope || document).querySelectorAll(selector));
    }

    function findHeading(regex) {
        return scopedNodes('h1,h2,h3,h4,h5,strong,label,div,span,p').find((el) => {
            if (el.closest('#oms_checkout_top_host, #oms_checkout_alerts_wrap, #oms_buyer_partner_row, #oms_invoice_partner_row, #oms_outstation_section, #oms_gift_combo_section')) {
                return false;
            }
            return regex.test(textOf(el));
        });
    }

    function shippingHeading() {
        return findHeading(/địa chỉ giao hàng/i);
    }

    function deliveryHeading() {
        return findHeading(/chọn một phương thức giao hàng/i);
    }

    function summaryHeading() {
        return findHeading(/tóm tắt đơn hàng/i);
    }

    function findLeftColumn() {
        const shipping = shippingHeading();
        const summary = summaryHeading();
        if (!shipping) {
            return checkoutScope();
        }
        let node = shipping;
        while (node && node !== document.body) {
            const cls = typeof node.className === 'string' ? node.className : '';
            const looksLikeCol = /(^|\s)col(-|_|\s|$)|col-lg-|col-md-|col-sm-|col-xl-/i.test(cls);
            if (looksLikeCol && !(summary && node.contains(summary))) {
                return node;
            }
            node = node.parentElement;
        }
        return shipping.parentElement || checkoutScope();
    }

    function insertAnchorBeforeShipping() {
        const left = findLeftColumn();
        const shipping = shippingHeading();
        if (!left || !shipping) {
            return left || checkoutScope();
        }
        let node = shipping;
        while (node && node.parentElement && node.parentElement !== left) {
            node = node.parentElement;
        }
        return node || shipping;
    }

    function ensureTopHost() {
        const left = findLeftColumn();
        const anchor = insertAnchorBeforeShipping();
        let host = document.getElementById('oms_checkout_top_host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'oms_checkout_top_host';
            host.className = 'oms-checkout-top-host';
        }
        if (left && !host.isConnected) {
            if (anchor && left.contains(anchor)) {
                anchor.insertAdjacentElement('beforebegin', host);
            } else {
                left.prepend(host);
            }
        }
        return host;
    }

    function ensureSection(id, className, parent) {
        let el = document.getElementById(id);
        if (!el) {
            el = document.createElement('section');
            el.id = id;
        }
        el.className = className;
        if (parent && el.parentElement !== parent) {
            parent.appendChild(el);
        }
        return el;
    }

    function removeDuplicateOmsBlocks() {
        ['oms_checkout_top_host', 'oms_checkout_alerts_wrap', 'oms_buyer_partner_row', 'oms_invoice_partner_row', 'oms_outstation_section', 'oms_gift_combo_section']
            .forEach((id) => {
                const nodes = Array.from(document.querySelectorAll(`#${id}`));
                nodes.slice(1).forEach((node) => node.remove());
            });
    }

    function renderAlerts() {
        const host = ensureTopHost();
        const wrap = ensureSection('oms_checkout_alerts_wrap', 'mb-3', host);
        const query = new URLSearchParams(window.location.search || '');
        const state = window.__omsCheckoutState || {};
        const error = query.get('oms_checkout_error');
        const quoteRequested = query.get('oms_quote_requested');
        const blocks = [];
        if (error) {
            blocks.push(`<div class="alert alert-danger mb-3" id="oms_checkout_error_alert"><i class="fa fa-warning me-2"></i>${esc(error)}</div>`);
        }
        if ((quoteRequested === '1' || quoteRequested === 'true') && state.has_contact_price_item) {
            blocks.push('<div class="alert alert-success mb-3"><i class="fa fa-check-circle me-2"></i>Đã gửi yêu cầu cho Sales. Bộ phận phụ trách sẽ liên hệ để báo giá và hỗ trợ đơn hàng.</div>');
        }
        wrap.innerHTML = blocks.join('');
    }

    function partnerCard(partner, selectedId, route, selectedLabel) {
        const selected = Number(selectedId || 0) === Number(partner.id || 0);
        const redirect = encodeURIComponent(window.location.pathname + window.location.search);
        return `
            <div class="oms_checkout_partner_col col-12 col-lg-6">
                <div class="oms_checkout_partner_card card h-100 ${selected ? 'is-selected border-primary bg-light' : ''}">
                    <div class="card-body d-flex flex-column">
                        <div class="oms_checkout_partner_name fw-bold">${esc(partner.name)}</div>
                        <div class="oms_checkout_partner_meta">
                            ${partner.email ? `<div><i class="fa fa-envelope"></i><span>${esc(partner.email)}</span></div>` : ''}
                            ${partner.phone ? `<div><i class="fa fa-phone"></i><span>${esc(partner.phone)}</span></div>` : ''}
                            ${partner.street ? `<div><i class="fa fa-map-marker"></i><span>${esc(partner.street)}</span></div>` : ''}
                            ${partner.city ? `<div><i class="fa fa-location-arrow"></i><span>${esc(partner.city)}</span></div>` : ''}
                            ${partner.vat ? `<div><i class="fa fa-file-text-o"></i><span>${esc(partner.vat)}</span></div>` : ''}
                        </div>
                        <div class="oms_checkout_partner_action mt-auto">
                            ${selected ? `<span class="oms_checkout_partner_selected">${selectedLabel || 'Đang chọn'}</span>` : `<a href="${route}?partner_id=${partner.id}&redirect=${redirect}" class="oms_checkout_partner_select">Chọn</a>`}
                        </div>
                    </div>
                </div>
            </div>`;
    }

    function renderBuyerSection(state) {
        const host = ensureTopHost();
        const wrap = ensureSection('oms_buyer_partner_row', 'oms_checkout_partner_section oms_checkout_partner_section_buyer', host);
        const cards = (state.buyer_customers || []).map((partner) => partnerCard(partner, state.selected_buyer_customer_id, '/oms_solar_followup/select_buyer', 'Đang chọn')).join('');
        wrap.innerHTML = `
            <div class="oms_checkout_partner_head">
                <span>01</span>
                <div>
                    <h4>Đối tác mua</h4>
                    <p>Chọn đối tác mua theo nhóm khách hàng cha – con đã cấu hình.</p>
                </div>
            </div>
            <div class="oms_checkout_partner_grid row">${cards}</div>
        `;
    }

    function renderInvoiceSection(state) {
        const host = ensureTopHost();
        const wrap = ensureSection('oms_invoice_partner_row', 'oms_checkout_partner_section oms_checkout_partner_section_invoice', host);
        const cards = (state.invoice_customers || []).map((partner) => partnerCard(partner, state.selected_invoice_customer_id, '/oms_solar_followup/select_invoice', 'Đang chọn')).join('');
        wrap.innerHTML = `
            <div class="oms_checkout_partner_head">
                <span>02</span>
                <div>
                    <h4>Địa chỉ nhận hóa đơn</h4>
                    <p>Chọn khách hàng / địa chỉ nhận hóa đơn theo đối tác mua đang chọn.</p>
                </div>
            </div>
            <div class="oms_checkout_partner_grid row">${cards}</div>
        `;
    }

    function deliveryBlock() {
        const heading = deliveryHeading();
        if (!heading) return null;
        let node = heading;
        const left = findLeftColumn();
        while (node && node !== document.body) {
            const txt = textOf(node);
            if (/lấy hàng tại kho/i.test(txt) && OUTSTATION_RE.test(txt) && (!left || left.contains(node))) {
                return node;
            }
            if (left && node.parentElement === left) {
                return node;
            }
            node = node.parentElement;
        }
        return heading.parentElement;
    }


    function deliveryRows() {
        const block = deliveryBlock() || findLeftColumn() || checkoutScope();
        const inputs = Array.from((block || document).querySelectorAll('input[type="radio"], input[type="checkbox"]'));
        const rows = [];
        inputs.forEach((input) => {
            const row = rowForInput(input);
            if (row && !rows.includes(row)) {
                rows.push(row);
            }
        });
        return rows;
    }

    function selectedDeliveryRow() {
        const block = deliveryBlock() || findLeftColumn() || checkoutScope();
        const checked = Array.from((block || document).querySelectorAll('input[type="radio"]:checked, input[type="checkbox"]:checked'));
        for (const input of checked) {
            const row = rowForInput(input);
            if (row && DELIVERY_METHOD_RE.test(textOf(row))) {
                return row;
            }
        }
        return null;
    }

    function outstationRow() {
        return deliveryRows().find((row) => OUTSTATION_RE.test(textOf(row))) || null;
    }

    function isDomOutstationSelected() {
        const row = selectedDeliveryRow();
        return !!(row && OUTSTATION_RE.test(textOf(row)));
    }

    function rowForInput(input) {
        let node = input;
        while (node && node !== document.body) {
            if (node.id === 'oms_outstation_section' || node.id === 'oms_checkout_top_host') {
                return null;
            }
            const txt = textOf(node);
            if (DELIVERY_METHOD_RE.test(txt)) {
                return node;
            }
            node = node.parentElement;
        }
        return null;
    }

    function isOutstationSelected(state) {
        const row = selectedDeliveryRow();
        if (row) {
            return OUTSTATION_RE.test(textOf(row));
        }
        return !!(state && (state.outstation_selected || state.delivery_method === 'outstation'));
    }

    function ensureDeliveryBadges() {
        deliveryRows().forEach((row) => {
            let badge = row.querySelector('.o_wsale_delivery_badge_price');
            if (badge) {
                if (!badge.firstChild) {
                    badge.textContent = (badge.textContent || 'Miễn phí').trim() || 'Miễn phí';
                }
                return;
            }
            const candidates = Array.from(row.querySelectorAll('span, div, strong, small, b, label'));
            badge = candidates.find((el) => {
                const txt = textOf(el);
                return txt === 'Miễn phí' || /phí vc khách hàng tự chi trả/i.test(txt);
            });
            if (badge) {
                badge.classList.add('o_wsale_delivery_badge_price');
                if (!badge.firstChild) {
                    badge.textContent = (badge.textContent || 'Miễn phí').trim() || 'Miễn phí';
                }
                return;
            }
            const header = Array.from(row.children || []).find((el) => /flex|justify-content|align-items/i.test(el.className || '')) || row;
            const newBadge = document.createElement('span');
            newBadge.className = 'o_wsale_delivery_badge_price';
            newBadge.textContent = 'Miễn phí';
            header.appendChild(newBadge);
        });
    }

    function rewriteOutstationPrice(state) {
        const selected = isOutstationSelected(state || {});
        ensureDeliveryBadges();
        deliveryRows().forEach((row) => {
            const badge = row.querySelector('.o_wsale_delivery_badge_price');
            if (!badge) return;
            if (OUTSTATION_RE.test(textOf(row))) {
                badge.textContent = selected ? 'Phí VC khách hàng tự chi trả' : 'Miễn phí';
                badge.dataset.omsOutstationPrice = '1';
            }
        });
    }

    function validateButton(state) {
        const btn = document.querySelector('a[name="website_sale_main_button"], button[name="website_sale_main_button"], #oms_quote_button');
        if (!btn) {
            return true;
        }
        const selected = isOutstationSelected(state || {});
        const payload = currentOutstationPayload();
        const isValid = !selected || (
            String(payload.oms_outstation_option_id || '').trim() &&
            String(payload.oms_transport_address || '').trim() &&
            String(payload.oms_transport_note || '').trim()
        );
        btn.dataset.omsDisabled = isValid ? '0' : '1';
        btn.setAttribute('aria-disabled', isValid ? 'false' : 'true');
        if (btn.tagName === 'BUTTON' && btn.id !== 'oms_quote_button') {
            btn.disabled = !isValid;
        }
        return isValid;
    }

    async function saveGiftComboSelection() {
        return true;
    }

    function renderOutstationSection(state) {
        ensureDeliveryBadges();
        const rowAnchor = outstationRow() || deliveryBlock();
        const anchor = rowAnchor || deliveryBlock() || findLeftColumn();
        const parent = anchor && anchor.parentElement ? anchor.parentElement : findLeftColumn();
        const wrap = ensureSection('oms_outstation_section', 'card border-info mt-3', parent);
        if (anchor && wrap.previousElementSibling !== anchor) {
            anchor.insertAdjacentElement('afterend', wrap);
        }
        const optionsHtml = ['<option value="">Chọn phương án vận chuyển</option>']
            .concat((state.options || []).map((option) => `<option value="${option.id}" ${Number(option.id) === Number(state.outstation_option_id || 0) ? 'selected' : ''}>${esc(option.name)}</option>`))
            .join('');
        const selected = isOutstationSelected(state || {});
        wrap.style.display = selected ? '' : 'none';
        const hasOptions = (state.options || []).length > 0;
        wrap.innerHTML = `
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-2">
                    <div class="fw-bold">Thông tin giao hàng ngoại tỉnh</div>
                    <div class="small text-muted">Áp dụng khi chọn phương thức <b>Giao hàng ngoại tỉnh</b></div>
                </div>
                <p class="small text-muted mb-3">Khi chọn giao hàng ngoại tỉnh, vui lòng nhập đầy đủ phương án vận chuyển và địa chỉ nhận hàng trước khi sang bước thanh toán.</p>
                ${hasOptions ? '' : '<div class="alert alert-warning py-2 small mb-3">Chưa có phương án vận chuyển ngoại tỉnh đang hoạt động. Vui lòng cấu hình trong OMS Solar.</div>'}
                <div id="oms_outstation_form" class="row g-3">
                    <div class="col-12 col-lg-6">
                        <label for="oms_outstation_option_id" class="form-label">Phương án vận chuyển <span class="text-danger">*</span></label>
                        <select id="oms_outstation_option_id" name="oms_outstation_option_id" class="form-select">${optionsHtml}</select>
                    </div>
                    <div class="col-12">
                        <label for="oms_transport_address" class="form-label">Địa chỉ nhận hàng <span class="text-danger">*</span></label>
                        <textarea id="oms_transport_address" name="oms_transport_address" class="form-control" rows="2">${esc(state.transport_address || '')}</textarea>
                    </div>
                    <div class="col-12">
                        <label for="oms_transport_note" class="form-label">Ghi chú vận chuyển <span class="text-danger">*</span></label>
                        <textarea id="oms_transport_note" name="oms_transport_note" class="form-control" rows="2" placeholder="Ví dụ: giao giờ hành chính, người nhận, số điện thoại...">${esc(state.transport_note || '')}</textarea>
                    </div>
                    <div class="col-12 d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <small id="oms_outstation_fee_notice" class="text-muted ${selected ? '' : 'd-none'}">${esc(state.shipping_customer_pay_note || 'Phí VC khách hàng tự chi trả')}</small>
                        <div class="d-flex gap-2 align-items-center flex-wrap">
                            <small id="oms_outstation_save_hint" class="text-muted"></small>
                            <button type="button" id="oms_outstation_save_btn" class="btn btn-outline-primary">Lưu thông tin giao hàng ngoại tỉnh</button>
                        </div>
                    </div>
                </div>
            </div>`;
    }

    function syncOutstationUi(state) {
        const selected = isOutstationSelected(state || {});
        const section = document.getElementById('oms_outstation_section');
        if (section) {
            section.style.display = selected ? '' : 'none';
        }
        const notice = document.getElementById('oms_outstation_fee_notice');
        if (notice) {
            notice.classList.toggle('d-none', !selected);
        }
        rewriteOutstationPrice(state || {});
    }

    function currentOutstationPayload() {
        const option = document.getElementById('oms_outstation_option_id');
        const address = document.getElementById('oms_transport_address');
        const note = document.getElementById('oms_transport_note');
        return {
            oms_delivery_method: isOutstationSelected(window.__omsCheckoutState || {}) ? 'outstation' : 'standard',
            oms_outstation_option_id: option && option.value ? option.value : false,
            oms_transport_address: address ? address.value.trim() : '',
            oms_transport_note: note ? note.value.trim() : '',
        };
    }

    async function saveOutstationInfo(showMessage) {
        const state = window.__omsCheckoutState || {};
        const selected = isOutstationSelected(state);
        const payload = currentOutstationPayload();
        const hint = document.getElementById('oms_outstation_save_hint');
        const saveBtn = document.getElementById('oms_outstation_save_btn');
        if (!selected) {
            if (hint) hint.textContent = '';
            return true;
        }
        if (!(payload.oms_outstation_option_id && payload.oms_transport_address && payload.oms_transport_note)) {
            if (showMessage) {
                showTopAlert('Khi chọn giao hàng ngoại tỉnh, bắt buộc chọn phương án vận chuyển, nhập địa chỉ nhận hàng và ghi chú vận chuyển.');
            }
            return false;
        }
        if (saveBtn && saveBtn.dataset.saving === '1') {
            return false;
        }
        try {
            if (saveBtn) {
                saveBtn.dataset.saving = '1';
                saveBtn.disabled = true;
            }
            if (hint) hint.textContent = 'Đang lưu...';
            const nextState = await jsonRpc('/oms_solar_followup/update_order_extras', {
                order_id: state.order_id,
                ...payload,
            });
            if (nextState && nextState.ok) {
                window.__omsCheckoutState = nextState;
                rerenderCheckoutFromState(nextState);
                if (hint) {
                    hint.textContent = 'Đã lưu';
                    setTimeout(() => {
                        const liveHint = document.getElementById('oms_outstation_save_hint');
                        if (liveHint && liveHint.textContent === 'Đã lưu') {
                            liveHint.textContent = '';
                        }
                    }, 1500);
                }
                return true;
            }
        } catch (error) {
            console.error('OMS save outstation failed', error);
            if (showMessage) {
                showTopAlert('Không lưu được thông tin giao hàng ngoại tỉnh. Vui lòng thử lại.');
            }
        } finally {
            if (saveBtn) {
                saveBtn.dataset.saving = '0';
                saveBtn.disabled = false;
            }
        }
        return false;
    }


    function renderGiftComboSection(state) {
        const sections = state.gift_combo_sections || [];
        const existing = document.getElementById('oms_gift_combo_section');
        if (!sections.length) {
            if (existing) existing.remove();
            return;
        }
        const wrap = ensureSection('oms_gift_combo_section', 'card border-success mt-3', findLeftColumn());
        wrap.innerHTML = `
            <div class="card-body">
                <div class="fw-bold mb-3">Combo quà tặng theo chương trình</div>
                <div class="row g-3">
                    ${sections.map((selection) => `
                        <div class="col-12 col-xl-6">
                            <div class="border rounded p-3 h-100">
                                <div class="fw-semibold">${esc(selection.promotion_name || selection.line_name || 'Khuyến mãi')}</div>
                                <div class="small text-muted mb-2">Sản phẩm chính: ${esc(selection.line_name || '')}</div>
                                <div class="small mb-2">Số lượng mua: <b>${esc(selection.purchased_qty)}</b> | Quà được áp dụng: <b>${esc(selection.allowed_qty)}</b></div>
                                <div class="mb-2">
                                    <label class="form-label">Combo quà cố định</label>
                                    <div class="form-control bg-light">${esc((selection.combo_options && selection.combo_options[0] && selection.combo_options[0].name) || selection.combo_name || 'Chưa cấu hình combo quà')}</div>
                                </div>
                                <div class="mb-2">
                                    <label class="form-label">Số lượng quà</label>
                                    <input type="number" readonly value="${esc(selection.selected_qty || selection.allowed_qty || 0)}" class="form-control bg-light">
                                </div>
                                ${selection.note ? `<div class="small text-muted">${esc(selection.note)}</div>` : ''}
                            </div>
                        </div>`).join('')}
                </div>
            </div>`;
        const outstation = document.getElementById('oms_outstation_section');
        if (outstation && wrap.previousElementSibling !== outstation) {
            outstation.insertAdjacentElement('afterend', wrap);
        }
    }

    function updateQuoteButton(state) {
        if (!state || !state.has_contact_price_item) {
            const form = document.querySelector('#oms_quote_button_form');
            if (form) {
                form.remove();
            }
            return;
        }
        const mainButton = document.querySelector('a[name="website_sale_main_button"], button[name="website_sale_main_button"]');
        if (mainButton && !document.querySelector('#oms_quote_button_form')) {
            const form = document.createElement('form');
            form.id = 'oms_quote_button_form';
            form.method = 'post';
            form.action = '/oms_solar_followup/request_quote';
            form.className = 'w-100';
            form.innerHTML = `<input type="hidden" name="order_id" value="${state.order_id}"><input type="hidden" name="redirect" value="${esc(window.location.pathname + window.location.search)}"><button type="submit" class="btn btn-warning w-100" id="oms_quote_button">Gửi Sales báo giá</button>`;
            mainButton.replaceWith(form);
        }
    }

    let deliverySyncInFlight = false;
    let lastSyncedDeliveryMode = null;

    async function syncDeliveryMethodToServer(forceMode) {
        const state = window.__omsCheckoutState || {};
        const mode = forceMode || (isOutstationSelected(state) ? 'outstation' : 'standard');
        if (!state.order_id) {
            return;
        }
        if (deliverySyncInFlight && lastSyncedDeliveryMode === mode) {
            return;
        }
        if (state.delivery_method === mode && state.outstation_selected === (mode === 'outstation')) {
            rerenderCheckoutFromState(state);
        }
        window.__omsCheckoutState = Object.assign({}, state, {
            ok: true,
            delivery_method: mode,
            outstation_selected: mode === 'outstation',
        });
        rerenderCheckoutFromState(window.__omsCheckoutState);
        deliverySyncInFlight = true;
        lastSyncedDeliveryMode = mode;
        try {
            const fresh = await jsonRpc('/oms_solar_followup/update_order_extras', {
                order_id: state.order_id,
                oms_delivery_method: mode,
            });
            if (fresh && fresh.ok) {
                window.__omsCheckoutState = fresh;
                rerenderCheckoutFromState(fresh);
            }
        } catch (error) {
            console.error('OMS delivery method sync failed', error);
        } finally {
            deliverySyncInFlight = false;
        }
    }

    function showTopAlert(message) {
        const host = ensureTopHost();
        const wrap = ensureSection('oms_checkout_alerts_wrap', 'mb-3', host);
        let alert = document.getElementById('oms_checkout_validation_alert');
        if (!alert) {
            alert = document.createElement('div');
            alert.id = 'oms_checkout_validation_alert';
            alert.className = 'alert alert-danger mb-3';
            wrap.prepend(alert);
        }
        alert.innerHTML = `<i class="fa fa-warning me-2"></i>${esc(message)}`;
    }



    function rerenderCheckoutFromState(state) {
        if (!state || !state.ok) {
            return;
        }
        renderOutstationSection(state);
        rewriteOutstationPrice(state);
        syncOutstationUi(state);
        validateButton(state);
    }
    async function initCheckout() {
        if (!isCheckout()) return;
        let state;
        try {
            state = await jsonRpc('/oms_solar_followup/checkout_state', {});
        } catch (error) {
            console.error('OMS checkout state failed', error);
            return;
        }
        if (!state || !state.ok) return;
        window.__omsCheckoutState = state;
        removeDuplicateOmsBlocks();
        renderAlerts();
        renderBuyerSection(state);
        renderInvoiceSection(state);
        ensureDeliveryBadges();
        renderOutstationSection(state);
        renderGiftComboSection(state);
        rewriteOutstationPrice(state);
        syncOutstationUi(state);
        updateQuoteButton(state);
        validateButton(state);

        if (!checkoutEventsBound) {
            checkoutEventsBound = true;
            document.addEventListener('change', function (ev) {
                if (ev.target.matches('#oms_outstation_option_id')) {
                    validateButton(window.__omsCheckoutState || {});
                    return;
                }
                if (ev.target.matches('input[type="radio"], input[type="checkbox"]')) {
                    const input = ev.target;
                    const row = rowForInput(input);
                    if (row && DELIVERY_METHOD_RE.test(textOf(row))) {
                        const mode = OUTSTATION_RE.test(textOf(row)) && input.checked ? 'outstation' : 'standard';
                        window.__omsCheckoutState = Object.assign({}, window.__omsCheckoutState || {}, {ok: true, delivery_method: mode, outstation_selected: mode === 'outstation'});
                        ensureDeliveryBadges();
                        rerenderCheckoutFromState(window.__omsCheckoutState);
                        syncDeliveryMethodToServer(mode);
                        return;
                    }
                    rerenderCheckoutFromState(window.__omsCheckoutState || {});
                }
            });

            document.addEventListener('input', function (ev) {
                if (ev.target.matches('#oms_transport_address, #oms_transport_note, #oms_outstation_option_id')) {
                    validateButton(window.__omsCheckoutState || {});
                    clearTimeout(outstationSaveTimer);
                    outstationSaveTimer = setTimeout(() => {
                        saveOutstationInfo(false);
                    }, 500);
                }
            });
            document.addEventListener('click', async function (ev) {
                const saveGift = ev.target.closest('.oms-gift-combo-save');
                if (saveGift) {
                    ev.preventDefault();
                    await saveGiftComboSelection(saveGift.dataset.selectionId);
                    return;
                }
                const saveOutstationBtn = ev.target.closest('#oms_outstation_save_btn');
                if (saveOutstationBtn) {
                    ev.preventDefault();
                    await saveOutstationInfo(true);
                    return;
                }
                const btn = ev.target.closest('a[name="website_sale_main_button"], .a-submit[href*="/shop/payment"], #oms_quote_button');
                if (!btn) return;
                if (btn.dataset.omsBypass === '1') {
                    btn.dataset.omsBypass = '0';
                    return;
                }
                if (btn.dataset.omsDisabled === '1') {
                    ev.preventDefault();
                    ev.stopPropagation();
                    showTopAlert('Khi chọn giao hàng ngoại tỉnh, bắt buộc chọn phương án vận chuyển, nhập địa chỉ nhận hàng và ghi chú vận chuyển.');
                    return;
                }
                if (isOutstationSelected(window.__omsCheckoutState || {})) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    const ok = await saveOutstationInfo(true);
                    if (!ok) {
                        return;
                    }
                    if (btn.tagName === 'A' && btn.href) {
                        window.location.href = btn.href;
                    } else if (btn.tagName === 'BUTTON') {
                        btn.dataset.omsBypass = '1';
                        btn.disabled = false;
                        btn.click();
                    }
                }
            });
        }
    }

    async function initProduct() {
        document.querySelectorAll('.oms-stock-info-box').forEach((box) => {
            try { box.remove(); } catch (e) { box.innerHTML = ''; }
        });
    }

    ready(function () {
        initCheckout();
        initProduct();
    });
})();
