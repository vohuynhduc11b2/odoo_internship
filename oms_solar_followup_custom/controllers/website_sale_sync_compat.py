from odoo import http

from odoo.addons.sale_custom.controllers.website_sale_sync import WebsiteSaleSync as SaleCustomWebsiteSaleSync


class WebsiteSaleSyncCompat(SaleCustomWebsiteSaleSync):

    @http.route(['/shop/checkout'], type='http', auth='public', website=True, sitemap=False)
    def checkout(self, **post):
        self._sync_order_pricing(apply_promo=True)
        parent = super()
        if hasattr(parent, "shop_checkout"):
            return parent.shop_checkout(**post)
        return parent.checkout(**post)

    @http.route(['/shop/payment'], type='http', auth='public', website=True, sitemap=False)
    def payment(self, **post):
        self._sync_order_pricing(apply_promo=True)
        parent = super()
        if hasattr(parent, "shop_payment"):
            return parent.shop_payment(**post)
        return parent.payment(**post)
