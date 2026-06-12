from odoo import models, fields, api

class OmsPromotionProductCategory(models.Model):
    _name = 'oms.promotion.product.category'
    _description = 'Danh mục sản phẩm khuyến mãi OMS/UC'

    name = fields.Char(string="Tên danh mục khuyến mãi", required=True)
    itms_grp_cod = fields.Char(string="Mã nhóm sản phẩm", required=True, index=True)
    itms_grp_nam = fields.Char(string="Tên nhóm sản phẩm")
    u_product_line = fields.Char(string="Dòng sản phẩm")
    u_product_family = fields.Char(string="Nhóm SP cha")
    u_brand = fields.Char(string="Thương hiệu", index=True)
    note = fields.Char(string="Ghi chú")
    product_categ_id = fields.Many2one(
        'product.category', string="Danh mục Odoo", ondelete="set null", help="Liên kết đến danh mục sản phẩm Odoo nếu cần mapping"
    )

    _sql_constraints = [
        ('itms_grp_cod_unique', 'unique(itms_grp_cod)', 'Mã nhóm sản phẩm (ItmsGrpCod) không được trùng!')
    ]
