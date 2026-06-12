from odoo import models, fields

class OmsPaymentTerms(models.Model):
    _name = 'oms.payment.terms'
    _description = 'Payment Terms (OMS)'

    name = fields.Char('Tên điều khoản thanh toán', required=True)
    group_num = fields.Integer('GroupNum', required=True, index=True)
    pymnt_group = fields.Char('Tên điều khoản', required=True)
    extra_days = fields.Integer('Số ngày cộng thêm', default=0)
    bsline_date = fields.Selection([
        ('Document Date', 'Document Date'),
        ('Posting Date', 'Posting Date')
    ], string='Ngày căn cứ', required=True)

    # Nếu muốn mapping nhanh sang Odoo native payment.term, có thể thêm trường liên kết Many2one
    payment_term_id = fields.Many2one('account.payment.term', string="Payment Term Odoo", ondelete='set null')
