/** @odoo-module **/

import { rpc } from '@web/core/network/rpc';
import publicWidget from '@web/legacy/js/public/public_widget';

const WebsiteSaleCheckout = publicWidget.registry.WebsiteSaleCheckout;

if (WebsiteSaleCheckout) {
    WebsiteSaleCheckout.include({
        events: Object.assign({}, WebsiteSaleCheckout.prototype.events, {
            'change #x_is_outstation': '_onOmsExtraChange',
            'change #x_outstation_transport_need': '_onOmsExtraChange',
            'input #x_outstation_delivery_address': '_onOmsExtraChange',
            'input #x_outstation_note': '_onOmsExtraChange',
            'click a[name="website_sale_main_button"]': '_onOmsMainButtonClick',
        }),

        async start() {
            const res = await this._super(...arguments);
            this._toggleOmsOutstationFields();
            return res;
        },

        _getOmsPayload() {
            return {
                x_is_outstation: !!this.el.querySelector('#x_is_outstation')?.checked,
                x_outstation_transport_need: this.el.querySelector('#x_outstation_transport_need')?.value || '',
                x_outstation_delivery_address: this.el.querySelector('#x_outstation_delivery_address')?.value || '',
                x_outstation_note: this.el.querySelector('#x_outstation_note')?.value || '',
            };
        },

        _toggleOmsOutstationFields() {
            const checked = !!this.el.querySelector('#x_is_outstation')?.checked;
            this.el.querySelector('#oms_outstation_fields')?.classList.toggle('d-none', !checked);
        },

        _validateOmsExtras() {
            const payload = this._getOmsPayload();
            if (!payload.x_is_outstation) {
                return true;
            }
            if (!payload.x_outstation_transport_need || !payload.x_outstation_delivery_address.trim()) {
                window.alert('Vui lòng nhập nhu cầu vận chuyển và địa chỉ nhận hàng cho giao hàng ngoại tỉnh.');
                return false;
            }
            return true;
        },

        async _saveOmsExtras() {
            const payload = this._getOmsPayload();
            await rpc('/shop/oms_order_extras', payload);
        },

        async _onOmsExtraChange() {
            this._toggleOmsOutstationFields();
            await this._saveOmsExtras();
        },

        async _onOmsMainButtonClick(ev) {
            if (!this._validateOmsExtras()) {
                ev.preventDefault();
                ev.stopPropagation();
                return;
            }
            await this._saveOmsExtras();
        },

        _updateAmountBadge(radio, rateData) {
            this._super(...arguments);
            const deliveryPriceBadge = this._getDeliveryPriceBadge(radio);
            if (rateData && rateData.success && rateData.is_free_delivery && deliveryPriceBadge) {
                deliveryPriceBadge.textContent = 'Phí VC khách hàng tự chi trả';
            }
        },
    });
}
