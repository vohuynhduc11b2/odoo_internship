from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    oms_solar_scope_status = fields.Selection([
        ('draft', 'Chưa thống nhất'),
        ('confirmed', 'Đã thống nhất áp dụng'),
        ('excluded', 'Không áp dụng'),
    ], string='Trạng thái áp dụng OMS Solar', default='draft')
    oms_solar_scope_note = fields.Text(string='Ghi chú thống nhất dữ liệu OMS Solar')
