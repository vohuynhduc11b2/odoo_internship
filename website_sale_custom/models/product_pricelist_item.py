# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    #=== CRUD METHODS ===#

    def write(self, vals):
        """Clear cache when pricelist items are updated to ensure website prices are refreshed."""
        res = super().write(vals)
        if self:
            self.env.registry.clear_cache()
        return res

    def unlink(self):
        """Clear cache when pricelist items are deleted."""
        res = super().unlink()
        if self:
            self.env.registry.clear_cache()
        return res

    #=== BUSINESS METHODS ===#

    def _show_discount_on_shop(self):
        """On ecommerce, formula rules are also expected to show discounts.

        Only for /shop, /product, and configurators, not on the cart or the checkout.
        """
        if not self:
            return False

        self.ensure_one()

        return self.compute_price == 'percentage' or (
            self.compute_price == 'formula'
            and self.price_discount
            and self.base in ('list_price', 'pricelist')
        )
