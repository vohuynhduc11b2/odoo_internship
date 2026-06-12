from odoo import fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    oms_lock_flag = fields.Boolean(string='Lock OMS')
    oms_lock_note = fields.Char(string='Lý do lock OMS')
    oms_expected_delivery_date = fields.Date(string='Ngày giao hàng dự kiến OMS')

    def copy(self, default=None):
        default = dict(default or {})
        default.setdefault('oms_lock_flag', self.oms_lock_flag)
        default.setdefault('oms_lock_note', self.oms_lock_note)
        default.setdefault('oms_expected_delivery_date', self.oms_expected_delivery_date)
        return super().copy(default)
