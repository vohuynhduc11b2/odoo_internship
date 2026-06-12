/** @odoo-module **/

import { Component } from "@odoo/owl";

export class WarningNotification extends Component {
    static template = "website_sale_custom.warningNotification";
    static props = {
        warning: [String, { toString: Function }],
    }
}
