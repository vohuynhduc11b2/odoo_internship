/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { jsonrpc } from "@web/core/network/rpc";

function parseQty(value) {
    const qty = parseInt((value || "1").toString(), 10);
    return Number.isNaN(qty) || qty < 1 ? 1 : qty;
}

publicWidget.registry.UcWebsiteSaleTierPriceFix = publicWidget.Widget.extend({
    selector: ".oe_website_sale",
    events: {
        "change input[name='add_qty']": "_onQtyChanged",
        "input input[name='add_qty']": "_onQtyChanged",
        "click .css_quantity .btn": "_onQtyButtonClicked",
        "change input.js_variant_change": "_onVariantChanged",
        "change select.js_variant_change": "_onVariantChanged",
    },

    start() {
        const res = this._super(...arguments);
        setTimeout(() => this._refreshTierBlock(), 50);
        return res;
    },

    _getQtyInput() {
        return this.$("input[name='add_qty']").first();
    },

    _getProductId() {
        const ids = [
            this.$("input[name='product_id']").first().val(),
            this.$("#product_detail").data("product-product-id"),
            this.$(".js_main_product [name='product_id']").first().val(),
            this.$("input.product_id:checked").val(),
            this.$("input.js_product_change:checked").val(),
        ].filter(Boolean);
        const id = parseInt(ids[0], 10);
        return Number.isNaN(id) ? null : id;
    },

    _onQtyButtonClicked() {
        setTimeout(() => this._refreshTierBlock(), 50);
    },

    _onQtyChanged() {
        this._refreshTierBlock();
    },

    _onVariantChanged() {
        setTimeout(() => this._refreshTierBlock(), 100);
    },

    async _refreshTierBlock() {
        const productId = this._getProductId();
        const $qtyInput = this._getQtyInput();
        if (!productId || !$qtyInput.length) {
            return;
        }

        const qty = parseQty($qtyInput.val());
        let data;
        try {
            data = await jsonrpc('/shop/uc/tier_price_info', {
                product_id: productId,
                add_qty: qty,
            });
        } catch (_err) {
            return;
        }
        if (!data || data.error) {
            return;
        }

        this._renderTierTable(data);
        this._updateDisplayedPrices(data);
    },

    _renderTierTable(data) {
        const $table = this.$('.uc_tier_price_table');
        if (!$table.length) {
            return;
        }
        let $tbody = $table.find('tbody');
        if (!$tbody.length) {
            $tbody = $('<tbody/>');
            $table.append($tbody);
        }
        $tbody.empty();

        (data.tiers || []).forEach((row, index) => {
            const isActive = index === data.active_index;
            const $tr = $(`
                <tr class="${isActive ? 'table-active uc-tier-active' : ''}" data-tier-index="${index}">
                    <td>${row.range_label}</td>
                    <td class="text-end">${row.price_html}</td>
                </tr>
            `);
            $tbody.append($tr);
        });
    },

    _updateDisplayedPrices(data) {
        const unitSelectors = [
            '.product_price h3 .oe_currency_value',
            '.product_price .oe_currency_value',
            '.oe_price .oe_currency_value',
            '.oe_website_sale .js_product .product_price .css_editable_mode_hidden .oe_currency_value',
            '.uc_unit_price_html',
        ];
        for (const selector of unitSelectors) {
            const $el = this.$(selector).first();
            if ($el.length) {
                $el.html(data.unit_price_html);
                break;
            }
        }

        const totalSelectors = [
            '.uc_total_price_html',
            '.uc_total_price_value',
            '.product_price_total .oe_currency_value',
            '.oe_price_total .oe_currency_value',
            '.js_main_product .text-nowrap.text-primary',
        ];
        for (const selector of totalSelectors) {
            const $el = this.$(selector).first();
            if ($el.length) {
                $el.html(data.total_price_html);
                break;
            }
        }
    },
});
