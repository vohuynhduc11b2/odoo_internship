# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.osv import expression

class ProductGroup(models.Model):
    _name = 'oms.product.group'
    _description = 'OMS Product Group'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'itms_grp_nam'  # field mặc định dùng làm tên nếu không override name_get

    itms_grp_cod = fields.Integer(string="Mã nhóm sản phẩm SAP", required=True, index=True)
    itms_grp_nam = fields.Char(string="Tên nhóm sản phẩm", required=True)

    u_cost_act1 = fields.Char(string="U_CostAct1")
    u_product_line = fields.Char(string="U_ProductLine")
    u_product_family = fields.Char(string="U_ProductFamily")
    u_brand = fields.Char(string="U_Brand")

    active = fields.Boolean(string="Active", default=True)
    odoo_category_id = fields.Many2one('product.category', string="Nhóm SP Odoo (mapping)")

    product_item_ids = fields.One2many('oms.product.item', 'product_group_id', string="Sản phẩm (OMS)")

    _sql_constraints = [
        ('itms_grp_cod_unique', 'unique(itms_grp_cod)', 'Mã nhóm sản phẩm (ItmsGrpCod) không được trùng!'),
    ]

    # ======= Smart button =======
    def action_open_items(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sản phẩm thuộc nhóm',
            'res_model': 'oms.product.item',
            'view_mode': 'list,form',
            'domain': [('product_group_id', '=', self.id)],
        }

    # ======= Hiển thị tên record =======
    def name_get(self):
        res = []
        for rec in self:
            code = rec.itms_grp_cod or ''
            name = rec.itms_grp_nam or ''
            label = "[%s] %s" % (code, name) if code else (name or str(rec.id))
            res.append((rec.id, label))
        return res

    # (không bắt buộc) Tìm theo cả mã số và tên
    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=80):
        args = args or []
        domain = []
        if name:
            # nếu người dùng gõ số, cho phép match chính xác theo mã nhóm
            num_domain = []
            if name.isdigit():
                num_domain = [('itms_grp_cod', '=', int(name))]
            # tìm theo tên + (nếu có) theo mã
            domain = expression.OR([num_domain, [('itms_grp_nam', operator, name)]])
        records = self.search(expression.AND([args, domain]), limit=limit)
        return records.name_get()
