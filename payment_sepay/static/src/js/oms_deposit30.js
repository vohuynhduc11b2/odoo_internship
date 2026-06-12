/** @odoo-module **/

import { PaymentForm } from "@payment/js/payment_form";

const KEY_MODE = "oms_payment_mode";
const KEY_DEPOSIT_PERCENT = "uc_deposit_percent";

// ---------- helpers ----------
function ensureHidden(name, defaultValue) {
    const form = document.querySelector("#o_payment_form");
    if (!form) return null;

    let el = form.querySelector(`input[name='${name}']`);
    if (!el) {
        el = document.createElement("input");
        el.type = "hidden";
        el.name = name;
        el.value = defaultValue ?? "";
        form.appendChild(el);
    }
    return el;
}

async function jsonRpc(url, params) {
    const payload = {
        jsonrpc: "2.0",
        method: "call",
        params: params || {},
        id: Date.now(),
    };
    const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        credentials: "same-origin",
    });
    const data = await resp.json();
    if (data.error) throw data.error;
    return data.result;
}

function getMode() {
    const el = document.querySelector("input[name='oms_payment_mode']:checked");
    return el ? (el.value || "").trim().toLowerCase() : "full";
}

function getDepositPercent() {
    // nếu bạn có input UI thì dùng id/name này; không có thì default 30
    const el =
        document.querySelector("#uc_deposit_percent") ||
        document.querySelector("input[name='uc_deposit_percent']");
    let v = el ? el.value : localStorage.getItem(KEY_DEPOSIT_PERCENT);
    v = (v ?? "").toString().trim();
    const f = parseFloat(v);
    return Number.isFinite(f) && f > 0 ? f : 30.0;
}

function resolveSePayMode(mode) {
    if (mode === "unc") return "unc";
    if (mode === "credit") return "credit";
    return "qr";
}

function applyHiddenState(mode, percent) {
    mode = (mode || "full").trim().toLowerCase();
    percent = Number.isFinite(percent) ? percent : 30.0;

    // these are what server side will read
    const hType = ensureHidden("website_payment_type", "");
    if (hType) hType.value = mode;

    const hSePay = ensureHidden("sepay_mode", "qr");
    if (hSePay) hSePay.value = resolveSePayMode(mode);

    const hIsDep = ensureHidden("uc_is_deposit", "0");
    if (hIsDep) hIsDep.value = mode === "deposit" ? "1" : "0";

    const hPercent = ensureHidden("uc_deposit_percent", "30");
    if (hPercent) hPercent.value = String(percent);
}

async function pushModeToServer(mode, percent) {
    try {
        await jsonRpc("/payment/sepay/set_mode", {
            mode,
            deposit: mode === "deposit" ? 1 : 0,
            percent,
        });
    } catch (e) {
        // không block UI, nhưng log để debug
        console.warn("[SePay] set_mode failed", e);
    }
}

// ---------- patch PaymentForm ----------
function injectExtraParams(params) {
    params = params || {};
    const mode = getMode() || (localStorage.getItem(KEY_MODE) || "full");
    const percent = getDepositPercent();

    params.website_payment_type = mode;
    params.sepay_mode = resolveSePayMode(mode);
    params.uc_is_deposit = mode === "deposit" ? 1 : 0;
    params.uc_deposit_percent = percent;

    return params;
}

function patchPaymentForm() {
    const proto = PaymentForm && PaymentForm.prototype;
    if (!proto) return;

    const candidates = [
        "_prepareTransactionRouteParams",
        "_prepareTransactionParams",
        "_getTransactionRouteParams",
        "_getTransactionParams",
    ];

    for (const name of candidates) {
        if (typeof proto[name] === "function") {
            const _super = proto[name];
            proto[name] = function (...args) {
                const params = _super.apply(this, args) || {};
                return injectExtraParams(params);
            };
            return;
        }
    }
}

// ---------- init ----------
function init() {
    // restore
    const savedMode = (localStorage.getItem(KEY_MODE) || "").trim().toLowerCase();
    const mode = savedMode || getMode() || "full";

    const savedPercent = parseFloat(localStorage.getItem(KEY_DEPOSIT_PERCENT) || "");
    const percent = Number.isFinite(savedPercent) ? savedPercent : getDepositPercent();

    // tick radio if needed
    const radio = document.querySelector(`input[name='oms_payment_mode'][value='${mode}']`);
    if (radio) radio.checked = true;

    applyHiddenState(mode, percent);
    pushModeToServer(mode, percent);

    // listeners
    document.addEventListener("change", (e) => {
        if (e.target && e.target.name === "oms_payment_mode") {
            const m = (e.target.value || "").trim().toLowerCase();
            localStorage.setItem(KEY_MODE, m);

            const p = getDepositPercent();
            applyHiddenState(m, p);
            pushModeToServer(m, p);
        }

        if (e.target && (e.target.id === "uc_deposit_percent" || e.target.name === "uc_deposit_percent")) {
            const p = getDepositPercent();
            localStorage.setItem(KEY_DEPOSIT_PERCENT, String(p));

            const m = getMode();
            applyHiddenState(m, p);
            pushModeToServer(m, p);
        }
    });

    patchPaymentForm();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
