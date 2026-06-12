# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class OmsPromotion(models.Model):
    _name = 'oms.promotion'
    _description = 'Promotion OMS/UC'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, valid_from desc'

    # --- Thông tin cơ bản ---
    code = fields.Char(string="Mã chương trình", required=True, tracking=True)
    name = fields.Char(string="Tên chương trình", required=True, tracking=True)
    active = fields.Boolean(string="Kích hoạt", default=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Nháp'),
        ('active', 'Đang áp dụng'),
        ('expired', 'Đã hết hạn'),
    ], string="Trạng thái", compute='_compute_state', store=True)

    sequence = fields.Integer(string="Thứ tự áp dụng", default=1, tracking=True)

    company_id = fields.Many2one(
        'res.company', string="Công ty",
        default=lambda self: self.env.company, required=True
    )

    bu = fields.Char(string="Đơn vị kinh doanh (BU)", tracking=True, default='AUT', readonly=True)

    channel = fields.Selection([
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('showroom', 'Showroom'),
    ], string="Kênh bán hàng", tracking=True)

    # --- Cách áp dụng (MỚI) ---
    apply_method = fields.Selection([
        ('auto', 'Tự động áp dụng'),
        ('manual', 'Chọn thủ công'),
        ('coupon', 'Nhập mã coupon'),
    ], string="Cách áp dụng", default='auto', required=True, tracking=True)

    # --- Thời gian và điều kiện đơn hàng ---
    valid_from = fields.Date(string="Ngày bắt đầu", tracking=True)
    valid_to = fields.Date(string="Ngày kết thúc", tracking=True)

    min_total_amount = fields.Monetary(
        string="Giá trị đơn hàng tối thiểu",
        tracking=True, currency_field='currency_id'
    )

    # Giữ field để tránh migration, nhưng ẩn trên form
    max_uses_per_order = fields.Integer(string="Giới hạn số lần áp dụng/đơn", tracking=True)
    max_uses_overall = fields.Integer(string="Giới hạn tổng số lần áp dụng", tracking=True)

    # --- Phạm vi áp dụng giảm giá ---
    apply_scope = fields.Selection([
        ('main_only', 'Chỉ sản phẩm chính'),
        ('main_and_bundle', 'Cả sản phẩm chính và bán kèm'),
    ], string="Giảm giá áp dụng cho", default='main_only', required=True, tracking=True)

    # --- Phạm vi khách hàng ---
    customer_scope = fields.Selection([
        ('all', 'Tất cả khách hàng'),
        ('specific', 'Theo khách hàng cụ thể'),
    ], string="Phạm vi khách hàng", default='all', required=True, tracking=True)

    partner_ids = fields.Many2many(
        'res.partner', string="Khách hàng áp dụng",
        help="Chỉ áp dụng cho các khách hàng này"
    )
    partner_category_ids = fields.Many2many(
        'res.partner.category', string="Nhóm khách hàng áp dụng"
    )

    # --- Khuyến mãi ---
    discount_type = fields.Selection([
        ('percent', 'Tỷ lệ %'),
        ('fixed', 'Giảm cố định'),
        ('bogo', 'Mua X tặng Y'),
        ('free_shipping', 'Miễn phí vận chuyển'),
    ], string="Loại khuyến mãi", tracking=True, default='percent')

    # % khi chọn "Tỷ lệ %"
    discount_percent = fields.Float(
        string="Tỷ lệ giảm (%)", digits=(16, 2),
        tracking=True, default=0.0
    )

    # Giá trị khi chọn "Giảm cố định"
    discount_value = fields.Monetary(
        string="Giảm cố định", currency_field='currency_id',
        tracking=True, default=0.0
    )

    currency_id = fields.Many2one(
        'res.currency', string="Tiền tệ",
        default=lambda self: self.env.company.currency_id, required=True
    )

    # --- Coupon code (nếu có) ---
    # Giữ field use_coupon để tránh migration/ảnh hưởng data cũ, nhưng sẽ sync với apply_method
    use_coupon = fields.Boolean(string="Sử dụng mã coupon", default=False)
    coupon_code = fields.Char(string="Mã coupon", index=True)
    coupon_valid_from = fields.Date(string="Coupon từ ngày")
    coupon_valid_to = fields.Date(string="Coupon đến ngày")
    max_uses_per_customer = fields.Integer(string="Giới hạn sử dụng/coupon/khách")

    # --- Điều kiện hợp đồng & thanh toán ---
    contract_code = fields.Char(string="Mã HĐ", tracking=True)

    contract_deposit = fields.Selection([
        ('none', 'Không cọc'),
        ('deposit', 'Có cọc')
    ], string="Có cọc?", default='none', tracking=True)

    contract_commitment_qty = fields.Float(string="SL cam kết", tracking=True)

    payment_type = fields.Selection([
        ('full', 'Thanh toán ngay'),
        ('down', 'Cọc trước'),
        ('installment', 'Trả góp'),
    ], string="Hình thức thanh toán", tracking=True)

    payment_percent = fields.Float(string="Tỷ lệ thanh toán (%)", tracking=True)

    # --- Liên kết sản phẩm & combo ---
    apply_product_line_ids = fields.One2many(
        'oms.promotion.apply.product.line', 'promotion_id',
        string="Sản phẩm áp dụng"
    )
    bundle_combo_ids = fields.One2many(
        'oms.promotion.bundle.combo', 'promotion_id',
        string="Combo mua kèm"
    )
    gift_combo_ids = fields.One2many(
        'oms.promotion.gift.combo', 'promotion_id',
        string="Combo quà tặng"
    )

    # --- Nhóm & kế thừa ---
    parent_id = fields.Many2one('oms.promotion', string="Chương trình cha", index=True)
    child_ids = fields.One2many('oms.promotion', 'parent_id', string="Chương trình con")

    is_group = fields.Boolean(
        string="Là nhóm chương trình",
        compute='_compute_is_group', store=True
    )

    # --- Kết hợp chương trình ---
    can_be_combined = fields.Boolean(string="Cho phép kết hợp", default=False)

    allowed_promotion_ids = fields.Many2many(
        'oms.promotion', 'oms_promotion_allowed_rel', 'promotion_id', 'allowed_promotion_id',
        string="Áp dụng đồng thời với"
    )

    restrict_combination = fields.Boolean(string="Chỉ kết hợp theo danh sách", default=True)

    # --- Lưu ý & mô tả ---
    note = fields.Text(string='Lưu ý', tracking=True)
    promo_description = fields.Text(
        string='Mô tả nhanh',
        compute='_compute_promo_description', store=False
    )

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Mã chương trình đã tồn tại!')
    ]

    # ==== COMPUTE / CONSTRAINS =================================================

    @api.depends('valid_from', 'valid_to')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.valid_to and rec.valid_to < today:
                rec.state = 'expired'
            elif rec.valid_from and rec.valid_from <= today <= (rec.valid_to or today):
                rec.state = 'active'
            else:
                rec.state = 'draft'

    @api.depends('child_ids')
    def _compute_is_group(self):
        for r in self:
            r.is_group = bool(r.child_ids)

    @api.onchange('customer_scope')
    def _onchange_customer_scope(self):
        if self.customer_scope == 'all':
            self.partner_ids = [(5,)]

    @api.onchange('discount_type')
    def _onchange_discount_type(self):
        if self.discount_type == 'percent':
            self.discount_value = 0.0
        elif self.discount_type == 'fixed':
            self.discount_percent = 0.0
        else:
            self.discount_percent = 0.0
            self.discount_value = 0.0

    # --- Sync apply_method <-> use_coupon ---
    @api.onchange('apply_method')
    def _onchange_apply_method(self):
        for r in self:
            if r.apply_method == 'coupon':
                r.use_coupon = True
            else:
                r.use_coupon = False
                # Dọn coupon data nếu không phải coupon
                r.coupon_code = False
                r.coupon_valid_from = False
                r.coupon_valid_to = False
                r.max_uses_per_customer = 0

    @api.constrains('discount_type', 'discount_percent', 'discount_value')
    def _check_discount_inputs(self):
        for r in self:
            if r.discount_type == 'percent':
                if r.discount_percent is None or r.discount_percent <= 0 or r.discount_percent > 100:
                    raise ValidationError("Tỷ lệ giảm (%) phải trong khoảng 0–100 và > 0.")
            if r.discount_type == 'fixed':
                if r.discount_value is None or r.discount_value <= 0:
                    raise ValidationError("Giảm cố định phải > 0.")

    @api.constrains('apply_method', 'use_coupon', 'coupon_code', 'coupon_valid_from', 'coupon_valid_to')
    def _check_apply_method(self):
        for r in self:
            if r.apply_method == 'coupon':
                if not r.coupon_code:
                    raise ValidationError("KM dạng 'Nhập mã coupon' bắt buộc có Mã coupon.")
                if r.coupon_valid_from and r.coupon_valid_to and r.coupon_valid_from > r.coupon_valid_to:
                    raise ValidationError("Coupon từ ngày không được lớn hơn Coupon đến ngày.")
            else:
                if r.use_coupon:
                    raise ValidationError("KM không phải dạng coupon thì không được bật 'Sử dụng mã coupon'.")

    @api.model_create_multi
    def create(self, vals_list):
        # mapping data cũ
        for vals in vals_list:
            if vals.get('use_coupon') and not vals.get('apply_method'):
                vals['apply_method'] = 'coupon'
            if vals.get('apply_method') == 'coupon':
                vals['use_coupon'] = True
            elif vals.get('apply_method') in ('auto', 'manual'):
                vals['use_coupon'] = False
        return super().create(vals_list)

    def write(self, vals):
        # sync khi đổi apply_method
        if 'apply_method' in vals:
            if vals['apply_method'] == 'coupon':
                vals.setdefault('use_coupon', True)
            else:
                vals.setdefault('use_coupon', False)
                vals.setdefault('coupon_code', False)
                vals.setdefault('coupon_valid_from', False)
                vals.setdefault('coupon_valid_to', False)
                vals.setdefault('max_uses_per_customer', 0)

        # sync khi set use_coupon trực tiếp
        if vals.get('use_coupon') and 'apply_method' not in vals:
            vals['apply_method'] = 'coupon'

        return super().write(vals)

    # Helper: kiểm tra hiệu lực "tại thời điểm hiện tại"
    def is_applicable_now(self, date=None):
        self.ensure_one()
        date = date or fields.Date.context_today(self)
        if not self.active or self.state != 'active':
            return False
        if self.valid_from and self.valid_from > date:
            return False
        if self.valid_to and self.valid_to < date:
            return False
        if self.apply_method == 'coupon':
            # coupon date range ưu tiên theo coupon_valid_* nếu có
            if self.coupon_valid_from and self.coupon_valid_from > date:
                return False
            if self.coupon_valid_to and self.coupon_valid_to < date:
                return False
        return True

    @api.depends(
        'name', 'code', 'bu', 'valid_from', 'valid_to',
        'apply_method', 'customer_scope', 'partner_ids',
        'discount_type', 'discount_percent', 'discount_value',
        'apply_product_line_ids', 'bundle_combo_ids', 'gift_combo_ids',
        'min_total_amount', 'can_be_combined', 'allowed_promotion_ids',
        'restrict_combination', 'note',
        'coupon_code', 'coupon_valid_from', 'coupon_valid_to'
    )
    def _compute_promo_description(self):
        label_apply_method = dict(self._fields['apply_method'].selection)
        for promo in self:
            lines = []
            lines.append(f"**{promo.name}** (Mã: {promo.code})")

            lines.append(f"- Cách áp dụng: {label_apply_method.get(promo.apply_method)}")

            if promo.customer_scope == 'specific' and promo.partner_ids:
                names = ', '.join(promo.partner_ids.mapped('name'))
                lines.append(f"- Áp dụng cho KH: {names}")
            else:
                lines.append("- Áp dụng cho: Tất cả khách hàng")

            if promo.bu:
                lines.append(f"- BU: {promo.bu}")

            if promo.valid_from and promo.valid_to:
                lines.append(f"- Hiệu lực: {promo.valid_from} → {promo.valid_to}")
            elif promo.valid_from and not promo.valid_to:
                lines.append(f"- Hiệu lực từ: {promo.valid_from}")
            elif promo.valid_to and not promo.valid_from:
                lines.append(f"- Hiệu lực đến: {promo.valid_to}")

            if promo.apply_method == 'coupon':
                lines.append(f"- Coupon: {promo.coupon_code or ''}".strip())
                if promo.coupon_valid_from and promo.coupon_valid_to:
                    lines.append(f"- Coupon hiệu lực: {promo.coupon_valid_from} → {promo.coupon_valid_to}")

            # Chiết khấu hiển thị theo loại
            if promo.discount_type == 'percent':
                lines.append(f"- Chiết khấu: {promo.discount_percent:.2f} %")
            elif promo.discount_type == 'fixed':
                symbol = promo.currency_id.symbol or ''
                lines.append(f"- Giảm cố định: {promo.discount_value:g} {symbol}".strip())
            elif promo.discount_type == 'bogo':
                lines.append("- Mua X tặng Y")
            elif promo.discount_type == 'free_shipping':
                lines.append("- Miễn phí vận chuyển")

            if promo.min_total_amount:
                lines.append(f"- Đơn tối thiểu: {promo.min_total_amount:g} {promo.currency_id.symbol}".strip())

            if promo.apply_product_line_ids:
                lines.append("- Áp dụng cho sản phẩm:")
                for pl in promo.apply_product_line_ids:
                    prod = pl.product_tmpl_id.display_name
                    qty_range = f"{pl.qty_from} → {pl.qty_to}"
                    lines.append(f"   • {prod}: {qty_range}")

            if promo.bundle_combo_ids:
                lines.append("- Combo mua kèm:")
                for combo in promo.bundle_combo_ids:
                    names = ', '.join(combo.product_tmpl_ids.mapped('name'))
                    lines.append(f"   • {combo.name}: {names}")

            if promo.gift_combo_ids:
                lines.append("- Combo quà tặng:")
                for gift in promo.gift_combo_ids:
                    names = ', '.join(gift.product_tmpl_ids.mapped('name'))
                    lines.append(f"   • {gift.name}: {names}")

            if promo.can_be_combined and promo.allowed_promotion_ids:
                lines.append("- Áp dụng đồng thời với:")
                for p in promo.allowed_promotion_ids:
                    lines.append(f"   • {p.name}")

            if promo.restrict_combination:
                lines.append("- Không kết hợp ngoài danh sách trên.")

            if promo.note:
                lines.append(f"- Lưu ý: {promo.note.strip()}")

            promo.promo_description = "\n".join(lines)


