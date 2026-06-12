# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class MassCancelOrders(models.TransientModel):
    _name = 'sale_custom.mass.cancel.orders'
    _description = "Cancel multiple quotations"

    sale_order_ids = fields.Many2many(
        comodel_name='sale_custom.order',
        relation='sale_custom_mass_cancel_rel',  # tên bảng M2M mới
        column1='wizard_id',                     # FK -> sale_custom.mass.cancel.orders
        column2='order_id',                      # FK -> sale_custom.order
        string="Sale orders to cancel",
        default=lambda self: self.env.context.get('active_ids'),
    )
    sale_orders_count = fields.Integer(compute='_compute_sale_orders_count')
    has_confirmed_order = fields.Boolean(compute='_compute_has_confirmed_order')

    @api.depends('sale_order_ids')
    def _compute_sale_orders_count(self):
        for wizard in self:
            wizard.sale_orders_count = len(wizard.sale_order_ids)

    @api.depends('sale_order_ids')
    def _compute_has_confirmed_order(self):
        for wizard in self:
            wizard.has_confirmed_order = bool(
                wizard.sale_order_ids.filtered(lambda so: so.state in ['sale', 'done'])
            )

    def action_mass_cancel(self):
        self.sale_order_ids._action_cancel()
