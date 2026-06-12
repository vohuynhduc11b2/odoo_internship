from odoo import models


class ProductCombo(models.Model):
    _inherit = "product.combo"

    def unlink(self):
        Selection = self.env["oms.solar.gift.combo.selection"].sudo().with_context(oms_skip_gift_sync=True)
        selections = Selection.search([("combo_id", "in", self.ids)])
        if selections:
            selections.write({"combo_id": False, "selected_qty": 0.0})
            selections._sync_gift_order_lines()
        return super().unlink()
