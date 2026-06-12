/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const TAG = "[OMS_PRICE]";
const DEBUG = true;
const RETRY_BOOT_DELAY = 250;
const RETRY_BOOT_MAX = 20;
const APPLY_DEBOUNCE_MS = 80;
const APPLY_SERIES_DELAYS = [0, 70, 180, 360, 700, 1100, 1600];
const MUTATION_REAPPLY_MS = 220;
const OWN_MUTATION_BLOCK_MS = 220;
const STORAGE_TTL_MS = 12 * 60 * 60 * 1000;
const STORAGE_PREFIXES = [
    "oms_price_sync:",
    "wsc_price_sync:",
    "wsc_price_sync_v2:",
    "wsc_price_sync_v3:",
];

const SHOP_LINK_SELECTORS = [
    '.oe_product_cart a[href*="/shop/"]',
    '.o_wsale_products_item a[href*="/shop/"]',
    '.oe_product a[href*="/shop/"]',
    'a[href*="/shop/"]',
].join(", ");

const SHOP_PRICE_SELECTORS = [
    ".oe_price",
    ".product_price",
    ".oe_currency_value",
    '[itemprop="price"]',
].join(", ");

const state = {
    productId: 0,
    templateId: 0,
    tiers: [],
    lastSignature: null,
    boundQtyEl: null,
    docBound: false,
    observerBound: false,
    observer: null,
    applyTimer: null,
    reloadTimer: null,
    applySeriesTimers: [],
    mutationTimer: null,
    applying: false,
    ownMutationBlockedUntil: 0,
    initialBasePrice: 0,
};

function log(...args) {
    if (DEBUG) console.log(TAG, ...args);
}
function warn(...args) {
    if (DEBUG) console.warn(TAG, ...args);
}
function err(...args) {
    console.error(TAG, ...args);
}

function n(v, def = 0) {
    const x = Number(v);
    return Number.isFinite(x) ? x : def;
}

function normalizeSpace(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
}

function normalizeText(txt) {
    return normalizeSpace(txt).toLowerCase();
}

function formatVnd(value) {
    return n(value, 0).toLocaleString("vi-VN");
}

function escapeHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function formatMoneyLikeSample(value, sampleText) {
    const sample = String(sampleText || "₫");
    const symbol = sample.includes("₫") ? "₫" : sample.toLowerCase().includes("đ") ? "đ" : "₫";
    return `${formatVnd(value)} ${symbol}`;
}

function parseMoney(text) {
    const raw = normalizeSpace(text);
    if (!raw) return null;
    const negative = raw.includes("-");
    const digits = raw.replace(/[^\d]/g, "");
    if (!digits) return null;
    const value = parseInt(digits, 10);
    return Number.isFinite(value) ? (negative ? -value : value) : null;
}

function looksLikeMoneyText(txt) {
    const s = normalizeText(txt).replace(/\u00a0/g, " ");
    return /^\d{1,3}(?:[.,]\d{3})+(?:\s*[₫đ])?$/.test(s);
}

function isOpenEnd(maxQty) {
    const max = n(maxQty, 0);
    return !max || max >= 999999;
}

function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && (rect.width > 0 || rect.height > 0);
}

function urlWithoutHash(url) {
    try {
        const u = new URL(url, window.location.origin);
        u.hash = "";
        return u.toString();
    } catch (_e) {
        return "";
    }
}

function getProductIdFromUrl(url) {
    const clean = String(url || "").split("?")[0].split("#")[0];
    const match = clean.match(/-(\d+)$/);
    return match ? parseInt(match[1], 10) : 0;
}

function getCurrentProductInfo(wrap) {
    const form = getProductForm();
    const productInput =
        (form && form.querySelector('input.product_id[name="product_id"]:checked')) ||
        (form && form.querySelector('input.product_id[name="product_id"][type="hidden"]')) ||
        (form && form.querySelector('input[name="product_id"][type="hidden"]')) ||
        (form && form.querySelector('input.product_id[name="product_id"]')) ||
        (form && form.querySelector('input[name="product_id"]'));
    const templateInput =
        (form && form.querySelector('input.product_template_id[name="product_template_id"]')) ||
        (form && form.querySelector('input[name="product_template_id"]'));

    const productId = n(productInput && productInput.value, 0) || n(wrap && wrap.getAttribute("data-product-id"), 0);
    const templateId = n(templateInput && templateInput.value, 0) || n(wrap && wrap.getAttribute("data-template-id"), 0);
    return { productId, templateId };
}

function storageSetWithPrefix(prefix, key, value) {
    const fullKey = prefix + key;
    const payload = JSON.stringify({ value, ts: Date.now() });
    try {
        sessionStorage.setItem(fullKey, payload);
    } catch (_e) {}
    try {
        localStorage.setItem(fullKey, payload);
    } catch (_e) {}
}

function storageSet(key, value) {
    storageSetWithPrefix(STORAGE_PREFIXES[0], key, value);
}

