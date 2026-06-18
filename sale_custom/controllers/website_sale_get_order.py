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
                _logger.warning(
                    "[PICK PL] step=1 session_lock pl_id=%s pl_name=%s",
                    pl.id, pl.name,
                )
                return pl

        # 2) order pricelist
        if getattr(order, "pricelist_id", False):
            if request:
                request.session[LOCK_KEY] = order.pricelist_id.id
            _logger.warning(
                "[PICK PL] step=2 order_pl pl_id=%s pl_name=%s order=%s",
                order.pricelist_id.id, order.pricelist_id.name, order.id,
            )
            return order.pricelist_id.sudo()

        # 3) ưu tiên website pricelist (bỏ qua partner property)
        pl = getattr(self, "pricelist_id", False)
        if pl:
            if request:
                request.session[LOCK_KEY] = pl.id
            _logger.warning(
                "[PICK PL] step=3 website_pl pl_id=%s pl_name=%s",
                pl.id, pl.name,
            )
            return pl.sudo()

        # 4) fallback
        try:
            pl = env.ref("product.list0").sudo()
            if request:
                request.session[LOCK_KEY] = pl.id
            _logger.warning("[PICK PL] step=4 fallback list0 pl_id=%s", pl.id)
            return pl
        except Exception:
            pl = env["product.pricelist"].sudo().search([], limit=1)
            if pl and request:
                request.session[LOCK_KEY] = pl.id
            _logger.warning("[PICK PL] step=4 fallback search pl_id=%s", pl.id)
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

            # stale price detection: nếu pricelist đã đổi giá nhưng order line
            # vẫn giữ giá cũ → recompute để lấy giá mới.
            if order.pricelist_id and order.order_line:
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
