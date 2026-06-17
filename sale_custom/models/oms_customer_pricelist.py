# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class OmsCustomerPricelist(models.Model):
    """
    Model trung gian để quản lý quan hệ Nhiều-Nhiều giữa khách hàng và bảng giá.
    Mỗi dòng thể hiện một bảng giá đang được áp dụng cho một khách hàng cụ thể.
    
    Cho phép:
    - Một khách hàng có nhiều bảng giá
    - Mỗi bảng giá có thể áp dụng cho nhiều khách hàng
    - Có ngày hiệu lực, độ ưu tiên và bảng giá mặc định
    """
    _name = 'oms.customer.pricelist'
    _description = 'Customer Pricelist Assignment'
    _order = 'partner_id, is_default desc, priority, valid_from desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    partner_id = fields.Many2one(
        'res.partner',
        string='Khách hàng',
        required=True,
        index=True,
        ondelete='cascade',
        tracking=True,
    )
    
    partner_name = fields.Char(
        string='Tên khách hàng',
        related='partner_id.name',
        store=True,
        readonly=True,
    )
    
    partner_card_code = fields.Char(
        string='Card Code',
        related='partner_id.x_oms_card_code',
        store=True,
        readonly=True,
    )
    
    pricelist_id = fields.Many2one(
        'product.pricelist',
        string='Bảng giá',
        required=True,
        index=True,
        ondelete='cascade',
        tracking=True,
    )
    
    pricelist_name = fields.Char(
        string='Tên bảng giá',
        related='pricelist_id.name',
        store=True,
        readonly=True,
    )
    
    # Liên kết với khung giá nguồn BE (nếu có)
    price_frame_id = fields.Many2one(
        'oms.pricelist.frame',
        string='Khung giá nguồn',
        related='pricelist_id.x_be_price_frame_id',
        store=True,
        readonly=True,
    )
    
    price_frame_name = fields.Char(
        string='Tên khung giá',
        related='price_frame_id.price_list_name',
        store=True,
        readonly=True,
    )
    
    is_default = fields.Boolean(
        string='Mặc định',
        default=False,
        help='Bảng giá mặc định cho khách hàng. Chỉ một bảng giá mặc định tại một thời điểm.',
        tracking=True,
    )
    
    priority = fields.Integer(
        string='Độ ưu tiên',
        default=10,
        help='Số nhỏ = ưu tiên cao hơn. Khi có nhiều bảng giá hợp lệ, '
             'hệ thống sẽ chọn theo: is_default > priority > valid_from mới nhất.',
        tracking=True,
    )
    
    valid_from = fields.Date(
        string='Ngày bắt đầu',
        help='Ngày bắt đầu áp dụng bảng giá cho khách hàng. '
             'Để trống nếu áp dụng từ mãi mãi.',
        tracking=True,
    )
    
    valid_to = fields.Date(
        string='Ngày kết thúc',
        help='Ngày kết thúc áp dụng bảng giá. '
             'Để trống nếu áp dụng mãi mãi.',
        tracking=True,
    )
    
    state = fields.Selection([
        ('draft', 'Nháp'),
        ('active', 'Đang áp dụng'),
        ('expired', 'Hết hiệu lực'),
        ('cancelled', 'Đã hủy'),
    ], string='Trạng thái', default='active', required=True, tracking=True)
    
    note = fields.Text(
        string='Ghi chú',
        help='Ghi chú về chính sách giá hoặc lý do gán bảng giá này.',
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Công ty',
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    
    active = fields.Boolean(
        string='Hoạt động',
        default=True,
        help='Ẩn/hiện cấu hình. Không xóa vật lý để giữ lịch sử.',
    )

    # Computed field để kiểm tra xem có đang trong thời gian hiệu lực không
    is_valid = fields.Boolean(
        string='Còn hiệu lực',
        compute='_compute_is_valid',
        search='_search_is_valid',
        store=False,
    )

    @api.depends('valid_from', 'valid_to', 'state', 'active')
    def _compute_is_valid(self):
        today = fields.Date.today()
        for rec in self:
            if rec.state != 'active' or not rec.active:
                rec.is_valid = False
                continue
            
            from_ok = not rec.valid_from or rec.valid_from <= today
            to_ok = not rec.valid_to or rec.valid_to >= today
            rec.is_valid = from_ok and to_ok

    def _search_is_valid(self, operator, value):
        today = fields.Date.today()
        if operator == '=' and value:
            return [
                '|',
                ('valid_from', '=', False),
                ('valid_from', '<=', today),
                '|',
                ('valid_to', '=', False),
                ('valid_to', '>=', today),
                ('state', '=', 'active'),
                ('active', '=', True),
            ]
        elif operator == '=' and not value:
            return [
                '|',
                ('state', '!=', 'active'),
                ('active', '=', False),
            ]
        return []

    @api.constrains('valid_from', 'valid_to')
    def _check_valid_dates(self):
        for rec in self:
            if rec.valid_from and rec.valid_to and rec.valid_from > rec.valid_to:
                raise ValidationError(
                    _('Ngày kết thúc (%s) phải lớn hơn hoặc bằng ngày bắt đầu (%s).')
                    % (rec.valid_to, rec.valid_from)
                )

    @api.constrains('partner_id', 'pricelist_id', 'valid_from', 'valid_to', 'active')
    def _check_unique_active_assignment(self):
        """
        Ràng buộc: Không cho tạo hai dòng active trùng partner_id + pricelist_id 
        trong cùng khoảng thời gian hiệu lực.
        """
        for rec in self:
            if not rec.active or rec.state != 'active':
                continue

            domain = [
                ('id', '!=', rec.id),
                ('partner_id', '=', rec.partner_id.id),
                ('pricelist_id', '=', rec.pricelist_id.id),
                ('active', '=', True),
                ('state', '=', 'active'),
            ]
            
            # Kiểm tra trùng khoảng ngày
            if rec.valid_from:
                domain += ['|', ('valid_to', '=', False), ('valid_to', '>=', rec.valid_from)]
            if rec.valid_to:
                domain += ['|', ('valid_from', '=', False), ('valid_from', '<=', rec.valid_to)]

            existing = self.search(domain, limit=1)
            if existing:
                raise ValidationError(
                    _('Khách hàng "%s" đã có bảng giá "%s" áp dụng trong khoảng thời gian này.')
                    % (rec.partner_id.name, rec.pricelist_id.name)
                )

    @api.constrains('partner_id', 'valid_from', 'valid_to', 'is_default', 'active')
    def _check_one_default_per_period(self):
        """
        Ràng buộc: Trong cùng một khoảng hiệu lực, mỗi khách hàng chỉ nên có 
        một dòng is_default = True.
        """
        for rec in self:
            if not rec.is_default or not rec.active or rec.state != 'active':
                continue

            domain = [
                ('id', '!=', rec.id),
                ('partner_id', '=', rec.partner_id.id),
                ('is_default', '=', True),
                ('active', '=', True),
                ('state', '=', 'active'),
            ]
            
            if rec.valid_from:
                domain += ['|', ('valid_to', '=', False), ('valid_to', '>=', rec.valid_from)]
            if rec.valid_to:
                domain += ['|', ('valid_from', '=', False), ('valid_from', '<=', rec.valid_to)]

            existing = self.search(domain, limit=1)
            if existing:
                raise ValidationError(
                    _('Khách hàng "%s" đã có bảng giá mặc định "%s" trong khoảng thời gian này. '
                      'Chỉ một bảng giá mặc định cho mỗi khách hàng tại một thời điểm.')
                    % (rec.partner_id.name, existing.pricelist_id.name)
                )

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """Khi thay đổi khách hàng, gợi ý bảng giá mặc định nếu chưa có."""
        if self.partner_id and not self.pricelist_id:
            # Ưu tiên lấy bảng giá công nợ hoặc trả trước nếu là khách chiến lược
            if self.partner_id.oms_is_strategic_customer:
                if self.partner_id.oms_debt_pricelist_id:
                    self.pricelist_id = self.partner_id.oms_debt_pricelist_id
                elif self.partner_id.oms_prepaid_pricelist_id:
                    self.pricelist_id = self.partner_id.oms_prepaid_pricelist_id

    @api.onchange('pricelist_id')
    def _onchange_pricelist_id(self):
        """Khi thay đổi bảng giá, kiểm tra xem bảng giá có active không."""
        if self.pricelist_id and not self.pricelist_id.active:
            return {
                'warning': {
                    'title': _('Cảnh báo'),
                    'message': _('Bảng giá "%s" đang bị inactive.') % self.pricelist_id.name,
                }
            }

    @api.onchange('valid_from', 'valid_to')
    def _onchange_validity_dates(self):
        """Tự động cập nhật trạng thái dựa vào ngày hiệu lực."""
        if self.state != 'active':
            return
        
        today = fields.Date.today()
        if self.valid_to and self.valid_to < today:
            self.state = 'expired'

    def action_activate(self):
        """Kích hoạt bảng giá cho khách hàng."""
        self.write({'state': 'active', 'active': True})

    def action_expire(self):
        """Đánh dấu hết hiệu lực."""
        self.write({'state': 'expired'})

    def action_cancel(self):
        """Hủy bỏ bảng giá cho khách hàng."""
        self.write({'state': 'cancelled', 'active': False})

    def action_set_default(self):
        """Đặt làm bảng giá mặc định cho khách hàng."""
        self.ensure_one()
        # Bỏ default của các dòng khác cùng khách hàng
        if self.partner_id and self.active and self.state == 'active':
            same_partner = self.search([
                ('partner_id', '=', self.partner_id.id),
                ('id', '!=', self.id),
                ('is_default', '=', True),
                ('active', '=', True),
            ])
            same_partner.write({'is_default': False})
        
        self.write({'is_default': True})

    @api.model
    def get_pricelist_for_partner(self, partner_id, at_date=None):
        """
        Lấy bảng giá phù hợp nhất cho khách hàng theo ngày.
        
        Quy tắc chọn:
        1. is_default = True
        2. priority ASC (số nhỏ = ưu tiên cao)
        3. valid_from DESC (mới nhất)
        4. create_date DESC
        
        Returns:
            recordset: product.pricelist hoặc empty recordset
        """
        at_date = at_date or fields.Date.today()
        
        if isinstance(partner_id, int):
            partner_id = self.env['res.partner'].browse(partner_id)
        
        domain = [
            ('partner_id', '=', partner_id.id),
            ('state', '=', 'active'),
            ('active', '=', True),
            '|',
            ('valid_from', '=', False),
            ('valid_from', '<=', at_date),
            '|',
            ('valid_to', '=', False),
            ('valid_to', '>=', at_date),
        ]
        
        assignment = self.search(domain, order='is_default desc, priority asc, valid_from desc, id desc', limit=1)
        
        if assignment and assignment.pricelist_id and assignment.pricelist_id.active:
            return assignment.pricelist_id
        
        # Fallback: thử lấy bảng giá mặc định của khách hàng
        if partner_id.oms_is_strategic_customer:
            if partner_id.oms_debt_pricelist_id and partner_id.oms_debt_pricelist_id.active:
                return partner_id.oms_debt_pricelist_id
            if partner_id.oms_prepaid_pricelist_id and partner_id.oms_prepaid_pricelist_id.active:
                return partner_id.oms_prepaid_pricelist_id
        
        # Fallback cuối cùng: lấy bảng giá [OMS] DEFAULT
        default_pricelist = self.env['product.pricelist'].search([
            ('name', '=', '[OMS] DEFAULT'),
            ('active', '=', True),
        ], limit=1)
        
        return default_pricelist

    @api.model
    def get_all_pricelists_for_partner(self, partner_id, at_date=None, only_active=True):
        """
        Lấy tất cả bảng giá áp dụng cho khách hàng.
        
        Returns:
            recordset: oms.customer.pricelist
        """
        at_date = at_date or fields.Date.today()
        
        if isinstance(partner_id, int):
            partner_id = self.env['res.partner'].browse(partner_id)
        
        domain = [
            ('partner_id', '=', partner_id.id),
        ]
        
        if only_active:
            domain += [
                ('state', '=', 'active'),
                ('active', '=', True),
                '|',
                ('valid_from', '=', False),
                ('valid_from', '<=', at_date),
                '|',
                ('valid_to', '=', False),
                ('valid_to', '>=', at_date),
            ]
        
        return self.search(domain, order='is_default desc, priority asc, valid_from desc')

    @api.model
    def get_partners_for_pricelist(self, pricelist_id):
        """
        Lấy danh sách khách hàng đang áp dụng một bảng giá cụ thể.
        
        Returns:
            recordset: res.partner
        """
        if isinstance(pricelist_id, int):
            pricelist_id = self.env['product.pricelist'].browse(pricelist_id)
        
        assignments = self.search([
            ('pricelist_id', '=', pricelist_id.id),
            ('state', '=', 'active'),
            ('active', '=', True),
        ])
        
        return assignments.mapped('partner_id')

    @api.model
    def _cron_update_expired_assignments(self):
        """Cron job để tự động cập nhật trạng thái các assignment hết hạn."""
        today = fields.Date.today()
        
        # Tìm các assignment đã hết hạn nhưng chưa được đánh dấu expired
        expired = self.search([
            ('state', '=', 'active'),
            ('valid_to', '<', today),
            ('active', '=', True),
        ])
        
        if expired:
            expired.write({'state': 'expired'})
            _logger = self.env['ir.logging']
            _logger._logger.info('Updated %s expired customer pricelist assignments', len(expired))
