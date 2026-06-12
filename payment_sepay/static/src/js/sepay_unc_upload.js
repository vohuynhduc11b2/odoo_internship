/** @odoo-module **/

const TAG = "[SePay][UNC]";

let uploading = false;
let uploaded = false;
let uploadedSignature = "";

function qs(selector, root = document) {
    return root.querySelector(selector);
}

function getMode() {
    const checked = qs('input[name="oms_payment_mode"]:checked');
    if (checked) {
        return (checked.value || "").trim().toLowerCase();
    }
    const hidden = qs('#oms_payment_mode_hidden, input[name="oms_payment_mode"]');
    return hidden ? (hidden.value || "").trim().toLowerCase() : "";
}

function isUNCMode() {
    const mode = getMode();
    return mode === "unc" || mode === "uy_nhiem_chi";
}

function getFileInput() {
    return qs('#o_ws_unc_files') || qs('input[name="oms_unc_files"]');
}

function getRetryButton() {
    return qs('#o_ws_unc_upload_btn');
}

function getUploadedInput() {
    let el = qs('#o_ws_unc_uploaded');
    if (!el) {
        el = document.createElement('input');
        el.type = 'hidden';
        el.id = 'o_ws_unc_uploaded';
        el.value = '0';
        (qs('#o_ws_unc_box .card') || document.body).appendChild(el);
    }
    return el;
}

function getAttachmentIdsInput() {
    let el = qs('#o_ws_unc_attachment_ids');
    if (!el) {
        el = document.createElement('input');
        el.type = 'hidden';
        el.id = 'o_ws_unc_attachment_ids';
        el.value = '';
        (qs('#o_ws_unc_box .card') || document.body).appendChild(el);
    }
    return el;
}

function getFileSignature() {
    const input = getFileInput();
    if (!input || !input.files || !input.files.length) {
        return "";
    }
    return Array.from(input.files)
        .map(file => `${file.name}:${file.size}:${file.lastModified}`)
        .join('|');
}

function setUploaded(flag, attachmentIds = []) {
    uploaded = !!flag;
    getUploadedInput().value = flag ? '1' : '0';
    getAttachmentIdsInput().value = (attachmentIds || []).join(',');
    if (!flag) {
        uploadedSignature = '';
    }
}

function isUploaded() {
    return uploaded || getUploadedInput().value === '1';
}

function setUploading(flag) {
    uploading = !!flag;
    const input = getFileInput();
    const retryBtn = getRetryButton();
    const uploadingBox = qs('#o_ws_unc_uploading');

    if (input) input.disabled = !!flag;
    if (retryBtn) retryBtn.disabled = !!flag;
    if (uploadingBox) uploadingBox.classList.toggle('d-none', !flag);
}

function showMessage(type, message) {
    const err = qs('#o_ws_unc_error');
    const ok = qs('#o_ws_unc_success');

    if (err) {
        err.classList.add('d-none');
        err.textContent = '';
    }
    if (ok) {
        ok.classList.add('d-none');
        ok.textContent = '';
    }

    if (!message) {
        return;
    }

    if (type === 'error' && err) {
        err.textContent = message;
        err.classList.remove('d-none');
    }
    if (type === 'success' && ok) {
        ok.textContent = message;
        ok.classList.remove('d-none');
    }
}

function resetUploadState(clearMessages = true) {
    uploaded = false;
    uploadedSignature = '';
    getUploadedInput().value = '0';
    getAttachmentIdsInput().value = '';
    if (clearMessages) {
        showMessage('', '');
    }
}

function buildFormData() {
    const input = getFileInput();
    if (!input || !input.files || !input.files.length) {
        throw new Error('Vui lòng chọn file ủy nhiệm chi.');
    }

    const fd = new FormData();

    const orderId = (qs('input[name="sale_order_id"]')?.value || '').trim();
    const orderModel = (qs('input[name="sale_order_model"]')?.value || '').trim();
    const reference = (qs('input[name="reference"]')?.value || '').trim();
    const accessToken = (qs('input[name="access_token"]')?.value || '').trim();

    if (orderId) fd.append('sale_order_id', orderId);
    if (orderModel) fd.append('sale_order_model', orderModel);
    if (reference) fd.append('reference', reference);
    if (accessToken) fd.append('access_token', accessToken);

    for (const file of input.files) {
        fd.append('oms_unc_files', file, file.name);
    }
    return fd;
}

