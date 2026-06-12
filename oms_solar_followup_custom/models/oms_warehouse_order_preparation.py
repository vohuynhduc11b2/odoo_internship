from odoo import api, fields, models


class OmsWarehouseOrderPreparation(models.Model):
    _inherit = 'oms.warehouse.order.preparation'

    oms_lock_flag = fields.Boolean(string='Lock OMS')
    oms_lock_note = fields.Char(string='Lý do lock OMS')
    oms_expected_delivery_date = fields.Date(string='Ngày giao hàng dự kiến OMS')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._oms_sync_from_sale_order()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._oms_sync_from_sale_order()
        return res

    def copy(self, default=None):
        default = dict(default or {})
        default.setdefault('oms_lock_flag', self.oms_lock_flag)
        default.setdefault('oms_lock_note', self.oms_lock_note)
        default.setdefault('oms_expected_delivery_date', self.oms_expected_delivery_date)
        return super().copy(default)

    def _oms_sync_from_sale_order(self):
        for rec in self:
            order = False
            for fname in ['sale_order_id', 'sale_custom_order_id', 'order_id', 'source_order_id']:
                if fname in rec._fields and rec[fname] and getattr(rec[fname], '_name', '') == 'sale_custom.order':
                    order = rec[fname]
                    break
            if not order:
                continue
            vals = {
                'oms_lock_flag': order.oms_lock_flag,
                'oms_lock_note': order.oms_lock_note,
                'oms_expected_delivery_date': order.oms_expected_delivery_date,
            }
            super(OmsWarehouseOrderPreparation, rec).write(vals)
