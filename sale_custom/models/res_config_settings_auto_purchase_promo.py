# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    auto_purchase_first_promo_id = fields.Many2one(
        "oms.promotion",
        string="KM tự động - Mua lần đầu",
        config_parameter="sale_custom.auto_purchase_first_promo_id",
    )
    auto_purchase_second_promo_id = fields.Many2one(
        "oms.promotion",
        string="KM tự động - Mua lần hai",
        config_parameter="sale_custom.auto_purchase_second_promo_id",
    )

    auto_purchase_second_days = fields.Integer(
        string="Số ngày áp dụng Mua lần hai",
        default=15,
        config_parameter="sale_custom.auto_purchase_second_days",
    )
    auto_purchase_reset_months = fields.Integer(
        string="Reset chu kỳ (tháng không mua)",
        default=12,
        config_parameter="sale_custom.auto_purchase_reset_months",
    )

    auto_purchase_exclude_group_name = fields.Char(
        string="Loại trừ nhóm hàng (vd: Tấm pin)",
        default="Tấm pin",
        config_parameter="sale_custom.auto_purchase_exclude_group_name",
    )