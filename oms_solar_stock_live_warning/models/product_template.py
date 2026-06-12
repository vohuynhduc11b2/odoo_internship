import logging
from collections import defaultdict

from odoo import models

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _oms_live_low_stock_threshold(self):
        self.ensure_one()
        categ = self.categ_id
        threshold = float(getattr(categ, 'oms_low_stock_threshold', 0.0) or 0.0) or 10.0
        message = getattr(categ, 'oms_low_stock_warning_message', False) or 'Sắp hết hàng, vui lòng liên hệ NVKD'
        enabled = bool(getattr(categ, 'oms_low_stock_warning_enabled', False))
        names = [
            getattr(categ, 'name', False),
            getattr(getattr(self, 'product_item_id', False), 'item_name', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'itms_grp_nam', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'u_product_family', False),
            getattr(getattr(getattr(self, 'product_item_id', False), 'product_group_id', False), 'u_product_line', False),
        ]
        haystack = ' '.join([str(x) for x in names if x]).lower()
        auto_keywords = ('inverter', 'battery', 'ắc quy', 'pin lưu trữ')
        auto_match = any(keyword in haystack for keyword in auto_keywords)
        return bool(enabled or auto_match or True), float(threshold), message

    def _oms_live_qty_map_from_cache(self):
        qty_by_code = defaultdict(float)
        codes = [code for code in {((p.default_code or '').strip()) for p in self if p.default_code} if code]
        registry = self.env.registry
        if registry.get('oms.inventory') and codes:
            inventories = self.env['oms.inventory'].sudo().search([('item_code', 'in', codes)])
            value_fields = ['u_available', 'available_qty', 'qty_available', 'onhand_qty', 'on_hand']
            for inv in inventories:
                value = 0.0
                for name in value_fields:
                    if name in inv._fields:
                        value = float(inv[name] or 0.0)
                        break
                qty_by_code[(getattr(inv, 'item_code', False) or '').strip()] += value
        result = {}
        for product in self:
            code = (product.default_code or '').strip()
            result[product.id] = float(qty_by_code.get(code, 0.0))
        return result

    def _oms_try_refresh_inventory_from_api(self):
        item_codes = [code for code in {((p.default_code or '').strip()) for p in self if p.default_code} if code]
        if not item_codes:
            return False
        candidates = [
            ('oms.inventory', ['sync_item_codes_from_api', '_sync_item_codes_from_api', 'refresh_item_codes_from_api', 'refresh_item_codes', 'fetch_from_api', '_fetch_from_api', 'sync_from_api', '_sync_from_api', 'get_available_inventory', 'get_available_inventory_multi']),
            ('product.template', ['sync_oms_inventory_from_api', '_sync_oms_inventory_from_api', 'action_sync_oms_inventory', 'refresh_inventory_from_api', 'get_available_inventory']),
            ('product.product', ['sync_oms_inventory_from_api', '_sync_oms_inventory_from_api', 'refresh_inventory_from_api', 'get_available_inventory']),
        ]
        payload_variants = [
            (item_codes,),
            ({'item_codes': item_codes},),
            (self,),
            (),
        ]
        for model_name, method_names in candidates:
            if not self.env.registry.get(model_name):
                continue
            target = self.env[model_name].sudo()
            if model_name == 'product.template':
                target = self.sudo()
            elif model_name == 'product.product':
                target = self.product_variant_ids.sudo()
            for method_name in method_names:
                method = getattr(target, method_name, None)
                if not callable(method):
                    continue
                for args in payload_variants:
                    try:
                        method(*args)
                        _logger.info('[OMS_LIVE_STOCK] Refreshed inventory via %s.%s', model_name, method_name)
                        return True
                    except TypeError:
                        continue
                    except Exception:
                        _logger.debug('[OMS_LIVE_STOCK] Refresh via %s.%s failed', model_name, method_name, exc_info=True)
                        break
        return False

    def _oms_live_stock_payload(self):
        products = self.sudo().exists()
        if not products:
            return {}
        try:
            products._oms_try_refresh_inventory_from_api()
        except Exception:
            _logger.debug('[OMS_LIVE_STOCK] Inventory refresh failed; fallback to cache', exc_info=True)
        qty_map = products._oms_live_qty_map_from_cache()
        result = {}
        for product in products:
            qty = float(qty_map.get(product.id, 0.0))
            enabled, threshold, message = product._oms_live_low_stock_threshold()
            warning = bool(enabled and qty < threshold)
            result[product.id] = {'qty': qty, 'warning': warning, 'message': message if warning else ''}
        return result
