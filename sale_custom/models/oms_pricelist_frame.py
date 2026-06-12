# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class OmsPriceListFrame(models.Model):
    _name = "oms.pricelist.frame"
    _description = "OMS PriceList Frame"
    _rec_name = "price_list_name"
    _order = "category_name, api_id"

    active = fields.Boolean(default=True)

    # Khóa chính theo API
    api_id = fields.Integer(string="API Id", required=True, index=True)

    category_id = fields.Integer(string="Category Id", index=True)
    category_name = fields.Char(string="Category Name", index=True)

    price_list_name = fields.Char(string="Price List Name", required=True)

    # Flag từ API: đang trả "x" hoặc rỗng => map boolean
    pm = fields.Boolean(string="PM")
    sup = fields.Boolean(string="SUP")
    sale = fields.Boolean(string="Sale")
    cc_tech = fields.Boolean(string="CC_Tech")

    # Bổ sung theo yêu cầu
    min_qty = fields.Float(string="Min Quantity", default=0.0)
    max_qty = fields.Float(string="Max Quantity", default=0.0)
    publish_pricelist_ids = fields.Many2many(
        "product.pricelist",
        "oms_pricelist_frame_publish_pricelist_rel",
        "frame_id",
        "pricelist_id",
        string="Bảng giá lấy",
        help=(
            "Chọn các bảng giá Odoo sẽ nhận giá từ frame này khi publish. "
            "Ví dụ: [OMS] DEFAULT, [OMS] DAC BIET hoặc bảng giá tự tạo."
        ),
    )

    _sql_constraints = [
        ("oms_pricelist_frame_api_id_uniq", "unique(api_id)", "API Id đã tồn tại!"),
    ]

    @api.constrains("min_qty", "max_qty")
    def _check_min_max_qty(self):
        for r in self:
            if r.min_qty < 0 or r.max_qty < 0:
                raise ValidationError(_("Min/Max Quantity không được âm."))
            if r.max_qty and r.min_qty and r.max_qty < r.min_qty:
                raise ValidationError(_("Max Quantity phải >= Min Quantity."))
