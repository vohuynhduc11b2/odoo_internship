/** @odoo-module */

import publicWidget from "@web/legacy/js/public/public_widget";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Reusable confirm-before-submit for portal forms.
 *
 * Replaces the native window.confirm() with the shared OWL ConfirmationDialog
 * (same dialog the reorder flow uses — see website_sale_custom/.../website_sale_reorder.js).
 *
 * Usage: add the attributes to any portal <form>:
 *   data-confirm="message"            (required — opting the form in)
 *   data-confirm-title="..."          (optional, dialog title)
 *   data-confirm-label="..."          (optional, confirm button text)
 *   data-confirm-variant="danger"     (optional, bootstrap variant for confirm button)
 */
publicWidget.registry.DatConfirmSubmit = publicWidget.Widget.extend({
    selector: ".o_portal",
    events: {
        "submit form[data-confirm]": "_onConfirmSubmit",
    },

    _onConfirmSubmit(ev) {
        const form = ev.currentTarget;
        // Already confirmed → allow the native submit to go through.
        if (form.dataset.datConfirmed === "1") {
            return;
        }
        ev.preventDefault();
        const variant = form.dataset.confirmVariant || "primary";
        this.call("dialog", "add", ConfirmationDialog, {
            title: form.dataset.confirmTitle || _t("Xác nhận"),
            body: form.dataset.confirm || _t("Bạn có chắc chắn muốn thực hiện thao tác này?"),
            confirmLabel: form.dataset.confirmLabel || _t("Đồng ý"),
            confirmClass: "btn-" + variant,
            cancelLabel: _t("Hủy bỏ"),
            confirm: () => {
                form.dataset.datConfirmed = "1";
                // Native submit bypasses this delegated handler → no re-prompt loop.
                form.submit();
            },
            cancel: () => {},
        });
    },
});

export default publicWidget.registry.DatConfirmSubmit;
