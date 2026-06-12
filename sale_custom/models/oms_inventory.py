# -*- coding: utf-8 -*-
from odoo import models, fields, api

INVENTORY_DIGITS = (16, 6)

class OmsInventory(models.Model):
    _name = 'oms.inventory'
    _description = 'Inventory by Warehouse (ItemsWarehouse)'
    _order = 'item_code, whs_code'
    _rec_name = 'display_name'

    # Khóa & liên kết
    item_code = fields.Char('Mã sản phẩm', required=True, index=True)
    product_id = fields.Many2one('product.product', string='Sản phẩm', index=True)
    product_tmpl_id = fields.Many2one('product.template', related='product_id.product_tmpl_id', store=True)

    whs_code = fields.Char('Mã kho', required=True, index=True)
    whs_name = fields.Char('Tên kho', index=True)
    whs_id   = fields.Many2one('oms.warehouse', string='Kho (OMS)', index=True)

    # Số lượng (theo API)
    on_hand     = fields.Float('OnHand',     digits=INVENTORY_DIGITS, default=0.0)
    is_commited = fields.Float('IsCommited', digits=INVENTORY_DIGITS, default=0.0)
    on_order    = fields.Float('OnOrder',    digits=INVENTORY_DIGITS, default=0.0)
    u_available = fields.Float('U_Available',digits=INVENTORY_DIGITS, default=0.0)

    # Hiển thị
    display_name = fields.Char(string='Diễn giải', compute='_compute_display_name', store=True)

    _sql_constraints = [
        ('uniq_item_whs', 'unique(item_code, whs_code)', 'Mỗi (ItemCode, WhsCode) chỉ được phép xuất hiện một lần.')
    ]

    @api.depends('item_code', 'whs_code', 'whs_name')
    def _compute_display_name(self):
        for r in self:
            r.display_name = f"{r.item_code or ''} @ {r.whs_code or ''} - {r.whs_name or ''}".strip()
