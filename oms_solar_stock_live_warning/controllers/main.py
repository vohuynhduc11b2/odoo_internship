from odoo import http
from odoo.http import request


class OMSSolarLiveStockController(http.Controller):

    @http.route('/oms_solar_stock_live_warning/product_warning_info', type='json', auth='public', website=True, csrf=False)
    def product_warning_info(self, template_ids=None):
        ids = []
        for item in template_ids or []:
            try:
                item_id = int(item)
            except Exception:
                continue
            if item_id > 0:
                ids.append(item_id)
        products = request.env['product.template'].sudo().browse(ids).exists()
        payload = products._oms_live_stock_payload() if products else {}
        return {'ok': True, 'products': payload}
