from odoo import models, fields, api, _

class OmsProductItem(models.Model):
    _name = 'oms.product.item'
    _description = 'OMS Product Item'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Thông tin cơ bản
    item_code = fields.Char(string="Mã sản phẩm", required=True, index=True)
    item_name = fields.Char(string="Tên sản phẩm")
    frgn_name = fields.Char(string="Tên nước ngoài")
    valid_for = fields.Char(string="Còn kinh doanh?")
    u_is_hidden = fields.Char(string="Ẩn sản phẩm?")

    # Thông tin bảo hành, tồn kho
    u_warr_time_vendor = fields.Integer(string="Bảo hành NCC")
    u_warr_time = fields.Integer(string="Bảo hành hãng")
    u_warr_time_dist = fields.Integer(string="Bảo hành đại lý")
    u_min_stock = fields.Float(string="Tồn tối thiểu")
    u_max_stock = fields.Float(string="Tồn tối đa")

    # Nguồn gốc, thời gian
    country_org = fields.Char(string="Nước sản xuất")
    lead_time = fields.Integer(string="Lead Time")
    toleran_day = fields.Integer(string="ToleranDay")

    # Thông tin kinh doanh
    u_item_power = fields.Float(string="Công suất")
    u_business_unit = fields.Char(string="Đơn vị kinh doanh")

    # QryGroup
    qry_group32 = fields.Char(string="Thương mại")
    qry_group33 = fields.Char(string="Trưng bày")
    qry_group34 = fields.Char(string="Ngưng SX")
    qry_group35 = fields.Char(string="Là tủ điện")
    qry_group36 = fields.Char(string="...")
    qry_group37 = fields.Char(string="...")

    firm_code = fields.Integer(string="Firm Code")
    man_ser_num = fields.Char(string="Quản lý Serial")
    man_btch_num = fields.Char(string="Quản lý Batch")
    qry_group24 = fields.Char(string="...")
    prcrmnt_mtd = fields.Char(string="Phương thức mua sắm")

    invntry_uom = fields.Char(string="ĐVT tồn kho")
    buy_unit_msr = fields.Char(string="ĐVT mua")
    sal_unit_msr = fields.Char(string="ĐVT bán")
    qry_group29 = fields.Char(string="...")
    qry_group30 = fields.Char(string="...")

    # ====== MAP NHÓM SP ======
    itms_grp_cod = fields.Integer(string="Mã nhóm sản phẩm SAP", index=True)
    product_group_id = fields.Many2one(
        'oms.product.group',
        string="Nhóm sản phẩm OMS",
        compute='_compute_product_group',
        store=True,
        index=True,
    )

    # Thông tin bổ sung, mô tả thêm
    u_info_text = fields.Text(string="Thông tin bổ sung")
    u_vis_order = fields.Integer(string="Thứ tự hiển thị")

    # VAT, thông số vật lý khi mua vào
    vat_group_pu = fields.Char(string="VAT Mua vào")
    b_height1 = fields.Float(string="Chiều cao (mua vào)")
    b_width1 = fields.Float(string="Chiều rộng (mua vào)")
    b_length1 = fields.Float(string="Chiều dài (mua vào)")
    b_weight1 = fields.Float(string="Trọng lượng (mua vào)")

    # VAT, thông số vật lý khi bán ra
    vat_gourp_sa = fields.Char(string="VAT Bán ra")
    s_height1 = fields.Float(string="Chiều cao (bán ra)")
    s_width1 = fields.Float(string="Chiều rộng (bán ra)")
    s_length1 = fields.Float(string="Chiều dài (bán ra)")
    s_weight1 = fields.Float(string="Trọng lượng (bán ra)")

    # Thời gian tạo/cập nhật trên hệ thống gốc
    create_date = fields.Datetime(string="Ngày tạo (SAP B1)")
    update_date = fields.Datetime(string="Ngày cập nhật (SAP B1)")

    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('item_code_unique', 'unique(item_code)', 'Không được trùng mã sản phẩm!'),
    ]

    # -------------------------
    # AUTO MAP theo ItmsGrpCod
    # -------------------------
    @api.depends('itms_grp_cod')
    def _compute_product_group(self):
        Group = self.env['oms.product.group']
        for rec in self:
            grp = Group.search([('itms_grp_cod', '=', rec.itms_grp_cod)], limit=1) if rec.itms_grp_cod else False
            rec.product_group_id = grp.id if grp else False

    @api.onchange('itms_grp_cod')
    def _onchange_itms_grp_cod(self):
        """Đổi mã nhóm → cập nhật group ngay trên UI."""
        for rec in self:
            rec._compute_product_group()

    # -------------------------
    # Batch action chạy hàng loạt
    # -------------------------
    def action_map_group_from_code(self):
        Group = self.env['oms.product.group']
        updated = 0
        for rec in self.sudo():
            if not rec.itms_grp_cod:
                continue
            grp = Group.search([('itms_grp_cod', '=', rec.itms_grp_cod)], limit=1)
            if grp and rec.product_group_id != grp:
                rec.product_group_id = grp.id
                updated += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': "OMS", 'message': f"Đã map nhóm cho {updated} sản phẩm.", 'sticky': False}
        }
