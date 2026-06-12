from odoo import _, api, fields, models


class OmsPaymentIncidentWizard(models.TransientModel):
    _name = 'oms.payment.incident.wizard'
    _description = 'Wizard ghi nhận sự cố thanh toán'

    title = fields.Char(string='Tiêu đề', required=True)
    scope = fields.Selection(
        selection=[
            ('payment', 'Thanh toán'),
            ('order', 'Đặt hàng'),
            ('checkout', 'Checkout'),
        ],
        default='payment',
        required=True,
    )
    incident_type = fields.Selection(
        selection=[('warning', 'Cảnh báo'), ('error', 'Lỗi')],
        string='Loại',
        default='error',
        required=True,
    )
    severity = fields.Selection(
        selection=[
            ('low', 'Thấp'),
            ('medium', 'Trung bình'),
            ('high', 'Cao'),
            ('critical', 'Nghiêm trọng'),
        ],
        string='Mức độ',
        default='medium',
        required=True,
    )
    category_id = fields.Many2one('oms.payment.incident.category', string='Danh mục sự cố')
    transaction_id = fields.Many2one('payment.transaction', string='Giao dịch thanh toán')
    sale_order_id = fields.Many2one('sale.order', string='Đơn bán hàng')
    partner_id = fields.Many2one('res.partner', string='Khách hàng')
    currency_id = fields.Many2one('res.currency', string='Tiền tệ')
    amount = fields.Monetary(string='Số tiền', currency_field='currency_id')
    root_cause = fields.Text(string='Nguyên nhân gốc', required=True)
    prevention_action = fields.Text(string='Cách phòng tránh', required=True)
    correction_action = fields.Text(string='Hướng xử lý/khắc phục')
    symptom = fields.Text(string='Mô tả hiện tượng')
    technical_note = fields.Text(string='Ghi chú kỹ thuật')
    is_repeat_issue = fields.Boolean(string='Lỗi lặp lại')

    @api.onchange('category_id')
    def _onchange_category_id(self):
        if self.category_id:
            self.incident_type = self.category_id.default_incident_type
            self.severity = self.category_id.default_severity
            if self.category_id.default_root_cause:
                self.root_cause = self.category_id.default_root_cause
            if self.category_id.default_prevention_action:
                self.prevention_action = self.category_id.default_prevention_action

    @api.onchange('transaction_id')
    def _onchange_transaction_id(self):
        if self.transaction_id:
            tx = self.transaction_id
            self.partner_id = tx.partner_id
            self.currency_id = tx.currency_id
            self.amount = tx.amount
            if hasattr(tx, '_get_primary_sale_order'):
                self.sale_order_id = tx._get_primary_sale_order()
            if not self.title:
                self.title = _('Sự cố thanh toán - %s') % (tx.reference or tx.id)

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if self.sale_order_id and not self.transaction_id:
            order = self.sale_order_id
            self.partner_id = order.partner_id
            self.currency_id = order.currency_id
            self.amount = order.amount_total
            if not self.title:
                self.title = _('Sự cố thanh toán - %s') % (order.name or order.id)

    def action_create_incident(self):
        self.ensure_one()
        vals = {
            'title': self.title,
            'scope': self.scope,
            'incident_type': self.incident_type,
            'severity': self.severity,
            'category_id': self.category_id.id,
            'transaction_id': self.transaction_id.id,
            'sale_order_id': self.sale_order_id.id,
            'partner_id': self.partner_id.id,
            'currency_id': self.currency_id.id,
            'amount': self.amount,
            'root_cause': self.root_cause,
            'prevention_action': self.prevention_action,
            'correction_action': self.correction_action,
            'symptom': self.symptom,
            'technical_note': self.technical_note,
            'is_repeat_issue': self.is_repeat_issue,
        }
        incident = self.env['oms.payment.incident.log'].create(vals)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sự cố thanh toán'),
            'res_model': 'oms.payment.incident.log',
            'view_mode': 'form',
            'res_id': incident.id,
            'target': 'current',
        }