function storageGet(key) {
    for (const prefix of STORAGE_PREFIXES) {
        const fullKey = prefix + key;
        let raw = null;
        try {
            raw = sessionStorage.getItem(fullKey);
        } catch (_e) {}
        if (!raw) {
            try {
                raw = localStorage.getItem(fullKey);
            } catch (_e) {}
        }
        if (!raw) continue;
        try {
            const data = JSON.parse(raw);
            if (!data || typeof data !== "object") continue;
            if (Date.now() - Number(data.ts || 0) > STORAGE_TTL_MS) {
                try {
                    sessionStorage.removeItem(fullKey);
                } catch (_e) {}
                try {
                    localStorage.removeItem(fullKey);
                } catch (_e) {}
                continue;
            }
            return data.value || null;
        } catch (_e) {
            continue;
        }
    }
    return null;
}

function rememberShopPriceFromCard(card, link) {
    if (!card || !link) return;
    const href = urlWithoutHash(link.href);
    if (!href) return;

    const priceCandidates = [];
    let node = card;
    for (let i = 0; i < 4 && node; i += 1, node = node.parentElement) {
        priceCandidates.push(...node.querySelectorAll(SHOP_PRICE_SELECTORS));
    }

    let best = null;
    for (const el of priceCandidates) {
        const value = parseMoney(el.textContent || el.getAttribute("content") || "");
        if (value !== null && value > 0) {
            best = {
                value,
                text: normalizeSpace(el.textContent || el.getAttribute("content") || ""),
                href,
                productId: getProductIdFromUrl(href),
            };
            break;
        }
    }
    if (!best) return;

    storageSet(`url:${href}`, best);
    if (best.productId) {
        storageSet(`id:${best.productId}`, best);
    }
    log("remembered shop price", best);
}

function scanShopPrices() {
    replaceContactPriceOnShopCards();

    document.querySelectorAll(SHOP_LINK_SELECTORS).forEach((link) => {
        const card = link.closest(".oe_product_cart, .o_wsale_products_item, .oe_product, .card, article, div");
        rememberShopPriceFromCard(card || link.parentElement, link);
    });
}

function replaceContactPriceOnShopCards(root = document) {
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
        priceBox.innerHTML = `<span class="h6 mb-0 fw-bold text-primary">Giá liên hệ</span>`;
    });
}

function bindShopTracking() {
    if (document.body.dataset.omsPriceShopTrackingBound === "1") return;
    document.body.dataset.omsPriceShopTrackingBound = "1";

    document.addEventListener(
        "click",
        (ev) => {
            const link = ev.target.closest(SHOP_LINK_SELECTORS);
            if (!link) return;
            const card = link.closest(".oe_product_cart, .o_wsale_products_item, .oe_product, .card, article, div");
            rememberShopPriceFromCard(card || link.parentElement, link);
        },
        true
    );
}

function getStoredShopPrice(productId) {
    const href = urlWithoutHash(window.location.href);
    const byUrl = storageGet(`url:${href}`);
    if (byUrl && byUrl.value > 0) return byUrl.value;

    const pid = productId || getProductIdFromUrl(href);
    if (pid) {
        const byId = storageGet(`id:${pid}`);
        if (byId && byId.value > 0) return byId.value;
    }
    return 0;
}

function getWrap() {
    return document.querySelector(".oms-price-tiers");
}

function getProductForm() {
    return (
        document.querySelector("form.js_main_product") ||
        document.querySelector("form.js_add_cart_variants") ||
        document.querySelector("#product_detail form") ||
        document.querySelector("#product_details form") ||
        document.querySelector("form[action*='/shop/cart/update']") ||
        document.querySelector("form") ||
        null
    );
}

function getProductRoot() {
    return (
        document.querySelector("#product_details") ||
        document.querySelector("#product_detail") ||
        getProductForm() ||
        document.querySelector(".oe_website_sale") ||
        document
    );
}

function getScopeRoot() {
    return (
        document.querySelector("#product_details") ||
        document.querySelector("#product_detail") ||
        getProductForm() ||
        document
    );
}

function getSummaryTotalNodes() {
    const scope = getScopeRoot();
    return uniqNodes([
        ...Array.from(document.querySelectorAll("#summary_total")),
        ...Array.from(scope.querySelectorAll?.("#summary_total, [data-total-price], [data-price-total]") || []),
    ]);
}

function getQtyInput() {
    return (
        document.querySelector('form.js_main_product input[name="add_qty"]') ||
        document.querySelector('form.js_main_product input.js_quantity') ||
        document.querySelector('input[name="add_qty"]') ||
        document.querySelector("input.js_quantity")
    );
}

function parseQtyFromInput() {
    const el = getQtyInput();
    const raw = el ? el.value : "1";
    const qty = Math.max(1, parseInt(raw || "1", 10));
    return Number.isFinite(qty) ? qty : 1;
}

