from odoo import models, fields, api

class OmsSpecialPrice(models.Model):
    _name = "oms.special.price"
    _description = "Special Price for Customer"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    customer_id = fields.Many2one(
        "res.partner", string="Customer", required=True, tracking=True, index=True)
    business_unit = fields.Char(string="Business Unit")
    level_code = fields.Char(string="Level Code")
    note = fields.Char(string="Note")
    valid_from = fields.Date(string="Ngày bắt đầu", required=True)
    valid_to = fields.Date(string="Ngày kết thúc", required=True)

    line_ids = fields.One2many("oms.special.price.line", "special_price_id", string="Special Price Lines")

    @api.onchange('valid_from', 'valid_to')
    def _onchange_date_sync_lines(self):
        """Tự động cập nhật ngày cho các dòng nếu chỉnh ở tổng, chỉ update dòng chưa sửa tay."""
        for rec in self:
            for line in rec.line_ids:
                if not line.manual_valid_from:
                    line.valid_from = rec.valid_from
                if not line.manual_valid_to:
                    line.valid_to = rec.valid_to

class OmsSpecialPriceLine(models.Model):
    _name = "oms.special.price.line"
    _description = "Special Price Line"
    _inherit = ['mail.thread']

    special_price_id = fields.Many2one(
        "oms.special.price", string="Special Price", required=True, ondelete="cascade")
    customer_id = fields.Many2one(
        "res.partner", related="special_price_id.customer_id", store=True, index=True, string="Customer")
    item_id = fields.Many2one("product.product", string="Item", required=True, tracking=True)
    item_code = fields.Char(string="Item Code", related="item_id.default_code", store=True, readonly=True)
    special_price = fields.Float(string="Special Price", required=True, tracking=True)
    # Manual flag cho ngày
    manual_valid_from = fields.Boolean(string="Sửa ngày bắt đầu", default=False)
    manual_valid_to = fields.Boolean(string="Sửa ngày kết thúc", default=False)
    valid_from = fields.Date(string="Valid From", required=True)
    valid_to = fields.Date(string="Valid To", required=True)
    note = fields.Char(string="Note")

    @api.model
    def create(self, vals):
        """Tự động lấy ngày tổng khi thêm mới nếu chưa nhập tay."""
        special_price_id = vals.get('special_price_id')
        price = self.env['oms.special.price'].browse(special_price_id) if special_price_id else False
        if not vals.get('valid_from') and price and price.valid_from:
            vals['valid_from'] = price.valid_from
        if not vals.get('valid_to') and price and price.valid_to:
            vals['valid_to'] = price.valid_to
        return super().create(vals)

    @api.onchange('special_price_id')
    def _onchange_special_price_id(self):
        if self.special_price_id:
            if not self.valid_from:
                self.valid_from = self.special_price_id.valid_from
            if not self.valid_to:
                self.valid_to = self.special_price_id.valid_to

    @api.onchange('valid_from')
    def _onchange_manual_valid_from(self):
        for rec in self:
            rec.manual_valid_from = (rec.valid_from != rec.special_price_id.valid_from)

    @api.onchange('valid_to')
    def _onchange_manual_valid_to(self):
        for rec in self:
            rec.manual_valid_to = (rec.valid_to != rec.special_price_id.valid_to)

    @api.model
    def get_special_price_for_customer(self, customer_id, product_id, order_date=None):
        order_date = order_date or fields.Date.today()
        line = self.search([
            ('customer_id', '=', customer_id),
            ('item_id', '=', product_id),
            ('valid_from', '<=', order_date),
            ('valid_to', '>=', order_date),
        ], order="valid_from desc", limit=1)
        return line.special_price if line else False

    _sql_constraints = [
        (
            'uniq_special_price_line',
            'unique(special_price_id, item_id, valid_from, valid_to)',
            'Each item must have only one special price for the same period and customer!'
        )
    ]
