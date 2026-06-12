# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class UcWebsiteSaleTierPriceController(http.Controller):

    @http.route('/shop/uc/tier_price_info', type='json', auth='public', website=True)
    def uc_tier_price_info(self, product_id, add_qty=1, **kwargs):
        product = request.env['product.product'].sudo().browse(int(product_id))
        if not product.exists():
            return {'error': 'Product not found'}

        try:
            qty = int(float(add_qty or 1))
        except Exception:
            qty = 1
        qty = max(qty, 1)

        website = request.website
        pricelist = website.pricelist_id.sudo()
        user = request.env.user
        partner = user.partner_id if user and not user._is_public() else website.user_id.partner_id

        unit_price = pricelist._get_product_price(product, qty, partner=partner)
        total_price = unit_price * qty
        tiers = pricelist.uc_get_effective_tier_table(product, partner=partner)

        active_index = 0
        for idx, row in enumerate(tiers):
            min_qty = row['min_qty']
            max_qty = row['max_qty']
            if (max_qty is None and qty >= min_qty) or (max_qty is not None and min_qty <= qty <= max_qty):
                active_index = idx
                break

        def _fmt_money(amount):
            return request.env['ir.qweb.field.monetary'].value_to_html(
                amount,
                {'display_currency': pricelist.currency_id},
            )

        return {
            'product_id': product.id,
            'qty': qty,
            'unit_price': unit_price,
            'unit_price_html': _fmt_money(unit_price),
            'total_price': total_price,
            'total_price_html': _fmt_money(total_price),
            'active_index': active_index,
            'tiers': [{
                'min_qty': row['min_qty'],
                'max_qty': row['max_qty'],
                'price': row['price'],
                'price_html': _fmt_money(row['price']),
                'range_label': f"{row['min_qty']} - {row['max_qty']}" if row['max_qty'] is not None else f"{row['min_qty']}+",
            } for row in tiers],
        }
