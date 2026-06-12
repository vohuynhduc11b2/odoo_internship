/** @odoo-module **/

function toggleUNCBox() {
    const checked = document.querySelector('input[name="oms_payment_mode"]:checked');
    const box = document.getElementById("o_ws_unc_box");
    if (!box) return;

    const mode = ((checked && checked.value) || "").trim().toLowerCase();
    if (mode === "unc" || mode === "uy_nhiem_chi") {
        box.classList.remove("d-none");
    } else {
        box.classList.add("d-none");
    }
}

document.addEventListener("change", (ev) => {
    if (ev.target.matches('input[name="oms_payment_mode"]')) {
        toggleUNCBox();
    }
});

document.addEventListener("DOMContentLoaded", () => {
    toggleUNCBox();
});