/** @odoo-module **/

function buildHtml() {
    return `
    <div class="mt-3 p-3 border rounded" id="oms_partial_payment_box">
        <div class="fw-bold mb-2">Hình thức thanh toán</div>

        <label class="form-check">
            <input class="form-check-input" type="radio" name="payment_mode" value="full" checked="checked"/>
            Thanh toán toàn bộ
        </label>

        <label class="form-check mt-2">
            <input class="form-check-input" type="radio" name="payment_mode" value="partial"/>
            Thanh toán một phần
        </label>

        <div id="partial_amount_box" class="mt-2 d-none">
            <input type="number" class="form-control" name="partial_amount"
                   placeholder="Nhập số tiền muốn thanh toán"/>
            <div class="form-text">Số tiền này sẽ dùng để tạo giao dịch thanh toán.</div>
        </div>
    </div>`;
}

function ensureInserted() {
    // Tránh chèn lặp
    if (document.getElementById("oms_partial_payment_box")) return;

    // Cố gắng tìm form chứa nút Pay now
    const payBtn =
        document.querySelector("button[type='submit'].btn-primary") ||
        document.querySelector("button:contains('Pay now')"); // selector này không chuẩn CSS, nên chỉ là fallback ý tưởng

    // Selector chắc hơn: tìm nút submit trong cột phải (Order summary)
    const submitBtn =
        document.querySelector("form button[type='submit']") ||
        document.querySelector("form .o_wsale_payment_submit button") ||
        document.querySelector("form button[name='o_payment_submit']");

    const btn = submitBtn || payBtn;
    if (!btn) return;

    const form = btn.closest("form");
    if (!form) return;

    // Chèn block trước nút Pay now để đảm bảo input nằm trong form và được submit  
    const wrapper = document.createElement("div");
    wrapper.innerHTML = buildHtml();
    form.insertBefore(wrapper.firstElementChild, btn);

    // Toggle input
    form.addEventListener("change", (e) => {
        if (e.target && e.target.name === "payment_mode") {
            const box = document.getElementById("partial_amount_box");
            if (!box) return;
            box.classList.toggle("d-none", e.target.value !== "partial");
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    ensureInserted();
    // Với Odoo assets lazy / DOM render sau: dùng thêm timer ngắn
    setTimeout(ensureInserted, 300);
    setTimeout(ensureInserted, 1000);
});
