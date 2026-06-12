from odoo import _, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    payment_incident_ids = fields.One2many(
        'oms.payment.incident.log',
        'sale_order_id',
        string='Sự cố thanh toán',
    )
    payment_incident_count = fields.Integer(compute='_compute_payment_incident_count', string='Sự cố thanh toán')

    def _compute_payment_incident_count(self):
        grouped = self.env['oms.payment.incident.log'].read_group(
            [('sale_order_id', 'in', self.ids)],
            ['sale_order_id'],
            ['sale_order_id'],
            lazy=False,
        ) if self.ids else []
        mapped_data = {row['sale_order_id'][0]: row['__count'] for row in grouped if row.get('sale_order_id')}
        for order in self:
            order.payment_incident_count = mapped_data.get(order.id, 0)

    def action_view_payment_incidents(self):
        self.ensure_one()
        action = self.env.ref('payment_incident_tracker.action_oms_payment_incident_log').read()[0]
        action['domain'] = [('sale_order_id', '=', self.id)]
        action['context'] = {
            'default_sale_order_id': self.id,
            'default_partner_id': self.partner_id.id if self.partner_id else False,
            'default_scope': 'payment',
        }
        return action

    def action_log_payment_incident(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Ghi nhận sự cố thanh toán'),
            'res_model': 'oms.payment.incident.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_sale_order_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_scope': 'payment',
                'default_title': _('Sự cố thanh toán - %s') % (self.name or self.id),
                'default_amount': self.amount_total,
                'default_currency_id': self.currency_id.id,
            },
        }
