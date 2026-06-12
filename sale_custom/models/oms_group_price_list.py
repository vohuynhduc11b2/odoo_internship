# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class OmsGroupPriceList(models.Model):
    _name = "oms.group.price.list"
    _description = "OMS Group Price List"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Tên bảng giá nhóm", required=True, tracking=True)
    note = fields.Char(string="Ghi chú", tracking=True)
    from_date = fields.Date(string="Từ ngày", required=True, tracking=True)
    to_date   = fields.Date(string="Đến ngày", required=True, tracking=True)

    line_ids = fields.One2many("oms.group.price.list.line", "pricelist_id", string="Lines")

    @api.constrains('from_date', 'to_date')
    def _check_date_range(self):
        for r in self:
            if r.from_date and r.to_date and r.to_date < r.from_date:
                raise ValidationError(_("Đến ngày phải >= Từ ngày."))

    @api.onchange('from_date', 'to_date')
    def _sync_line_dates(self):
        for rec in self:
            for line in rec.line_ids:
                if not line.manual_from_date:
                    line.from_date = rec.from_date
                if not line.manual_to_date:
                    line.to_date = rec.to_date

    def apply_for(self, *, product, order_currency, base_price, qty,
                  on_date=None, partner=None, product_group_id=None, **_):
        self.ensure_one()
        order_currency = order_currency or self.env.company.currency_id
        on_date = on_date or fields.Date.today()

        # Lọc line theo ngày hiệu lực
        candidates = self.line_ids.filtered(lambda l:
            l.active
            and (not l.from_date or l.from_date <= on_date)
            and (not l.to_date or l.to_date >= on_date)
        )

        best, best_score = None, -1.0
        for rule in candidates:
            sc = rule._match_score(
                partner=partner,
                product_group_id=product_group_id,
            )
            if sc > best_score:
                best, best_score = rule, sc

        if not best or best_score < 0:
            return None, None

        new_price = best.compute_new_price(
            order_currency=order_currency,
            base_price=base_price,
            on_date=on_date,
        )
        return new_price, best


class OmsGroupPriceListLine(models.Model):
    _name = "oms.group.price.list.line"
    _description = "OMS Group Price List Line"
    _order = "sequence, id"

    pricelist_id = fields.Many2one(
        "oms.group.price.list", string="Header", required=True, index=True, ondelete="cascade"
    )
    active = fields.Boolean(default=True, tracking=True)

    # 👇 Đổi nhãn cho dễ hiểu trên UI
    sequence = fields.Integer(string="Độ ưu tiên", default=10, tracking=True,
                              help="Ưu tiên khi nhiều dòng cùng khớp")

    manual_from_date = fields.Boolean(default=False)
    manual_to_date   = fields.Boolean(default=False)
    from_date = fields.Date(string="Từ ngày")
    to_date   = fields.Date(string="Đến ngày")

    partner_scope = fields.Selection([
        ('all',     'Tất cả KH'),
        ('exclude', 'Tất cả trừ các KH này'),
        ('include', 'Chỉ các KH này'),
    ], default='all', required=True, tracking=True)

    # ✅ Chỉ khách hàng
    partner_ids = fields.Many2many(
        'oms.customer',
        string="Khách hàng OMS",
    )
    product_group_ids = fields.Many2many(
        'oms.product.group',
        'oms_grp_pricelist_line_pg_in_rel', 'line_id', 'group_id',
        string="Nhóm SP áp dụng", index=True, required=True
    )

    extra_amount = fields.Float(string="Cộng thêm/đơn vị", required=True, help="Chỉ số dương.")
    currency_id = fields.Many2one('res.currency',
                                  default=lambda s: s.env.company.currency_id.id,
                                  required=True)

    _sql_constraints = [
        ('extra_positive', 'CHECK(extra_amount > 0)', 'Giá cộng thêm phải > 0.'),
    ]

    @api.constrains('partner_scope', 'partner_ids')
    def _check_partner_scope(self):
        for r in self:
            if r.partner_scope in ('include', 'exclude') and not r.partner_ids:
                raise ValidationError(_("Vui lòng chọn Khách hàng khi dùng chế độ này."))

    @api.onchange('partner_scope')
    def _onchange_partner_scope(self):
        if self.partner_scope == 'all':
            self.partner_ids = [(5, 0, 0)]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        pid = self.env.context.get('active_id') or self.env.context.get('default_pricelist_id')
        if pid:
            res.setdefault('pricelist_id', pid)
        return res

    def _match_score(self, *, partner, product_group_id):
        if not self.active:
            return -1
        if not product_group_id or product_group_id not in self.product_group_ids.ids:
            return -1

        partner_cp = partner.commercial_partner_id if partner else False
        if self.partner_scope == 'include':
            if not partner_cp or partner_cp not in self.partner_ids.mapped('commercial_partner_id'):
                return -1
            return 10.0
        if self.partner_scope == 'exclude':
            if partner_cp and partner_cp in self.partner_ids.mapped('commercial_partner_id'):
                return -1
            return 5.0
        return 3.0

    def compute_new_price(self, *, order_currency, base_price, on_date=None):
        on_date = on_date or fields.Date.today()
        extra = self.currency_id._convert(self.extra_amount, order_currency, self.env.company, on_date)
        return (base_price or 0.0) + extra
