from odoo import models, fields, api, _

class ApprovalWorkflow(models.Model):
    _name = 'approval.workflow'
    _description = 'Quy trình duyệt động'

    name = fields.Char(string="Tên quy trình", required=True)
    step_ids = fields.One2many('approval.workflow.step', 'workflow_id', string="Các bước duyệt")


class ApprovalWorkflowStep(models.Model):
    _name = 'approval.workflow.step'
    _description = 'Bước duyệt'

    workflow_id = fields.Many2one(
        'approval.workflow',
        string='Quy trình',
        ondelete='cascade',
        required=True
    )
    sequence = fields.Integer(string="Thứ tự", default=1)
    name = fields.Char(string="Tên bước", required=True)

    approver_type = fields.Selection([
        ('user', 'Người dùng cụ thể'),
        ('group', 'Nhóm quyền'),
        ('custom', 'Danh sách người dùng'),
        ('leader', 'Leader của Sales'),      # mới
        ('role', 'Theo Role (Accountant/BOD)') # mới
    ], string="Loại người duyệt", default='user')

    # Người duyệt cụ thể
    approver_id = fields.Many2one('res.users', string="Người duyệt")
    # Nhóm quyền
    group_id = fields.Many2one('res.groups', string="Nhóm quyền duyệt")
    # Phòng ban

    # Danh sách user tùy chọn
    approver_ids = fields.Many2many('res.users', string="Danh sách người duyệt")
    # Role duyệt (Accountant, BOD, ...)
    role_code = fields.Selection([
        ('accountant', 'Kế toán'),
        ('bod', 'Ban Giám Đốc (BOD)'),
        ('sm', 'Sales Manager/Leader'),
    ], string="Role duyệt")

    next_sequence = fields.Integer(string="Bước tiếp theo", default=0)
    conditions_json = fields.Json(string="Điều kiện chuyển bước", default=list)

    # ==============================
    # HÀM KIỂM TRA USER CÓ QUYỀN DUYỆT KHÔNG
    # ==============================
    def can_user_approve(self, user, order=None):
        self.ensure_one()
        if self.approver_type == 'user' and self.approver_id == user:
            return True
        if self.approver_type == 'group' and self.group_id in user.groups_id:
            return True
        if self.approver_type == 'custom' and user in self.approver_ids:
            return True
        if self.approver_type == 'leader' and order:
            # Leader = manager của Sales (user_id)
            if order.user_id and order.user_id.parent_id == user:
                return True
        if self.approver_type == 'role':
            if self.role_code == 'accountant' and user.has_group('sale_custom.group_accountant'):
                return True
            if self.role_code == 'bod' and user.has_group('sale_custom.group_bod'):
                return True
            if self.role_code == 'sm' and user.has_group('sale_custom.group_sale_manager'):
                return True
        return False

    