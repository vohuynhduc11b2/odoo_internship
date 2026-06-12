# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
import random
from odoo.http import route
from datetime import datetime

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.http import request
from odoo.osv import expression
from odoo.tools import float_is_zero
# -*- coding: utf-8 -*-


_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale_custom.order'

    website_id = fields.Many2one(
        help="Website through which this order was placed for eCommerce orders.",
        comodel_name='website',
        readonly=True,
    )

    cart_recovery_email_sent = fields.Boolean(string="Cart recovery email already sent")
    shop_warning = fields.Char(string="Warning")

    # Computed fields
    website_order_line = fields.One2many(
        string="Order Lines displayed on Website",
        comodel_name='sale_custom.order.line',
        compute='_compute_website_order_line',
    )  # should not be used for computation purpose.',
    amount_delivery = fields.Monetary(
        string="Delivery Amount",
        compute='_compute_amount_delivery',
        help="Tax included or excluded depending on the website configuration.",
    )
    cart_quantity = fields.Integer(string="Cart Quantity", compute='_compute_cart_info')
    only_services = fields.Boolean(string="Only Services", compute='_compute_cart_info')
    is_abandoned_cart = fields.Boolean(
        string="Abandoned Cart", compute='_compute_abandoned_cart', search='_search_abandoned_cart',
    )

    carrier_id = fields.Many2one("delivery.carrier", string="Delivery Method", readonly=False)
    delivery_price = fields.Monetary(
        string="Delivery Price",
        currency_field="currency_id",
        default=0.0,
        copy=False,
    )

    has_carrier = fields.Boolean(compute="_compute_has_carrier", store=False)
    has_deliverable_products = fields.Boolean(compute="_compute_has_deliverable_products", store=False)
    pickup_location_data = fields.Json(string="Pickup Location Data", default=dict, copy=False)

    @api.depends("carrier_id")
    def _compute_has_carrier(self):
        for order in self:
            order.has_carrier = bool(order.carrier_id)

    @api.depends("order_line.product_id", "only_services")
    def _compute_has_deliverable_products(self):
        for order in self:
            # dùng logic bạn đã có
            order.has_deliverable_products = bool(order.order_line.product_id) and not order.only_services

    # Alias tương thích chỗ controller gọi
    def _compute_amount_total_without_delivery(self):
        """website_sale/payment expects this name in some versions."""
        self.ensure_one()
        return self._get_amount_total_excluding_delivery()

    def _get_amount_total_excluding_delivery(self):
        self.ensure_one()
        return sum(self._get_non_delivery_lines().mapped("price_total"))

    def set_delivery_line(self, carrier, price):
        """Minimal implementation for website flow: create/update a delivery line."""
        self.ensure_one()
        # nếu bạn không muốn tạo line giao hàng thật thì cứ no-op:
        return True


    #=== COMPUTE METHODS ===#

    @api.depends('order_line')
    def _compute_website_order_line(self):
        for order in self:
            order.website_order_line = order.order_line.filtered(
                lambda sol: sol._show_in_cart() or sol._show_as_promo_line_in_cart()
            )

    @api.depends('order_line.price_total', 'order_line.price_subtotal')
    def _compute_amount_delivery(self):
        for order in self:
            order.amount_delivery = 0.0
    
        for order in self.filtered('website_id'):
            delivery_lines = order.order_line.filtered(lambda l: bool(getattr(l, 'is_delivery', False)))
            if order.website_id.show_line_subtotals_tax_selection == 'tax_excluded':
                order.amount_delivery = sum(delivery_lines.mapped('price_subtotal'))
            else:
                order.amount_delivery = sum(delivery_lines.mapped('price_total'))


    @api.depends('order_line.product_uom_qty', 'order_line.product_id')
    def _compute_cart_info(self):
        for order in self:
            sale_lines = order.website_order_line.filtered(lambda l: not l._show_as_promo_line_in_cart())
            order.cart_quantity = int(sum(sale_lines.mapped('product_uom_qty')))
            order.only_services = all(sol.product_id.type == 'service' for sol in sale_lines)

    @api.depends('website_id', 'date_order', 'order_line', 'state', 'partner_id')
    def _compute_abandoned_cart(self):
        for order in self:
            # a quotation can be considered as an abandonned cart if it is linked to a website,
            # is in the 'draft' state and has an expiration date
            if order.website_id and order.state == 'draft' and order.date_order:
                public_partner_id = order.website_id.user_id.partner_id
                # by default the expiration date is 1 hour if not specified on the website configuration
                abandoned_delay = order.website_id.cart_abandoned_delay or 1.0
                abandoned_datetime = datetime.utcnow() - relativedelta(hours=abandoned_delay)
                order.is_abandoned_cart = bool(order.date_order <= abandoned_datetime and order.partner_id != public_partner_id and order.order_line)
            else:
                order.is_abandoned_cart = False

    def _compute_require_signature(self):
        website_orders = self.filtered('website_id')
        website_orders.require_signature = False
        super(SaleOrder, self - website_orders)._compute_require_signature()

    def _compute_payment_term_id(self):
        super()._compute_payment_term_id()
        website_orders = self.filtered(
            lambda so: so.website_id and not so.payment_term_id
        )
        if not website_orders:
            return

        # Try to find a payment term even if there wasn't any set on the partner
        default_pt = self.env.ref(
            'account.account_payment_term_immediate', raise_if_not_found=False)
        for order in website_orders:
            if default_pt and (
                order.company_id == default_pt.company_id
                or not default_pt.company_id
            ):
                order.payment_term_id = default_pt
            else:
                order.payment_term_id = order.env['account.payment.term'].search([
                    ('company_id', '=', order.company_id.id),
                ], limit=1)

    def _compute_pricelist_id(self):
        # Override to compute pricelists for carts using the partner's GeoIP,
        # providing a fallback in case they don't have an address set.
        if not (country_code := self.env['website']._get_geoip_country_code()):
            return super()._compute_pricelist_id()
        if website_orders := self.filtered('website_id'):
            website_orders = website_orders.with_context(country_code=country_code)
            super(SaleOrder, website_orders)._compute_pricelist_id()
        return super(SaleOrder, self - website_orders)._compute_pricelist_id()

    def _search_abandoned_cart(self, operator, value):
        website_ids = self.env['website'].search_read(fields=['id', 'cart_abandoned_delay', 'partner_id'])
        deadlines = [[
            '&', '&',
            ('website_id', '=', website_id['id']),
            ('date_order', '<=', fields.Datetime.to_string(datetime.utcnow() - relativedelta(hours=website_id['cart_abandoned_delay'] or 1.0))),
            ('partner_id', '!=', website_id['partner_id'][0])
        ] for website_id in website_ids]
        abandoned_domain = [
            ('state', '=', 'draft'),
            ('order_line', '!=', False)
        ]
        abandoned_domain.extend(expression.OR(deadlines))
        abandoned_domain = expression.normalize_domain(abandoned_domain)
        # is_abandoned domain possibilities
        if (operator not in expression.NEGATIVE_TERM_OPERATORS and value) or (operator in expression.NEGATIVE_TERM_OPERATORS and not value):
            return abandoned_domain
        return expression.distribute_not(['!'] + abandoned_domain)  # negative domain

    def _compute_user_id(self):
        """ Do not assign self.env.user as salesman for e-commerce orders.

        Leave salesman empty if no salesman is specified on partner or website.

        c/p of the logic in Website._prepare_sale_order_values
        """
        website_orders = self.filtered('website_id')
        super(SaleOrder, self - website_orders)._compute_user_id()
        for order in website_orders:
            if not order.user_id:
                order.user_id = (
                    order.website_id.salesperson_id
                    or order.partner_id.user_id.id
                    or order.partner_id.parent_id.user_id.id
                )

    #=== CRUD METHODS ===#

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('website_id'):
                website = self.env['website'].browse(vals['website_id'])
                if 'company_id' in vals:
                    company = self.env['res.company'].browse(vals['company_id'])
                    if website.company_id.id != company.id:
                        raise ValueError(_(
                            "The company of the website you are trying to sell from (%(website_company)s)"
                            " is different than the one you want to use (%(company)s)",
                            website_company=website.company_id.name,
                            company=company.name,
                        ))
                else:
                    vals['company_id'] = website.company_id.id
        return super().create(vals_list)

    #=== ACTION METHODS ===#

    def action_preview_sale_order(self):
        action = super().action_preview_sale_order()
        if action['url'].startswith('/'):
            # URL should always be relative, safety check
            action['url'] = f'/@{action["url"]}'
        return action

    def action_recovery_email_send(self):
        for order in self:
            order._portal_ensure_token()
        composer_form_view_id = self.env.ref('mail.email_compose_message_wizard_form').id

        template_id = self._get_cart_recovery_template().id

        return {
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'view_id': composer_form_view_id,
            'target': 'new',
            'context': {
                'default_composition_mode': 'mass_mail' if len(self.ids) > 1 else 'comment',
                'default_email_layout_xmlid': 'mail.mail_notification_layout_with_responsible_signature',
                'default_res_ids': self.ids,
                'default_model': 'sale_custom.order',
                'default_template_id': template_id,
                'website_sale_send_recovery_email': True,
            },
        }

    def _get_cart_recovery_template(self):
        """ Return the cart recovery template record for a set of orders.

        If they all belong to the same website, we return the website-specific template;
        otherwise we return the default template.
        If the default is not found, the empty ['mail.template'] is returned.
        """
        websites = self.mapped('website_id')
        template = websites.cart_recovery_mail_template_id if len(websites) == 1 else False
        template = template or self.env.ref('website_sale_custom.mail_template_sale_cart_recovery', raise_if_not_found=False)
        return template or self.env['mail.template']

    #=== BUSINESS METHODS ===#

    @api.model
    def _get_note_url(self):
        website_id = self._context.get('website_id')
        if website_id:
            return self.env['website'].browse(website_id).get_base_url()
        return super()._get_note_url()

    def _get_non_delivery_lines(self):
        """Exclude delivery-related lines."""
        return self.order_line.filtered(lambda line: not bool(getattr(line, "is_delivery", False)))


    def _get_amount_total_excluding_delivery(self):
        return sum(self._get_non_delivery_lines().mapped('price_total'))

    def _oms_get_strategic_pricing_partner(self):
        self.ensure_one()
        partners = (
            self.partner_invoice_id
            | self.partner_id
            | self.partner_shipping_id
        ).exists()
        for partner in partners:
            if hasattr(partner, "_oms_get_strategic_pricing_partner"):
                pricing_partner = partner._oms_get_strategic_pricing_partner()
                if pricing_partner:
                    return pricing_partner
        return self.env['res.partner']

    def _oms_normalize_payment_mode(self, payment_mode=None):
        payment_mode = (payment_mode or "").strip().lower()
        aliases = {
            "qr": "full",
            "qrcode": "full",
            "prepaid": "full",
            "prepay": "full",
            "tra_truoc": "full",
            "trả trước": "full",
            "coc": "deposit",
            "cọc": "deposit",
            "partial": "deposit",
            "uy_nhiem_chi": "unc",
            "phieu_uy_nhiem_chi": "unc",
            "credit": "credit",
            "debt": "credit",
            "congno": "credit",
            "cong_no": "credit",
            "công nợ": "credit",
            "pay_later": "credit",
        }
        return aliases.get(payment_mode, payment_mode or "credit")

    def _oms_is_prepaid_payment_mode(self, payment_mode=None):
        return self._oms_normalize_payment_mode(payment_mode) in ("full", "deposit", "unc")

    def _oms_get_strategic_pricelist(self, payment_mode=None):
        self.ensure_one()
        pricing_partner = self._oms_get_strategic_pricing_partner()
        if not pricing_partner:
            return self.env['product.pricelist']

        payment_mode = (payment_mode or "").strip().lower()
        if not payment_mode:
            if pricing_partner.oms_prepaid_pricelist_id and pricing_partner.oms_debt_pricelist_id:
                return self.env['product.pricelist']
            if pricing_partner.oms_prepaid_pricelist_id:
                return pricing_partner.oms_prepaid_pricelist_id
            return pricing_partner.oms_debt_pricelist_id or pricing_partner.property_product_pricelist

        if self._oms_is_prepaid_payment_mode(payment_mode):
            return pricing_partner.oms_prepaid_pricelist_id or pricing_partner.oms_debt_pricelist_id
        return pricing_partner.oms_debt_pricelist_id or pricing_partner.property_product_pricelist

    def _oms_apply_strategic_payment_pricelist(self, payment_mode=None):
        for order in self:
            if not order.website_id or order.state != 'draft':
                continue

            target_pricelist = order._oms_get_strategic_pricelist(payment_mode)
            if not target_pricelist:
                continue
            if (
                hasattr(target_pricelist, "_is_available_on_website")
                and not target_pricelist._is_available_on_website(order.website_id)
            ):
                continue

            if order.pricelist_id != target_pricelist:
                order.sudo().write({"pricelist_id": target_pricelist.id})
                order.with_context(uc_preserve_website_line_prices=True)._recompute_taxes()

            for line in order._get_non_delivery_lines().filtered(lambda l: l.product_id and not l.display_type):
                if hasattr(order, "_uc_apply_tier_price_on_line"):
                    order._uc_apply_tier_price_on_line(line, force=True)

            if hasattr(order, "_amount_all"):
                order._amount_all()
            elif hasattr(order, "_compute_amounts"):
                order._compute_amounts()

        return True

    def _uc_wait_sales_price_lines(self):
        self.ensure_one()
        pricelist = self._uc_get_current_website_pricelist()
        return self.order_line.filtered(
            lambda line: line.product_id
            and not line.display_type
            and not bool(getattr(line, "is_delivery", False))
            and not bool(getattr(line, "is_gift", False))
            and not bool(getattr(line, "is_bundle", False))
            and hasattr(line.product_id, "_is_oms_managed_pricelist")
            and line.product_id._is_oms_managed_pricelist(pricelist)
            and (
                line._uc_is_wait_sales_price_line()
                if hasattr(line, "_uc_is_wait_sales_price_line")
                else (
                    not line.currency_id
                    or line.currency_id.is_zero(line.price_unit or 0.0)
                    or float(line.price_unit or 0.0) <= 1.0
                )
            )
        )

    def _uc_has_wait_sales_price(self):
        self.ensure_one()
        return bool(self._uc_wait_sales_price_lines())

    
    
    # --- ADD: helpers tính giá tier giống PDP ---
    def _uc_get_current_website_pricelist(self):
        """Lấy pricelist giống website đang browse (ưu tiên website/session)."""
        self.ensure_one()
        if self.pricelist_id:
            return self.pricelist_id

        # ưu tiên request.website (đúng với flow frontend)
        try:
            if request and getattr(request, "website", False):
                return request.website.get_current_pricelist()
        except Exception:
            pass
        
        # fallback theo website_id của order
        try:
            if self.website_id:
                return self.website_id.get_current_pricelist()
        except Exception:
            pass
        
        return self.pricelist_id
    
    
    def _uc_compute_tier_price_unit(self, product, qty):
        """Tính unit price theo tier/pricelist như PDP."""
        self.ensure_one()
        qty = float(qty or 0.0)
        pricelist = self._uc_get_current_website_pricelist()
        partner = self.partner_id
    
        # 1) Nếu bạn đã có hàm tier custom dùng cho PDP -> ưu tiên gọi nó (để khớp tuyệt đối)
        for hook_name in ("uc_get_website_unit_price", "uc_get_tier_price_unit", "uc_get_tier_price"):
            if hasattr(product, hook_name):
                try:
                    price = getattr(product, hook_name)(order=self, qty=qty, pricelist=pricelist, partner=partner)
                    if price is not None:
                        return float(price)
                except Exception:
                    pass
                
        if pricelist and hasattr(product, "_is_oms_managed_pricelist") and product._is_oms_managed_pricelist(pricelist):
            return 0.0

        # 2) Nếu tier của bạn đang embed vào _get_contextual_price (rất hay dùng trên website)
        if hasattr(product, "_get_contextual_price") and pricelist:
            try:
                ctx = dict(self.env.context)
                ctx.update({
                    "pricelist": pricelist.id,
                    "quantity": qty,
                    "partner": partner.id,
                })
                price = product.with_context(ctx)._get_contextual_price()
                if price is not None:
                    return float(price)
            except Exception:
                pass
            
        # 3) Fallback: Odoo pricelist chuẩn (min_quantity sẽ tự ra tier đúng)
        try:
            if pricelist:
                return float(pricelist._get_product_price(product, qty, partner))
        except Exception:
            pass
        
        return float(product.lst_price)
    
    
    def _uc_apply_tier_price_on_line(self, line, force=False):
        """Set lại price_unit theo tier cho line vừa add/update qty."""
        self.ensure_one()
        if not line or not line.product_id or line.display_type:
            return
    
        # né delivery/gift/bundle nếu hệ bạn có
        if bool(getattr(line, "is_delivery", False)) or bool(getattr(line, "is_gift", False)) or bool(getattr(line, "is_bundle", False)):
            return
    
        # preserve existing sales/manual line prices unless explicitly forced
        if not force and float(line.price_unit or 0.0) > 0.0:
            return

        price_unit = self._uc_compute_tier_price_unit(line.product_id, line.product_uom_qty)
        if (
            float(price_unit or 0.0) <= 1.0
            and float(line.price_unit or 0.0) > 1.0
        ):
            return
        if self.currency_id:
            price_unit = self.currency_id.round(price_unit)

        _logger.info(
            "[UC TIER PRICE] order=%s product=%s qty=%s -> price_unit=%s (pl=%s)",
            getattr(self, "name", self.id),
            line.product_id.display_name,
            line.product_uom_qty,
            price_unit,
            getattr(self._uc_get_current_website_pricelist(), "display_name", None),
        )

        line.sudo().write({"price_unit": price_unit})

        # nếu bạn có engine KM/voucher chạy sau base price thì gọi lại
        if hasattr(line, "apply_promotions_to_line"):
            line.apply_promotions_to_line()

    def _cart_update_order_line(self, product_id, quantity, order_line, **kwargs):
        self.ensure_one()
    
        if order_line and quantity <= 0:
            order_line.unlink()
            order_line = self.env['sale_custom.order.line']
    
        elif order_line:
            update_values = self._prepare_order_line_update_values(order_line, quantity, **kwargs)
            if update_values:
                self._update_cart_line_values(order_line, update_values)
    
            # >>> ADD: sync giá tier sau khi đổi qty
            self._uc_apply_tier_price_on_line(order_line, force=True)
    
        elif quantity > 0:
            order_line_values = self._prepare_order_line_values(product_id, quantity, **kwargs)
            order_line = self.env['sale_custom.order.line'].sudo().create(order_line_values)
    
            # >>> ADD: set giá tier ngay khi tạo line mới
            self._uc_apply_tier_price_on_line(order_line, force=True)
    
        return order_line

    def _update_address(self, partner_id, fnames=None):
        if not fnames:
            return

        fpos_before = self.fiscal_position_id
        pricelist_before = self.pricelist_id

        self = self.with_context(
            uc_skip_autofill_partner=True,   # <<< FLAG CHỐT
            mail_notrack=True,
            tracking_disable=True,
        )
        self.write(dict.fromkeys(fnames, partner_id))

        fpos_changed = fpos_before != self.fiscal_position_id
        if fpos_changed:
            # Recompute taxes on fpos change
            self._recompute_taxes()

        # If the user has explicitly selected a valid pricelist, we don't want to change it
        if selected_pricelist_id := request.session.get('website_sale_selected_pl_id'):
            selected_pricelist = (
                self.env['product.pricelist'].browse(selected_pricelist_id).exists()
            )
            if (
                selected_pricelist
                and selected_pricelist._is_available_on_website(self.website_id)
                and selected_pricelist._is_available_in_country(
                    self.partner_id.country_id.code
                )
            ):
                self.pricelist_id = selected_pricelist
            else:
               request.session.pop('website_sale_selected_pl_id', None)

        if self.pricelist_id != pricelist_before or fpos_changed:
            # Pricelist may have been recomputed by the `partner_id` field update
            # we need to recompute the prices to match the new pricelist if it changed
            self._recompute_prices()
            if not self.website_id:
                for line in self._get_non_delivery_lines():
                    self._uc_apply_tier_price_on_line(line)

            request.session['website_sale_current_pl'] = self.pricelist_id.id
            self.website_id.invalidate_recordset(['pricelist_id'])

        if self.carrier_id and 'partner_shipping_id' in fnames and self._has_deliverable_products():
            # Update the delivery method on shipping address change.
            delivery_methods = self._get_delivery_methods()
            delivery_method = self._get_preferred_delivery_method(delivery_methods)
            self._set_delivery_method(delivery_method)

        if 'partner_id' in fnames:
            # Only add the main partner as follower of the order
            self._message_subscribe([partner_id])

    def _cart_update_pricelist(self, pricelist_id=None):
        self.ensure_one()

        if self.pricelist_id.id != pricelist_id:
            self.pricelist_id = pricelist_id
            self._recompute_prices()
            if not self.website_id:
                for line in self._get_non_delivery_lines():
                    self._uc_apply_tier_price_on_line(line)

    def _cart_find_product_line(
        self,
        product_id,
        line_id=None,
        linked_line_id=False,
        no_variant_attribute_value_ids=None,
        **kwargs
    ):
        """Find the cart line matching the given parameters.

        Custom attributes won't be matched (but no_variant & dynamic ones will be)

        :param int product_id: the product being added/removed, as a `product.product` id
        :param int line_id: optional, the line the customer wants to edit (/shop/cart page), as a
            `sale_custom.order.line` id
        :param int linked_line_id: optional, the parent line (for optional products), as a
            `sale_custom.order.line` id
        :param list optional_product_ids: optional, the optional products of the line, as a list
            of `product.product` ids
        :param list no_variant_attribute_value_ids: list of `product.template.attribute.value` ids
            whose attribute is configured as `no_variant`
        :param dict kwargs: unused parameters, maybe used in overrides or other cart update methods
        """
        self.ensure_one()

        if not self.order_line:
            return self.env['sale_custom.order.line']

        if line_id:
            # If we update a specific line, there is no need to filter anything else
            return self.order_line.filtered(
                lambda sol: sol.product_id.id == product_id and sol.id == line_id
            )

        product = self.env['product.product'].browse(product_id)
        if product.type == 'combo':
            return self.env['sale_custom.order.line']

        domain = [
            ('product_id', '=', product_id),
            ('product_custom_attribute_value_ids', '=', False),
            ('linked_line_id', '=', linked_line_id),
            ('combo_item_id', '=', False),
        ]

        filtered_sol = self.order_line.filtered_domain(domain)
        if not filtered_sol:
            return self.env['sale_custom.order.line']

        has_configurable_no_variant_attributes = any(
            len(line.value_ids) > 1 or line.attribute_id.display_type == 'multi'
            for line in product.attribute_line_ids
            if line.attribute_id.create_variant == 'no_variant'
        )
        if has_configurable_no_variant_attributes:
            filtered_sol = filtered_sol.filtered(
                lambda sol:
                    sol.product_no_variant_attribute_value_ids.ids == no_variant_attribute_value_ids
            )

        return filtered_sol

    # hook to be overridden
    def _verify_updated_quantity(self, order_line, product_id, new_qty, **kwargs):
        return new_qty, ''

    def _prepare_order_line_values(
        self, product_id, quantity, linked_line_id=False,
        no_variant_attribute_value_ids=None, product_custom_attribute_values=None,
        combo_item_id=None,
        **kwargs
    ):
        self.ensure_one()
        product = self.env['product.product'].browse(product_id)

        no_variant_attribute_values = product.env['product.template.attribute.value'].browse(
            no_variant_attribute_value_ids
        )
        received_combination = product.product_template_attribute_value_ids | no_variant_attribute_values
        product_template = product.product_tmpl_id

        # handle all cases where incorrect or incomplete data are received
        combination = product_template._get_closest_possible_combination(received_combination)

        # get or create (if dynamic) the correct variant
        product = product_template._create_product_variant(combination)

        if not product:
            raise UserError(_("The given combination does not exist therefore it cannot be added to cart."))

        values = {
            'product_id': product.id,
            'product_uom_qty': quantity,
            'order_id': self.id,
            'linked_line_id': linked_line_id,
            'combo_item_id': combo_item_id,
        }

        # add no_variant attributes that were not received
        no_variant_attribute_values |= combination.filtered(
            lambda ptav: ptav.attribute_id.create_variant == 'no_variant'
        )

        if no_variant_attribute_values:
            values['product_no_variant_attribute_value_ids'] = [Command.set(no_variant_attribute_values.ids)]

        # add is_custom attribute values that were not received
        custom_values = product_custom_attribute_values or []
        received_custom_values = product.env['product.template.attribute.value'].browse([
            int(ptav['custom_product_template_attribute_value_id'])
            for ptav in custom_values
        ])

        for ptav in combination.filtered(lambda ptav: ptav.is_custom and ptav not in received_custom_values):
            custom_values.append({
                'custom_product_template_attribute_value_id': ptav.id,
                'custom_value': '',
            })

        if custom_values:
            values['product_custom_attribute_value_ids'] = [
                fields.Command.create({
                    'custom_product_template_attribute_value_id': custom_value['custom_product_template_attribute_value_id'],
                    'custom_value': custom_value['custom_value'],
                }) for custom_value in custom_values
            ]

        return values

    def _prepare_order_line_update_values(
        self, order_line, quantity, linked_line_id=False, **kwargs
    ):
        self.ensure_one()
        values = {}

        if quantity != order_line.product_uom_qty:
            values['product_uom_qty'] = quantity
        if linked_line_id and linked_line_id != order_line.linked_line_id.id:
            values['linked_line_id'] = linked_line_id

        return values

    # hook to be overridden
    def _update_cart_line_values(self, order_line, update_values):
        self.ensure_one()
        order_line.write(update_values)

    def _cart_accessories(self):
        """ Suggest accessories based on 'Accessory Products' of products in cart """
        product_ids = set(self.website_order_line.product_id.ids)
        all_accessory_products = self.env['product.product']
        for line in self.website_order_line.filtered('product_id'):
            accessory_products = line.product_id.product_tmpl_id._get_website_accessory_product()
            if accessory_products:
                # Do not read ptavs if there is no accessory products to filter
                combination = line.product_id.product_template_attribute_value_ids + line.product_no_variant_attribute_value_ids
                all_accessory_products |= accessory_products.filtered(lambda product:
                    product.id not in product_ids
                    and product._website_show_quick_add()
                    and product.filtered_domain(self.env['product.product']._check_company_domain(line.company_id))
                    and product._is_variant_possible(parent_combination=combination)
                    and (
                        not self.website_id.prevent_zero_price_sale
                        or product._get_contextual_price()
                    )
                )

        return random.sample(all_accessory_products, len(all_accessory_products))

    def _cart_recovery_email_send(self):
        """Send the cart recovery email on the current recordset,
        making sure that the portal token exists to avoid broken links, and marking the email as sent.
        Similar method to action_recovery_email_send, made to be called in automation rules.
        Contrary to the former, it will use the website-specific template for each order."""
        sent_orders = self.env['sale_custom.order']
        for order in self:
            template = order._get_cart_recovery_template()
            if template:
                order._portal_ensure_token()
                template.send_mail(order.id)
                sent_orders |= order
        sent_orders.write({'cart_recovery_email_sent': True})

    def _message_mail_after_hook(self, mails):
        """ After sending recovery cart emails, update orders to avoid sending
        it again. """
        if self.env.context.get('website_sale_send_recovery_email'):
            self.filtered_domain([
                ('cart_recovery_email_sent', '=', False),
                ('is_abandoned_cart', '=', True)
            ]).cart_recovery_email_sent = True
        return super()._message_mail_after_hook(mails)

    def _message_post_after_hook(self, message, msg_vals):
        """ After sending recovery cart emails, update orders to avoid sending
        it again. """
        if self.env.context.get('website_sale_send_recovery_email'):
            self.cart_recovery_email_sent = True
        return super()._message_post_after_hook(message, msg_vals)

    def _notify_get_recipients_groups(self, message, model_description, msg_vals=None):
        """ In case of cart recovery email, update link to redirect directly
        to the cart (like ``mail_template_sale_cart_recovery`` template). """
        groups = super()._notify_get_recipients_groups(
            message, model_description, msg_vals=msg_vals
        )
        if not self:
            return groups

        self.ensure_one()
        customer_portal_group = next((group for group in groups if group[0] == 'portal_customer'), None)
        if customer_portal_group:
            access_opt = customer_portal_group[2].setdefault('button_access', {})
            if self._context.get('website_sale_send_recovery_email'):
                access_opt['title'] = _('Resume Order')
                access_opt['url'] = '%s/shop/cart?access_token=%s' % (self.get_base_url(), self.access_token)
        return groups

    def _is_reorder_allowed(self):
        self.ensure_one()
        return self.state == 'sale' and any(
            line._is_reorder_allowed() for line in self.order_line if line.product_id
        )

    def _filter_can_send_abandoned_cart_mail(self):
        self.website_id.ensure_one()
        abandoned_datetime = datetime.utcnow() - relativedelta(hours=self.website_id.cart_abandoned_delay)

        sales_after_abandoned_date = self.env['sale_custom.order'].search([
            ('state', '=', 'sale'),
            ('partner_id', 'in', self.partner_id.ids),
            ('create_date', '>=', abandoned_datetime),
            ('website_id', '=', self.website_id.id),
        ])
        latest_create_date_per_partner = {}
        for sale in self:
            if sale.partner_id not in latest_create_date_per_partner:
                latest_create_date_per_partner[sale.partner_id] = sale.create_date
            else:
                latest_create_date_per_partner[sale.partner_id] = max(latest_create_date_per_partner[sale.partner_id], sale.create_date)
        has_later_sale_order = {}
        for sale in sales_after_abandoned_date:
            if has_later_sale_order_custom.get(sale.partner_id, False):
                continue
            has_later_sale_order[sale.partner_id] = latest_create_date_per_partner[sale.partner_id] <= sale.date_order

        # Customer needs to be signed in otherwise the mail address is not known.
        # We therefore consider only sales with a known mail address.

        # If a payment processing error occurred when the customer tried to complete their checkout,
        # then the email won't be sent.

        # If all the products in the checkout are free, and the customer does not visit the shipping page to add a
        # shipping fee or the shipping fee is also free, then the email won't be sent.

        # If a potential customer creates one or more abandoned sale order and then completes a sale order before
        # the recovery email gets sent, then the email won't be sent.

        return self.filtered(
            lambda abandoned_sale_order:
            abandoned_sale_order_custom.partner_id.email
            and not any(transaction.sudo().state == 'error' for transaction in abandoned_sale_order_custom.transaction_ids)
            and any(not float_is_zero(line.price_unit, precision_rounding=line.currency_id.rounding) for line in abandoned_sale_order_custom.order_line)
            and not has_later_sale_order_custom.get(abandoned_sale_order_custom.partner_id, False)
        )

    def _has_deliverable_products(self):
        """ Return whether the order has lines with products that should be delivered.

        :return: Whether the order has deliverable products.
        :rtype: bool
        """
        return bool(self.order_line.product_id) and not self.only_services

    def _remove_delivery_line(self):
        """Remove delivery line(s) safely even when parent chain does not provide delivery logic."""
        self.ensure_one()

        # Try calling parent implementation if it exists AND is not this same method
        parent_method = None
        try:
            parent_method = super()._remove_delivery_line
        except AttributeError:
            parent_method = None

        # Guard against Odoo MRO returning the same method (would recurse)
        if parent_method and getattr(parent_method, "__func__", None) is getattr(self._remove_delivery_line, "__func__", None):
            parent_method = None

        if parent_method:
            return parent_method()

        # Fallback: remove lines marked as delivery
        delivery_lines = self.order_line.filtered(lambda l: bool(getattr(l, "is_delivery", False)))
        if delivery_lines:
            delivery_lines.unlink()
        return True



    def _get_preferred_delivery_method(self, available_delivery_methods):
        """ Get the preferred delivery method based on available delivery methods for the order.

        The preferred delivery method is selected as follows:

        1. The one that is already set if it is compatible.
        2. The default one if compatible.
        3. The first compatible one.

        :param delivery.carrier available_delivery_methods: The available delivery methods for
               the order.
        :return: The preferred delivery method for the order.
        :rtype: delivery.carrier
        """
        self.ensure_one()

        delivery_method = self.carrier_id
        if available_delivery_methods and delivery_method not in available_delivery_methods:
            if self.partner_shipping_id.property_delivery_carrier_id in available_delivery_methods:
                delivery_method = self.partner_shipping_id.property_delivery_carrier_id
            else:
                delivery_method = available_delivery_methods[0]
        return delivery_method

    def _set_delivery_method(self, delivery_method, rate=None, **kwargs):
        self.ensure_one()

        # set carrier
        if "carrier_id" in self._fields:
            self.carrier_id = delivery_method

        # nếu rate chưa truyền vào, tự tính
        if rate is None and delivery_method:
            rate = delivery_method.rate_shipment(self)

        # rate_shipment thường trả dict
        success = True
        price = 0.0
        if isinstance(rate, dict):
            success = rate.get("success", True)
            price = rate.get("price") or 0.0
        else:
            try:
                price = float(rate or 0.0)
            except Exception:
                price = 0.0     

        # lưu delivery_price (phải là số)
        if "delivery_price" in self._fields:
            self.delivery_price = price

        # nếu bạn có logic tạo delivery line thì gọi ở đây
        if delivery_method and success and hasattr(self, "set_delivery_line"):
            self.set_delivery_line(delivery_method, price)

        return success
    def _get_delivery_methods(self):
        # searching on website_published will also search for available website (_search method on computed field)
        return self.env['delivery.carrier'].sudo().search([
            ('website_published', '=', True),
            *self.env['delivery.carrier']._check_company_domain(self.company_id),
        ]).filtered(lambda carrier: carrier._is_available_for_order(self))

    #=== TOOLING ===#

    def _is_anonymous_cart(self):
        """ Return whether the cart was created by the public user and no address was added yet.

        Note: `self.ensure_one()`

        :return: Whether the cart is anonymous.
        :rtype: bool
        """
        self.ensure_one()
        return self.partner_id.id == request.website.user_id.sudo().partner_id.id

    def _get_lang(self):
        res = super()._get_lang()

        if self.website_id and request and request.is_frontend:
            # Use request lang as cart lang if request comes from frontend
            return request.env.lang

        return res

    def _get_shop_warning(self, clear=True):
        self.ensure_one()
        warn = self.shop_warning
        if clear:
            self.shop_warning = ''
        return warn

    def _is_cart_ready(self):
        """ Whether the cart is valid and can be confirmed (and paid for)

        :rtype: bool
        """
        return True

    def _check_cart_is_ready_to_be_paid(self):
        # Luôn cho phép đi qua bước pay (bỏ shipping check)
        self.ensure_one()
        if hasattr(self, "_is_cart_ready") and not self._is_cart_ready():
            # vẫn giữ logic cart-ready cơ bản để tránh cart rỗng/invalid
            return super()._check_cart_is_ready_to_be_paid()
        return True

    def _is_delivery_ready(self):
        return not self._has_deliverable_products() or self.carrier_id

    uc_deposit_percent = fields.Float(
        string="Tỷ lệ cọc (%)",
        default=30.0,
        help="Tỷ lệ cọc mặc định khi khách chọn thanh toán một phần."
    )

    # tiền đã cọc (chỉ phần deposit)
    uc_amount_deposited = fields.Monetary(
        string="Đã cọc",
        currency_field="currency_id",
        default=0.0,
        copy=False,
        readonly=True,
    )

    # tổng tiền khách đã thanh toán (deposit + các lần thanh toán tiếp theo)
    uc_amount_paid = fields.Monetary(
        string="Đã thanh toán",
        currency_field="currency_id",
        default=0.0,
        copy=False,
        readonly=True,
    )

    # tiền cọc dự kiến theo %
    uc_deposit_amount_expected = fields.Monetary(
        string="Cọc dự kiến",
        currency_field="currency_id",
        compute="_compute_uc_deposit_expected",
        store=True,
    )

    # còn phải trả
    uc_amount_due = fields.Monetary(
        string="Còn phải trả",
        currency_field="currency_id",
        compute="_compute_uc_amount_due",
        store=True,
    )

    uc_is_fully_paid = fields.Boolean(
        string="Đã thanh toán đủ",
        compute="_compute_uc_is_fully_paid",
        store=True,
    )

    # để idempotent: tránh cộng tiền 2 lần nếu webhook/return gọi lại
    uc_payment_tx_ids = fields.Many2many(
        comodel_name="payment.transaction",
        relation="sale_custom_order_payment_tx_rel",
        column1="order_id",
        column2="tx_id",
        string="Giao dịch thanh toán",
        copy=False,
        readonly=True,
    )

    # -------------------------
    # Compute helpers
    # -------------------------
    @api.depends("amount_total", "uc_deposit_percent", "currency_id")
    def _compute_uc_deposit_expected(self):
        for o in self:
            pct = max(0.0, min(100.0, float(o.uc_deposit_percent or 0.0)))
            total = float(o.amount_total or 0.0)
            val = total * pct / 100.0
            if o.currency_id:
                val = o.currency_id.round(val)
            o.uc_deposit_amount_expected = val

    @api.depends("amount_total", "uc_amount_paid", "currency_id")
    def _compute_uc_amount_due(self):
        for o in self:
            total = float(o.amount_total or 0.0)
            paid = float(o.uc_amount_paid or 0.0)
            due = total - paid
            if o.currency_id:
                due = o.currency_id.round(due)
            o.uc_amount_due = max(0.0, due)

    @api.depends("uc_amount_due")
    def _compute_uc_is_fully_paid(self):
        for o in self:
            o.uc_is_fully_paid = (float(o.uc_amount_due or 0.0) <= 0.0)

    # -------------------------
    # Business: tính số tiền cần thu theo lựa chọn
    # -------------------------
    def uc_get_amount_to_charge(self, pay_kind="full", deposit_percent=None):
        """
        pay_kind:
          - 'deposit'  : thu phần cọc còn thiếu tới mức dự kiến (hoặc tới mức còn phải trả)
          - 'full'/'remain' : thu số tiền còn phải trả (tự trừ phần đã thanh toán/cọc)
        """
        self.ensure_one()
        cur = self.currency_id
        total = float(self.amount_total or 0.0)
        paid = float(self.uc_amount_paid or 0.0)
        deposited = float(self.uc_amount_deposited or 0.0)
        due = max(0.0, total - paid)

        if pay_kind == "deposit":
            pct = self.uc_deposit_percent if deposit_percent is None else float(deposit_percent or 0.0)
            pct = max(0.0, min(100.0, pct))
            expected = total * pct / 100.0
            to_deposit = max(0.0, expected - deposited)
            amount = min(due, to_deposit)  # không vượt quá còn phải trả
        else:
            # full/remain: thu đúng phần còn lại (tự trừ cọc)
            amount = due

        if cur:
            amount = cur.round(amount)
        return max(0.0, amount)

    def uc_register_payment_tx(self, tx, pay_kind="full"):
        """
        Ghi nhận tiền đã cọc/đã thanh toán theo tx.
        Idempotent theo tx.id (tránh cộng 2 lần).
        """
        self.ensure_one()
        if not tx:
            return False

        if tx.id in self.uc_payment_tx_ids.ids:
            return False

        amount = float(tx.amount or 0.0)
        if self.currency_id:
            amount = self.currency_id.round(amount)

        vals = {
            "uc_payment_tx_ids": [Command.link(tx.id)],
            "uc_amount_paid": (float(self.uc_amount_paid or 0.0) + amount),
        }

        if pay_kind == "deposit":
            vals["uc_amount_deposited"] = (float(self.uc_amount_deposited or 0.0) + amount)

        # clamp không cho vượt total (đỡ bị âm do rounding / gọi lại)
        total = float(self.amount_total or 0.0)
        new_paid = min(total, float(vals["uc_amount_paid"] or 0.0))
        vals["uc_amount_paid"] = self.currency_id.round(new_paid) if self.currency_id else new_paid

        self.sudo().write(vals)
        return True

    @api.model
    def _slp_user_from_partner(self, partner):
        """Trả res.users theo thứ tự ưu tiên:
           oms.customer.slp_code (theo partner.ref) -> partner.user_id -> env.user
           NOTE: dùng được trong create() (self có thể là recordset rỗng)
        """
        User = self.env["res.users"].sudo()

        # partner có thể là None / recordset rỗng
        partner = (partner or self.env["res.partner"]).sudo()
        if partner:
            partner = partner.commercial_partner_id

        # 1) ưu tiên theo partner.ref -> oms.customer.slp_code -> res.users.slp_code
        if partner and partner.ref:
            cust = self.env["oms.customer"].sudo().search(
                [("card_code", "=", partner.ref)], limit=1
            )
            if cust and getattr(cust, "slp_code", False):
                u = User.search(
                    [("slp_code", "=", cust.slp_code), ("active", "=", True)],
                    limit=1,
                )
                if u:
                    return u

        # 2) fallback theo partner.user_id
        if partner and getattr(partner, "user_id", False):
            return partner.user_id.sudo()

        # 3) fallback cuối
        return self.env.user.sudo()
    
