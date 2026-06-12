/** @odoo-module **/

const TAG = "[OMS_PAYMENT_MODE]";

function qs(sel) { return document.querySelector(sel); }
function qsa(sel) { return Array.from(document.querySelectorAll(sel)); }

function getMode() {
  const el = qs('input[name="oms_payment_mode"]:checked');
  return el ? el.value : "full";
}

function setVisible(el, show) {
  if (!el) return;
  el.classList.toggle("d-none", !show);
}

function toggleUI() {
  const mode = getMode();

  const paymentMethod = qs("#payment_method");  // khu payment gateway (SePay nằm trong đây)
  const uncBox = qs("#o_ws_unc_box");
  const creditBox = qs("#o_ws_credit_box");
  const payBtn = qs('button[name="o_payment_submit_button"], button[type="submit"].o_payment_submit, .o_payment_submit_button');

  // Default: full/deposit => show payment method
  const isSePayMode = (mode === "full" || mode === "deposit");
  setVisible(paymentMethod, isSePayMode);

  // UNC => show upload
  setVisible(uncBox, mode === "unc");

  // Credit => show checkbox
  setVisible(creditBox, mode === "credit");

  // Nếu credit mà chưa tick confirm => disable pay now
  if (payBtn) {
    if (mode === "credit") {
      const ok = qs("#oms_credit_confirm")?.checked;
      payBtn.disabled = !ok;
    } else {
      payBtn.disabled = false;
    }
  }

  console.log(TAG, "mode=", mode);
}

function injectHiddenModeIntoPaymentForm() {
  // Khi bấm Pay now, form của payment sẽ submit.
  // Ta gắn hidden input vào form để controller nhận mode.
  const form = qs("form.oe_website_sale, form#o_payment_form, form[action*='/shop/payment']");
  if (!form) return;

  const mode = getMode();
  let hidden = qs('input[type="hidden"][name="oms_payment_mode_hidden"]');
  if (!hidden) {
    hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "oms_payment_mode_hidden";
    form.appendChild(hidden);
  }
  hidden.value = mode;

  let hidden2 = qs('input[type="hidden"][name="website_payment_type"]');
  if (!hidden2) {
    hidden2 = document.createElement("input");
    hidden2.type = "hidden";
    hidden2.name = "website_payment_type";
    form.appendChild(hidden2);
  }
  hidden2.value = mode;

  let hidden3 = qs('input[type="hidden"][name="sepay_mode"]');
  if (!hidden3) {
    hidden3 = document.createElement("input");
    hidden3.type = "hidden";
    hidden3.name = "sepay_mode";
    form.appendChild(hidden3);
  }
  hidden3.value = mode === "unc" ? "unc" : mode === "credit" ? "credit" : "qr";
}

function setup() {
  const radios = qsa('input[name="oms_payment_mode"]');
  if (!radios.length) return;

  radios.forEach(r => r.addEventListener("change", () => {
    toggleUI();
    injectHiddenModeIntoPaymentForm();
  }));

  const creditCb = qs("#oms_credit_confirm");
  if (creditCb) {
    creditCb.addEventListener("change", () => {
      toggleUI();
      injectHiddenModeIntoPaymentForm();
    });
  }

  // init
  toggleUI();
  injectHiddenModeIntoPaymentForm();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", setup);
} else {
  setup();
}
