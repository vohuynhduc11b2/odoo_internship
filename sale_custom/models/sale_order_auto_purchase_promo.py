# -*- coding: utf-8 -*-
import logging
from datetime import timedelta
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo import Command

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale_custom.order"

    website_auto_purchase_promo_id = fields.Many2one(
        "oms.promotion",
        string="KM tự động (mua lần đầu/hai)",
        copy=False,
        readonly=True,
    )
    website_auto_purchase_stage = fields.Selection(
        [("first", "Mua lần đầu"), ("second", "Mua lần hai")],
        string="Loại KM tự động",
        copy=False,
        readonly=True,
    )
    website_auto_purchase_applied = fields.Boolean(
        string="Đang áp dụng KM tự động",
        compute="_compute_website_auto_purchase_applied",
        store=False,
    )

    @api.depends("website_auto_purchase_promo_id")
    def _compute_website_auto_purchase_applied(self):
        for o in self:
            o.website_auto_purchase_applied = bool(o.website_auto_purchase_promo_id)

    # ==========================================================
    # LOG HELPERS
    # ==========================================================
    def _uc_log_lines(self, title=""):
        self.ensure_one()
        rows = []
        for l in self.order_line:
            if not l.product_id:
                continue
            rows.append({
                "line_id": l.id,
                "prod": l.product_id.display_name,
                "qty": float(getattr(l, "product_uom_qty", 0.0) or 0.0),
                "discount": float(getattr(l, "discount", 0.0) or 0.0),
                "is_gift": bool(getattr(l, "is_gift", False)),
                "is_bundle": bool(getattr(l, "is_bundle", False)),
                "linked_line_id": getattr(getattr(l, "linked_line_id", False), "id", False),
                "promo_ids": (l.promotion_ids.ids if "promotion_ids" in l._fields else []),
            })
        _logger.info(
            "[UC_LINES] %s order=%s stage=%s promo_id=%s lines=%s",
            title,
            self.id,
            getattr(self, "website_auto_purchase_stage", False),
            getattr(getattr(self, "website_auto_purchase_promo_id", False), "id", False),
            rows
        )

    # ==========================================================
    # CONFIG HELPERS
    # ==========================================================
    def _auto_purchase_cfg(self):
        ICP = self.env["ir.config_parameter"].sudo()

        def _int(key, default):
            try:
                return int(ICP.get_param(key, default))
            except Exception:
                return default

        return {
            "first_id": _int("sale_custom.auto_purchase_first_promo_id", 0),
            "second_id": _int("sale_custom.auto_purchase_second_promo_id", 0),
            "second_days": _int("sale_custom.auto_purchase_second_days", 15),
            "reset_months": _int("sale_custom.auto_purchase_reset_months", 12),
            "exclude_group_name": (ICP.get_param("sale_custom.auto_purchase_exclude_group_name", "Tấm pin") or "").strip(),
        }

    def _get_auto_purchase_promos(self):
        cfg = self._auto_purchase_cfg()
        Promo = self.env["oms.promotion"].sudo()
        first_promo = Promo.browse(cfg["first_id"]).exists() if cfg["first_id"] else Promo
        second_promo = Promo.browse(cfg["second_id"]).exists() if cfg["second_id"] else Promo
        return first_promo, second_promo, cfg

    # ==========================================================
    # PRODUCT EXCLUDE (tấm pin)
    # ==========================================================
    def _is_excluded_auto_purchase_product(self, product, cfg):
        if not product:
            return False
        exclude_name = (cfg.get("exclude_group_name") or "").lower().strip()
        if not exclude_name:
            return False

        candidates = [
            getattr(product, "ItmsGrpNam", False),
            getattr(product, "itms_grp_nam", False),
            getattr(product, "itmsgr_nam", False),
        ]
        tmpl = getattr(product, "product_tmpl_id", False)
        if tmpl:
            candidates += [
                getattr(tmpl, "ItmsGrpNam", False),
                getattr(tmpl, "itms_grp_nam", False),
            ]

        for v in candidates:
            if v and str(v).strip().lower() == exclude_name:
                return True
        return False

    # ==========================================================
    # STAGE RULE
    # ==========================================================
    def _compute_auto_purchase_stage(self, partner, cfg):
        """Lần 1/Lần 2 tính theo số đơn (báo giá) đã tạo, không cần paid/sale.
           Kẹp thêm gate theo OMS u_last_buy_date/u_first_buy_date:
           - Nếu đã mua trong reset_months (vd 12 tháng) => chặn luôn (False)
           - Nếu null => không chặn, vẫn dùng logic cũ để ra first/second theo order history
        """
        self.ensure_one()
        if not partner:
            return False
    
        partner = partner.sudo().commercial_partner_id
        today = fields.Date.context_today(self)
    
        reset_months = max(int(cfg.get("reset_months") or 12), 0)
    
        # =========================================================
        # OMS GATE: chặn nếu OMS nói khách đã mua trong 12 tháng
        # =========================================================
        try:
            card_code = False
            if "x_oms_card_code" in partner._fields and partner.x_oms_card_code:
                card_code = (partner.x_oms_card_code or "").strip()
            elif partner.ref:
                card_code = (partner.ref or "").strip()
    
            if card_code and reset_months:
                cust = self.env["oms.customer"].sudo().search([("card_code", "=", card_code)], limit=1)
                if cust:
                    # Nếu OMS có last_buy_date và vẫn nằm trong 12 tháng -> chặn KM
                    if cust.u_last_buy_date:
                        last_date = fields.Date.to_date(fields.Datetime.to_datetime(cust.u_last_buy_date))
                        # trong 12 tháng: today <= last + reset_months => không áp
                        if today <= (last_date + relativedelta(months=reset_months)):
                            return False
                    # Nếu first/last đều null => coi như "mới" theo OMS,
                    # nhưng KHÔNG return 'first' tại đây để đơn thứ 2 vẫn ra 'second' theo order history.
        except Exception:
            # Nếu OMS lỗi/missing data thì bỏ qua gate, fallback logic cũ
            pass
        
        # =========================================================
        # LOGIC CŨ: tính stage theo order history trên Odoo
        # =========================================================
        Order = self.env[self._name].sudo()  # sale_custom.order
        domain = [
            ("partner_id", "child_of", partner.id),
            ("id", "!=", self.id),
            ("state", "!=", "cancel"),
        ]
        # chỉ tính đơn website (để không lẫn đơn nội bộ)
        if "website_id" in Order._fields and self.website_id:
            domain.append(("website_id", "=", self.website_id.id))
    
        orders = Order.search(domain, order="date_order asc")
    
        # Không có đơn nào trước đó -> lần đầu
        if not orders:
            return "first"
    
        def _odate(o):
            dt = o.date_order or o.create_date
            return fields.Date.to_date(dt)
    
        # reset theo chu kỳ dựa trên lịch sử đơn (giữ nguyên)
        last_dt = _odate(orders[-1])
        if reset_months and today > (last_dt + relativedelta(months=reset_months)):
            return "first"
    
        cycle_start = orders[0]
        prev_dt = _odate(orders[0])
        for o in orders[1:]:
            dt = _odate(o)
            if reset_months and dt > (prev_dt + relativedelta(months=reset_months)):
                cycle_start = o
            prev_dt = dt
    
        cycle_orders = orders.filtered(
            lambda o: (o.date_order or o.create_date) >= (cycle_start.date_order or cycle_start.create_date)
        )
    
        # Trong cycle đã có >=2 đơn rồi -> không áp KM mua lần 1/2 nữa
        if len(cycle_orders) != 1:
            return False
    
        # Có đúng 1 đơn trước đó trong cycle -> xét “lần 2” theo second_days
        second_days = max(int(cfg.get("second_days") or 15), 0)
        first_dt = _odate(cycle_orders[0])
        if second_days and today <= (first_dt + timedelta(days=second_days)):
            return "second"
        return False

    # ==========================================================
    # APPLY/REMOVE purchase promos on lines
    # ==========================================================
    def _remove_auto_purchase_promos_from_lines(self, first_promo, second_promo):
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: l.product_id and not getattr(l, "display_type", False))
        if "is_gift" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_gift)
        if "is_bundle" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_bundle)

        if not lines or "promotion_ids" not in lines._fields:
            return

        rm_ids = [p.id for p in (first_promo, second_promo) if p]
        if not rm_ids:
            return

        for ln in lines:
            keep = [pid for pid in ln.promotion_ids.ids if pid not in rm_ids]
            ln.promotion_ids = [Command.set(keep)]

        try:
            lines.apply_promotions_to_line()
        except Exception:
            _logger.exception("[UC_PROMO] apply_promotions_to_line failed after remove")

    # ==========================================================
    # MAIN ENTRY: gọi sau mỗi cart update / load cart
    # ==========================================================
    def website_apply_auto_purchase_promo(self):
        """FIX DỨT ĐIỂM:
        - Không được continue khi promo đã đúng (vì line mới add chưa có promo_ids).
        - Luôn ensure promo vào tất cả line eligible, rồi apply_promotions_to_line().
        """
        for order in self:
            if order.env.context.get("skip_auto_purchase_promo"):
                continue
            if "website_id" in order._fields and not order.website_id:
                continue
            if getattr(order, "state", "draft") != "draft":
                continue

            first_promo, second_promo, cfg = order._get_auto_purchase_promos()
            if not first_promo and not second_promo:
                continue

            stage = order._compute_auto_purchase_stage(order.partner_id, cfg)
            target_promo = first_promo if stage == "first" else (second_promo if stage == "second" else False)

            _logger.info("[UC_PROMO] auto_purchase order=%s stage=%s target=%s current=%s",
                         order.id, stage, getattr(target_promo, "id", False),
                         getattr(getattr(order, "website_auto_purchase_promo_id", False), "id", False))
            order._uc_log_lines("BEFORE website_apply_auto_purchase_promo")

            # line thật
            lines = order.order_line.filtered(lambda l: l.product_id and not getattr(l, "display_type", False))
            if "is_gift" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_gift)
            if "is_bundle" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_bundle)

            # CASE: không còn áp
            if not target_promo:
                if order.website_auto_purchase_promo_id:
                    order.with_context(skip_auto_purchase_promo=True)._remove_auto_purchase_promos_from_lines(first_promo, second_promo)
                order.sudo().write({"website_auto_purchase_promo_id": False, "website_auto_purchase_stage": False})
                order._uc_log_lines("AFTER website_apply_auto_purchase_promo (no target)")
                continue

            # Nếu target đổi => remove cả 2 trước
            if order.website_auto_purchase_promo_id and order.website_auto_purchase_promo_id.id != target_promo.id:
                order.with_context(skip_auto_purchase_promo=True)._remove_auto_purchase_promos_from_lines(first_promo, second_promo)

            # Ensure target promo có trên ALL eligible lines (đây là FIX chính)
            eligible = lines.filtered(lambda l: not order._is_excluded_auto_purchase_product(l.product_id, cfg))
            excluded = lines - eligible

            if "promotion_ids" in lines._fields:
                missing = eligible.filtered(lambda l: target_promo.id not in l.promotion_ids.ids)
                _logger.info("[UC_PROMO] eligible=%s missing=%s excluded=%s",
                             len(eligible), len(missing), len(excluded))

                if missing:
                    missing.sudo().write({"promotion_ids": [(4, target_promo.id)]})
                    _logger.info("[UC_PROMO] added promo=%s to lines=%s", target_promo.id, missing.ids)

                # đảm bảo excluded không dính auto-purchase
                if excluded:
                    for ln in excluded:
                        if target_promo.id in ln.promotion_ids.ids:
                            keep = [pid for pid in ln.promotion_ids.ids if pid != target_promo.id]
                            ln.promotion_ids = [Command.set(keep)]

                # Apply lại promo cho eligible (để nó set discount)
                try:
                    if eligible:
                        eligible.apply_promotions_to_line()
                except Exception:
                    _logger.exception("[UC_PROMO] apply_promotions_to_line failed on eligible")

            # update banner ALWAYS
            order.sudo().write({
                "website_auto_purchase_promo_id": target_promo.id,
                "website_auto_purchase_stage": stage,
            })

            order._uc_log_lines("AFTER website_apply_auto_purchase_promo")

        return True