async function uploadUNCFiles() {
    const signature = getFileSignature();
    if (!signature) {
        throw new Error('Vui lòng chọn file ủy nhiệm chi.');
    }

    if (uploading) {
        throw new Error('Hệ thống đang tải file UNC, vui lòng chờ hoàn tất.');
    }

    if (isUploaded() && uploadedSignature === signature) {
        return true;
    }

    setUploading(true);
    showMessage('', '');

    try {
        const res = await fetch('/payment/sepay/upload_unc_files', {
            method: 'POST',
            body: buildFormData(),
            credentials: 'same-origin',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
            },
        });

        let data = {};
        try {
            data = await res.json();
        } catch (_) {}

        if (!res.ok || !data.ok) {
            throw new Error(data.message || 'Upload UNC thất bại.');
        }

        uploaded = true;
        uploadedSignature = signature;
        getUploadedInput().value = '1';
        getAttachmentIdsInput().value = (data.attachment_ids || []).join(',');
        showMessage('success', data.message || 'Đã tải file UNC thành công.');
        console.log(TAG, 'upload success', data);
        return true;
    } catch (err) {
        resetUploadState(false);
        showMessage('error', err.message || 'Không thể tải file UNC.');
        console.error(TAG, 'upload failed', err);
        throw err;
    } finally {
        setUploading(false);
    }
}

function isPaymentButton(target) {
    return !!(target && target.closest && target.closest(
        'button[name="o_payment_submit_button"], .o_payment_submit_button, #o_payment_form_pay, button[type="submit"]'
    ));
}

function toggleUNCBox() {
    const box = qs('#o_ws_unc_box');
    if (!box) return;
    box.classList.toggle('d-none', !isUNCMode());
}

function blockIfUNCNotReady(ev) {
    if (!isUNCMode()) return false;

    const input = getFileInput();
    if (!input || !input.files || !input.files.length) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        showMessage('error', 'Bạn đã chọn Phiếu ủy nhiệm chi, vui lòng chọn và tải file UNC trước khi thanh toán.');
        return true;
    }

    const currentSignature = getFileSignature();
    if (uploading) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        showMessage('error', 'Hệ thống đang tải file UNC, vui lòng chờ hoàn tất rồi thanh toán.');
        return true;
    }

    if (!isUploaded() || uploadedSignature !== currentSignature) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        showMessage('error', 'File UNC chưa được tải lên thành công. Hệ thống sẽ thử tải lại ngay bây giờ.');
        uploadUNCFiles().catch(() => {});
        return true;
    }

    return false;
}

async function onRetryUploadClick(ev) {
    ev.preventDefault();
    try {
        await uploadUNCFiles();
    } catch (_) {}
}

document.addEventListener('DOMContentLoaded', () => {
    console.log(TAG, 'js loaded');
    toggleUNCBox();
    const retryBtn = getRetryButton();
    if (retryBtn && retryBtn.dataset.bound !== '1') {
        retryBtn.dataset.bound = '1';
        retryBtn.addEventListener('click', onRetryUploadClick);
    }
});

document.addEventListener('change', (ev) => {
    const target = ev.target;
    if (!target || !target.matches) return;

    if (target.matches('input[name="oms_payment_mode"]')) {
        toggleUNCBox();
        resetUploadState();
        return;
    }

    if (target.matches('#o_ws_unc_files, input[name="oms_unc_files"]')) {
        resetUploadState();
        if (isUNCMode() && target.files && target.files.length) {
            uploadUNCFiles().catch(() => {});
        }
    }
}, true);

document.addEventListener('click', (ev) => {
    if (!isPaymentButton(ev.target)) return;
    blockIfUNCNotReady(ev);
}, true);

document.addEventListener('submit', (ev) => {
    const form = ev.target;
    if (!form || form.id !== 'o_payment_form') return;
    blockIfUNCNotReady(ev);
}, true);