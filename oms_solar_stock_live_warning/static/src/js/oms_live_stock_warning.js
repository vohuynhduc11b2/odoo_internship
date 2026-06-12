/** @odoo-module **/

(function () {
    'use strict';

    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
        } else {
            document.addEventListener('DOMContentLoaded', fn, { once: true });
        }
    }

    async function jsonRpc(route, params) {
        const response = await fetch(route, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: params || {}, id: Date.now() }),
        });
        const data = await response.json();
        if (data.error) throw data.error;
        return data.result;
    }

    function esc(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function collectTemplateIds() {
        const ids = new Set();
        document.querySelectorAll('input[name="product_template_id"], [data-product-template-id]').forEach((el) => {
            const raw = el.value || el.getAttribute('data-product-template-id');
            const id = parseInt(raw || '0', 10);
            if (id > 0) ids.add(id);
        });
        return Array.from(ids);
    }

    function findContainersByTemplateId(id) {
        const selector = `input[name="product_template_id"][value="${id}"], [data-product-template-id="${id}"]`;
        const result = [];
        document.querySelectorAll(selector).forEach((el) => {
            const container = el.closest('.oe_product, .o_wsale_product_grid_wrapper, .o_wsale_product_information, #product_detail, form.js_main_product, .js_product, .card, article') || el.parentElement;
            if (container) result.push(container);
        });
        return result;
    }

    function clearLegacyStock(container) {
        container.querySelectorAll('.oms-stock-info-box, .oms-live-stock-warning, .oms-out-of-stock-bar').forEach((el) => el.remove());
        container.classList.remove('oms-is-out-of-stock', 'oms-is-low-stock');
    }

    function renderWarning(container, info) {
        clearLegacyStock(container);
        if (!info || !info.warning) return;
        const qty = Number(info.qty || 0);
        const imageHost = container.querySelector('.oms_product_image');
        const infoHost = container.querySelector('.o_wsale_product_information, .product_price, .o_product_page_summary, .css_quantity') || container;
        container.classList.add(qty <= 0 ? 'oms-is-out-of-stock' : 'oms-is-low-stock');
        if (imageHost && !imageHost.querySelector('.oms_stock_warning_badge, .o_ribbon')) {
            const bar = document.createElement('div');
            bar.className = 'oms-out-of-stock-bar';
            bar.textContent = 'SẮP HẾT HÀNG';
            imageHost.appendChild(bar);
        }
        if (info.message) {
            const box = document.createElement('div');
            box.className = 'oms-live-stock-warning small mt-2';
            box.innerHTML = `<div class="text-warning fw-semibold">${esc(info.message)}</div>`;
            infoHost.appendChild(box);
        }
    }

    async function applyLiveWarnings() {
        const ids = collectTemplateIds();
        if (!ids.length) return;
        let resp;
        try {
            resp = await jsonRpc('/oms_solar_stock_live_warning/product_warning_info', { template_ids: ids });
        } catch (error) {
            return;
        }
        if (!resp || !resp.ok) return;
        Object.entries(resp.products || {}).forEach(([id, info]) => {
            findContainersByTemplateId(id).forEach((container) => renderWarning(container, info));
        });
    }

    ready(function () {
        applyLiveWarnings();
    });
})();
