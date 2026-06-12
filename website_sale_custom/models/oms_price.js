/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const TAG = "[OMS_PRICE]";
const DEBUG = true;
const RETRY_BOOT_DELAY = 250;
const RETRY_BOOT_MAX = 20;
const APPLY_DEBOUNCE_MS = 140;
const MUTATION_REAPPLY_MS = 180;
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
    ".text-primary",
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
    mutationTimer: null,
    applying: false,
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
        (form && form.querySelector('input.product_id[name="product_id"]'));
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
    document.querySelectorAll(SHOP_LINK_SELECTORS).forEach((link) => {
        const card = link.closest(".oe_product_cart, .o_wsale_products_item, .oe_product, .card, article, div");
        rememberShopPriceFromCard(card || link.parentElement, link);
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
        null
    );
}

function getProductRoot() {
    return (
        getProductForm() ||
        document.querySelector("#product_detail") ||
        document.querySelector("#product_details") ||
        document.querySelector(".oe_website_sale") ||
        document
    );
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
        
        // Try to parse as number, keep original if not numeric
        const numPrice = n(price, null);
        if (numPrice !== null) {
            price = numPrice;
        }
        
        // Skip if price is numeric and <= 0
        if (typeof price === 'number' && price <= 0) continue;
        // Skip if price is empty string
        if (typeof price === 'string' && !price.trim()) continue;
        
        if (!isOpenEnd(max) && max < min) continue;

        const key = `${min}|${max}|${price}`;
        if (seen.has(key)) continue;
        seen.add(key);

        out.push({
            ...t,
            min_qty: min,
            max_qty: max,
            price,
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

        // Only compare prices if both are numeric
        if (typeof a.price === 'number' && typeof b.price === 'number') {
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
            is_special: !!rule.is_special,
            _tier_key: `${start}-${end || 0}`,
        };

        const prev = result[result.length - 1];
        if (
            prev &&
            isOpenEnd(prev.max_qty) &&
            prev.price === seg.price &&
            (prev.name || "") === (seg.name || "")
        ) {
            continue;
        }

        const canMerge =
            prev &&
            !isOpenEnd(prev.max_qty) &&
            seg.min_qty === prev.max_qty + 1 &&
            prev.price === seg.price &&
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

function injectStoredBasePrice(tiers, productId) {
    const basePrice = getStoredShopPrice(productId);
    if (!basePrice || !Array.isArray(tiers) || !tiers.length) {
        return tiers;
    }

    const cloned = tiers.map((t) => ({ ...t }));
    const firstTier = chooseRuleForQty(cloned, 1) || cloned.slice().sort((a, b) => a.min_qty - b.min_qty)[0];
    if (!firstTier) return cloned;

    firstTier.price = basePrice;
    firstTier._tier_key = `${firstTier.min_qty}-${firstTier.max_qty || 0}`;
    log("injectStoredBasePrice", { productId, basePrice, firstTier });
    return cloned;
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
        .map(
            (t) => {
                const priceDisplay = typeof t.price === 'number' ? `${formatVnd(t.price)} đ` : String(t.price);
                return `
            <tr data-tier-key="${t._tier_key}">
                <td>${qtyLabelOfTier(t)}</td>
                <td>${escapeHtml(t.name || "")}</td>
                <td class="text-end">${priceDisplay}</td>
            </tr>
        `;
            }
        )
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
    return Boolean(el && el.closest(".oms-price-tiers, #oms_price_table, table"));
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
    for (let i = 0; i < 4 && node; i += 1, node = node.parentElement) {
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

function findTotalLabelElement() {
    const root = getProductRoot();
    const els = Array.from(root.querySelectorAll("div, span, p, strong, label, td, th"));
    return els.find((el) => isVisible(el) && hasTotalText(el)) || null;
}

function findSummaryRow(labelEl) {
    if (!labelEl) return null;
    let node = labelEl;
    while (node && node !== document.body) {
        const moneyNodes = Array.from(node.querySelectorAll("span, strong, div, td, p"))
            .filter((el) => isVisible(el))
            .filter((el) => !isInsideQtyTable(el))
            .filter((el) => parseMoney(el.textContent || "") !== null);
        if (moneyNodes.length) return node;
        node = node.parentElement;
    }
    return labelEl.parentElement || null;
}

function findTotalPriceNode() {
    const labelEl = findTotalLabelElement();
    const row = findSummaryRow(labelEl);
    if (!row) return null;

    const rowCandidates = Array.from(row.querySelectorAll("span, strong, b, div, td, p"))
        .filter((el) => isVisible(el))
        .filter((el) => !isInsideQtyTable(el))
        .filter((el) => !hasTotalText(el))
        .filter((el) => parseMoney(el.textContent || "") !== null)
        .sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            if (rb.left !== ra.left) return rb.left - ra.left;
            return (rb.width * rb.height) - (ra.width * ra.height);
        });

    return rowCandidates[0] || null;
}

function findUnitPriceNode() {
    const root = getProductRoot();
    const totalNode = findTotalPriceNode();
    const all = getVisibleMoneyNodes(root).filter((node) => node !== totalNode);
    const candidates = all.filter((node) => !isInTotalBlock(node));
    const pool = candidates.length ? candidates : all;
    if (!pool.length) return null;

    pool.sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        if (ra.top !== rb.top) return ra.top - rb.top;
        if (ra.left !== rb.left) return ra.left - rb.left;
        return (rb.width * rb.height) - (ra.width * ra.height);
    });

    return pool[0];
}

function setNodeMoney(node, value, sampleText) {
    if (!node) return;

    const formatted = formatVnd(value);
    const original = normalizeSpace(node.textContent || "");
    const sample = sampleText || original || getCurrencySample(getProductRoot());
    const moneyWithSymbol = formatMoneyLikeSample(value, sample);

    if (node.tagName === "META") {
        node.setAttribute("content", String(Math.round(value || 0)));
        return;
    }

    if (node.classList?.contains("oe_currency_value")) {
        node.textContent = formatted;
    } else if (looksLikeMoneyText(original) || parseMoney(original) !== null) {
        node.textContent = moneyWithSymbol;
    } else {
        node.textContent = moneyWithSymbol;
    }

    node.setAttribute("data-oms-updated", "1");
}

function setDisplayedAmounts(unitPrice, qty) {
    // Only update if price is numeric
    if (typeof unitPrice !== 'number') {
        log("setDisplayedAmounts skipped - non-numeric price", { unitPrice });
        return;
    }
    
    const unit = n(unitPrice, 0);
    const total = unit * Math.max(1, n(qty, 1));
    const root = getProductRoot();
    const sample = getCurrencySample(root);
    const unitNode = findUnitPriceNode();
    const totalNode = findTotalPriceNode();

    state.applying = true;
    try {
        if (unitNode) {
            setNodeMoney(unitNode, unit, sample);
        } else {
            warn("Unit price node not found");
        }

        if (totalNode && totalNode !== unitNode) {
            setNodeMoney(totalNode, total, sample);
        } else {
            warn("Total price node not found");
        }
    } finally {
        state.applying = false;
    }

    log("setDisplayedAmounts", { unit, qty, total, unitNode, totalNode });
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
    log("effective tiers", tiers);
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

    const signature = `${qty}|${tier._tier_key}|${tier.price}`;
    if (!force && state.lastSignature === signature) return;

    setDisplayedAmounts(tier.price, qty);
    highlightTierRow(tier);
    state.lastSignature = signature;

    log("applyPrice", { qty, tier, signature });
}

function scheduleApply(force = true, delay = APPLY_DEBOUNCE_MS) {
    clearTimeout(state.applyTimer);
    state.applyTimer = setTimeout(() => applyPrice(force), delay);
}

function scheduleBootReload(delay = MUTATION_REAPPLY_MS) {
    clearTimeout(state.reloadTimer);
    state.reloadTimer = setTimeout(() => bootOnce(true), delay);
}

function bindQtyEvents() {
    const qtyEl = getQtyInput();
    if (qtyEl && qtyEl !== state.boundQtyEl) {
        qtyEl.addEventListener("input", () => scheduleApply(true, 120));
        qtyEl.addEventListener("change", () => scheduleApply(true, 120));
        qtyEl.addEventListener("keyup", () => scheduleApply(true, 120));
        qtyEl.addEventListener("blur", () => scheduleApply(true, 120));
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
                    setTimeout(() => scheduleApply(true, 160), 0);
                }
            },
            true
        );

        document.addEventListener("variant_changed", () => {
            state.lastSignature = null;
            scheduleBootReload(180);
        });

        document.addEventListener(
            "change",
            (ev) => {
                if (ev.target.closest("form.js_main_product")) {
                    state.lastSignature = null;
                    scheduleBootReload(160);
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
        if (state.applying) return;
        clearTimeout(state.mutationTimer);
        state.mutationTimer = setTimeout(() => {
            const wrap = getWrap();
            const { productId, templateId } = getCurrentProductInfo(wrap);
            if (
                (productId && productId !== state.productId) ||
                (templateId && templateId !== state.templateId)
            ) {
                scheduleBootReload(20);
                return;
            }
            scheduleApply(true, 80);
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
    scheduleApply(true, 100);

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
    retryBoot(0);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
