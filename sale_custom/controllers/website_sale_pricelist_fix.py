# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale


class WebsiteSalePricelistFix(WebsiteSale):

    def _resolve_pricelist(self, order):
        """Chọn pricelist: ưu tiên website pricelist (bỏ qua partner property
        để tránh bị ghi đè bởi partner pricelist cũ)."""
        website = request.website
        pl = website.get_current_pricelist()
        _logger.warning(
            "[PRICELIST FIX] _resolve_pricelist → website=%s pl_id=%s pl_name=%s",
            getattr(website, 'name', '?'),
            pl.id if pl else False,
            getattr(pl, 'name', '?'),
        )
        return pl

    def _sync_pricelist(self, order):
        pl = self._resolve_pricelist(order)
        if pl:
            # 1) ép session pricelist để /sale/get_combination_info dùng đúng
            request.session["website_sale_current_pl"] = pl.id

            # 2) ép sale order pricelist + recompute line (khi pricelist ID đổi)
            pricelist_changed = (
                order
                and order.pricelist_id != pl
            )
            if pricelist_changed:
                order.pricelist_id = pl.id

                # Recompute giá (tùy bạn đang dùng sale.order hay sale_custom.order)
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

            # 3) Stale price detection: cùng pricelist nhưng giá item đã đổi
            #    → recompute order line để lấy giá mới từ pricelist hiện tại.
            #    Phải so sánh price_unit hiện tại với giá mới từ pricelist,
            #    không phải so sánh technical_price_unit với price_unit (vì cả 2
            #    bằng nhau thì guard trong _compute_price_unit sẽ bỏ qua recompute).
            if not pricelist_changed and order and order.pricelist_id and order.order_line:
                def _is_stale(l):
                    if not (l.product_id and not l.display_type
                            and not getattr(l, 'is_delivery', False)
                            and not getattr(l, 'is_gift', False)
                            and not getattr(l, 'is_bundle', False)):
                        return False
                    try:
                        pl_price = order.pricelist_id._get_product_price(
                            product=l.product_id,
                            quantity=l.product_uom_qty or 1.0,
                            currency=l.currency_id,
                            date=l._get_order_date(),
                        )
                    except Exception:
                        return False
                    return (
                        float(l.technical_price_unit or 0.0) > 0
                        and float(pl_price or 0.0) > 0
                        and l.price_unit != pl_price
                    )
                stale_lines = order.order_line.filtered(_is_stale)
                if stale_lines:
                    stale_lines.with_context(force_price_recomputation=True)._compute_price_unit()
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