function ensureHighlightStyle() {
    if (document.getElementById("oms_price_tier_inline_style")) return;

    const style = document.createElement("style");
    style.id = "oms_price_tier_inline_style";
    style.textContent = `
        .oms-price-tiers tbody tr.oms-tier-active > td {
            background: #dbeafe !important;
            color: #0f172a !important;
            font-weight: 700 !important;
        }
    `;
    document.head.appendChild(style);
}

function sanitizeRules(rawTiers) {
    const out = [];
    const seen = new Set();

    for (const t of rawTiers || []) {
        const min = Math.max(1, n(t.min_qty, 1));
        const max = n(t.max_qty, 0);
        let price = t.price;

        let parsedPrice = null;
        if (typeof price === "number") {
            parsedPrice = Number.isFinite(price) ? price : null;
        } else if (typeof price === "string") {
            parsedPrice = parseMoney(price);
            if (parsedPrice === null) {
                const asNumber = n(price, null);
                parsedPrice = asNumber !== null ? asNumber : null;
            }
        } else {
            const asNumber = n(price, null);
            parsedPrice = asNumber !== null ? asNumber : null;
        }

        if (parsedPrice !== null) {
            price = parsedPrice;
        }

        const isContact = Boolean(t.is_contact) || (typeof price === "number" && price <= 1);
        if (isContact) {
            price = 0;
        }

        if (typeof price === "number" && price <= 0 && !isContact) continue;
        if (typeof price === "string" && !price.trim()) continue;
        if (!isOpenEnd(max) && max < min) continue;

        const key = `${min}|${max}|${price}|${isContact ? 1 : 0}`;
        if (seen.has(key)) continue;
        seen.add(key);

        out.push({
            ...t,
            min_qty: min,
            max_qty: max,
            price,
            is_contact: isContact,
        });
    }

    return out;
}

function ruleMatches(rule, qty) {
    return qty >= rule.min_qty && (isOpenEnd(rule.max_qty) || qty <= rule.max_qty);
}

function chooseRuleForQty(rules, qty) {
    const matched = (rules || []).filter((r) => ruleMatches(r, qty));
    if (!matched.length) return null;

    matched.sort((a, b) => {
        const aSpecial = a.is_special ? 1 : 0;
        const bSpecial = b.is_special ? 1 : 0;
        if (bSpecial !== aSpecial) return bSpecial - aSpecial;

        if (b.min_qty !== a.min_qty) return b.min_qty - a.min_qty;

        const aMax = isOpenEnd(a.max_qty) ? Number.MAX_SAFE_INTEGER : a.max_qty;
        const bMax = isOpenEnd(b.max_qty) ? Number.MAX_SAFE_INTEGER : b.max_qty;
        if (aMax !== bMax) return aMax - bMax;

        if (typeof a.price === "number" && typeof b.price === "number") {
            return a.price - b.price;
        }
        return 0;
    });

    return matched[0];
}

function buildEffectiveTiers(rawTiers) {
    const rules = sanitizeRules(rawTiers);
    if (!rules.length) return [];

    const points = new Set([1]);
    for (const r of rules) {
        points.add(r.min_qty);
        if (!isOpenEnd(r.max_qty)) points.add(r.max_qty + 1);
    }

    const sortedPoints = Array.from(points)
        .filter((x) => x >= 1)
        .sort((a, b) => a - b);

    const result = [];

    for (let i = 0; i < sortedPoints.length; i++) {
        const start = sortedPoints[i];
        const nextStart = i < sortedPoints.length - 1 ? sortedPoints[i + 1] : 0;
        const rule = chooseRuleForQty(rules, start);
        if (!rule) continue;

        let end = nextStart > 0 ? nextStart - 1 : 0;
        if (!isOpenEnd(rule.max_qty)) {
            if (!end || end > rule.max_qty) end = rule.max_qty;
        } else {
            end = 0;
        }
        if (end >= 999999) end = 0;

        const seg = {
            min_qty: start,
            max_qty: end,
            price: rule.price,
            name: rule.name || rule.price_frame_name || "",
            is_contact: !!rule.is_contact,
            is_special: !!rule.is_special,
            _tier_key: `${start}-${end || 0}`,
        };

        const prev = result[result.length - 1];
        if (
            prev &&
            isOpenEnd(prev.max_qty) &&
            prev.price === seg.price &&
            prev.is_contact === seg.is_contact &&
            (prev.name || "") === (seg.name || "")
        ) {
            continue;
        }

        const canMerge =
            prev &&
            !isOpenEnd(prev.max_qty) &&
            seg.min_qty === prev.max_qty + 1 &&
            prev.price === seg.price &&
            prev.is_contact === seg.is_contact &&
            (prev.name || "") === (seg.name || "");

        if (canMerge) {
            prev.max_qty = seg.max_qty;
            prev._tier_key = `${prev.min_qty}-${prev.max_qty || 0}`;
            prev.is_special = prev.is_special || seg.is_special;
        } else {
            result.push(seg);
        }
    }

    return result;
}

