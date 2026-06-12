/** @odoo-module **/

// 1) Bắt buộc load module gốc của sale trước (để nó add sol_o2m/sol_text trước)
import "@sale/js/sale_order_line_field/sale_order_line_field";

import { registry } from "@web/core/registry";
import { CharField } from "@web/views/fields/char/char_field";
import {
    productLabelSectionAndNoteOne2Many,
    ProductLabelSectionAndNoteOne2Many,
    ProductLabelSectionAndNoteListRender,
} from "@account/components/product_label_section_and_note_field/product_label_section_and_note_field";
import {
    listSectionAndNoteText,
    sectionAndNoteFieldOne2Many,
    sectionAndNoteText,
    ListSectionAndNoteText,
    SectionAndNoteText,
} from "@account/components/section_and_note_fields_backend/section_and_note_fields_backend";

export class SaleOrderLineListRenderer extends ProductLabelSectionAndNoteListRender {
    static recordRowTemplate = "sale_custom.ListRenderer.RecordRow";

    getCellTitle(column, record) {
        if (column.name === "product_id" || column.name === "product_template_id") return;
        return super.getCellTitle(column, record);
    }

    getActiveColumns(list) {
        let activeColumns = super.getActiveColumns(list);
        const productTmplCol = activeColumns.find((col) => col.name === "product_template_id");
        const productCol = activeColumns.find((col) => col.name === "product_id");
        if (productCol && productTmplCol) {
            activeColumns = activeColumns.filter((col) => col.name !== "product_template_id");
        }
        return activeColumns;
    }

    isSectionOrNote(record = null) {
        return super.isSectionOrNote(record) || this.isCombo(record);
    }

    getRowClass(record) {
        let classNames = super.getRowClass(record);
        if (this.isCombo(record) || this.isComboItem(record)) {
            classNames = classNames.replace("o_row_draggable", "");
        }
        return `${classNames} ${this.isCombo(record) ? "o_is_line_section" : ""}`;
    }

    isCellReadonly(column, record) {
        return (
            super.isCellReadonly(column, record) ||
            (this.isComboItem(record) && ![this.titleField, "tax_id", "qty_delivered"].includes(column.name))
        );
    }

    async onDeleteRecord(record) {
        if (this.isCombo(record)) {
            await record.update({ selected_combo_items: JSON.stringify([]) });
        }
        await super.onDeleteRecord(record);
    }

    isCombo(record) {
        return record.data.product_type === "combo";
    }
    isComboItem(record) {
        return !!record.data.combo_item_id;
    }
}

export class SaleOrderLineOne2Many extends ProductLabelSectionAndNoteOne2Many {
    static components = {
        ...ProductLabelSectionAndNoteOne2Many.components,
        ListRenderer: SaleOrderLineListRenderer,
    };
}

export const saleOrderLineOne2Many = {
    ...productLabelSectionAndNoteOne2Many,
    component: SaleOrderLineOne2Many,
    additionalClasses: sectionAndNoteFieldOne2Many.additionalClasses,
};

export class SaleOrderLineText extends SectionAndNoteText {
    get componentToUse() {
        return this.props.record.data.product_type === "combo" ? CharField : super.componentToUse;
    }
}

export class ListSaleOrderLineText extends ListSectionAndNoteText {
    get componentToUse() {
        return this.props.record.data.product_type === "combo" ? CharField : super.componentToUse;
    }
}

export const saleOrderLineText = { ...sectionAndNoteText, component: SaleOrderLineText };
export const listSaleOrderLineText = { ...listSectionAndNoteText, component: ListSaleOrderLineText };

// 2) Override SAU khi sale đã register xong
const fields = registry.category("fields");

if (fields.contains("sol_o2m")) fields.remove("sol_o2m");
fields.add("sol_o2m", saleOrderLineOne2Many);

if (fields.contains("sol_text")) fields.remove("sol_text");
fields.add("sol_text", saleOrderLineText);

if (fields.contains("list.sol_text")) fields.remove("list.sol_text");
fields.add("list.sol_text", listSaleOrderLineText);
