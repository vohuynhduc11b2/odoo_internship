# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request


class WebsiteQuoteRequestController(http.Controller):

    @http.route("/shop/request_quote", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def request_quote(self, **post):
        order = request.website.sale_get_order()
        if not order:
            return request.redirect("/shop/cart")

        # Lọc các dòng cần báo giá: price_unit < 1, loại quà/bundle
        def _need_quote(l):
            return (
                l.product_id
                and not l.display_type
                and not getattr(l, "is_gift", False)
                and not getattr(l, "is_bundle", False)
                and (l.price_unit or 0.0) < 1.0
            )

        quote_lines = order.order_line.filtered(_need_quote)

        if not quote_lines:
            request.session["uc_quote_msg"] = _("Không có sản phẩm nào cần báo giá.")
            return request.redirect("/shop/cart")

        order_sudo = order.sudo()
        order_sudo.action_request_quote_from_sale(quote_lines=quote_lines)

        request.session["uc_quote_msg"] = _("Đã gửi yêu cầu báo giá. Sale sẽ liên hệ sớm.")
        return request.redirect("/shop/cart")