function getMetaPriceValue() {
    const root = getProductRoot();
    const nodes = [
        root.querySelector("meta[itemprop='price']"),
        root.querySelector("[itemprop='price'][content]"),
    ].filter(Boolean);
    for (const node of nodes) {
        const value = parseMoney(node.getAttribute("content") || node.textContent || "");
        if (value !== null && value > 0) return value;
    }
    return 0;
}

function getDisplayedUnitPriceFallback() {
    const node = findUnitPriceNode();
    const direct = parseMoney(node?.textContent || node?.getAttribute("content") || "");
    if (direct !== null && direct > 0) return direct;

    const metaValue = getMetaPriceValue();
    if (metaValue > 0) return metaValue;

    const root = getProductRoot();
    const candidates = getVisibleMoneyNodes(root).filter((el) => !isInTotalBlock(el));
    candidates.sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        if (ra.top !== rb.top) return ra.top - rb.top;
        return ra.left - rb.left;
    });
    for (const el of candidates) {
        const value = parseMoney(el.textContent || el.getAttribute("content") || "");
        if (value !== null && value > 0) return value;
    }
    return 0;
}

function qtyLabelOfTier(t) {
    if (isOpenEnd(t.max_qty)) return `≥ ${t.min_qty}`;
    if (t.min_qty === t.max_qty) return `${t.min_qty}`;
    return `${t.min_qty} – ${t.max_qty}`;
}

function renderTiersTable(tiers) {
    const tbody = document.getElementById("oms_price_table");
    if (!tbody) {
        warn("#oms_price_table not found");
        return false;
    }

    tbody.innerHTML = (tiers || [])
        .map((t) => {
            const priceDisplay = typeof t.price === "number" ? `${formatVnd(t.price)} đ` : String(t.price);
            const display = t.is_contact ? "Giá liên hệ" : priceDisplay;
            return `
            <tr data-tier-key="${t._tier_key}">
                <td>${qtyLabelOfTier(t)}</td>
                <td>${escapeHtml(t.name || "")}</td>
                <td class="text-end">${display}</td>
            </tr>
        `;
        })
        .join("");

    return true;
}

function highlightTierRow(tier) {
    const tbody = document.getElementById("oms_price_table");
    if (!tbody) return;

    tbody.querySelectorAll("tr").forEach((row) => row.classList.remove("oms-tier-active"));
    if (!tier) return;

    const key = tier._tier_key || `${tier.min_qty}-${tier.max_qty || 0}`;
    const row = tbody.querySelector(`tr[data-tier-key="${key}"]`);
    if (row) row.classList.add("oms-tier-active");
}

function getOwnText(el) {
    if (!el) return "";
    let txt = "";
    for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) txt += node.textContent || "";
    }
    return txt.replace(/\s+/g, " ").trim().toLowerCase();
}

function uniqNodes(nodes) {
    return Array.from(new Set((nodes || []).filter(Boolean)));
}

function isInsideQtyTable(el) {
    // Only exclude OMS-specific tier tables — NOT all <table> elements.
    // Odoo may render "Tổng cộng" inside a generic <table>; blocking all tables
    // prevents findTotalPriceNode() from finding the amount node.
    return Boolean(el && el.closest(".oms-price-tiers, #oms_price_table"));
}

function hasTotalText(el) {
    const own = normalizeText(getOwnText(el));
    const full = normalizeText(el?.textContent || "");
    return (
        own.includes("tổng cộng") ||
        own.includes("tổng tiền") ||
        own.includes("tong cong") ||
        own.includes("tong tien") ||
        full.includes("tổng cộng") ||
        full.includes("tổng tiền") ||
        full.includes("tong cong") ||
        full.includes("tong tien")
    );
}

function isInTotalBlock(el) {
    if (!el) return false;
    let node = el;
    for (let i = 0; i < 5 && node; i += 1, node = node.parentElement) {
        if (hasTotalText(node)) return true;
    }
    return false;
}

function getVisibleMoneyNodes(scope) {
    const root = scope || getProductRoot();
    const out = [];

    root.querySelectorAll(".oe_currency_value, .oe_price, [itemprop='price']").forEach((node) => {
        if (isVisible(node) && !isInsideQtyTable(node)) out.push(node);
    });

    root.querySelectorAll("span, strong, b, a, div, p, h2, h3, h4").forEach((el) => {
        if (!isVisible(el) || isInsideQtyTable(el)) return;
        const own = getOwnText(el);
        if (!looksLikeMoneyText(own)) return;

        const childHasMoney = Array.from(el.children || []).some((child) => looksLikeMoneyText(child.textContent || ""));
        if (childHasMoney) return;
        out.push(el);
    });

    return uniqNodes(out);
}

function getCurrencySample(root) {
    const candidates = getVisibleMoneyNodes(root);
    for (const el of candidates) {
        if (isInsideQtyTable(el)) continue;
        const text = normalizeSpace(el.textContent || el.getAttribute("content") || "");
        if (text && /[₫đ]/i.test(text)) return text;
    }
    return "0 đ";
}


