from odoo import api, fields, models


class OmsPromotion(models.Model):
    _inherit = "oms.promotion"

    oms_allow_customer_select_gift_combo = fields.Boolean(
        string="Khách hàng được chọn combo quà",
        default=False,
        help="OMS Solar đang áp dụng nghiệp vụ 1 chương trình = 1 combo quà cố định.",
    )
    oms_gift_qty_limit = fields.Float(
        string="Giới hạn số lượng quà",
        default=0.0,
        help="0 = không giới hạn riêng, hệ thống mặc định lấy số lượng quà bằng số lượng combo sản phẩm khách hàng mua.",
    )
    oms_force_gift_qty_by_combo_qty = fields.Boolean(
        string="Quà tặng theo SL combo mua",
        default=True,
        help="Bật để hệ thống lấy số lượng quà tặng theo đúng số lượng combo sản phẩm khách hàng mua, sau đó áp giới hạn nếu có.",
    )
    oms_apply_main_product_price = fields.Boolean(
        string="Áp giá sản phẩm chính theo CTKM",
        default=False,
        help="Cho phép PM nhập mức giá áp dụng trực tiếp cho sản phẩm chính khi chương trình khuyến mãi được áp dụng.",
    )
    oms_main_product_price = fields.Float(
        string="Giá áp dụng cho sản phẩm chính",
        digits="Product Price",
        default=0.0,
    )
    oms_gift_combo_note = fields.Char(
        string="Ghi chú chọn quà",
        default="Combo quà của chương trình được gán cố định theo cấu hình khuyến mãi.",
    )
    oms_has_gift_combo = fields.Boolean(
        string="Có combo quà tặng",
        compute="_compute_oms_has_gift_combo",
    )

    @api.depends_context("uid")
    def _compute_oms_has_gift_combo(self):
        for promo in self:
            combos = promo._oms_get_gift_combo_records()
            promo.oms_has_gift_combo = bool(combos)

    def _oms_get_gift_combo_records(self):
        """Return exactly one effective combo for each promotion.

        Nghiệp vụ OMS Solar: 1 CTKM = 1 combo quà cố định.
        Trường dữ liệu gốc vẫn là gift_combo_ids của hệ thống hiện hữu,
        nhưng addon chỉ lấy combo hợp lệ đầu tiên để áp dụng.
        """
        self.ensure_one()
        Combo = self.env['product.combo'].sudo()
        if 'gift_combo_ids' not in self._fields:
            return Combo.browse()

        raw = self.gift_combo_ids
        ids = []
        for combo in raw:
            try:
                combo_id = int(combo.id)
            except Exception:
                combo_id = False
            if combo_id:
                ids.append(combo_id)

        if not ids:
            return Combo.browse()

        # Re-resolve on the target model to avoid phantom/stale ids from broken M2M config.
        # Only keep the first valid combo because 1 promotion = 1 combo.
        return Combo.search([('id', 'in', ids)], order='id', limit=1)

    def _oms_get_effective_main_price(self):
        self.ensure_one()
        if not self.oms_apply_main_product_price:
            return False
        price = float(self.oms_main_product_price or 0.0)
        return price if price > 0 else False

    def _oms_get_effective_gift_qty(self, purchased_qty):
        self.ensure_one()
        purchased_qty = float(purchased_qty or 0.0)
        if purchased_qty <= 0:
            return 0.0
        qty = purchased_qty if self.oms_force_gift_qty_by_combo_qty or not self.oms_gift_qty_limit else float(self.oms_gift_qty_limit or 0.0)
        limit = float(self.oms_gift_qty_limit or 0.0)
        if limit > 0:
            qty = min(qty, limit)
        return max(qty, 0.0)
