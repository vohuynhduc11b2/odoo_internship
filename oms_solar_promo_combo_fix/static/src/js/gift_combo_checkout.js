/** @odoo-module **/

(function () {
    function esc(v) {
        return String(v || '').replace(/[&<>"']/g, function (c) {
            return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
        });
    }

    async function jsonRpc(url, params) {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: params || {}, id: Date.now()}),
        });
        const data = await resp.json();
        return data.result;
    }

    function getSelectionState(selectionId) {
        const state = window.__omsCheckoutState || {};
        const sections = state.gift_combo_sections || [];
        return sections.find((selection) => Number(selection.selection_id) === Number(selectionId)) || null;
    }

    function renderGiftSection() {
        const state = window.__omsCheckoutState || {};
        const sections = state.gift_combo_sections || [];
        const wrap = document.getElementById('oms_gift_combo_section');
        if (!wrap) return;
        const body = wrap.querySelector('.card-body');
        if (!body) return;
        if (!sections.length) {
            wrap.remove();
            return;
        }
        body.innerHTML = `
            <div class="fw-bold mb-3">Combo quà tặng theo chương trình</div>
            <div class="row g-3">
                ${sections.map((selection) => {
                    const options = selection.combo_options || [];
                    const selectable = options.length > 1;
                    return `
                    <div class="col-12 col-xl-6">
                        <div class="border rounded p-3 h-100">
                            <div class="fw-semibold">${esc(selection.promotion_name || selection.line_name || 'Khuyến mãi')}</div>
                            <div class="small text-muted mb-2">Sản phẩm áp dụng: ${esc(selection.line_name || '')}</div>
                            <div class="small mb-2">Số lượng combo mua: <b>${esc(selection.purchased_qty)}</b> | Quà tối đa: <b>${esc(selection.allowed_qty)}</b></div>
                            <div class="mb-2">
                                <label class="form-label">Combo quà tặng</label>
                                ${selectable ? `
                                    <div class="input-group">
                                        <select class="form-select oms-gift-combo-select" data-selection-id="${selection.selection_id}">
                                            <option value="">Chọn combo quà</option>
                                            ${options.map((o) => `<option value="${o.id}" ${selection.combo_id === o.id ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}
                                        </select>
                                        <button type="button" class="btn btn-outline-primary oms-gift-combo-save" data-selection-id="${selection.selection_id}">Lưu</button>
                                    </div>` : `<div class="form-control bg-light">${esc((options[0] && options[0].name) || selection.combo_name || 'Chưa cấu hình combo quà')}</div>`}
                            </div>
                            <div class="mb-2">
                                <label class="form-label">Số lượng quà</label>
                                <input type="number" readonly value="${esc(selection.allowed_qty || selection.selected_qty || 0)}" class="form-control bg-light">
                            </div>
                            ${selection.main_product_price ? `<div class="small text-success mb-2">Giá CTKM áp dụng: <b>${esc(selection.main_product_price)}</b></div>` : ''}
                            ${selection.note ? `<div class="small text-muted">${esc(selection.note)}</div>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>`;
    }

    async function saveSelection(selectionId) {
        const select = document.querySelector(`.oms-gift-combo-select[data-selection-id="${selectionId}"]`);
        if (!select) return;
        const comboId = parseInt(select.value || 0, 10) || false;
        const state = window.__omsCheckoutState || {};
        const selection = getSelectionState(selectionId);
        const result = await jsonRpc('/oms_solar_followup/update_gift_combo_selection', {
            order_id: state.order_id,
            selection_id: parseInt(selectionId, 10),
            combo_id: comboId,
            selected_qty: selection ? (selection.selected_qty || selection.allowed_qty || 0) : 0,
        });
        if (result && result.ok) {
            window.__omsCheckoutState = result;
            renderGiftSection();
        }
    }

    let lastHash = '';
    function computeHash() {
        try {
            return JSON.stringify((window.__omsCheckoutState || {}).gift_combo_sections || []);
        } catch (e) {
            return '';
        }
    }
    function sync() {
        const hash = computeHash();
        if (hash !== lastHash) {
            lastHash = hash;
            renderGiftSection();
        }
    }

    document.addEventListener('click', function (ev) {
        const btn = ev.target.closest('.oms-gift-combo-save');
        if (!btn) return;
        ev.preventDefault();
        saveSelection(btn.dataset.selectionId);
    });
    document.addEventListener('change', function (ev) {
        if (ev.target.matches('.oms-gift-combo-select')) {
            // optional auto-save could be added later
        }
        setTimeout(sync, 250);
    });
    document.addEventListener('DOMContentLoaded', function () {
        sync();
        setInterval(sync, 1000);
    });
})();