function findUnitPriceNode() {
    const root = getProductRoot();

    const candidates = getVisibleMoneyNodes(root)
        .filter((el) => !isInsideQtyTable(el))
        .filter((el) => !isInTotalBlock(el))
        .filter((el) => !hasTotalText(el))
        .filter((el) => parseMoney(el.textContent || el.getAttribute("content") || "") !== null)
        .sort((a, b) => {
            const aPreferred = a.matches?.(".oe_price, .oe_currency_value, [itemprop='price']") ? 0 : 1;
            const bPreferred = b.matches?.(".oe_price, .oe_currency_value, [itemprop='price']") ? 0 : 1;
            if (aPreferred !== bPreferred) return aPreferred - bPreferred;

            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();

            const aArea = ra.width * ra.height;
            const bArea = rb.width * rb.height;
            if (bArea !== aArea) return bArea - aArea;

            if (ra.top !== rb.top) return ra.top - rb.top;
            if (ra.left !== rb.left) return ra.left - rb.left;

            return 0;
        });

    return candidates[0] || null;
}

function getUnitSourceNodes() {
    const root = getProductRoot();
    const omsDetail = document.querySelector('.oms_product_detail');
    if (omsDetail && root && omsDetail.contains(root)) {
        return uniqNodes(Array.from(root.querySelectorAll(
            ".oms_pd_price_block .oe_price, .oms_pd_price_block .oe_currency_value, .oms_pd_price_block [itemprop='price'], .oms_pd_price_block meta[itemprop='price'], .oms_pd_price_block [data-price], .oms_pd_price_block [data-unit-price], .oms_pd_price_block [data-product-price], .oms_pd_price_block [data-list-price]"
        )).filter((node) => {
            if (!node) return false;
            if (isInsideQtyTable(node) || isInTotalBlock(node) || hasTotalText(node)) return false;
            if (node.tagName === "META") return true;
            return parseMoney(node.textContent || node.getAttribute("content") || "") !== null;
        }));
    }

    const nodes = [findUnitPriceNode()];

    nodes.push(
        ...root.querySelectorAll(
            ".oe_price, .oe_currency_value, [itemprop='price'], meta[itemprop='price'], [data-price], [data-unit-price], [data-product-price], [data-list-price], span, strong, b, a, div, p, h2, h3, h4"
        )
    );

    return uniqNodes(
        nodes.filter((node) => {
            if (!node) return false;
            if (isInsideQtyTable(node) || isInTotalBlock(node) || hasTotalText(node)) return false;
            if (node.tagName === "META") return true;
            return parseMoney(node.textContent || node.getAttribute("content") || "") !== null;
        })
    );
}

function findTotalLabelElement() {
    const root = getProductRoot();
    const els = Array.from(root.querySelectorAll("div, span, p, strong, label, td, th"));
    return els.find((el) => isVisible(el) && hasTotalText(el)) || null;
}

function getRect(el) {
    try {
        return el?.getBoundingClientRect?.() || null;
    } catch (_e) {
        return null;
    }
}

function rectArea(rect) {
    if (!rect) return 0;
    return Math.max(0, rect.width || 0) * Math.max(0, rect.height || 0);
}

function rectCenterY(rect) {
    return rect ? rect.top + rect.height / 2 : 0;
}

function isLikelySameRow(aRect, bRect) {
    if (!aRect || !bRect) return false;
    const overlap = Math.min(aRect.bottom, bRect.bottom) - Math.max(aRect.top, bRect.top);
    const minHeight = Math.max(1, Math.min(aRect.height || 0, bRect.height || 0));
    if (overlap >= minHeight * 0.35) return true;
    return Math.abs(rectCenterY(aRect) - rectCenterY(bRect)) <= Math.max(18, minHeight * 0.8);
}

function getMoneyNodesWithin(node) {
    if (!node) return [];
    return Array.from(node.querySelectorAll("a, span, strong, b, div, td, p, h1, h2, h3, h4, h5, h6"))
        .filter((el) => isVisible(el))
        .filter((el) => !isInsideQtyTable(el))
        .filter((el) => !hasTotalText(el))
        .filter((el) => parseMoney(el.textContent || el.getAttribute("content") || "") !== null);
}

function findSummaryRow(labelEl) {
    if (!labelEl) return null;
    const labelRect = getRect(labelEl);
    let node = labelEl;
    while (node && node !== document.body) {
        const moneyNodes = getMoneyNodesWithin(node);
        const sameRowNodes = moneyNodes.filter((el) => isLikelySameRow(labelRect, getRect(el)));
        if (sameRowNodes.length) return node;
        node = node.parentElement;
    }
    return labelEl.parentElement || null;
}

