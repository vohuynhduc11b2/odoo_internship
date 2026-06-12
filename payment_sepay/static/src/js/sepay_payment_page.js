/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

function $(sel) {
    return document.querySelector(sel);
}

function setBadge(state) {
    const el = $("#sepay_state_badge");
    if (!el) return;
    el.textContent = state || "unknown";
    el.classList.remove("bg-secondary", "bg-success", "bg-warning", "bg-danger");
    if (state === "done") {
        el.classList.add("bg-success");
    } else if (["cancel", "canceled", "error"].includes(state)) {
        el.classList.add("bg-danger");
    } else {
        el.classList.add("bg-secondary");
    }
}

async function poll(reference, accessToken) {
    try {
        const res = await rpc("/payment/sepay/status", {
            reference,
            access_token: accessToken || "",
        });
        if (!res || !res.ok) return;

        const state = res.state;
        setBadge(state);

        if (state === "done") {
            const doneBox = $("#sepay_done_box");
            if (doneBox) doneBox.classList.remove("d-none");
            // chuyển về confirmation
            window.location.href = "/shop/confirmation";
        }
        if (state === "cancel" || state === "canceled" || state === "error") {
            const cancelBox = $("#sepay_cancel_box");
            if (cancelBox) cancelBox.classList.remove("d-none");
        }
    } catch (e) {
        // im lặng để tránh spam console
    }
}

function boot() {
    const refEl = $("#sepay_reference");
    if (!refEl) return;
    const reference = refEl.value;
    const accessToken = ($("#sepay_access_token") || {}).value || "";

    // poll mỗi 3s
    poll(reference, accessToken);
    setInterval(() => poll(reference, accessToken), 3000);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
} else {
    boot();
}
