# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale


class WebsiteSalePricelistFix(WebsiteSale):

    def _resolve_pricelist(self, order):
        """Chọn 1 pricelist làm nguồn sự thật.

        RULE A (khuyến nghị cho B2B): ưu tiên pricelist của partner (nếu có), fallback website pricelist
        RULE B (nếu muốn luôn theo website): chỉ dùng website.get_current_pricelist()
        """
        website = request.website
        partner = (order.partner_id if order and order.partner_id else request.env.user.partner_id)

        # RULE A:
        partner_pl = getattr(partner, "property_product_pricelist", False)
        return partner_pl or website.get_current_pricelist()

        # RULE B:
        # return website.get_current_pricelist()

    def _sync_pricelist(self, order):
        pl = self._resolve_pricelist(order)
        if pl:
            # 1) ép session pricelist để /sale/get_combination_info dùng đúng
            request.session["website_sale_current_pl"] = pl.id

            # 2) ép sale order pricelist + recompute line
            if order and order.pricelist_id != pl:
                order.pricelist_id = pl.id

                # Recompute giá (tùy bạn đang dùng sale.order hay sale_custom.order)
                # Cố gắng gọi hàm chuẩn; nếu không có thì fallback compute line:
                if getattr(order, "website_id", False):
                    if hasattr(order, "_recompute_taxes"):
                        order._recompute_taxes()
                    elif hasattr(order, "_amount_all"):
                        order._amount_all()
                elif hasattr(order, "_recompute_prices"):
                    order._recompute_prices()
                else:
                    order.order_line._compute_price_unit()
                    if hasattr(order.order_line, "_compute_discount"):
                        order.order_line._compute_discount()
                    if hasattr(order, "_amount_all"):
                        order._amount_all()
        return pl

    @http.route('/shop/cart/update_json', type='json', auth="public", website=True, csrf=False)
    def cart_update_json(self, product_id, line_id=None, add_qty=None, set_qty=None, display=True, **kw):
        order = request.website.sale_get_order(force_create=True)
        self._sync_pricelist(order)
        return super().cart_update_json(product_id, line_id=line_id, add_qty=add_qty, set_qty=set_qty, display=display, **kw)

    @http.route('/sale/get_combination_info', type='json', auth="public", website=True, csrf=False)
    def get_combination_info(self, **kw):
        order = request.website.sale_get_order()
        self._sync_pricelist(order)
        return super().get_combination_info(**kw)
