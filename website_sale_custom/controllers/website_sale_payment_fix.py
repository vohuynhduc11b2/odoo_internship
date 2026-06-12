# -*- coding: utf-8 -*-
import logging
from psycopg2 import errors as pg_errors

from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)


class WebsiteSalePaymentFix(WebsiteSale):

    # ===== helpers giống cart_update_json =====
    def _oms_get_payment_mode(self, kwargs=None):
        kwargs = kwargs or {}
        return (
            kwargs.get("oms_payment_mode_hidden")
            or kwargs.get("oms_payment_mode")
            or request.session.get("oms_payment_mode")
            or request.session.get("website_payment_type")
            or ""
        )

    def _force_website_pl(self, order):
        pl = getattr(order, "pricelist_id", False)
        if not pl:
            try:
                pl = request.website.get_current_pricelist()
            except Exception:
                pl = getattr(request.website, "pricelist_id", False)
        if not pl or not order:
            return None
        request.session["website_sale_current_pl"] = pl.id
        if getattr(order, "pricelist_id", False) and order.pricelist_id.id != pl.id:
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
        if hasattr(order, "_uc_apply_tier_price_on_line"):
            for line in lines:
                order._uc_apply_tier_price_on_line(line, force=True)

    def _compute_amounts(self, order):
        if hasattr(order, "_amount_all"):
            order._amount_all()
        elif hasattr(order, "_compute_amounts"):
            order._compute_amounts()

    def _apply_promos(self, order):
        # idempotent: engine của bạn thường tự clear gift/bundle rồi apply lại
        with request.env.cr.savepoint():
            if hasattr(order, "_auto_apply_promotions"):
                order.sudo()._auto_apply_promotions(for_website=True)
            if hasattr(order, "apply_promotions_to_line"):
                order.sudo().apply_promotions_to_line()

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

    def _fix_pricing_like_cart(self, order):
        """
        Pipeline giống cart_update_json:
        - pin website PL
        - tier đúng
        - promo/voucher/gift (idempotent)
        - pin lại (vì promo có thể đổi pricelist)
        - tier lại
        - auto discount
        - totals
        """
        if hasattr(order, "_oms_apply_strategic_payment_pricelist"):
            order._oms_apply_strategic_payment_pricelist(self._oms_get_payment_mode())
        self._force_website_pl(order)
        self._reprice_base(order)
        self._compute_amounts(order)

        self._apply_promos(order)

        self._force_website_pl(order)      # PIN lại sau promo (điểm bắt buộc)
        self._reprice_base(order)

        self._apply_auto_discount_idempotent(order, self._get_auto_discount_pct(order))
        self._compute_amounts(order)

    # ===== HOOK ĐÚNG CHỖ: chạy ngay trước render payment =====
    def _get_shop_payment_values(self, order, **kwargs):
        values = super()._get_shop_payment_values(order, **kwargs)

        if order:
            try:
                if hasattr(order, "_oms_apply_strategic_payment_pricelist"):
                    order._oms_apply_strategic_payment_pricelist(self._oms_get_payment_mode(kwargs))
                self._fix_pricing_like_cart(order)
            except pg_errors.SerializationFailure:
                request.env.cr.rollback()

            if hasattr(self, "_get_shop_payment_errors"):
                values["errors"] = self._get_shop_payment_errors(order)

            # đảm bảo values dùng đúng số mới (nếu template lấy từ values)
            values["amount"] = order.amount_total
            try:
                values["minor_amount"] = payment_utils.to_minor_currency_units(
                    order._get_amount_total_excluding_delivery(), order.currency_id
                )
            except Exception:
                # fallback
                values["minor_amount"] = payment_utils.to_minor_currency_units(order.amount_total, order.currency_id)

        return values

    # ===== (tuỳ chọn) làm luôn checkout cho đồng bộ =====
    def _get_shop_checkout_values(self, order, **kwargs):
        # nếu core có method này thì hook luôn
        values = super()._get_shop_checkout_values(order, **kwargs)

        if order:
            try:
                self._fix_pricing_like_cart(order)
            except pg_errors.SerializationFailure:
                request.env.cr.rollback()

        return values
