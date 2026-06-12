from odoo import models, fields, api

class OmsPrjInfo(models.Model):
    _name = 'oms.prjinfo'
    _description = 'Thông tin Dự án OMS'

    name = fields.Char('Tên dự án', compute='_compute_name', store=True, readonly=False)
    prj_code = fields.Char('Mã dự án', required=True, index=True)
    prj_name = fields.Char('Tên dự án', required=True)
    u_card_code = fields.Char('Mã khách hàng')
    card_name = fields.Char('Tên khách hàng')
    u_category = fields.Char('Loại dự án')
    u_subcate = fields.Char('Phân loại')
    u_appendix_amount = fields.Float('Số tiền phụ lục')
    u_mkt_amount = fields.Float('Số tiền marketing')
    nv_tao = fields.Char('Nhân viên tạo')
    nv_cap_nhat = fields.Char('Nhân viên cập nhật')
    ngay_tao = fields.Datetime('Ngày tạo')
    ngay_cap_nhat = fields.Datetime('Ngày cập nhật')

    @api.depends('prj_name')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.prj_name or ''
