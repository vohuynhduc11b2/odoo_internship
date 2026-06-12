from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    oms_low_stock_warning_enabled = fields.Boolean(
        string='Cảnh báo tồn kho thấp OMS',
        default=False,
        help='Bật để cảnh báo tồn kho thấp trên website/đơn OMS cho nhóm sản phẩm này.',
    )
    oms_low_stock_threshold = fields.Float(
        string='Ngưỡng cảnh báo tồn kho',
        default=10.0,
    )
    oms_low_stock_warning_message = fields.Char(
        string='Thông điệp cảnh báo',
        default='Sắp hết hàng, vui lòng liên hệ NVKD',
    )
