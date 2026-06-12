from odoo import models, fields

class MarketingCampaign(models.Model):
    _name = 'oms.marketing.campaign'
    _description = 'Marketing Campaign'

    code = fields.Char(string="Mã chương trình", required=True)
    name = fields.Char(string="Tên chiến dịch", required=True)
    doc_entry = fields.Integer(string="DocEntry")
    canceled = fields.Selection([
        ('Y', 'Đã huỷ'),
        ('N', 'Hoạt động'),
    ], string="Đã huỷ?", default='N')
    object = fields.Char(string="Object")
    log_inst = fields.Char(string="LogInst")
    user_sign = fields.Integer(string="User Sign")
    transfered = fields.Selection([
        ('Y', 'Đã chuyển'),
        ('N', 'Chưa chuyển'),
    ], string="Transfered", default='N')
    create_date_api = fields.Datetime(string="Ngày tạo (API)")
    update_date_api = fields.Datetime(string="Ngày cập nhật (API)")
    data_source = fields.Char(string="Nguồn dữ liệu")
    u_active = fields.Selection([
        ('Y', 'Có hiệu lực'),
        ('N', 'Không hiệu lực'),
    ], string="Kích hoạt", default='Y')
    u_has_price_list = fields.Selection([
        ('Y', 'Có bảng giá'),
        ('N', 'Không có bảng giá'),
    ], string="Có bảng giá?", default='N')
    u_from_date = fields.Datetime(string="Ngày bắt đầu")
    u_to_date = fields.Datetime(string="Ngày kết thúc")
    u_is_clear_stock = fields.Selection([
        ('Y', 'Clear tồn'),
        ('N', 'Không clear tồn'),
    ], string="Clear tồn?", default='N')
