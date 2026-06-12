from odoo import models, fields, api

class OmsWarehouse(models.Model):
    _name = 'oms.warehouse'
    _description = 'Warehouse Info OMS'
    _order = 'whs_code desc'

    name = fields.Char('Tên kho', compute='_compute_name', store=True)
    whs_code = fields.Char('Mã kho', required=True, index=True)
    whs_name = fields.Char('Tên kho gốc')
    store_id = fields.Char('Mã Store')
    store_name = fields.Char('Tên Store')
    u_whs_type = fields.Char('Loại kho (U_WhsType)')
    active = fields.Boolean('Còn hoạt động', default=True)

    @api.depends('whs_name')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.whs_name or ''
