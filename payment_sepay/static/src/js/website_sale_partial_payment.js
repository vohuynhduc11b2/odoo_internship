/** @odoo-module **/

document.addEventListener("change", function (e) {
    if (e.target && e.target.name === "payment_mode") {
        const box = document.getElementById("partial_amount_box");
        if (!box) return;
        box.classList.toggle("d-none", e.target.value !== "partial");
    }
});
