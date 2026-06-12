from odoo import api, fields, models
from odoo.exceptions import ValidationError


class OmsSolarGiftComboSelection(models.Model):
    _name = 'oms.solar.gift.combo.selection'
    _description = 'OMS Solar Gift Combo Selection'
    _order = 'order_id desc, id desc'
    _rec_name = 'display_name'

    display_name = fields.Char(compute='_compute_display_name', store=False)
    order_id = fields.Many2one('sale_custom.order', string='Đơn OMS', required=True, ondelete='cascade', index=True)
    line_id = fields.Many2one('sale_custom.order.line', string='Dòng sản phẩm chính', required=True, ondelete='cascade', index=True)
    promotion_id = fields.Many2one('oms.promotion', string='Chương trình khuyến mãi', required=True, ondelete='cascade', index=True)
    available_combo_ids = fields.Many2many('product.combo', string='Combo quà được chọn', compute='_compute_available_combo_ids')
    combo_id = fields.Many2one('product.combo', string='Combo quà tặng', domain="[('id', 'in', available_combo_ids)]", ondelete='set null')
    purchased_qty = fields.Float(string='SL combo sản phẩm mua', readonly=True)
    allowed_qty = fields.Float(string='SL quà tối đa', readonly=True)
    selected_qty = fields.Float(string='SL quà đã chọn', default=0.0)
    main_product_price = fields.Float(string='Giá áp dụng SP chính', digits='Product Price', readonly=True)
    note = fields.Char(string='Ghi chú')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('oms_gift_combo_selection_unique', 'unique(order_id, line_id, promotion_id)', 'Mỗi chương trình chỉ được có một dòng chọn combo quà trên mỗi dòng sản phẩm.'),
    ]

    @api.depends('line_id', 'promotion_id', 'combo_id')
    def _compute_display_name(self):
        for rec in self:
            parts = [
                rec.order_id.display_name if rec.order_id else False,
                rec.line_id.product_id.display_name if rec.line_id and getattr(rec.line_id, 'product_id', False) else rec.line_id.display_name if rec.line_id else False,
                rec.promotion_id.display_name if rec.promotion_id else False,
                rec.combo_id.display_name if rec.combo_id else False,
            ]
            rec.display_name = ' / '.join([p for p in parts if p]) or 'Lựa chọn combo quà'

    def name_get(self):
        return [(rec.id, rec.display_name or 'Lựa chọn combo quà') for rec in self]

    @api.depends('promotion_id')
    def _compute_available_combo_ids(self):
        Combo = self.env['product.combo']
        for rec in self:
            rec.available_combo_ids = rec.promotion_id._oms_get_gift_combo_records() if rec.promotion_id else Combo

    @api.constrains('selected_qty', 'allowed_qty')
    def _check_selected_qty(self):
        for rec in self:
            if rec.selected_qty < 0:
                raise ValidationError('Số lượng quà đã chọn không được âm.')
            if rec.allowed_qty and rec.selected_qty > rec.allowed_qty:
                raise ValidationError('Số lượng quà đã chọn không được vượt giới hạn của chương trình.')

    def _sanitize_combo_vals(self, vals):
        vals = dict(vals or {})
        Combo = self.env['product.combo'].sudo()
        combo_id = vals.get('combo_id')
        if combo_id:
            combo = Combo.browse(int(combo_id)).exists()
            vals['combo_id'] = combo.id if combo else False
        elif 'combo_id' in vals:
            vals['combo_id'] = False
        if vals.get('combo_id') in (False, None) and 'selected_qty' in vals:
            try:
                vals['selected_qty'] = max(float(vals.get('selected_qty') or 0.0), 0.0)
            except Exception:
                vals['selected_qty'] = 0.0
        return vals

    def action_apply_selection(self):
        return True

    @api.model_create_multi
    def create(self, vals_list):
        clean_vals_list = [self._sanitize_combo_vals(vals) for vals in vals_list]
        records = super().create(clean_vals_list)
        for rec in records:
            if rec.combo_id and not rec.selected_qty:
                rec.selected_qty = rec.allowed_qty
        return records

    def write(self, vals):
        clean_vals = self._sanitize_combo_vals(vals)
        return super().write(clean_vals)
