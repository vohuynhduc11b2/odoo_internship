from collections import defaultdict

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    oms_available_qty = fields.Float(string='Tồn OMS khả dụng', compute='_compute_oms_stock_info')
    oms_show_low_stock_warning = fields.Boolean(string='Hiện cảnh báo tồn thấp OMS', compute='_compute_oms_stock_info')
    oms_low_stock_warning_message_display = fields.Char(string='Thông điệp cảnh báo tồn', compute='_compute_oms_stock_info')

    def _oms_get_low_stock_policy(self):
        self.ensure_one()
        category = self.categ_id
        threshold = float(getattr(category, 'oms_low_stock_threshold', 0.0) or 0.0)
        message = getattr(category, 'oms_low_stock_warning_message', False) or 'Sắp hết hàng, vui lòng liên hệ NVKD'
        enabled = bool(getattr(category, 'oms_low_stock_warning_enabled', False))

        names = [
            getattr(category, 'name', False),
            getattr(getattr(self, 'product_item_id', False), 'item_name', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'itms_grp_nam', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'u_product_family', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'u_product_line', False),
        ]
        haystack = ' '.join(filter(None, names)).lower()
        auto_keywords = ('inverter', 'battery', 'ắc quy', 'pin lưu trữ')
        auto_match = any(keyword in haystack for keyword in auto_keywords)

        if auto_match and threshold <= 0:
            threshold = 10.0
        return bool(enabled or auto_match), float(threshold or 0.0), message

    @api.depends('default_code', 'categ_id', 'product_item_id', 'product_item_id.product_group_id')
    def _compute_oms_stock_info(self):
        inventories = self.env['oms.inventory'].sudo().search([
            ('item_code', 'in', [code for code in {((p.default_code or '').strip()) for p in self} if code])
        ])
        qty_by_code = defaultdict(float)
        for inv in inventories:
            qty_by_code[(inv.item_code or '').strip()] += float(inv.u_available or 0.0)

        for product in self:
            code = (product.default_code or '').strip()
            qty = qty_by_code.get(code, 0.0)
            product.oms_available_qty = qty
            enabled, threshold, message = product._oms_get_low_stock_policy()
            product.oms_show_low_stock_warning = bool(enabled and qty < threshold)
            product.oms_low_stock_warning_message_display = message if product.oms_show_low_stock_warning else False

    def _get_combination_info(self, combination=False, product_id=False, add_qty=1.0, parent_combination=False, only_template=False):
        info = super()._get_combination_info(
            combination=combination,
            product_id=product_id,
            add_qty=add_qty,
            parent_combination=parent_combination,
            only_template=only_template,
        )
        price = float(info.get('price') or 0.0)
        currency = info.get('currency')
        is_vnd = bool(currency and getattr(currency, 'name', '') == 'VND')
        info['is_contact_price_one'] = bool(is_vnd and round(price, 0) == 1)
        info['oms_available_qty'] = float(self.oms_available_qty or 0.0)
        info['oms_show_low_stock_warning'] = bool(self.oms_show_low_stock_warning)
        info['oms_low_stock_warning_message_display'] = self.oms_low_stock_warning_message_display or False
        return info