function scoreTotalCandidate(labelRect, rect) {
    if (!labelRect || !rect) return Number.POSITIVE_INFINITY;
    const sameRowPenalty = isLikelySameRow(labelRect, rect) ? 0 : 100000;
    const rightPenalty = rect.left >= labelRect.left ? 0 : 20000;
    const distanceX = Math.abs(rect.left - labelRect.right);
    const distanceY = Math.abs(rectCenterY(rect) - rectCenterY(labelRect));
    const areaBonus = Math.min(rectArea(rect), 50000) / 1000;
    return sameRowPenalty + rightPenalty + distanceX + distanceY * 4 - areaBonus;
}

function getGlobalTotalCandidates(labelEl) {
    if (!labelEl) return [];

    const root = getProductRoot();
    const labelRect = getRect(labelEl);
    const row = findSummaryRow(labelEl);
    const localRowCandidates = row
        ? getMoneyNodesWithin(row).filter((el) => !el.contains(labelEl) && !labelEl.contains(el))
        : [];

    const globalCandidates = getMoneyNodesWithin(root).filter((el) => {
        if (!el || el === labelEl || el.contains(labelEl) || labelEl.contains(el)) return false;
        const rect = getRect(el);
        if (!rect) return false;
        if (!labelRect) return true;

        const sameRow = isLikelySameRow(labelRect, rect);
        const closeBand = Math.abs(rectCenterY(rect) - rectCenterY(labelRect)) <= Math.max(28, labelRect.height * 1.5);
        const onRight = rect.left >= labelRect.left - 8;
        return (sameRow && onRight) || (closeBand && onRight);
    });

    return uniqNodes([...localRowCandidates, ...globalCandidates]).sort((a, b) => {
        const sa = scoreTotalCandidate(labelRect, getRect(a));
        const sb = scoreTotalCandidate(labelRect, getRect(b));
        if (sa !== sb) return sa - sb;

        const aPreferred = a.matches?.(".oe_currency_value, .oe_price, [data-total-price], [data-price-total]") ? 0 : 1;
        const bPreferred = b.matches?.(".oe_currency_value, .oe_price, [data-total-price], [data-price-total]") ? 0 : 1;
        if (aPreferred !== bPreferred) return aPreferred - bPreferred;

        return rectArea(getRect(b)) - rectArea(getRect(a));
    });
}

function findTotalPriceNode() {
    const labelEl = findTotalLabelElement();
    const candidates = getGlobalTotalCandidates(labelEl);
    return candidates[0] || null;
}

function getTotalSourceNodes() {
    const labelEl = findTotalLabelElement();
    const labelRect = getRect(labelEl);
    const candidates = getGlobalTotalCandidates(labelEl);
    const nodes = [];
    const best = candidates[0] || null;
    const bestScore = best ? scoreTotalCandidate(labelRect, getRect(best)) : Number.POSITIVE_INFINITY;

    if (best) {
        nodes.push(best);

        let current = best;
        for (let i = 0; i < 3 && current; i += 1, current = current.parentElement) {
            nodes.push(current);
            current.querySelectorAll?.(
                ".oe_currency_value, .oe_price, [data-total-price], [data-price-total], a, span, strong, b, div, td, p, h1, h2, h3, h4, h5, h6"
            ).forEach((el) => nodes.push(el));
        }
    }

    candidates.forEach((node) => {
        const score = scoreTotalCandidate(labelRect, getRect(node));
        if (score <= bestScore + 120) nodes.push(node);
    });

    const root = getProductRoot();
    root.querySelectorAll("[data-total-price], [data-price-total]").forEach((node) => nodes.push(node));

    return uniqNodes(
        nodes.filter((node) => {
            if (!node || isInsideQtyTable(node) || hasTotalText(node)) return false;
            if (node.tagName === "META") return true;
            return parseMoney(node.textContent || node.getAttribute("content") || "") !== null;
        })
    );
}

function setNodeMoney(node, value, sampleText) {
    if (!node) return;

    const rounded = Math.round(n(value, 0));
    const formatted = formatVnd(rounded);
    const original = normalizeSpace(node.textContent || "");
    const sample = sampleText || original || getCurrencySample(getProductRoot());
    const moneyWithSymbol = formatMoneyLikeSample(rounded, sample);

    if (node.tagName === "META") {
        node.setAttribute("content", String(rounded));
        return;
    }

    if (node.classList?.contains("oe_currency_value")) {
        node.textContent = formatted;
    } else {
        node.textContent = moneyWithSymbol;
    }

    node.setAttribute("data-oms-updated", "1");
}

function setAttrIfPresent(node, names, value) {
    if (!node) return;
    const val = String(Math.round(n(value, 0)));
    for (const name of names) {
        if (node.hasAttribute?.(name)) node.setAttribute(name, val);
        if (node.dataset && name.startsWith("data-")) {
            const key = name
                .slice(5)
                .split("-")
                .map((part, idx) => (idx === 0 ? part : part.charAt(0).toUpperCase() + part.slice(1)))
                .join("");
            if (key in node.dataset) node.dataset[key] = val;
        }
    }
}

