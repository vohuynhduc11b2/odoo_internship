(function () {
    'use strict';

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

    function isCheckout() {
        return /\/shop\/checkout/.test(window.location.pathname || '');
    }

    function findLeftColumn() {
        return document.querySelector('#oms_checkout_top_host')
            || document.querySelector('#shop_checkout')
            || document.querySelector('#wrapwrap main')
            || document.querySelector('main')
            || document.body;
    }

    function ensureSection(parent) {
        let el = document.getElementById('oms_gift_combo_section');
        if (!el) {
            el = document.createElement('section');
            el.id = 'oms_gift_combo_section';
            el.className = 'card border-success mt-3';
        }
        if (parent && el.parentElement !== parent) {
            parent.appendChild(el);
        }
        return el;
    }

    async function fetchState() {
        return jsonRpc('/oms_solar_followup/checkout_state', {});
    }

    function selectionCard(selection) {
        const options = (selection.combo_options || []).map((combo) => (
            `<option value="${combo.id}" ${Number(combo.id) === Number(selection.combo_id || 0) ? 'selected' : ''}>${esc(combo.name)}</option>`
        )).join('');
        const canChoose = (selection.combo_options || []).length > 1;
        const comboInput = canChoose
            ? `<select class="form-select oms-gift-combo-select" data-selection-id="${selection.selection_id}"><option value="">Chọn combo quà</option>${options}</select>`
            : `<div class="form-control bg-light">${esc((selection.combo_options && selection.combo_options[0] && selection.combo_options[0].name) || '')}</div>`;
        return `
            <div class="col-12 col-xl-6">
                <div class="border rounded p-3 h-100">
                    <div class="fw-semibold">${esc(selection.promotion_name || selection.line_name || 'Khuyến mãi')}</div>
                    <div class="small text-muted mb-2">Sản phẩm chính: ${esc(selection.line_name || '')}</div>
                    <div class="small mb-2">Số lượng combo mua: <b>${esc(selection.purchased_qty)}</b> | Quà tối đa: <b>${esc(selection.allowed_qty)}</b></div>
                    <div class="mb-2">
                        <label class="form-label">Combo quà tặng</label>
                        ${comboInput}
                    </div>
                    <div class="mb-2">
                        <label class="form-label">Số lượng quà</label>
                        <input type="number" min="0" max="${esc(selection.allowed_qty || 0)}" step="1" value="${esc(selection.selected_qty || selection.allowed_qty || 0)}" class="form-control oms-gift-combo-qty" data-selection-id="${selection.selection_id}">
                    </div>
                    ${selection.main_product_price ? `<div class="small text-success mb-2">Giá áp dụng cho sản phẩm chính: <b>${esc(selection.main_product_price)}</b></div>` : ''}
                    ${selection.note ? `<div class="small text-muted">${esc(selection.note)}</div>` : ''}
                </div>
            </div>`;
    }

    function render(state) {
        const sections = (state && state.gift_combo_sections) || [];
        const existing = document.getElementById('oms_gift_combo_section');
        if (!sections.length) {
            if (existing) existing.remove();
            return;
        }
        const wrap = ensureSection(findLeftColumn());
        wrap.innerHTML = `
            <div class="card-body">
                <div class="fw-bold mb-3">Combo quà tặng theo chương trình</div>
                <div class="row g-3">${sections.map(selectionCard).join('')}</div>
            </div>`;
    }

    let saving = false;
    async function saveSelection(selectionId, comboId, qty) {
        if (saving) return;
        saving = true;
        try {
            const state = await jsonRpc('/oms_solar_followup/update_gift_combo_selection', {
                selection_id: selectionId,
                combo_id: comboId || false,
                selected_qty: qty,
            });
            if (state && state.ok) {
                render(state);
            }
        } catch (error) {
            console.error('OMS promo combo save failed', error);
        } finally {
            saving = false;
        }
    }

    function bind() {
        document.addEventListener('change', function (ev) {
            const select = ev.target.closest('.oms-gift-combo-select');
            if (select) {
                const selectionId = select.dataset.selectionId;
                const card = select.closest('.border');
                const qtyInput = card && card.querySelector('.oms-gift-combo-qty');
                const qty = qtyInput ? qtyInput.value : 0;
                saveSelection(selectionId, select.value, qty);
                return;
            }
            const qtyInput = ev.target.closest('.oms-gift-combo-qty');
            if (qtyInput) {
                const selectionId = qtyInput.dataset.selectionId;
                const card = qtyInput.closest('.border');
                const selectInput = card && card.querySelector('.oms-gift-combo-select');
                const comboId = selectInput ? selectInput.value : null;
                saveSelection(selectionId, comboId, qtyInput.value);
            }
        }, {passive: true});
    }

    ready(async function () {
        if (!isCheckout()) return;
        bind();
        try {
            const state = await fetchState();
            if (state && state.ok) {
                render(state);
            }
        } catch (error) {
            console.error('OMS promo combo initial render failed', error);
        }
    });
})();
