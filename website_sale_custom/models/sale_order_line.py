# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale_custom.order.line'

    name_short = fields.Char(compute='_compute_name_short')
    shop_warning = fields.Char(string="Warning")

    #=== COMPUTE METHODS ===#

    @api.depends('product_id.display_name')
    def _compute_name_short(self):
        """ Compute a short name for this sale order line, to be used on the website where we don't have much space.
            To keep it short, instead of using the first line of the description, we take the product name without the internal reference.
        """
        for record in self:
            record.name_short = record.product_id.with_context(display_default_code=False).display_name

    #=== BUSINESS METHODS ===#

    def get_description_following_lines(self):
        return self.name.splitlines()[1:]

    def _get_order_date(self):
        self.ensure_one()
        if self.order_id.website_id and self.state == 'draft':
            # cart prices must always be computed based on the current time, not on the order
            # creation date.
            return fields.Datetime.now()
        return super()._get_order_date()

    def _get_shop_warning(self, clear=True):
        self.ensure_one()
        warn = self.shop_warning
        if clear:
            self.shop_warning = ''
        return warn

    def _get_displayed_unit_price(self):
        show_tax = self.order_id.website_id.show_line_subtotals_tax_selection
        tax_display = 'total_excluded' if show_tax == 'tax_excluded' else 'total_included'
        is_combo = self.product_type == 'combo'

        return self.tax_id.compute_all(
            price_unit=self._get_display_price_ignore_combo() if is_combo else self.price_unit,
            currency=self.currency_id,
            quantity=1.0,
            product=self.product_id,
            partner=self.order_partner_id,
        )[tax_display]

    def _get_displayed_quantity(self):
        rounded_uom_qty = round(self.product_uom_qty,
                                self.env['decimal.precision'].precision_get('Product Unit of Measure'))
        return int(rounded_uom_qty) == rounded_uom_qty and int(rounded_uom_qty) or rounded_uom_qty

    def _show_in_cart(self):
        is_delivery = bool(getattr(self, "is_delivery", False))
        is_gift = bool(getattr(self, "is_gift", False))
        is_bundle = bool(getattr(self, "is_bundle", False))
        return (
            (not is_delivery)
            and (not is_gift)
            and (not is_bundle)
            and (not bool(self.display_type))
            and (not bool(getattr(self, "combo_item_id", False)))
        )

    def _show_as_promo_line_in_cart(self):
        self.ensure_one()
        if bool(getattr(self, "is_delivery", False)) or bool(self.display_type) or bool(getattr(self, "combo_item_id", False)):
            return False
        kind = (getattr(self, "line_kind", None) or getattr(self, "U_isDiscount", None) or "").upper()
        return bool(getattr(self, "is_gift", False) or kind == "KM")

    def _is_not_sellable_line(self):
        res = super()._is_not_sellable_line()
        self.ensure_one()
        return res or self._show_as_promo_line_in_cart()


    def _is_reorder_allowed(self):
        self.ensure_one()
        return bool(self.product_id) and self.product_id._is_add_to_cart_allowed()

    def _get_cart_display_price(self):
        self.ensure_one()
        price_type = (
            'price_subtotal'
            if self.order_id.website_id.show_line_subtotals_tax_selection == 'tax_excluded'
            else 'price_total'
        )
        return sum(self._get_lines_with_price().mapped(price_type))

    def _uc_is_wait_sales_price_line(self):
        self.ensure_one()
        order = self.order_id
        if not order or not self.product_id or self.display_type:
            return False
        if bool(getattr(self, "is_delivery", False)) or bool(getattr(self, "is_gift", False)) or bool(getattr(self, "is_bundle", False)):
            return False

        pricelist = order._uc_get_current_website_pricelist() if hasattr(order, "_uc_get_current_website_pricelist") else order.pricelist_id
        if not hasattr(self.product_id, "_is_oms_managed_pricelist") or not self.product_id._is_oms_managed_pricelist(pricelist):
            return False

        line_price = float(self.price_unit or 0.0)
        if hasattr(self, 'technical_price_unit'):
            line_price = max(line_price, float(self.technical_price_unit or 0.0))
        if line_price > 1.0:
            return False

        current_price = self.price_unit or 0.0
        if hasattr(self, 'technical_price_unit'):
            current_price = max(current_price, float(self.technical_price_unit or 0.0))
        if hasattr(order, "_uc_compute_tier_price_unit"):
            try:
                current_price = order._uc_compute_tier_price_unit(
                    self.product_id,
                    self.product_uom_qty or 1.0,
                )
            except Exception:
                current_price = self.price_unit or 0.0

        currency = self.currency_id or order.currency_id
        if currency:
            return currency.is_zero(current_price or 0.0) or float(current_price or 0.0) <= 1.0
        return float(current_price or 0.0) <= 1.0
