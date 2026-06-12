/** @odoo-module **/

const getDeleteModal = () => document.querySelector('.oms_cart_delete_modal');

const closeDeleteModal = (modal) => {
    if (!modal) {
        return;
    }
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    modal._omsPendingDelete = null;
};

const openDeleteModal = (trigger) => {
    const modal = getDeleteModal();
    if (!modal) {
        return false;
    }

    const productName = trigger.dataset.omsProductName || 'sản phẩm này';
    const nameNode = modal.querySelector('.oms_cart_delete_name');
    if (nameNode) {
        nameNode.textContent = productName;
    }

    modal._omsPendingDelete = trigger;
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('.oms_cart_delete_cancel')?.focus();
    return true;
};

document.addEventListener('click', (event) => {
    const deleteTrigger = event.target.closest('.oms_cart_qty_delete[data-oms-confirm-delete="1"]');
    if (deleteTrigger && !deleteTrigger.dataset.omsConfirmedDelete) {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        openDeleteModal(deleteTrigger);
        return;
    }

    const modal = getDeleteModal();
    if (!modal) {
        return;
    }

    if (event.target.closest('.oms_cart_delete_cancel') || event.target.classList.contains('oms_cart_delete_modal_backdrop')) {
        closeDeleteModal(modal);
        return;
    }

    if (event.target.closest('.oms_cart_delete_confirm')) {
        const trigger = modal._omsPendingDelete;
        closeDeleteModal(modal);
        if (trigger) {
            trigger.dataset.omsConfirmedDelete = '1';
            trigger.click();
            delete trigger.dataset.omsConfirmedDelete;
        }
    }
}, true);

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        closeDeleteModal(getDeleteModal());
    }
});
