from odoo import fields, models


class OmsSolarOutstationOption(models.Model):
    _name = 'oms.solar.outstation.option'
    _description = 'OMS Solar Outstation Option'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()
