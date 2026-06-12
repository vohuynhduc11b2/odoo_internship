/** @odoo-module **/

(function () {
    "use strict";

    function normalizeSpace(text) {
        return String(text || "").replace(/\s+/g, " ").trim();
    }

    function parseMoney(text) {
        const raw = normalizeSpace(text);
        if (!raw) return null;
        const digits = raw.replace(/[^\d]/g, "");
        if (!digits) return null;
        const value = parseInt(digits, 10);
        return Number.isFinite(value) ? value : null;
    }

    function applyContactPrice(root = document) {
        const cards = root.querySelectorAll?.(".oe_product_cart, .o_wsale_products_item, .oe_product, .o_wsale_product_grid_wrapper") || [];
        cards.forEach((card) => {
            const priceBox = card.querySelector(".product_price");
            if (!priceBox) return;
            if (/giá\s*liên\s*hệ/i.test(normalizeSpace(priceBox.textContent || ""))) return;

            const priceNode = Array.from(priceBox.querySelectorAll(".oe_currency_value, .oe_price, [itemprop='price'], span, em")).find((node) => {
                const text = normalizeSpace(node.textContent || node.getAttribute("content") || "");
                const value = parseMoney(text);
                return value !== null && value <= 1;
            });
            if (!priceNode) return;

            priceBox.querySelectorAll("[itemprop='price'], [itemprop='priceCurrency']").forEach((node) => node.remove());
            priceBox.innerHTML = '<span class="h6 mb-0 fw-bold text-primary">Giá liên hệ</span>';
        });
    }

    function start() {
        applyContactPrice();
        setTimeout(applyContactPrice, 250);
        setTimeout(applyContactPrice, 1000);

        const observer = new MutationObserver(() => {
            clearTimeout(observer._timer);
            observer._timer = setTimeout(applyContactPrice, 80);
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
