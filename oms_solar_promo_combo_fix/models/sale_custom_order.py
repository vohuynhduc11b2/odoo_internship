from odoo import models


class SaleCustomOrder(models.Model):
    _inherit = 'sale_custom.order'

    def action_sync_oms_gift_combo(self):
        for order in self:
            lines = getattr(order, 'order_line', self.env['sale_custom.order.line'])
            lines = lines.filtered(lambda l: getattr(l, 'product_id', False) and not getattr(l, 'display_type', False))
            if hasattr(lines, '_oms_sync_gift_combo_promotions'):
                lines._oms_sync_gift_combo_promotions()
        return True
