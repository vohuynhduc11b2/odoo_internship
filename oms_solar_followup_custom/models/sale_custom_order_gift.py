from odoo import fields, models


class SaleCustomOrderGift(models.Model):
    _inherit = 'sale_custom.order'

    oms_gift_combo_selection_ids = fields.One2many('oms.solar.gift.combo.selection', 'order_id', string='Lựa chọn combo quà tặng')
    oms_gift_combo_selection_count = fields.Integer(string='Số lựa chọn combo quà', compute='_compute_oms_gift_combo_selection_count')

    def _compute_oms_gift_combo_selection_count(self):
        for order in self:
            order.oms_gift_combo_selection_count = len(order.oms_gift_combo_selection_ids)

    def action_sync_oms_gift_combo(self):
        for order in self:
            lines = getattr(order, 'order_line', self.env['sale_custom.order.line'])
            if hasattr(lines, '_oms_sync_gift_combo_promotions'):
                lines._oms_sync_gift_combo_promotions()
        return True

    def action_confirm(self):
        self.action_sync_oms_gift_combo()
        return super().action_confirm()
