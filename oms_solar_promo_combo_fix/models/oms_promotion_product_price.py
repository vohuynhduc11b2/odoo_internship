from odoo import fields, models


class OmsPromotionProductPrice(models.Model):
    _name = 'oms.promotion.product.price'
    _description = 'OMS Promotion Product Price'
    _order = 'sequence, id'
    _sql_constraints = [
        ('oms_promotion_product_price_unique', 'unique(promotion_id, product_tmpl_id, apply_scope)', 'Mỗi sản phẩm chỉ có một dòng giá CTKM cho từng phạm vi áp dụng.'),
    ]

    sequence = fields.Integer(default=10)
    promotion_id = fields.Many2one('oms.promotion', required=True, ondelete='cascade', index=True)
    product_tmpl_id = fields.Many2one('product.template', string='Sản phẩm', required=True, ondelete='cascade')
    apply_scope = fields.Selection([
        ('main', 'Sản phẩm chính'),
        ('accessory', 'Sản phẩm bán kèm'),
        ('all', 'Cả chính và bán kèm'),
    ], string='Áp dụng cho', default='all', required=True)
    promo_price = fields.Float(string='Giá khuyến mãi', digits='Product Price', required=True)
    note = fields.Char(string='Ghi chú')
    active = fields.Boolean(default=True)
