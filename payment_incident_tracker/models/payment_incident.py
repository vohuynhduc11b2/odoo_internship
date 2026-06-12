from odoo import _, api, fields, models


class OmsPaymentIncidentCategory(models.Model):
    _name = 'oms.payment.incident.category'
    _description = 'Danh mục sự cố thanh toán'
    _order = 'sequence, name'

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    scope = fields.Selection(
        selection=[
            ('payment', 'Thanh toán'),
            ('order', 'Đặt hàng'),
            ('both', 'Cả hai'),
        ],
        default='payment',
        required=True,
    )
    default_incident_type = fields.Selection(
        selection=[('warning', 'Cảnh báo'), ('error', 'Lỗi')],
        default='error',
        required=True,
    )
    default_severity = fields.Selection(
        selection=[
            ('low', 'Thấp'),
            ('medium', 'Trung bình'),
            ('high', 'Cao'),
            ('critical', 'Nghiêm trọng'),
        ],
        default='medium',
        required=True,
    )
    default_root_cause = fields.Text(string='Nguyên nhân mặc định')
    default_prevention_action = fields.Text(string='Cách phòng tránh mặc định')
    note = fields.Text(string='Ghi chú')

    _sql_constraints = [
        ('oms_payment_incident_category_code_uniq', 'unique(code)', 'Mã danh mục phải là duy nhất.'),
    ]


class OmsPaymentIncidentLog(models.Model):
    _name = 'oms.payment.incident.log'
    _description = 'Nhật ký sự cố thanh toán'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'incident_date desc, id desc'

    name = fields.Char(
        string='Mã sự cố',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        tracking=True,
        index=True,
    )
    active = fields.Boolean(default=True)
    title = fields.Char(string='Tiêu đề', required=True, tracking=True)
    incident_date = fields.Datetime(
        string='Thời điểm phát sinh',
        required=True,
        default=fields.Datetime.now,
        tracking=True,
        index=True,
    )
    scope = fields.Selection(
        selection=[
            ('payment', 'Thanh toán'),
            ('order', 'Đặt hàng'),
            ('checkout', 'Checkout'),
        ],
        string='Phạm vi',
        default='payment',
        required=True,
        tracking=True,
    )
    incident_type = fields.Selection(
        selection=[('warning', 'Cảnh báo'), ('error', 'Lỗi')],
        string='Loại',
        default='error',
        required=True,
        tracking=True,
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
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ('open', 'Mới ghi nhận'),
            ('in_progress', 'Đang xử lý'),
            ('resolved', 'Đã xử lý'),
            ('closed', 'Đóng'),
        ],
        string='Trạng thái',
        default='open',
        required=True,
        tracking=True,
        index=True,
    )
    category_id = fields.Many2one(
        'oms.payment.incident.category',
        string='Danh mục sự cố',
        tracking=True,
    )
    transaction_id = fields.Many2one(
        'payment.transaction',
        string='Giao dịch thanh toán',
        index=True,
        tracking=True,
        ondelete='set null',
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Đơn bán hàng',
        index=True,
        tracking=True,
        ondelete='set null',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Khách hàng',
        index=True,
        tracking=True,
        ondelete='set null',
    )
    provider_id = fields.Many2one(
        'payment.provider',
        string='Cổng thanh toán',
        index=True,
        tracking=True,
        ondelete='set null',
    )
    currency_id = fields.Many2one('res.currency', string='Tiền tệ')
    amount = fields.Monetary(string='Số tiền', currency_field='currency_id', tracking=True)
    root_cause = fields.Text(string='Nguyên nhân gốc', required=True)
    prevention_action = fields.Text(string='Cách phòng tránh', required=True)
    correction_action = fields.Text(string='Hướng xử lý/khắc phục')
    symptom = fields.Text(string='Mô tả hiện tượng')
    resolution_note = fields.Text(string='Kết quả xử lý')
    technical_note = fields.Text(string='Ghi chú kỹ thuật')
    responsible_id = fields.Many2one(
        'res.users',
        string='Phụ trách',
        default=lambda self: self.env.user,
        tracking=True,
        ondelete='set null',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Công ty',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    auto_generated = fields.Boolean(string='Tự động sinh', default=False, tracking=True)
    is_repeat_issue = fields.Boolean(string='Lỗi lặp lại', default=False, tracking=True)
    repeat_reference = fields.Char(string='Mã nhóm lặp')
    incident_year = fields.Integer(string='Năm', compute='_compute_period', store=True, index=True)
    incident_quarter = fields.Selection(
        selection=[('1', 'Q1'), ('2', 'Q2'), ('3', 'Q3'), ('4', 'Q4')],
        string='Quý',
        compute='_compute_period',
        store=True,
        index=True,
    )
    incident_period_label = fields.Char(string='Kỳ', compute='_compute_period', store=True)

    @api.depends('incident_date')
    def _compute_period(self):
        for rec in self:
            if rec.incident_date:
                dt = fields.Datetime.context_timestamp(rec, rec.incident_date)
                quarter = ((dt.month - 1) // 3) + 1
                rec.incident_year = dt.year
                rec.incident_quarter = str(quarter)
                rec.incident_period_label = f'Q{quarter}/{dt.year}'
            else:
                rec.incident_year = False
                rec.incident_quarter = False
                rec.incident_period_label = False

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = seq.next_by_code('oms.payment.incident.log') or _('New')
            self._sync_linked_values(vals)
        records = super().create(vals_list)
        return records

    def write(self, vals):
        self._sync_linked_values(vals)
        return super().write(vals)

    def _sync_linked_values(self, vals):
        tx = False
        order = False
        if vals.get('transaction_id'):
            tx = self.env['payment.transaction'].browse(vals['transaction_id'])
        elif self and len(self) == 1 and self.transaction_id:
            tx = self.transaction_id

        if vals.get('sale_order_id'):
            order = self.env['sale.order'].browse(vals['sale_order_id'])
        elif self and len(self) == 1 and self.sale_order_id:
            order = self.sale_order_id

        if tx:
            vals.setdefault('provider_id', tx.provider_id.id if tx.provider_id else False)
            vals.setdefault('partner_id', tx.partner_id.id if tx.partner_id else False)
            vals.setdefault('currency_id', tx.currency_id.id if tx.currency_id else False)
            vals.setdefault('amount', tx.amount or 0.0)
            if not vals.get('sale_order_id'):
                linked_order = tx._get_primary_sale_order() if hasattr(tx, '_get_primary_sale_order') else False
                if linked_order:
                    vals['sale_order_id'] = linked_order.id
        elif order:
            vals.setdefault('partner_id', order.partner_id.id if order.partner_id else False)
            vals.setdefault('currency_id', order.currency_id.id if order.currency_id else False)
            vals.setdefault('amount', order.amount_total or 0.0)

    def action_mark_in_progress(self):
        self.write({'state': 'in_progress'})

    def action_mark_resolved(self):
        self.write({'state': 'resolved'})

    def action_mark_closed(self):
        self.write({'state': 'closed'})

    def action_reopen(self):
        self.write({'state': 'open'})

    def action_open_transaction(self):
        self.ensure_one()
        if not self.transaction_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Giao dịch thanh toán'),
            'res_model': 'payment.transaction',
            'view_mode': 'form',
            'res_id': self.transaction_id.id,
            'target': 'current',
        }

    def action_open_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _('Đơn bán hàng'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
            'target': 'current',
        }