function syncPriceSourceAttributes(unit, total) {
    const root = getProductRoot();
    const scope = getScopeRoot();
    const form = getProductForm();
    const labelEl = findTotalLabelElement();
    const summaryRow = findSummaryRow(labelEl);
    const summaryTotalEls = getSummaryTotalNodes();

    [root, scope, form].filter(Boolean).forEach((node) => {
        node.setAttribute("data-oms-unit-price", String(Math.round(unit)));
        node.setAttribute("data-oms-total-price", String(Math.round(total)));
    });

    if (summaryRow) {
        summaryRow.setAttribute("data-oms-total-price", String(Math.round(total)));
        setAttrIfPresent(summaryRow, ["data-total-price", "data-price-total"], total);
    }

    scope.querySelectorAll("meta[itemprop='price'], [itemprop='price'][content]").forEach((node) => {
        node.setAttribute("content", String(Math.round(unit)));
    });

    scope.querySelectorAll("[data-price], [data-unit-price], [data-product-price], [data-list-price]").forEach((node) => {
        if (isInsideQtyTable(node)) return;

        if (isInTotalBlock(node) || node.id === "summary_total") {
            setAttrIfPresent(node, ["data-price", "data-unit-price"], unit);
            setAttrIfPresent(node, ["data-total-price", "data-price-total"], total);
            return;
        }

        setAttrIfPresent(node, ["data-price", "data-unit-price", "data-product-price", "data-list-price"], unit);
    });

    scope.querySelectorAll("[data-total-price], [data-price-total]").forEach((node) => {
        if (isInsideQtyTable(node)) return;
        setAttrIfPresent(node, ["data-total-price", "data-price-total"], total);
    });

    summaryTotalEls.forEach((node) => {
        setAttrIfPresent(node, ["data-price", "data-unit-price"], unit);
        setAttrIfPresent(node, ["data-total-price", "data-price-total"], total);
        setNodeMoney(node, total, getCurrencySample(scope));
    });
}

function setDisplayedAmounts(unitPrice, qty) {
    if (typeof unitPrice !== "number" || !Number.isFinite(unitPrice) || unitPrice <= 0) {
        log("setDisplayedAmounts skipped - invalid price", { unitPrice });
        return;
    }

    const unit = Math.round(n(unitPrice, 0));
    const total = unit * Math.max(1, n(qty, 1));
    const root = getProductRoot();
    const sample = getCurrencySample(root);
    const unitNodes = getUnitSourceNodes().filter((node) => !hasTotalText(node) && node.id !== "summary_total");
    const directSummaryNodes = getSummaryTotalNodes();
    const totalNodes = getTotalSourceNodes().filter((node) => !unitNodes.includes(node));

    state.ownMutationBlockedUntil = Date.now() + OWN_MUTATION_BLOCK_MS;
    state.applying = true;
    try {
        unitNodes.forEach((node) => setNodeMoney(node, unit, sample));
        totalNodes.forEach((node) => setNodeMoney(node, total, sample));
        directSummaryNodes.forEach((node) => setNodeMoney(node, total, sample));
        syncPriceSourceAttributes(unit, total);
    } finally {
        state.applying = false;
    }

    log("setDisplayedAmounts", { unit, qty, total, unitNodes, totalNodes, directSummaryNodes, totalNode: findTotalPriceNode(), totalLabel: findTotalLabelElement() });
}

async function loadTiers(productId, templateId = 0) {
    let rawTiers = [];
    try {
        rawTiers = await rpc("/oms/price_tiers", {
            product_id: productId,
            product_template_id: templateId,
        });
    } catch (e) {
        err("RPC /oms/price_tiers failed", e);
        return [];
    }

    const tiers = buildEffectiveTiers(rawTiers);
    log("effective tiers", { rawTiers, tiers });
    return tiers;
}

function applyPrice(force = false) {
    if (!state.tiers.length) return;

    const qty = parseQtyFromInput();
    const tier = chooseRuleForQty(state.tiers, qty);
    if (!tier) {
        highlightTierRow(null);
        return;
    }

    if (tier.is_contact) {
        highlightTierRow(tier);
        state.lastSignature = `${qty}|${tier._tier_key}|contact`;
        return;
    }

    if (typeof tier.price !== "number" || !Number.isFinite(tier.price) || tier.price <= 0) {
        warn("applyPrice skipped - tier price invalid", { qty, tier });
        highlightTierRow(tier);
        return;
    }

    const signature = `${qty}|${tier._tier_key}|${tier.price}`;
    if (!force && state.lastSignature === signature) return;

    setDisplayedAmounts(tier.price, qty);
    highlightTierRow(tier);
    state.lastSignature = signature;

    log("applyPrice", { qty, tier, signature });
}

function clearApplySeriesTimers() {
    state.applySeriesTimers.forEach((id) => clearTimeout(id));
    state.applySeriesTimers = [];
}

function scheduleApply(force = true, delay = APPLY_DEBOUNCE_MS) {
    clearTimeout(state.applyTimer);
    state.applyTimer = setTimeout(() => applyPrice(force), delay);
}