class OmsPromotionApplyProductLine(models.Model):
    _name = 'oms.promotion.apply.product.line'
    _description = 'Dòng sản phẩm áp dụng khuyến mãi'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    promotion_id = fields.Many2one('oms.promotion', required=True, ondelete='cascade', tracking=True)
    product_tmpl_id = fields.Many2one('product.template', string="Sản phẩm", required=True, tracking=True)
    product_category_id = fields.Many2one('product.category', string="Danh mục", tracking=True)
    qty_from = fields.Integer(string="Số lượng từ", required=True, default=1, tracking=True)
    qty_to = fields.Integer(string="Đến số lượng", required=True, default=999999, tracking=True)


class OmsPromotionBundleCombo(models.Model):
    _name = 'oms.promotion.bundle.combo'
    _description = 'Combo mua kèm'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    promotion_id = fields.Many2one('oms.promotion', required=True, ondelete='cascade', tracking=True)
    name = fields.Char(string="Tên combo", required=True, tracking=True)

    product_tmpl_ids = fields.Many2many(
        'product.template', 'oms_bundle_combo_product_rel',
        'combo_id', 'product_tmpl_id', string="Sản phẩm", tracking=True
    )


class OmsPromotionGiftCombo(models.Model):
    _name = 'oms.promotion.gift.combo'
    _description = 'Combo quà tặng'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    promotion_id = fields.Many2one('oms.promotion', required=True, ondelete='cascade', tracking=True)
    name = fields.Char(string="Tên combo quà tặng", required=True, tracking=True)

    product_tmpl_ids = fields.Many2many(
        'product.template', 'oms_gift_combo_product_rel',
        'combo_id', 'product_tmpl_id', string="Sản phẩm", tracking=True
    )
