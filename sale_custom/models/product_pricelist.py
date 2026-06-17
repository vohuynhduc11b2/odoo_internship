# -*- coding: utf-8 -*-
"""
Mở rộng model product.pricelist để hỗ trợ bảng giá AUT.
Bổ sung các field để liên kết với khung giá BE và đánh dấu bảng giá AUT.
"""
from odoo import models, fields, api


class ProductPricelistAUT(models.Model):
    """
    Mở rộng product.pricelist với các field dành riêng cho bảng giá AUT.
    
    Các field bổ sung:
    - x_be_price_frame_id: Liên kết với khung giá nguồn BE
    - x_source_pricelist_code: Mã/tên bảng giá nguồn từ BE
    - x_sync_key: Khóa duy nhất để chống trùng khi đồng bộ
    - x_group_code: Lưu Group Code từ BE
    - x_level_code: Lưu Level Code từ BE
    - x_is_aut_pricelist: Đánh dấu bảng giá thuộc luồng AUT
    - x_is_strategic: Đánh dấu bảng giá chiến lược (Đặc biệt)
    """
    _inherit = 'product.pricelist'

    # ========================
    # Liên kết với khung giá BE
    # ========================
    x_be_price_frame_id = fields.Many2one(
        'oms.pricelist.frame',
        string='Khung giá nguồn BE',
        index=True,
        copy=False,
        help='Khung giá nguồn từ BE mà bảng giá này được tạo từ đó.',
    )
    x_frame_publish_pricelist_ids = fields.Many2many(
        'product.pricelist',
        string='Bảng giá lấy',
        related='x_be_price_frame_id.publish_pricelist_ids',
        readonly=True,
        help='Danh sách bảng giá nhận giá từ khung giá này (từ UC PriceList Frame).',
    )
    x_frame_max_qty = fields.Float(
        string='Max Quantity',
        related='x_be_price_frame_id.max_qty',
        readonly=True,
        help='Max Quantity từ khung giá nguồn (từ UC PriceList Frame).',
    )
    
    x_source_pricelist_code = fields.Char(
        string='Mã bảng giá nguồn',
        index=True,
        copy=False,
        help='Mã/tên bảng giá gốc từ BE (ví dụ: AUT ItemPriceAUT - v1).',
    )
    
    x_sync_key = fields.Char(
        string='Khóa đồng bộ',
        index=True,
        copy=False,
        help='Khóa duy nhất để chống tạo trùng khi đồng bộ từ BE.',
    )
    
    # ========================
    # Lưu thông tin từ BE
    # ========================
    x_group_code = fields.Char(
        string='Group Code',
        index=True,
        help='Group Code từ BE, dùng để lọc và phân nhóm khách hàng.',
    )
    
    x_level_code = fields.Char(
        string='Level Code',
        index=True,
        help='Level Code từ BE, dùng để phân tầng giá.',
    )
    
    x_category_id = fields.Many2one(
        'product.category',
        string='Danh mục sản phẩm',
        index=True,
        copy=False,
        ondelete='set null',
        help='Danh mục sản phẩm được lọc cho bảng giá này (từ BE).',
    )

    x_category_name = fields.Char(
        string='Tên danh mục',
        related='x_category_id.display_name',
        store=False,
        readonly=True,
        help='Tên danh mục sản phẩm, tự động lấy từ danh mục đã chọn.',
    )
    
    # ========================
    # Đánh dấu loại bảng giá
    # ========================
    x_is_aut_pricelist = fields.Boolean(
        string='Bảng giá AUT',
        default=False,
        index=True,
        help='Đánh dấu đây là bảng giá thuộc luồng AUT (từ BE).',
    )
    
    x_is_strategic = fields.Boolean(
        string='Bảng giá chiến lược',
        default=False,
        index=True,
        help='Đánh dấu đây là bảng giá chiến lược/đặc biệt (khách hàng ưu tiên).',
    )
    
    # ========================
    # Thông tin đồng bộ
    # ========================
    x_last_sync_date = fields.Datetime(
        string='Ngày đồng bộ cuối',
        copy=False,
        help='Thời điểm đồng bộ cuối cùng từ BE.',
    )
    
    x_sync_job_id = fields.Many2one(
        'oms.sync.job',
        string='Sync Job',
        copy=False,
        help='Job đồng bộ cuối cùng tạo ra bảng giá này.',
    )

    # ========================
    # Related/computed fields
    # ========================
    customer_count = fields.Integer(
        string='Số khách hàng',
        compute='_compute_customer_count',
        store=False,
        help='Số khách hàng đang được áp dụng bảng giá này.',
    )

    x_customer_pricelist_ids = fields.One2many(
        'oms.customer.pricelist',
        'pricelist_id',
        string='Gán khách hàng',
        readonly=True,
        help='Danh sách khách hàng được gán bảng giá này.',
    )

    def _compute_customer_count(self):
        """Đếm số khách hàng đang áp dụng bảng giá này."""
        CustomerPricelist = self.env['oms.customer.pricelist']
        for pricelist in self:
            count = CustomerPricelist.search_count([
                ('pricelist_id', '=', pricelist.id),
                ('state', '=', 'active'),
                ('active', '=', True),
            ])
            pricelist.customer_count = count

    @api.onchange('x_category_id')
    def _onchange_x_category_id(self):
        """
        Khi chọn danh mục, x_category_name sẽ tự resolve qua related field.
        Nếu x_category_id chứa legacy integer (từ db cũ), chuyển thành record.
        """
        if self.x_category_id and isinstance(self.x_category_id, int):
            self.x_category_id = self.env['product.category'].browse(self.x_category_id)

    @api.onchange('x_is_aut_pricelist')
    def _onchange_is_aut_pricelist(self):
        """
        Khi bật AUT, kiểm tra xem tên có nên thêm prefix [AUT] không.
        """
        if self.x_is_aut_pricelist and self.name and not self.name.startswith('[AUT]'):
            self.name = f'[AUT] {self.name}'

    @api.onchange('x_is_strategic')
    def _onchange_is_strategic(self):
        """
        Khi bật chiến lược, kiểm tra xem tên có nên thêm prefix [OMS] không.
        """
        if self.x_is_strategic and self.name and not self.name.startswith('[OMS]'):
            self.name = f'[OMS] {self.name}'

    def action_view_customers(self):
        """
        Xem danh sách khách hàng đang áp dụng bảng giá này.
        """
        self.ensure_one()
        
        CustomerPricelist = self.env['oms.customer.pricelist']
        customer_assignments = CustomerPricelist.search([
            ('pricelist_id', '=', self.id),
            ('state', '=', 'active'),
            ('active', '=', True),
        ])
        
        partner_ids = customer_assignments.mapped('partner_id').ids
        
        return {
            'name': f'Khách hàng áp dụng: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': [('id', 'in', partner_ids)],
            'context': {
                'default_custom_pricelist_id': self.id,
            },
        }

    def action_view_assignments(self):
        """
        Xem danh sách assignment của bảng giá này.
        """
        self.ensure_one()
        
        return {
            'name': f'Gán bảng giá: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'oms.customer.pricelist',
            'view_mode': 'list,form',
            'domain': [
                ('pricelist_id', '=', self.id),
                ('active', '=', True),
            ],
            'context': {
                'default_pricelist_id': self.id,
                'default_pricelist_name': self.name,
            },
        }

    @api.model
    def get_aut_pricelists(self, category_id=None):
        """
        Lấy danh sách bảng giá AUT theo category.
        
        Args:
            category_id: Lọc theo category ID (tùy chọn)
            
        Returns:
            recordset: product.pricelist
        """
        domain = [
            ('x_is_aut_pricelist', '=', True),
            ('active', '=', True),
        ]
        
        if category_id:
            domain.append(('x_category_id', '=', category_id))
        
        return self.search(domain, order='name')

    @api.model
    def get_pricelist_by_sync_key(self, sync_key):
        """
        Tìm bảng giá theo sync_key.
        
        Args:
            sync_key: Khóa đồng bộ
            
        Returns:
            recordset: product.pricelist hoặc empty
        """
        return self.search([
            ('x_sync_key', '=', sync_key),
            ('active', '=', True),
        ], limit=1)

    @api.model
    def create_aut_pricelist(self, vals):
        """
        Tạo bảng giá AUT mới với các giá trị mặc định.
        
        Args:
            vals: Dictionary chứa các giá trị tạo bảng giá
            
        Returns:
            recordset: product.pricelist vừa tạo
        """
        # Đảm bảo các giá trị mặc định
        if 'currency_id' not in vals:
            vals['currency_id'] = self.env.company.currency_id.id
        
        if 'x_is_aut_pricelist' not in vals:
            vals['x_is_aut_pricelist'] = True
        
        return self.create(vals)

    @api.model
    def upsert_aut_pricelist(self, sync_key, vals):
        """
        Tạo hoặc cập nhật bảng giá AUT theo sync_key.
        
        Args:
            sync_key: Khóa đồng bộ duy nhất
            vals: Dictionary chứa các giá trị cần tạo/cập nhật
            
        Returns:
            recordset: product.pricelist vừa tạo hoặc cập nhật
        """
        existing = self.get_pricelist_by_sync_key(sync_key)
        
        vals['x_sync_key'] = sync_key
        vals['x_is_aut_pricelist'] = True
        
        if existing:
            existing.write(vals)
            return existing
        else:
            return self.create_aut_pricelist(vals)
