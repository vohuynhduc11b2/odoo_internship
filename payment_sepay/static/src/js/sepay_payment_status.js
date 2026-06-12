/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const POLL_SECONDS = 3;

function setBadge(state) {
    const badge = document.getElementById("sepay_state_badge");
    if (!badge) return;

    // Reset classes
    badge.classList.remove("bg-secondary", "bg-success", "bg-danger", "bg-warning", "bg-info");
    badge.dataset.state = state;

    // Friendly label + color
    let label = state;
    let cls = "bg-secondary";

    if (["draft", "pending", "authorized"].includes(state)) {
        label = "pending";
        cls = "bg-warning";
    } else if (["done"].includes(state)) {
        label = "done";
        cls = "bg-success";
    } else if (["cancel", "error"].includes(state)) {
        label = state;
        cls = "bg-danger";
    } else {
        cls = "bg-secondary";
    }

    badge.classList.add(cls);
    badge.textContent = label;
}

function showWaiting(isWaiting) {
    const hint = document.getElementById("sepay_waiting_hint");
    const spinner = document.getElementById("sepay_spinner");
    if (hint) hint.classList.toggle("d-none", !isWaiting);
    if (spinner) spinner.classList.toggle("d-none", !isWaiting);
}

function showDone() {
    document.getElementById("sepay_done_box")?.classList.remove("d-none");
    document.getElementById("sepay_cancel_box")?.classList.add("d-none");
    showWaiting(false);
}

function showFail() {
    document.getElementById("sepay_cancel_box")?.classList.remove("d-none");
    document.getElementById("sepay_done_box")?.classList.add("d-none");
    showWaiting(false);
}

async function checkStatus(reference) {
    const res = await rpc("/payment/sepay/status", { reference });
    return (res && res.state) ? res.state : "not_found";
}

function startCountdown(reference) {
    const countdownEl = document.getElementById("sepay_countdown");
    let left = POLL_SECONDS;

    const tick = async () => {
        if (!countdownEl) return;

        countdownEl.textContent = String(left);
        left -= 1;

        if (left < 0) {
            left = POLL_SECONDS;

            try {
                const state = await checkStatus(reference);
                setBadge(state);

                if (state === "done") {
                    showDone();
                    // Delay nhẹ để user thấy message
                    setTimeout(() => {
                        window.location.href = "/shop/confirmation";
                    }, 800);
                    return; // stop
                }

                if (state === "cancel" || state === "error" || state === "not_found") {
                    showFail();
                    // vẫn cho tiếp tục polling nếu muốn, nhưng thường nên dừng
                    // return;
                } else {
                    showWaiting(true);
                }
            } catch (e) {
                // Nếu RPC lỗi, vẫn tiếp tục countdown/poll lại
            }
        }

        setTimeout(tick, 1000);
    };

    tick();
}

function setupButtons(reference) {
    // Refresh now
    const refreshBtn = document.getElementById("sepay_refresh_btn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", async () => {
            try {
                const state = await checkStatus(reference);
                setBadge(state);
                if (state === "done") {
                    showDone();
                    setTimeout(() => (window.location.href = "/shop/confirmation"), 800);
                } else if (state === "cancel" || state === "error") {
                    showFail();
                } else {
                    showWaiting(true);
                }
            } catch (e) {}
        });
    }

    // Copy reference
    const copyBtn = document.getElementById("sepay_copy_ref_btn");
    if (copyBtn) {
        copyBtn.addEventListener("click", async () => {
            const text = document.getElementById("sepay_ref_text")?.textContent?.trim() || reference;
            try {
                await navigator.clipboard.writeText(text);
                copyBtn.textContent = "Đã copy";
                setTimeout(() => (copyBtn.textContent = "Copy"), 1000);
            } catch (e) {}
        });
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const reference = document.getElementById("sepay_reference")?.value;
    const initState = document.getElementById("sepay_initial_state")?.value || "pending";
    if (!reference) return;

    setBadge(initState);

    if (initState === "done") {
        showDone();
        setTimeout(() => (window.location.href = "/shop/confirmation"), 800);
        return;
    }

    showWaiting(true);
    setupButtons(reference);
    startCountdown(reference);
});