function scheduleBootReload(delay = MUTATION_REAPPLY_MS) {
    clearTimeout(state.reloadTimer);
    state.reloadTimer = setTimeout(() => bootOnce(true), delay);
}

function scheduleApplySeries(force = true, delays = APPLY_SERIES_DELAYS) {
    clearApplySeriesTimers();
    delays.forEach((delay) => {
        const timer = setTimeout(() => applyPrice(force), delay);
        state.applySeriesTimers.push(timer);
    });
}

function bindQtyEvents() {
    const qtyEl = getQtyInput();
    if (qtyEl && qtyEl !== state.boundQtyEl) {
        qtyEl.addEventListener("input", () => {
            state.lastSignature = null;
            scheduleApplySeries(true, [0, 80, 180, 380, 800]);
        });
        qtyEl.addEventListener("change", () => {
            state.lastSignature = null;
            scheduleApplySeries(true);
        });
        qtyEl.addEventListener("keyup", () => {
            state.lastSignature = null;
            scheduleApplySeries(true, [120, 320, 700]);
        });
        qtyEl.addEventListener("blur", () => {
            state.lastSignature = null;
            scheduleApplySeries(true, [0, 160, 500]);
        });
        state.boundQtyEl = qtyEl;
    }

    if (!state.docBound) {
        document.addEventListener(
            "click",
            (ev) => {
                const btn = ev.target.closest("button, a");
                if (!btn) return;
                const txt = normalizeSpace(btn.textContent || "");
                const cls = btn.className || "";
                const looksLikeQtyButton =
                    txt === "+" ||
                    txt === "-" ||
                    /js_add|js_subtract|quantity|spinner|add_qty|sub_qty/i.test(cls);

                if (looksLikeQtyButton) {
                    state.lastSignature = null;
                    setTimeout(() => scheduleApplySeries(true), 0);
                }
            },
            true
        );

        document.addEventListener("variant_changed", () => {
            state.lastSignature = null;
            scheduleBootReload(120);
        });

        document.addEventListener(
            "change",
            (ev) => {
                if (ev.target.closest("form.js_main_product")) {
                    state.lastSignature = null;
                    scheduleBootReload(100);
                }
            },
            true
        );

        state.docBound = true;
    }
}

function bindMutationObserver() {
    if (state.observerBound) return;
    state.observerBound = true;

    state.observer = new MutationObserver(() => {
        if (Date.now() < state.ownMutationBlockedUntil) return;
        clearTimeout(state.mutationTimer);
        state.mutationTimer = setTimeout(() => {
            replaceContactPriceOnShopCards();

            const wrap = getWrap();
            const { productId, templateId } = getCurrentProductInfo(wrap);
            if (
                (productId && productId !== state.productId) ||
                (templateId && templateId !== state.templateId)
            ) {
                state.lastSignature = null;
                scheduleBootReload(20);
                return;
            }

            state.lastSignature = null;
            scheduleApplySeries(true, [40, 160, 420, 900]);
        }, MUTATION_REAPPLY_MS);
    });

    state.observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
    });
}

async function bootOnce(forceReload = false) {
    scanShopPrices();

    const wrap = getWrap();
    if (!wrap) {
        warn(".oms-price-tiers not found");
        return false;
    }

    ensureHighlightStyle();

    const { productId, templateId } = getCurrentProductInfo(wrap);
    if (!productId) {
        warn("Invalid product id");
        return false;
    }

    if (state.productId !== productId || state.templateId !== templateId) {
        state.initialBasePrice = 0;
    }
    if (!state.initialBasePrice) {
        state.initialBasePrice = getStoredShopPrice(productId) || getDisplayedUnitPriceFallback() || getMetaPriceValue() || 0;
    }

    const tbody = document.getElementById("oms_price_table");
    if (!tbody) {
        warn("#oms_price_table missing");
        return false;
    }

    const needReload =
        forceReload ||
        state.productId !== productId ||
        state.templateId !== templateId ||
        !state.tiers.length ||
        !tbody.children.length;

    if (needReload) {
        state.productId = productId;
        state.templateId = templateId;
        state.tiers = await loadTiers(productId, templateId);
        state.lastSignature = null;
        renderTiersTable(state.tiers);
    }

    bindQtyEvents();
    bindMutationObserver();
    scheduleApplySeries(true, [80, 200, 500, 1000]);

    return true;
}

function retryBoot(attempt = 0) {
    bootOnce(false)
        .then((ok) => {
            if (ok) return;
            if (attempt >= RETRY_BOOT_MAX) return;
            setTimeout(() => retryBoot(attempt + 1), RETRY_BOOT_DELAY);
        })
        .catch((e) => {
            err("bootOnce error", e);
            if (attempt >= RETRY_BOOT_MAX) return;
            setTimeout(() => retryBoot(attempt + 1), RETRY_BOOT_DELAY);
        });
}

function start() {
    bindShopTracking();
    scanShopPrices();
    replaceContactPriceOnShopCards();
    retryBoot(0);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
