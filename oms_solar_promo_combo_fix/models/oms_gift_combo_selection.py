from odoo import api, models
from odoo.exceptions import ValidationError


class OmsSolarGiftComboSelection(models.Model):
    _inherit = 'oms.solar.gift.combo.selection'

    @api.depends('promotion_id')
    def _compute_available_combo_ids(self):
        Combo = self.env['product.combo']
        for rec in self:
            rec.available_combo_ids = rec.promotion_id._oms_get_gift_combo_records() if rec.promotion_id else Combo

    @api.constrains('combo_id', 'selected_qty', 'allowed_qty')
    def _check_selection(self):
        for rec in self:
            if rec.combo_id and rec.combo_id not in rec.available_combo_ids:
                raise ValidationError('Combo quà không thuộc chương trình khuyến mãi này.')
            if rec.selected_qty < 0:
                raise ValidationError('Số lượng quà không được âm.')
            if rec.allowed_qty and rec.selected_qty > rec.allowed_qty:
                raise ValidationError('Số lượng quà không được vượt giới hạn cho phép.')

    @api.model
    def _sanitize_combo_id(self, combo_id):
        if not combo_id:
            return False
        combo = self.env['product.combo'].sudo().browse(int(combo_id)).exists()
        return combo.id if combo else False

    @api.model_create_multi
    def create(self, vals_list):
        clean = []
        for vals in vals_list:
            vals = dict(vals or {})
            if 'combo_id' in vals:
                vals['combo_id'] = self._sanitize_combo_id(vals.get('combo_id'))
            clean.append(vals)
        return super().create(clean)

    def write(self, vals):
        vals = dict(vals or {})
        if 'combo_id' in vals:
            vals['combo_id'] = self._sanitize_combo_id(vals.get('combo_id'))
        return super().write(vals)

    def action_apply_selection(self):
        """Selection is persisted on the order. Gift-line creation is intentionally
        decoupled from voucher apply to avoid breaking core promotion flow.
        """
        return True
