from werkzeug.urls import url_parse, url_encode

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.addons.website_sale_custom.controllers.main import WebsiteSale


class OmsSolarFollowupController(WebsiteSale):

    def _serialize_gift_combo(self, combo):
        return {
            'id': combo.id,
            'name': combo.display_name or '',
            'note': getattr(combo, 'description_sale', False) or getattr(combo, 'description', False) or '',
        }

    def _serialize_gift_combo_selection(self, selection):
        line = selection.line_id
        product = getattr(line, 'product_id', False)
        return {
            'selection_id': selection.id,
            'line_id': line.id if line else False,
            'line_name': product.display_name if product else (line.display_name if line else ''),
            'promotion_id': selection.promotion_id.id if selection.promotion_id else False,
            'promotion_name': selection.promotion_id.display_name if selection.promotion_id else '',
            'combo_id': selection.combo_id.id if selection.combo_id else False,
            'purchased_qty': float(selection.purchased_qty or 0.0),
            'allowed_qty': float(selection.allowed_qty or 0.0),
            'selected_qty': float(selection.selected_qty or 0.0),
            'main_product_price': float(selection.main_product_price or 0.0),
            'note': selection.note or '',
            'combo_options': [self._serialize_gift_combo(combo) for combo in selection.available_combo_ids],
        }

    def _serialize_partner_card(self, partner):
        return {
            'id': partner.id,
            'name': partner.display_name or '',
            'email': partner.email or '',
            'phone': partner.mobile or partner.phone or '',
            'street': ' '.join(filter(None, [partner.street, partner.street2])),
            'city': ', '.join(filter(None, [partner.city, partner.state_id.name if partner.state_id else False, partner.country_id.name if partner.country_id else False])),
            'vat': partner.vat or '',
            'type': partner.type or '',
            'is_company': bool(partner.is_company),
        }

    def _get_order(self):
        return request.website.sale_get_order()

    def _get_redirect_url(self, redirect=None, default='/shop/checkout?try_skip_step=true', **extra_params):
        redirect = redirect or request.httprequest.referrer or default
        parsed = url_parse(redirect)
        params = dict(parsed.decode_query())
        for key, value in extra_params.items():
            if value in (False, None, ''):
                params.pop(key, None)
            else:
                params[key] = str(value)
        return parsed.replace(query=url_encode(params)).to_url()

    def _get_buyer_candidates(self, order):
        partner = order.oms_buyer_partner_id or getattr(order, 'partner_id', False) or getattr(order, 'partner_invoice_id', False)
        if not partner:
            return request.env['res.partner']
        return order.sudo()._oms_get_group_companies(partner).filtered(lambda p: p.oms_allow_buyer_selection)

    def _get_invoice_candidates(self, order, buyer=None):
        buyer = (buyer or order.oms_buyer_partner_id or getattr(order, 'partner_id', False) or getattr(order, 'partner_invoice_id', False))
        if not buyer:
            return request.env['res.partner']
        return order.sudo()._oms_get_invoice_candidates(buyer)

    def _get_group_delivery_addresses(self, order):
        partner = order.oms_buyer_partner_id or getattr(order, 'partner_id', False) or getattr(order, 'partner_shipping_id', False)
        partner = partner.commercial_partner_id if partner else False
        if not partner:
            return request.env['res.partner']
        Partner = request.env['res.partner'].sudo().with_context(show_address=1)
        companies = order.sudo()._oms_get_group_companies(partner)
        deliveries = Partner.browse()
        for company in companies:
            deliveries |= Partner.search([
                ('id', 'child_of', company.ids),
                '|',
                ('type', 'in', ['delivery', 'other', 'contact']),
                ('id', '=', company.id),
            ], order='type, id')
        deliveries |= companies
        selected_delivery = getattr(order, 'partner_shipping_id', False)
        return deliveries.sorted(lambda p: (0 if selected_delivery and p.id == selected_delivery.id else 1, p.display_name or '', p.id))

    def _prepare_checkout_page_values(self, order_sudo, **kwargs):
        values = super()._prepare_checkout_page_values(order_sudo, **kwargs)
        buyer_partners = self._get_buyer_candidates(order_sudo)
        selected_buyer = order_sudo.oms_buyer_partner_id or getattr(order_sudo, 'partner_id', False) or getattr(order_sudo, 'partner_invoice_id', False)
        invoice_partners = self._get_invoice_candidates(order_sudo, selected_buyer)
        selected_invoice_partner = order_sudo.oms_invoice_customer_id or getattr(order_sudo, 'partner_invoice_id', False) or selected_buyer
        delivery_partners_sudo = self._get_group_delivery_addresses(order_sudo)
        options = request.env['oms.solar.outstation.option'].sudo().search([('active', '=', True)], order='sequence, id')
        if hasattr(order_sudo, 'action_sync_oms_gift_combo'):
            order_sudo.sudo().action_sync_oms_gift_combo()
        has_contact_price_item = bool(order_sudo.oms_has_contact_price_item)
        values.update({
            'billing_addresses': invoice_partners,
            'delivery_addresses': delivery_partners_sudo,
            'oms_buyer_customers': buyer_partners,
            'oms_selected_buyer_customer': selected_buyer,
            'oms_invoice_customers': invoice_partners,
            'oms_selected_invoice_customer': selected_invoice_partner,
            'oms_outstation_options': options,
            'oms_outstation_selected': order_sudo._oms_is_outstation_selected(),
            'oms_checkout_error': kwargs.get('oms_checkout_error') or request.params.get('oms_checkout_error'),
            'oms_quote_requested': (kwargs.get('oms_quote_requested') or request.params.get('oms_quote_requested')) if has_contact_price_item else False,
        })
        return values

    @http.route()
    def shop_update_address(self, partner_id=None, address_type='billing', **kw):
        if not partner_id:
            return {'ok': True, 'skipped': True}
        response = super().shop_update_address(partner_id=partner_id, address_type=address_type, **kw)
        order = self._get_order()
        if order:
            order = order.sudo()
            order._oms_apply_sync_updates()
        return response

    @http.route('/shop/payment', type='http', auth='public', website=True, sitemap=False)
    def shop_payment(self, **post):
        order = self._get_order()
        if order:
            try:
                order.sudo()._oms_validate_checkout_requirements()
            except ValidationError as err:
                return request.redirect(self._get_redirect_url('/shop/checkout?try_skip_step=true', oms_checkout_error=err.args[0]))
        return super().shop_payment(**post)

    @http.route('/oms_solar_followup/select_buyer', type='http', auth='public', website=True, csrf=False)
    def select_buyer(self, partner_id=None, redirect=None, **kw):
        order = self._get_order()
        if not order or not partner_id:
            return request.redirect(self._get_redirect_url(redirect))
        buyer = request.env['res.partner'].sudo().browse(int(partner_id)).exists()
        if not buyer:
            return request.redirect(self._get_redirect_url(redirect))
        allowed = self._get_buyer_candidates(order)
        if buyer not in allowed:
            return request.redirect(self._get_redirect_url(redirect, oms_checkout_error='Đối tác mua không hợp lệ trong nhóm khách hàng hiện tại.'))
        vals = {'oms_buyer_partner_id': buyer.id}
        if 'partner_id' in order._fields:
            vals['partner_id'] = buyer.id
        invoice_candidates = self._get_invoice_candidates(order, buyer)
        default_invoice = invoice_candidates.filtered(lambda p: p.id == buyer.id)[:1] or invoice_candidates[:1]
        if default_invoice:
            vals['oms_invoice_customer_id'] = default_invoice.id
            if 'partner_invoice_id' in order._fields:
                vals['partner_invoice_id'] = default_invoice.id
        order.sudo().write(vals)
        return request.redirect(self._get_redirect_url(redirect))

    @http.route(['/oms_solar_followup/select_invoice', '/oms_solar_followup/select_invoice_partner'], type='http', auth='public', website=True, csrf=False)
    def select_invoice(self, partner_id=None, redirect=None, **kw):
        order = self._get_order()
        if not order or not partner_id:
            return request.redirect(self._get_redirect_url(redirect))
        invoice = request.env['res.partner'].sudo().browse(int(partner_id)).exists()
        if not invoice:
            return request.redirect(self._get_redirect_url(redirect))
        allowed = self._get_invoice_candidates(order)
        if invoice not in allowed:
            return request.redirect(self._get_redirect_url(redirect, oms_checkout_error='Địa chỉ hoá đơn không hợp lệ với đối tác mua hiện tại.'))
        vals = {'oms_invoice_customer_id': invoice.id}
        if 'partner_invoice_id' in order._fields:
            vals['partner_invoice_id'] = invoice.id
        order.sudo().write(vals)
        return request.redirect(self._get_redirect_url(redirect))

    @http.route('/oms_solar_followup/save_outstation', type='http', auth='public', website=True, csrf=False, methods=['POST'])
    def save_outstation(self, redirect=None, **post):
        order = self._get_order()
        if not order:
            return request.redirect(self._get_redirect_url(redirect))
        vals = {
            'oms_delivery_method': 'outstation' if order._oms_is_outstation_selected() else 'standard',
            'oms_transport_address': (post.get('oms_transport_address') or '').strip(),
            'oms_transport_note': (post.get('oms_transport_note') or '').strip(),
        }
        option_id = post.get('oms_outstation_option_id')
        if option_id:
            option = request.env['oms.solar.outstation.option'].sudo().browse(int(option_id)).exists()
            if option:
                vals.update({'oms_outstation_option_id': option.id, 'oms_transport_need': option.name})
        order.sudo().write(vals)
        error = False
        if order._oms_is_outstation_selected():
            try:
                order.sudo()._oms_validate_checkout_requirements()
            except ValidationError as err:
                error = err.args[0]
        return request.redirect(self._get_redirect_url(redirect, oms_checkout_error=error, oms_saved='1' if not error else False))

    @http.route('/oms_solar_followup/checkout_state', type='json', auth='public', website=True, csrf=False)
    def checkout_state(self, order_id=None):
        order = self._get_order()
        if not order:
            return {'ok': False, 'message': 'Không tìm thấy đơn OMS hiện tại'}
        if order_id and int(order_id) != order.id:
            return {'ok': False, 'message': 'Sai order_id'}

        order = order.sudo()
        if hasattr(order, 'action_sync_oms_gift_combo'):
            order.action_sync_oms_gift_combo()

        options = request.env['oms.solar.outstation.option'].sudo().search([('active', '=', True)], order='sequence, id')
        buyer_partners = self._get_buyer_candidates(order)
        selected_buyer = order.oms_buyer_partner_id or getattr(order, 'partner_id', False) or getattr(order, 'partner_invoice_id', False)
        invoice_partners = self._get_invoice_candidates(order, selected_buyer)
        selected_invoice_partner = order.oms_invoice_customer_id or getattr(order, 'partner_invoice_id', False) or selected_buyer
        delivery_record = order._oms_get_selected_delivery_record()
        shipping_customer_pay_note = False
        if order._oms_is_outstation_selected():
            shipping_customer_pay_note = (
                getattr(delivery_record, 'oms_customer_pay_shipping_text', False)
                or 'Phí VC khách hàng tự chi trả'
            )

        return {
            'ok': True,
            'order_id': order.id,
            'delivery_method': order.oms_delivery_method or 'standard',
            'outstation_selected': bool(order._oms_is_outstation_selected()),
            'outstation_option_id': order.oms_outstation_option_id.id if order.oms_outstation_option_id else False,
            'transport_address': order.oms_transport_address or '',
            'transport_note': order.oms_transport_note or '',
            'expected_delivery_date': order.oms_expected_delivery_date.isoformat() if order.oms_expected_delivery_date else False,
            'shipping_customer_pay_note': shipping_customer_pay_note,
            'has_contact_price_item': bool(order.oms_has_contact_price_item),
            'low_stock_warning_text': order.oms_low_stock_warning_text or False,
            'selected_buyer_customer_id': selected_buyer.id if selected_buyer else False,
            'selected_invoice_customer_id': selected_invoice_partner.id if selected_invoice_partner else False,
            'buyer_customers': [self._serialize_partner_card(partner) for partner in buyer_partners],
            'invoice_customers': [self._serialize_partner_card(partner) for partner in invoice_partners],
            'gift_combo_sections': [
                self._serialize_gift_combo_selection(selection)
                for selection in getattr(order, 'oms_gift_combo_selection_ids', request.env['oms.solar.gift.combo.selection'])
            ],
            'options': [
                {'id': option.id, 'name': option.name, 'code': option.code or '', 'note': option.note or ''}
                for option in options
            ],
        }

    @http.route('/oms_solar_followup/update_order_extras', type='json', auth='public', website=True, csrf=False)
    def update_order_extras(self, order_id=None, **payload):
        order = self._get_order()
        if not order:
            return {'ok': False, 'message': 'Không tìm thấy đơn OMS hiện tại'}
        if order_id and int(order_id) != order.id:
            return {'ok': False, 'message': 'Sai order_id'}

        vals = {}
        if payload.get('oms_delivery_method') in ('standard', 'outstation'):
            vals['oms_delivery_method'] = payload.get('oms_delivery_method')
        option_id = payload.get('oms_outstation_option_id')
        if option_id:
            option = request.env['oms.solar.outstation.option'].sudo().browse(int(option_id)).exists()
            if option:
                vals['oms_outstation_option_id'] = option.id
                vals['oms_transport_need'] = option.name
        elif payload.get('oms_delivery_method') == 'standard':
            vals.update({'oms_outstation_option_id': False, 'oms_transport_need': False})
        if 'oms_transport_address' in payload:
            vals['oms_transport_address'] = (payload.get('oms_transport_address') or '').strip()
        if 'oms_transport_note' in payload:
            vals['oms_transport_note'] = (payload.get('oms_transport_note') or '').strip()

        if vals:
            order.sudo().write(vals)
        state = self.checkout_state(order_id=order.id)
        state.update({'ok': True})
        return state


    @http.route('/oms_solar_followup/update_gift_combo_selection', type='json', auth='public', website=True, csrf=False)
    def update_gift_combo_selection(self, selection_id=None, combo_id=None, selected_qty=None, order_id=None):
        order = self._get_order()
        if not order:
            return {'ok': False, 'message': 'Không tìm thấy đơn OMS hiện tại'}
        if order_id and int(order_id) != order.id:
            return {'ok': False, 'message': 'Sai order_id'}
        selection = request.env['oms.solar.gift.combo.selection'].sudo().browse(int(selection_id or 0)).exists()
        if not selection or selection.order_id.id != order.id:
            return {'ok': False, 'message': 'Không tìm thấy lựa chọn combo quà hợp lệ'}
        vals = {}
        if combo_id not in (None, ''):
            if combo_id:
                combo = request.env['product.combo'].sudo().browse(int(combo_id)).exists()
                if combo and combo in selection.available_combo_ids:
                    vals['combo_id'] = combo.id
            else:
                vals['combo_id'] = False
        if selected_qty is not None:
            try:
                qty = float(selected_qty)
            except Exception:
                qty = selection.selected_qty
            vals['selected_qty'] = min(max(qty, 0.0), float(selection.allowed_qty or 0.0))
        if vals.get('combo_id', selection.combo_id.id if selection.combo_id else False) is False:
            vals['selected_qty'] = 0.0
        if vals:
            selection.write(vals)
            selection.action_apply_selection()
        state = self.checkout_state(order_id=order.id)
        state.update({'ok': True})
        return state

    @http.route('/oms_solar_followup/product_stock_info', type='json', auth='public', website=True, csrf=False)
    def product_stock_info(self, template_ids=None):
        # Website stock display is handled by addon `oms_solar_stock_live_warning`.
        return {'ok': True, 'products': {}}


    def _get_shop_payment_errors(self, order):
        errors = super()._get_shop_payment_errors(order)
        if getattr(order, 'oms_has_contact_price_item', False):
            errors.append((
                _('Đơn hàng có sản phẩm cần báo giá'),
                _('Có ít nhất một sản phẩm đang ở giá liên hệ/1đ. Vui lòng gửi Sales báo giá thay vì xác nhận thanh toán trực tiếp.'),
            ))
        return errors

    @http.route('/oms_solar_followup/request_quote', type='http', auth='public', website=True, csrf=False, methods=['POST'])
    def request_quote(self, order_id=None, redirect=None, **post):
        order = self._get_order()
        if order_id and order and int(order_id) != order.id:
            order = request.env['sale_custom.order'].sudo().browse(int(order_id)).exists()
        if order and order.sudo().oms_has_contact_price_item:
            order.sudo().action_request_sales_quote()
            return request.redirect(self._get_redirect_url(redirect or '/shop/checkout', oms_quote_requested='1'))
        return request.redirect(self._get_redirect_url(redirect or '/shop/checkout', oms_quote_requested=False))
