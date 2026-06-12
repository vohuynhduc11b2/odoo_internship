# -*- coding: utf-8 -*-
import logging
from psycopg2 import errors as pg_errors

from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)


class WebsiteSalePaymentFix(WebsiteSale):

    def _force_website_pl(self, order):
        pl = getattr(order, "pricelist_id", False) or getattr(request.website, "pricelist_id", False)
        if not pl or not order:
            return None
        request.session["website_sale_current_pl"] = pl.id
        if order.pricelist_id and order.pricelist_id.id != pl.id:
            order.sudo().write({"pricelist_id": pl.id})
        return pl

    def _sale_lines(self, order):
        lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
        if "is_gift" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_gift)
        if "is_bundle" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_bundle)
        if "linked_line_id" in lines._fields:
            lines = lines.filtered(lambda l: not l.linked_line_id)
        return lines

    def _reprice_base(self, order):
        if getattr(order, "website_id", False):
            self._compute_amounts(order)
            return
        lines = self._sale_lines(order)
        if hasattr(lines, "_compute_price_unit"):
            lines._compute_price_unit()
        else:
            order.sudo()._recompute_prices()

    def _compute_amounts(self, order):
        if hasattr(order, "_amount_all"):
            order._amount_all()
        elif hasattr(order, "_compute_amounts"):
            order._compute_amounts()

    def _get_auto_discount_pct(self, order):
        if not order.partner_id:
            return 0.0
        partner = order.partner_id.commercial_partner_id
        pl = getattr(partner, "property_product_pricelist", False)
        if not pl:
            return 0.0

        Item = request.env["product.pricelist.item"].sudo()
        domain = [
            ("pricelist_id", "=", pl.id),
            ("applied_on", "=", "3_global"),
            ("compute_price", "in", ("percentage", "formula")),
            ("min_quantity", "<=", 1),
        ]
        item = Item.search(domain, order="min_quantity asc, id asc", limit=1)
        if not item:
            return 0.0

        if item.compute_price == "percentage":
            return float(getattr(item, "percent_price", 0.0) or 0.0)

        d = float(getattr(item, "price_discount", 0.0) or 0.0)
        return (-d * 100.0) if d < 0 else 0.0

    def _apply_auto_discount_idempotent(self, order, pct):
        if not pct or pct <= 0:
            request.session.pop("uc_auto_discount_pct", None)
            return

        Line = order.order_line
        if "discount" not in Line._fields:
            return

        pct_prev = float(request.session.get("uc_auto_discount_pct") or 0.0)
        lines = self._sale_lines(order)

        for l in lines:
            total = float(l.discount or 0.0)

            promo = total
            if 0 < pct_prev < 100:
                denom = (1.0 - pct_prev / 100.0)
                if denom > 0:
                    promo = 100.0 * (1.0 - (1.0 - total / 100.0) / denom)

            eff = 100.0 * (1.0 - (1.0 - promo / 100.0) * (1.0 - pct / 100.0))
            l.sudo().write({"discount": eff})

        request.session["uc_auto_discount_pct"] = pct

    def _lock_order_and_lines(self, order):
        request.env.cr.execute(f'SELECT id FROM "{order._table}" WHERE id=%s FOR UPDATE', [order.id])
        if order.order_line:
            request.env.cr.execute(f'SELECT id FROM "{order.order_line._table}" WHERE order_id=%s FOR UPDATE', [order.id])

    def _fix_pricing_for_payment_pages(self, order):
        # lock chống concurrent update
        self._lock_order_and_lines(order)

        # pin website pricelist + bốc tier + totals
        # Do not recompute price_unit on checkout/payment. Sales may already
        # have entered quote prices for contact-price lines.
        self._force_website_pl(order)

        # KHÔNG gọi promo engine ở payment (tránh double-add).
        # voucher/auto KM đã được xử lý ở cart_update_json và /shop/voucher/apply.
        self._compute_amounts(order)

        # auto discount
        self._apply_auto_discount_idempotent(order, self._get_auto_discount_pct(order))
        self._compute_amounts(order)

    @http.route(['/shop/checkout'], type='http', auth='public', website=True, sitemap=False)
    def checkout(self, **post):
        try:
            order = request.website.sale_get_order(force_create=False, update_pricelist=False)
        except TypeError:
            order = request.website.sale_get_order(force_create=False)

        if order:
            try:
                self._fix_pricing_for_payment_pages(order)
            except pg_errors.SerializationFailure:
                # để trang vẫn load, user có thể refresh
                request.env.cr.rollback()

        return super().checkout(**post)

    @http.route(['/shop/payment'], type='http', auth='public', website=True, sitemap=False)
    def payment(self, **post):
        try:
            order = request.website.sale_get_order(force_create=False, update_pricelist=False)
        except TypeError:
            order = request.website.sale_get_order(force_create=False)

        if order:
            try:
                self._fix_pricing_for_payment_pages(order)
            except pg_errors.SerializationFailure:
                request.env.cr.rollback()

        return super().payment(**post)
