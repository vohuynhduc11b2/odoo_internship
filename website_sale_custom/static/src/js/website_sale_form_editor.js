/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import FormEditorRegistry from "@website/js/form_editor_registry";

const KEY = "create_customer";

// Guard toàn cục để chống load 2 lần do asset/bundle editor
const GUARD = "__wsale_custom_form_editor_create_customer__";

if (!globalThis[GUARD]) {
    globalThis[GUARD] = true;

    // Một số phiên bản FormEditorRegistry có .has/.contains; nếu không có thì fallback try/catch
    const hasKey =
        (typeof FormEditorRegistry.has === "function" && FormEditorRegistry.has(KEY)) ||
        (typeof FormEditorRegistry.contains === "function" && FormEditorRegistry.contains(KEY));

    if (!hasKey) {
        FormEditorRegistry.add(KEY, {
            formFields: [
                {
                    type: "char",
                    modelRequired: true,
                    name: "name",
                    fillWith: "name",
                    string: _t("Your Name"),
                },
                {
                    type: "email",
                    required: true,
                    fillWith: "email",
                    name: "email",
                    string: _t("Your Email"),
                },
                {
                    type: "tel",
                    fillWith: "phone",
                    name: "phone",
                    string: _t("Phone Number"),
                },
                {
                    type: "char",
                    name: "company_name",
                    fillWith: "commercial_company_name",
                    string: _t("Company Name"),
                },
            ],
        });
    }
} else {
    // đã register rồi -> không làm gì
}
