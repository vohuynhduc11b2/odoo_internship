from odoo import fields, models


class OmsDeliveryLeadtimeRule(models.Model):
    _name = 'oms.delivery.leadtime.rule'
    _description = 'OMS Delivery Leadtime Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    state_id = fields.Many2one('res.country.state', string='Tỉnh/Thành', required=True, ondelete='cascade')
    delivery_days = fields.Integer(string='Số ngày giao', required=True, default=1)
    note = fields.Text(string='Ghi chú')
    company_id = fields.Many2one('res.company', string='Công ty', default=lambda self: self.env.company)

    _sql_constraints = [
        (
            'oms_delivery_leadtime_rule_unique',
            'unique(state_id, company_id)',
            'Mỗi công ty chỉ nên có một rule ngày giao cho mỗi tỉnh/thành.',
        ),
    ]
