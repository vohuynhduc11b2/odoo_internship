/** @odoo-module */

import publicWidget from "@web/legacy/js/public/public_widget";

/**
 * Client-side payment-status quick filter for /my/orders.
 *
 * Order-state filtering is server-side (see controllers/portal.py searchbar_filters);
 * payment_status is a non-stored computed field, so we filter the rendered page
 * by the `data-pay-key` attribute set on each row / card.
 */
publicWidget.registry.DatOrdersPaymentFilter = publicWidget.Widget.extend({
    selector: ".oms_portal_orders_page",
    events: {
        "click .dat-chip": "_onChipClick",
    },

    start() {
        this.$items = this.$(".dat-orders-row, .dat-order-card");
        // One "no match" message per list container (table + cards).
        this.$emptyMsgs = this.$(".dat-orders-table-card, .dat-orders-cards").map((i, el) => {
            const msg = document.createElement("div");
            msg.className = "dat-chips-empty d-none";
            msg.textContent = "Không có đơn nào ở trạng thái thanh toán này.";
            el.appendChild(msg);
            return msg;
        });
        return this._super(...arguments);
    },

    _onChipClick(ev) {
        const chip = ev.currentTarget;
        const key = chip.dataset.payFilter || "all";

        this.$(".dat-chip").removeClass("is-active");
        chip.classList.add("is-active");

        let visible = 0;
        this.$items.each((i, el) => {
            const match = key === "all" || el.dataset.payKey === key;
            el.classList.toggle("is-hidden", !match);
            if (match) {
                visible += 1;
            }
        });

        this.$emptyMsgs.toggleClass("d-none", visible !== 0);
    },
});

export default publicWidget.registry.DatOrdersPaymentFilter;
