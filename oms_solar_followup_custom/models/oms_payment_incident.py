from odoo import api, fields, models


class OmsPaymentIncident(models.Model):
    _name = 'oms.payment.incident'
    _description = 'OMS Payment Incident'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'occurred_at desc, id desc'

    name = fields.Char(string='Mã/Sự cố', required=True, copy=False, default='New', tracking=True)
    active = fields.Boolean(default=True)
    occurred_at = fields.Datetime(string='Thời điểm', required=True, default=fields.Datetime.now, tracking=True)
    incident_type = fields.Selection([
        ('warning', 'Cảnh báo'),
        ('error', 'Lỗi'),
        ('cancel', 'Khách huỷ / Huỷ giao dịch'),
        ('mismatch', 'Sai lệch dữ liệu'),
        ('webhook', 'Không nhận callback/webhook'),
        ('unc', 'Thiếu chứng từ UNC'),
        ('other', 'Khác'),
    ], default='warning', required=True, tracking=True)
    severity = fields.Selection([
        ('low', 'Thấp'),
        ('medium', 'Trung bình'),
        ('high', 'Cao'),
        ('critical', 'Nghiêm trọng'),
    ], default='medium', required=True, tracking=True)
    state = fields.Selection([
        ('open', 'Mới ghi nhận'),
        ('in_progress', 'Đang xử lý'),
        ('resolved', 'Đã xử lý'),
        ('ignored', 'Bỏ qua'),
    ], default='open', required=True, tracking=True)
    sale_order_id = fields.Many2one('sale_custom.order', string='Đơn OMS', index=True, tracking=True)
    payment_transaction_id = fields.Many2one('payment.transaction', string='Giao dịch thanh toán', index=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Khách hàng', index=True)
    provider_code = fields.Char(string='Provider')
    payment_reference = fields.Char(string='Mã tham chiếu')
    short_description = fields.Char(string='Mô tả ngắn', required=True, tracking=True)
    root_cause = fields.Text(string='Nguyên nhân gốc')
    prevention = fields.Text(string='Cách phòng tránh')
    solution = fields.Text(string='Hướng xử lý')
    result_note = fields.Text(string='Kết quả xử lý')
    company_id = fields.Many2one('res.company', string='Công ty', default=lambda self: self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = seq.next_by_code('oms.payment.incident') or 'New'
        return super().create(vals_list)
