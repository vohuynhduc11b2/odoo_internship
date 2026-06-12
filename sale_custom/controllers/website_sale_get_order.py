import logging
from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)

LOCK_KEY = "sale_custom_locked_pricelist_id"


class Website(models.Model):
    _inherit = "website"

    def _sale_custom_pick_pricelist(self, order):
        env = self.env

        # 1) session lock
        locked_id = request.session.get(LOCK_KEY) if request else None
        if locked_id:
            pl = env["product.pricelist"].sudo().browse(int(locked_id)).exists()
            if pl:
                return pl

        # 2) order pricelist
        if getattr(order, "pricelist_id", False):
            if request:
                request.session[LOCK_KEY] = order.pricelist_id.id
            return order.pricelist_id.sudo()

        # 3) partner property pricelist (B2B)
        partner = getattr(order, "partner_id", False)
        partner = partner.commercial_partner_id if partner else env.user.partner_id.commercial_partner_id
        pl = getattr(partner, "property_product_pricelist", False)
        if pl:
            if request:
                request.session[LOCK_KEY] = pl.id
            return pl.sudo()

        # 4) website pricelist
        pl = getattr(self, "pricelist_id", False)
        if pl:
            if request:
                request.session[LOCK_KEY] = pl.id
            return pl.sudo()

        # 5) fallback
        try:
            pl = env.ref("product.list0").sudo()
            if request:
                request.session[LOCK_KEY] = pl.id
            return pl
        except Exception:
            pl = env["product.pricelist"].sudo().search([], limit=1)
            if pl and request:
                request.session[LOCK_KEY] = pl.id
            return pl

    def sale_get_order(self, force_create=False, **kwargs):
        # gọi super tương thích nhiều version
        try:
            order = super().sale_get_order(force_create=force_create, **kwargs)
        except TypeError:
            kwargs.pop("update_pricelist", None)
            order = super().sale_get_order(force_create=force_create, **kwargs)

        # không có request (cron, backend) thì thôi
        if not request:
            return order

        if not order:
            request.session.pop(LOCK_KEY, None)
            return order

        # cart rỗng -> clear lock
        if not getattr(order, "order_line", False):
            request.session.pop(LOCK_KEY, None)
            return order

        try:
            pl = self._sale_custom_pick_pricelist(order)

            # ép pricelist
            if pl and hasattr(order, "pricelist_id"):
                if (not order.pricelist_id) or (order.pricelist_id.id != pl.id):
                    order.sudo().write({"pricelist_id": pl.id})

            # recompute giá
            if getattr(order, "website_id", False):
                if hasattr(order, "_recompute_taxes"):
                    order.sudo()._recompute_taxes()
                elif hasattr(order, "_compute_amounts"):
                    order.sudo()._compute_amounts()
            elif hasattr(order, "_recompute_prices"):
                order.sudo()._recompute_prices()

            # nếu có auto KM website thì đồng bộ luôn
            if hasattr(order, "_auto_apply_promotions"):
                order.sudo()._auto_apply_promotions(for_website=True)

            # log để bạn bắt đúng pricelist đang dùng
            lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
            _logger.warning(
                "[SALE_GET_ORDER SYNC] path=%s order=%s pricelist=%s locked=%s untaxed=%s total=%s first_lines=%s",
                request.httprequest.path,
                order.id,
                order.pricelist_id.id if order.pricelist_id else False,
                request.session.get(LOCK_KEY),
                getattr(order, "amount_untaxed", None),
                getattr(order, "amount_total", None),
                [(l.product_id.default_code, l.product_uom_qty, l.price_unit, l.price_subtotal) for l in lines[:5]],
            )
        except Exception:
            _logger.exception("sale_get_order sync failed")

        return order
