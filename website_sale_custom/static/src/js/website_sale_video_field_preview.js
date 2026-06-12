/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class FieldVideoPreview extends Component {
    static template = "website_sale_custom.FieldVideoPreview";
    static props = { ...standardFieldProps };
}

export const fieldVideoPreview = { component: FieldVideoPreview };

const fields = registry.category("fields");

// nếu đã có rồi thì bỏ qua (tránh crash khi asset load lại)
if (!fields.contains("video_preview")) {
    fields.add("video_preview", fieldVideoPreview);
} else {
    // nếu bạn muốn override chắc chắn thì dùng force:
    fields.add("video_preview", fieldVideoPreview, { force: true });
}
