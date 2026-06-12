# -*- coding: utf-8 -*-
import logging
from psycopg2 import errors as pg_errors

from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class WebsiteVoucherController(http.Controller):

    @http.route("/shop/voucher/apply", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def apply_voucher(self, **post):
        redirect_url = post.get("r") or "/shop/cart"

        # IMPORTANT: không để core tự update pricelist theo partner
        try:
            order = request.website.sale_get_order(force_create=False, update_pricelist=False)
        except TypeError:
            order = request.website.sale_get_order(force_create=False)

        if not order:
            return request.redirect(redirect_url)

        def _force_website_pl(_order):
            pl = getattr(_order, "pricelist_id", False)
            if not pl:
                try:
                    pl = request.website.get_current_pricelist()
                except Exception:
                    pl = getattr(request.website, "pricelist_id", False)
            if not pl:
                return None
            request.session["website_sale_current_pl"] = pl.id
            if getattr(_order, "pricelist_id", False) and _order.pricelist_id.id != pl.id:
                _order.sudo().write({"pricelist_id": pl.id})
            return pl

        def _sale_lines(_order):
            lines = _order.order_line.filtered(lambda l: l.product_id and not l.display_type)
            if "is_gift" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_gift)
            if "is_bundle" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_bundle)
            if "linked_line_id" in lines._fields:
                lines = lines.filtered(lambda l: not l.linked_line_id)
            return lines

        def _reprice_base(_order):
            if getattr(_order, "website_id", False):
                _compute_amounts(_order)
                return
            lines = _sale_lines(_order)
            if hasattr(lines, "_compute_price_unit"):
                lines._compute_price_unit()
            else:
                _order.sudo()._recompute_prices()
            if hasattr(_order, "_uc_apply_tier_price_on_line"):
                for line in lines:
                    _order._uc_apply_tier_price_on_line(line)

        def _compute_amounts(_order):
            if hasattr(_order, "_amount_all"):
                _order._amount_all()
            elif hasattr(_order, "_compute_amounts"):
                _order._compute_amounts()

        def _sync_gift_combo(_order):
            """Keep OMS gift-combo selections in sync before/after voucher validation."""
            if hasattr(_order, "action_sync_oms_gift_combo"):
                try:
                    _order.sudo().action_sync_oms_gift_combo()
                except Exception:
                    _logger.exception("action_sync_oms_gift_combo failed (ignored).")

        def _apply_promos(_order):
            """Áp promo engine chung (nếu có) - có chặn RecursionError."""
            with request.env.cr.savepoint():
                if hasattr(_order, "_auto_apply_promotions"):
                    try:
                        try:
                            # nếu core/engine của bạn có context guard thì pass luôn cho an toàn
                            _order.sudo().with_context(skip_auto_purchase_promo=True)._auto_apply_promotions(for_website=True)
                        except TypeError:
                            _order.sudo().with_context(skip_auto_purchase_promo=True)._auto_apply_promotions()
                    except RecursionError:
                        # Tránh spam stacktrace (lỗi đang nằm ở override _auto_apply_promotions bên sale_custom)
                        _logger.error("Skip _auto_apply_promotions due to RecursionError (check sale_custom override).")
                    except Exception:
                        _logger.exception("Error when calling _auto_apply_promotions (ignored for website flow).")

                if hasattr(_order, "apply_promotions_to_line"):
                    try:
                        _order.sudo().apply_promotions_to_line()
                    except Exception:
                        _logger.exception("apply_promotions_to_line failed (ignored).")

        def _get_auto_discount_pct(_order):
            if not _order.partner_id:
                return 0.0
            partner = _order.partner_id.commercial_partner_id
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
            order_terms = []
            for f in ("min_quantity", "id"):
                if f in Item._fields:
                    order_terms.append(f"{f} asc")
            item = Item.search(domain, order=", ".join(order_terms) or "id asc", limit=1)
            if not item:
                return 0.0

            if getattr(item, "compute_price", "") == "percentage":
                return float(getattr(item, "percent_price", 0.0) or 0.0)

            d = float(getattr(item, "price_discount", 0.0) or 0.0)
            return (-d * 100.0) if d < 0 else 0.0

        def _apply_auto_discount_idempotent(_order, pct):
            if not pct or pct <= 0:
                request.session.pop("uc_auto_discount_pct", None)
                return

            Line = _order.order_line
            if "discount" not in Line._fields:
                return

            pct_prev = float(request.session.get("uc_auto_discount_pct") or 0.0)
            lines = _sale_lines(_order)

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

        def _lock_order_and_lines(_order):
            request.env.cr.execute(f'SELECT id FROM "{_order._table}" WHERE id=%s FOR UPDATE', [_order.id])
            if _order.order_line:
                request.env.cr.execute(
                    f'SELECT id FROM "{_order.order_line._table}" WHERE order_id=%s FOR UPDATE',
                    [_order.id]
                )

        # ✅ helper: dọn kiểu cũ (nếu auto mua lần đầu/hai từng bị nhét vào website_voucher_id)
        def _cleanup_old_auto_voucher(_order):
            if not hasattr(_order, "_get_auto_purchase_promos"):
                return
            first_promo, second_promo, _cfg = _order._get_auto_purchase_promos()
            wv = getattr(_order, "website_voucher_id", False)
            if wv and (wv == first_promo or wv == second_promo):
                _order.sudo().website_clear_voucher()

        def _promotion_ids_snapshot(_order):
            """Snapshot promotion_ids trên sale lines (nếu field tồn tại)."""
            try:
                if "promotion_ids" in _order.order_line._fields:
                    return set(_sale_lines(_order).mapped("promotion_ids").ids)
            except Exception:
                pass
            return set()

        try:
            raw = (post.get("promo_id") or post.get("promo") or "").strip()
            promo_id = int(raw) if raw.isdigit() else 0

            for attempt in range(3):
                try:
                    _lock_order_and_lines(order)

                    # ✅ 0) dọn kẹt kiểu cũ trước khi xử voucher
                    _cleanup_old_auto_voucher(order)

                    # (1) pin website pricelist + tier base
                    _force_website_pl(order)
                    _reprice_base(order)
                    _sync_gift_combo(order)

                    # (2) apply/clear voucher trong savepoint
                    with request.env.cr.savepoint():
                        if not promo_id:
                            order.sudo().website_clear_voucher()
                            request.session["uc_voucher_msg"] = _("Đã gỡ voucher.")
                            request.session["uc_voucher_type"] = "info"
                        else:
                            promo = request.env["oms.promotion"].sudo().browse(promo_id)
                            if not promo.exists():
                                raise ValidationError(_("Voucher không tồn tại."))
                            order.sudo().website_apply_voucher(promo)
                            request.session["uc_voucher_msg"] = _("Đã áp dụng voucher: %s") % (promo.code or promo.name)
                            request.session["uc_voucher_type"] = "success"

                    # (3) voucher có thể đổi pricelist => PIN lại + bốc tier lại
                    _force_website_pl(order)
                    _reprice_base(order)

                    # ✅ 3.1) chạy promo engine chung (nếu bạn có auto-add/auto-apply khác)
                    _apply_promos(order)
                    _sync_gift_combo(order)

                    # ✅ 3.2) áp KM tự động mới “Mua lần đầu / Mua lần hai” + set message xanh
                    before_ids = _promotion_ids_snapshot(order)

                    if hasattr(order, "website_apply_auto_purchase_promo"):
                        res = None
                        try:
                            res = order.sudo().website_apply_auto_purchase_promo()
                        except Exception:
                            _logger.exception("website_apply_auto_purchase_promo failed (ignored).")

                        after_ids = _promotion_ids_snapshot(order)
                        added_ids = list(after_ids - before_ids)

                        auto_msg = None
                        promo_name = None

                        # ưu tiên nếu method của bạn có return dict / record promo
                        if isinstance(res, dict):
                            auto_msg = res.get("message") or res.get("msg")
                            promo_rec = res.get("promo") or res.get("applied")
                            if promo_rec and hasattr(promo_rec, "exists") and promo_rec.exists():
                                promo_name = promo_rec.code or promo_rec.name
                        elif res and hasattr(res, "exists") and res.exists():
                            promo_name = res.code or res.name

                        # fallback: detect promo mới add
                        if not promo_name and added_ids:
                            p = request.env["oms.promotion"].sudo().browse(added_ids[0])
                            if p.exists():
                                promo_name = p.code or p.name

                        else:
                            request.session.pop("uc_auto_msg", None)
                            request.session.pop("uc_auto_type", None)

                    # ✅ 3.3) pin lại + reprice sau khi auto-promo chạy
                    _force_website_pl(order)
                    _reprice_base(order)
                    _sync_gift_combo(order)

                    # (4) totals + auto discount
                    _compute_amounts(order)
                    _apply_auto_discount_idempotent(order, _get_auto_discount_pct(order))
                    _compute_amounts(order)

                    break

                except pg_errors.SerializationFailure:
                    request.env.cr.rollback()
                    if attempt == 2:
                        raise
                    continue

        except ValidationError as e:
            request.session["uc_voucher_msg"] = (e.args[0] if e.args else str(e))
            request.session["uc_voucher_type"] = "danger"
        except Exception as e:
            _logger.exception("Website apply voucher failed: %s", e)
            request.session["uc_voucher_msg"] = _("Không thể áp dụng voucher. Vui lòng thử lại.")
            request.session["uc_voucher_type"] = "danger"

        return request.redirect(redirect_url)
