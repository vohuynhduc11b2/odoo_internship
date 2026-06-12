from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    oms_group_parent_id = fields.Many2one(
        'res.partner',
        string='Nhóm KH cha OMS',
        domain=[('is_company', '=', True)],
        help='Dùng cho trường hợp khách hàng A mua hàng nhưng xuất hoá đơn cho khách hàng B trong cùng nhóm.',
    )
    oms_group_child_ids = fields.One2many('res.partner', 'oms_group_parent_id', string='Khách hàng con OMS')
    oms_allow_buyer_selection = fields.Boolean(
        string='Cho phép chọn làm đối tác mua',
        default=True,
        help='Bật để đối tác này xuất hiện trong danh sách Khách hàng mua trên OMS.',
    )
    oms_allow_invoice_selection = fields.Boolean(
        string='Cho phép chọn làm KH xuất hóa đơn',
        default=True,
        help='Bật để đối tác/địa chỉ này xuất hiện trong danh sách xuất hóa đơn trên OMS.',
    )
    oms_ready_for_portal = fields.Boolean(string='Đã đưa vào danh sách cấp tài khoản OMS', default=False)
    oms_portal_deadline = fields.Date(string='Hạn chuẩn bị tài khoản OMS')
    oms_portal_prepared = fields.Boolean(string='Đã cấp/chuẩn bị tài khoản OMS', default=False)
    oms_portal_note = fields.Text(string='Ghi chú chuẩn bị tài khoản OMS')
    oms_effective_group_root_id = fields.Many2one('res.partner', string='Nhóm gốc hiệu lực', compute='_compute_oms_effective_group_root_id')

    @api.depends('oms_group_parent_id')
    def _compute_oms_effective_group_root_id(self):
        for partner in self:
            partner.oms_effective_group_root_id = partner.oms_group_parent_id or partner.commercial_partner_id
