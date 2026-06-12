/** @odoo-module **/

import { Component, onMounted, xml } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { RPCError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { UncaughtPromiseError } from "@web/core/errors/error_service";

const MISSING_ERROR_NAME = "odoo.exceptions.MissingError";
const MISSING_ERROR_MESSAGE = _t("Ban ghi khong con ton tai hoac da bi xoa.");

try {
    const currentAction = window.sessionStorage?.getItem("current_action") || "";
    if (currentAction.includes("sale_custom.order")) {
        window.sessionStorage.removeItem("current_action");
    }
} catch {
    // Ignore browser storage access errors.
}

class IgnoreMissingErrorDialog extends Component {
    static template = xml`<t/>`;
    static props = ["*"];

    setup() {
        onMounted(() => {
            this.props.close?.();
        });
    }
}

function isMissingError(originalError) {
    const data = originalError?.data || {};
    const context = data.context || {};
    return (
        originalError?.exceptionName === MISSING_ERROR_NAME ||
        data.name === MISSING_ERROR_NAME ||
        context.exception_class === MISSING_ERROR_NAME
    );
}

function ignoreMissingRecord(env, error, originalError) {
    if (!(error instanceof UncaughtPromiseError) || !(originalError instanceof RPCError)) {
        return false;
    }
    if (!isMissingError(originalError)) {
        return false;
    }

    error.unhandledRejectionEvent?.preventDefault?.();
    env.services.notification?.add(MISSING_ERROR_MESSAGE, { type: "warning" });
    return true;
}

registry.category("error_notifications").add(
    MISSING_ERROR_NAME,
    { message: MISSING_ERROR_MESSAGE, type: "warning" },
    { force: true }
);

registry.category("error_dialogs").add(
    MISSING_ERROR_NAME,
    IgnoreMissingErrorDialog,
    { force: true }
);

registry.category("error_handlers").add(
    "oms_solar_ignore_missing_record",
    ignoreMissingRecord,
    { sequence: 96 }
);
