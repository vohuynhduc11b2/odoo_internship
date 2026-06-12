/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';
import { rpc } from '@web/core/network/rpc';

function formatQty(value) {
    const num = Number(value || 0);
    return Number.isInteger(num) ? String(num) : num.toFixed(2).replace(/\.00$/, '');
}

function esc(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

publicWidget.registry.OMSSolarFrontend = publicWidget.Widget.extend({
    selector: 'body',

    async start() {
        await this._super(...arguments);
        this._query = new URLSearchParams(window.location.search || '');
        await this._initCheckoutPage();
        await this._initProductStockInfo();
    },

    _isCheckoutPage() {
        return (window.location.pathname || '').includes('/shop/checkout');
    },

    _checkoutRoot() {
        return document.querySelector('#shop_checkout')
            || document.querySelector('main')
            || document.querySelector('#wrap')
            || document.body;
    },

    _findHeading(regex) {
        const candidates = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, .h1, .h2, .h3, .h4, .h5, strong, label'));
        return candidates.find((el) => regex.test((el.textContent || '').trim()));
    },

    _findAddressSectionAnchor() {
        const deliveryHeading = this._findHeading(/chọn một phương thức giao hàng/i);
        if (deliveryHeading) {
            return deliveryHeading.closest('div, section') || deliveryHeading;
        }
        const shippingHeading = this._findHeading(/địa chỉ giao hàng/i);
        if (shippingHeading) {
            return shippingHeading.closest('div, section') || shippingHeading;
        }
        return this._checkoutRoot();
    },

    _findDeliveryMethodsAnchor() {
        const heading = this._findHeading(/chọn một phương thức giao hàng/i);
        if (heading) {
            let node = heading.closest('div, section') || heading.parentElement;
            while (node && node !== document.body) {
                if ((node.textContent || '').match(/lấy hàng tại kho|giao hàng tiêu chuẩn|giao hàng ngoại tỉnh/i)) {
                    return node;
                }
                node = node.parentElement;
            }
            return heading.closest('div, section') || heading;
        }
        return this._checkoutRoot();
    },

    async _initCheckoutPage() {
        if (!this._isCheckoutPage()) {
            return;
        }
        this.mainButton = document.querySelector('a[name="website_sale_main_button"], button[name="website_sale_main_button"]');
        await this._loadCheckoutState();
        this._renderQueryAlerts();
        this._bindCheckoutEvents();
        this._toggleOutstationUI();
    },

    async _loadCheckoutState() {
        try {
            this.checkoutState = await rpc('/oms_solar_followup/checkout_state', {});
        } catch (e) {
            this.checkoutState = null;
            return;
        }
        if (!this.checkoutState || !this.checkoutState.ok) {
            return;
        }
        this._renderBuyerSection();
        this._renderInvoiceSection();
        this._renderOutstationSection();
        this._renderQuoteButton();
        this._rewriteOutstationPrice();
        this.mainButton = document.querySelector('a[name="website_sale_main_button"], button[name="website_sale_main_button"], #oms_quote_button');
    },

    _renderQueryAlerts() {
        const host = this._checkoutRoot();
        if (!host) {
            return;
        }
        let wrap = document.querySelector('#oms_checkout_alerts_wrap');
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.id = 'oms_checkout_alerts_wrap';
            host.prepend(wrap);
        }
        const state = this.checkoutState || {};
        const error = this._query.get('oms_checkout_error');
        const quoteRequested = this._query.get('oms_quote_requested');
        const blocks = [];
        if (error) {
            blocks.push(`
                <div class="alert alert-danger mb-3" id="oms_checkout_error_alert">
                    <i class="fa fa-warning me-2"></i>${esc(error)}
                </div>
            `);
        }
        if ((quoteRequested === '1' || quoteRequested === 'true') && state.has_contact_price_item) {
            blocks.push(`
                <div class="alert alert-success mb-3" id="oms_quote_requested_alert">
                    <i class="fa fa-check-circle me-2"></i>
                    Đã gửi yêu cầu cho Sales. Bộ phận phụ trách sẽ liên hệ để báo giá và hỗ trợ đơn hàng.
                </div>
            `);
        }
        wrap.innerHTML = blocks.join('');
    },

    _partnerCardHtml(partner, selectedId, route, labelSelected = 'Đang chọn') {
        const isSelected = Number(selectedId || 0) === Number(partner.id || 0);
        const href = `${route}?partner_id=${partner.id}&redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`;
        return `
            <div class="oms_checkout_partner_col col-12 col-lg-6">
                <div class="oms_checkout_partner_card card h-100 ${isSelected ? 'is-selected border-primary bg-light' : ''}">
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
                            ${isSelected ? `<span class="oms_checkout_partner_selected">${labelSelected}</span>` : `<a href="${href}" class="oms_checkout_partner_select">Chọn</a>`}
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    _renderBuyerSection() {
        const state = this.checkoutState;
        if (!state) {
            return;
        }
        const anchor = this._findAddressSectionAnchor();
        if (!anchor) {
            return;
        }
        let box = document.querySelector('#oms_buyer_partner_row');
        if (!box) {
            box = document.createElement('div');
            box.id = 'oms_buyer_partner_row';
            box.className = 'oms_checkout_partner_section oms_checkout_partner_section_buyer';
            anchor.insertAdjacentElement('beforebegin', box);
        }
        box.className = 'oms_checkout_partner_section oms_checkout_partner_section_buyer';
        const buyers = state.buyer_customers || [];
        box.innerHTML = `
            <div class="oms_checkout_partner_head">
                <span>01</span>
                <div>
                    <h4>Đối tác mua</h4>
                    <p>Chọn đối tác mua theo nhóm khách hàng cha – con đã cấu hình.</p>
                </div>
            </div>
            <div class="oms_checkout_partner_grid row">${buyers.map((p) => this._partnerCardHtml(p, state.selected_buyer_customer_id, '/oms_solar_followup/select_buyer')).join('')}</div>
        `;
    },

    _renderInvoiceSection() {
        const state = this.checkoutState;
        if (!state) {
            return;
        }
        let box = document.querySelector('#oms_invoice_partner_row');
        const buyerBox = document.querySelector('#oms_buyer_partner_row');
        const host = buyerBox || this._findAddressSectionAnchor();
        if (!host) {
            return;
        }
        if (!box) {
            box = document.createElement('div');
            box.id = 'oms_invoice_partner_row';
            box.className = 'oms_checkout_partner_section oms_checkout_partner_section_invoice';
            host.insertAdjacentElement('afterend', box);
        }
        box.className = 'oms_checkout_partner_section oms_checkout_partner_section_invoice';
        const invoices = state.invoice_customers || [];
        box.innerHTML = `
            <div class="oms_checkout_partner_head">
                <span>02</span>
                <div>
                    <h4>Địa chỉ nhận hóa đơn</h4>
                    <p>Chọn khách hàng / địa chỉ nhận hóa đơn theo đối tác mua đang chọn.</p>
                </div>
            </div>
            <div class="oms_checkout_partner_grid row">${invoices.map((p) => this._partnerCardHtml(p, state.selected_invoice_customer_id, '/oms_solar_followup/select_invoice')).join('')}</div>
        `;
    },

    _renderOutstationSection() {
        const state = this.checkoutState;
        if (!state) {
            return;
        }
        const anchor = this._findDeliveryMethodsAnchor();
        if (!anchor) {
            return;
        }
        let box = document.querySelector('#oms_outstation_section');
        if (!box) {
            box = document.createElement('div');
            box.id = 'oms_outstation_section';
            box.className = 'card mt-3 mb-3';
            anchor.insertAdjacentElement('afterend', box);
        }
        const optionsHtml = ['<option value="">Chọn phương án vận chuyển</option>']
            .concat((state.options || []).map((opt) => `<option value="${opt.id}" ${Number(opt.id) === Number(state.outstation_option_id || 0) ? 'selected' : ''}>${esc(opt.name)}</option>`))
            .join('');
        box.innerHTML = `
            <div class="card-body">
                <div id="oms_outstation_fee_notice" class="alert alert-warning py-2 px-3 mb-3 ${state.outstation_selected ? '' : 'd-none'}">Phí VC khách hàng tự chi trả</div>
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h5 class="mb-0">Thông tin giao hàng ngoại tỉnh</h5>
                    <small class="text-muted">Áp dụng khi chọn phương thức “Giao hàng ngoại tỉnh”</small>
                </div>
                <p class="text-muted small mb-3">Khi chọn giao hàng ngoại tỉnh, vui lòng nhập đầy đủ phương án vận chuyển, địa chỉ nhận hàng và ghi chú vận chuyển trước khi sang bước thanh toán.</p>
                <form action="/oms_solar_followup/save_outstation" method="post" id="oms_outstation_form" class="row g-3">
                    <input type="hidden" name="redirect" value="${esc(window.location.pathname + window.location.search)}"/>
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
                        <textarea id="oms_transport_note" name="oms_transport_note" class="form-control" rows="2" placeholder="Ví dụ: book Lalamove, Grab, gửi chành, thông tin người nhận, thời gian giao mong muốn...">${esc(state.transport_note || '')}</textarea>
                    </div>
                    <div class="col-12 d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <small class="text-muted">Phí VC khách hàng tự chi trả. Cấu hình phương án giao ngoại tỉnh tại menu OMS Solar / Cấu hình / Giao hàng ngoại tỉnh.</small>
                        <button type="submit" class="btn btn-outline-primary">Lưu thông tin giao hàng ngoại tỉnh</button>
                    </div>
                </form>
            </div>
        `;
        this.outstationSection = box;
        this.outstationFeeNotice = box.querySelector('#oms_outstation_fee_notice');
        this.outstationOption = box.querySelector('#oms_outstation_option_id');
        this.outstationAddress = box.querySelector('#oms_transport_address');
        this.outstationNote = box.querySelector('#oms_transport_note');
    },

    _renderQuoteButton() {
        const state = this.checkoutState;
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
            form.innerHTML = `
                <input type="hidden" name="order_id" value="${state.order_id}">
                <input type="hidden" name="redirect" value="${esc(window.location.pathname + window.location.search)}">
                <button type="submit" class="btn btn-warning w-100" id="oms_quote_button">Gửi Sales báo giá</button>
            `;
            mainButton.replaceWith(form);
        }
    },

    _getOutstationRow() {
        const nodes = Array.from(document.querySelectorAll('label, .form-check, .card, .list-group-item, .border, .delivery_method, li, div'));
        return nodes.find((node) => /giao hàng ngoại tỉnh/i.test(node.textContent || '')) || null;
    },

    _getOutstationInput() {
        const row = this._getOutstationRow();
        return row ? row.querySelector('input[type="radio"], input[type="checkbox"]') : null;
    },

    _isOutstationSelected() {
        const input = this._getOutstationInput();
        if (input) {
            return !!input.checked;
        }
        return !!(this.checkoutState && this.checkoutState.outstation_selected);
    },

    _rewriteOutstationPrice() {
        const row = this._getOutstationRow();
        if (!row) {
            return;
        }
        row.querySelectorAll('*').forEach((el) => {
            const text = (el.textContent || '').trim();
            if (text === 'Miễn phí' || el.dataset.omsOutstationPrice === '1') {
                el.textContent = 'Phí VC khách hàng tự chi trả';
                el.dataset.omsOutstationPrice = '1';
            }
        });
    },

    _toggleOutstationUI() {
        const selected = this._isOutstationSelected();
        if (this.outstationSection) {
            this.outstationSection.classList.toggle('border-warning', selected);
        }
        if (this.outstationFeeNotice) {
            this.outstationFeeNotice.classList.toggle('d-none', !selected);
        }
        this._rewriteOutstationPrice();
        this._validateCheckoutButton();
    },

    _validateCheckoutButton() {
        const btn = document.querySelector('a[name="website_sale_main_button"], button[name="website_sale_main_button"], #oms_quote_button');
        if (!btn) {
            return;
        }
        const selected = this._isOutstationSelected();
        const isValid = !selected || (
            (this.outstationOption?.value || '').trim() &&
            (this.outstationAddress?.value || '').trim() &&
            (this.outstationNote?.value || '').trim()
        );
        btn.classList.toggle('disabled', !isValid);
        btn.setAttribute('aria-disabled', isValid ? 'false' : 'true');
        if (btn.tagName === 'A') {
            btn.dataset.omsDisabled = isValid ? '0' : '1';
        } else if (btn.id !== 'oms_quote_button') {
            btn.disabled = !isValid;
        }
    },

    _bindCheckoutEvents() {
        document.addEventListener('change', (ev) => {
            if (ev.target.closest('input[type="radio"], input[type="checkbox"]')) {
                setTimeout(() => this._toggleOutstationUI(), 50);
            }
        });
        document.addEventListener('input', (ev) => {
            if (ev.target.matches('#oms_transport_address, #oms_transport_note, #oms_outstation_option_id')) {
                this._validateCheckoutButton();
            }
        });
        document.addEventListener('click', (ev) => {
            const btn = ev.target.closest('a[name="website_sale_main_button"], #oms_quote_button');
            if (!btn) {
                return;
            }
            if (btn.dataset.omsDisabled === '1' || btn.disabled) {
                ev.preventDefault();
                ev.stopPropagation();
                const wrap = document.querySelector('#oms_checkout_alerts_wrap') || this._checkoutRoot();
                let alertBox = document.querySelector('#oms_checkout_error_alert');
                if (!alertBox && wrap) {
                    alertBox = document.createElement('div');
                    alertBox.id = 'oms_checkout_error_alert';
                    alertBox.className = 'alert alert-danger mb-3';
                    wrap.prepend(alertBox);
                }
                if (alertBox) {
                    alertBox.innerHTML = '<i class="fa fa-warning me-2"></i>Khi chọn giao hàng ngoại tỉnh, bắt buộc chọn phương án vận chuyển, nhập địa chỉ nhận hàng và ghi chú vận chuyển.';
                }
            }
        });
    },

    async _initProductStockInfo() {
        const ids = new Set();
        document.querySelectorAll('input[name="product_template_id"], [data-product-template-id]').forEach((el) => {
            const value = el.value || el.dataset.productTemplateId || el.getAttribute('data-product-template-id');
            const id = parseInt(value || '0', 10);
            if (id > 0) {
                ids.add(id);
            }
        });
        if (!ids.size) {
            return;
        }
        let resp;
        try {
            resp = await rpc('/oms_solar_followup/product_stock_info', {template_ids: [...ids]});
        } catch (e) {
            return;
        }
        if (!resp || !resp.ok) {
            return;
        }
        for (const el of document.querySelectorAll('input[name="product_template_id"], [data-product-template-id]')) {
            const value = el.value || el.dataset.productTemplateId || el.getAttribute('data-product-template-id');
            const id = parseInt(value || '0', 10);
            const info = resp.products?.[id];
            if (!info) {
                continue;
            }
            const container = el.closest('.oe_product, .o_wsale_product_grid_wrapper, .o_wsale_product_information, #product_detail, form.js_main_product, .js_product, .card, article') || el.parentElement;
            if (!container || container.querySelector('.oms-stock-info-box')) {
                continue;
            }
            const host = container.querySelector('.o_wsale_product_information, .product_price, .o_product_page_summary, .css_quantity') || container;
            const box = document.createElement('div');
            box.className = 'oms-stock-info-box small text-muted mt-2';
            box.innerHTML = `<div>Tồn kho: <strong>${formatQty(info.qty)}</strong></div>`;
            if (info.warning && info.message) {
                const warning = document.createElement('div');
                warning.className = 'text-warning fw-semibold';
                warning.textContent = info.message;
                box.appendChild(warning);
            }
            if (info.is_contact_price) {
                const contact = document.createElement('div');
                contact.className = 'text-primary fw-semibold';
                contact.textContent = 'Liên hệ NVKD';
                box.appendChild(contact);
            }
            host.appendChild(box);
        }
    },
});
