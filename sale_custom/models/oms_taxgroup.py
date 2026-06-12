from odoo import models, fields

class OmsTaxGroup(models.Model):
    _name = 'oms.taxgroup'
    _description = 'Tax Group OMS'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Mã thuế không được trùng!')
    ]

    code = fields.Char(string="Code", required=True, index=True)
    name = fields.Char(string="Tax Name", required=True)
    rate = fields.Float(string="Rate (%)", required=True, digits=(12, 6))
    active = fields.Boolean(string="Active", default=True)
