# -*- coding: utf-8 -*-
import logging

from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)

LOCK_KEY = "sale_custom_locked_pricelist_id"


class WebsiteSaleSync(WebsiteSale):

    def _pick_pricelist(self, order):
        website = request.website
        env = request.env

        # 1) nếu đã lock trong session -> dùng đúng cái đó
        locked_id = request.session.get(LOCK_KEY)
        if locked_id:
            pl = env["product.pricelist"].sudo().browse(int(locked_id)).exists()
            if pl:
                _logger.warning(
                    "[PICK PL sync] step=1 session_lock pl_id=%s pl_name=%s",
                    pl.id, pl.name,
                )
                return pl

        # 2) nếu order đã có pricelist -> lock lại và dùng
        if order.pricelist_id:
            request.session[LOCK_KEY] = order.pricelist_id.id
            _logger.warning(
                "[PICK PL sync] step=2 order_pl pl_id=%s pl_name=%s order=%s",
                order.pricelist_id.id, order.pricelist_id.name, order.id,
            )
            return order.pricelist_id.sudo()

        # 3) ưu tiên website pricelist (bỏ qua partner property)
        pl = getattr(website, "pricelist_id", False)
        if pl:
            request.session[LOCK_KEY] = pl.id
            _logger.warning(
                "[PICK PL sync] step=3 website_pl pl_id=%s pl_name=%s",
                pl.id, pl.name,
            )
            return pl.sudo()

        # 4) fallback: public pricelist (list0) hoặc 1 pricelist bất kỳ
        try:
            pl = env.ref("product.list0").sudo()
            request.session[LOCK_KEY] = pl.id
            _logger.warning("[PICK PL sync] step=4 fallback list0 pl_id=%s", pl.id)
            return pl
        except Exception:
            pl = env["product.pricelist"].sudo().search([], limit=1)
            if pl:
                request.session[LOCK_KEY] = pl.id
                _logger.warning("[PICK PL sync] step=4 fallback search pl_id=%s", pl.id)
            return pl

    def _apply_promotions(self, order):
        """Đảm bảo KM/voucher được apply lại (không recompute pricelist)."""
        if not order:
            return

        # ưu tiên engine custom của bạn nếu có
        if hasattr(order, "apply_promotions_to_line"):
            try:
                order.sudo().apply_promotions_to_line()
                return
            except Exception:
                _logger.exception("apply_promotions_to_line failed")

        # fallback auto KM (nếu có)
        if hasattr(order, "_auto_apply_promotions"):
            try:
                order.sudo()._auto_apply_promotions(for_website=True)
            except Exception:
                _logger.exception("_auto_apply_promotions failed")

    def _sync_order_pricing(self, apply_promo=False):
        """LOCK pricelist + (option) apply promo. KHÔNG recompute để tránh mất discount."""
        try:
            order = request.website.sale_get_order(force_create=False, update_pricelist=True)
        except TypeError:
            order = request.website.sale_get_order(force_create=False)

        if not order:
            request.session.pop(LOCK_KEY, None)
            return None

        if not order.order_line:
            request.session.pop(LOCK_KEY, None)
            return order

        # LOCK pricelist
        pl = self._pick_pricelist(order)
        if pl and ((not order.pricelist_id) or (order.pricelist_id.id != pl.id)):
            # chỉ set pricelist, không recompute
            order.sudo().write({"pricelist_id": pl.id})

        # Detect stale cached prices: nếu pricelist item đã đổi giá nhưng order line
        # vẫn giữ giá cũ (price_unit == technical_price_unit = giá auto-computed cũ) → recompute.
        # Không làm gì đến dòng Sales đã set thủ công (price_unit != technical_price_unit).
        if order.pricelist_id and order.order_line:
            def _is_stale_sync(l):
                if not (l.product_id and not l.display_type
                        and not getattr(l, 'is_delivery', False)
                        and not getattr(l, 'is_gift', False)
                        and not getattr(l, 'is_bundle', False)):
                    return False
                try:
                    pl_price = l.order_id.pricelist_id._get_product_price(
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
            stale_lines = order.order_line.filtered(_is_stale_sync)
            if stale_lines:
                stale_lines.with_context(force_price_recomputation=True)._compute_price_unit()

        # Apply KM/voucher nếu cần (đặc biệt checkout/payment)
        if apply_promo:
            self._apply_promotions(order)

        # Log debug (có discount/subtotal)
        try:
            lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
            _logger.warning(
                "[WSALE SYNC] path=%s order=%s pricelist=%s locked=%s untaxed=%s total=%s lines=%s",
                request.httprequest.path,
                order.id,
                order.pricelist_id.id if order.pricelist_id else False,
                request.session.get(LOCK_KEY),
                order.amount_untaxed,
                order.amount_total,
                [
                    (l.product_id.default_code, l.product_uom_qty, l.price_unit, getattr(l, "discount", 0.0), l.price_subtotal)
                    for l in lines
                ],
            )
        except Exception:
            _logger.exception("WSALE SYNC log failed")

        return order

    @http.route(['/shop/cart'], type='http', auth='public', website=True, sitemap=False)
    def cart(self, access_token=None, revive='', **post):
        # cart GET: chỉ lock pricelist, không apply lại KM mỗi lần reload
        self._sync_order_pricing(apply_promo=False)
        return super().cart(access_token=access_token, revive=revive, **post)

    @http.route(['/shop/checkout'], type='http', auth='public', website=True, sitemap=False)
    def checkout(self, **post):
        # checkout: ensure KM/voucher còn đúng
        self._sync_order_pricing(apply_promo=True)
        return super().checkout(**post)

    @http.route(['/shop/payment'], type='http', auth='public', website=True, sitemap=False)
    def payment(self, **post):
        # payment: ensure KM/voucher còn đúng (đây là điểm bạn đang bị sai)
        self._sync_order_pricing(apply_promo=True)
        return super().payment(**post)

    @http.route(['/shop/cart/update_json'], type='json', auth='public', website=True, methods=['POST'])
    def cart_update_json(self, product_id, line_id=None, add_qty=None, set_qty=None, display=True, **kw):
        # super đã update line/qty theo core
        res = super().cart_update_json(
            product_id,
            line_id=line_id,
            add_qty=add_qty,
            set_qty=set_qty,
            display=display,
            **kw
        )
        # sau khi update qty: apply KM/voucher lại để giữ subtotal đúng
        self._sync_order_pricing(apply_promo=True)
        return res