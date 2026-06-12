from werkzeug.exceptions import Forbidden

from odoo.http import request, route

from odoo.addons.website_sale_custom.controllers.main import WebsiteSale as WebsiteSaleCustom


class WebsiteSaleDeepFix(WebsiteSaleCustom):

    def _prepare_checkout_page_values(self, order_sudo, **kwargs):
        values = super()._prepare_checkout_page_values(order_sudo, **kwargs)
        PartnerSudo = order_sudo.partner_id.with_context(show_address=1)
        commercial = order_sudo.partner_id.commercial_partner_id
        billing = PartnerSudo.search([
            ("id", "child_of", commercial.ids),
            "|", "|",
            ("type", "in", ["invoice", "other", "contact"]),
            ("id", "=", commercial.id),
            ("id", "=", order_sudo.partner_id.id),
        ], order="id desc")
        delivery = PartnerSudo.search([
            ("id", "child_of", commercial.ids),
            "|", "|",
            ("type", "in", ["delivery", "other", "contact"]),
            ("id", "=", commercial.id),
            ("id", "=", order_sudo.partner_id.id),
        ], order="id desc")
        values.update({
            "billing_addresses": billing,
            "delivery_addresses": delivery,
        })
        return values

    @route('/shop/update_address', type='json', auth='public', website=True)
    def shop_update_address(self, partner_id=None, address_type='billing', **kw):
        if not partner_id:
            return {"ok": False, "message": "Missing partner_id"}
        partner_id = int(partner_id)
        order_sudo = request.website.sale_get_order()
        if not order_sudo:
            return {"ok": False, "message": "No active order"}

        ResPartner = request.env['res.partner'].sudo()
        partner_sudo = ResPartner.browse(partner_id).exists()
        children = ResPartner._search([
            ('id', 'child_of', order_sudo.partner_id.commercial_partner_id.id),
            ('type', 'in', ('invoice', 'delivery', 'other', 'contact')),
        ])
        if (
            partner_sudo != order_sudo.partner_id
            and partner_sudo != order_sudo.partner_id.commercial_partner_id
            and partner_sudo.id not in children
        ):
            raise Forbidden()

        partner_fnames = set()
        if address_type == 'billing' and partner_sudo != order_sudo.partner_invoice_id:
            partner_fnames.add('partner_invoice_id')
        elif address_type == 'delivery' and partner_sudo != order_sudo.partner_shipping_id:
            partner_fnames.add('partner_shipping_id')

        if partner_fnames:
            order_sudo._update_address(partner_id, partner_fnames)
            if hasattr(order_sudo, '_oms_apply_expected_delivery_rule'):
                order_sudo.sudo()._oms_apply_expected_delivery_rule()
        return {"ok": True}
        return {"ok": True}

    @route('/shop/oms_order_extras', type='json', auth='public', website=True)
    def shop_oms_order_extras(self, **payload):
        order = request.website.sale_get_order()
        if not order:
            return {"ok": False, "message": "Không tìm thấy đơn hàng hiện tại."}
        values = {
            "x_is_outstation": bool(payload.get("x_is_outstation")),
            "x_outstation_transport_need": payload.get("x_outstation_transport_need") or False,
            "x_outstation_delivery_address": (payload.get("x_outstation_delivery_address") or "").strip(),
            "x_outstation_note": (payload.get("x_outstation_note") or "").strip(),
        }
        order.sudo().write(values)
        return {"ok": True, "expected_delivery_date": str(order.expected_delivery_date or "")}
