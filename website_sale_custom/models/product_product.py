# Part of Odoo. See LICENSE file for full copyright and licensing details.

import unicodedata

from werkzeug.urls import url_join

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


OMS_DEFAULT_PRICELIST_NAME = "[OMS] DEFAULT"
OMS_SPECIAL_PRICELIST_NAME = "[OMS] DAC BIET"
OMS_SPECIAL_PRICE_MARKERS = ("dac biet", "chien luoc", "chien luot")


def _oms_norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("đ", "d").replace("Đ", "D")
    return " ".join(value.lower().split())


def _is_oms_special_price_name(value):
    normalized = _oms_norm(value)
    return any(marker in normalized for marker in OMS_SPECIAL_PRICE_MARKERS)


def _oms_bg_priority(value):
    value = str(value or "").upper().strip()
    if value.startswith("BG"):
        try:
            return int(value[2:])
        except Exception:
            return 9999
    return 9999


class Product(models.Model):
    _inherit = 'product.product'
    _mail_post_access = 'read'

    variant_ribbon_id = fields.Many2one(string="Variant Ribbon", comodel_name='product.ribbon')
    website_id = fields.Many2one(related='product_tmpl_id.website_id', readonly=False)

    product_variant_image_ids = fields.One2many(
        string="Extra Variant Images",
        comodel_name='product.image',
        inverse_name='product_variant_id',
    )

    base_unit_count = fields.Float(
        string="Base Unit Count",
        help="Display base unit price on your eCommerce pages. Set to 0 to hide it for this"
             " product.",
        required=True,
        default=1,
    )
    base_unit_id = fields.Many2one(
        string="Custom Unit of Measure",
        help="Define a custom unit to display in the price per unit of measure field.",
        comodel_name='website.base.unit',
    )
    base_unit_price = fields.Monetary(
        string="Price Per Unit",
        compute='_compute_base_unit_price',
    )
    base_unit_name = fields.Char(
        help="Displays the custom unit for the products if defined or the selected unit of measure"
            " otherwise.",
        compute='_compute_base_unit_name',
    )

    website_url = fields.Char(
        string="Website URL",
        help="The full URL to access the document through the website.",
        compute='_compute_product_website_url',
    )

    #=== COMPUTE METHODS ===#

    def _get_base_unit_price(self, price):
        self.ensure_one()
        return self.base_unit_count and price / self.base_unit_count

    @api.depends('lst_price', 'base_unit_count')
    def _compute_base_unit_price(self):
        for product in self:
            if not product.id:
                product.base_unit_price = 0
            else:
                product.base_unit_price = product._get_base_unit_price(product.lst_price)

    @api.depends('uom_name', 'base_unit_id')
    def _compute_base_unit_name(self):
        for product in self:
            product.base_unit_name = product.base_unit_id.name or product.uom_name

    @api.depends_context('lang')
    @api.depends('product_tmpl_id.website_url', 'product_template_attribute_value_ids')
    def _compute_product_website_url(self):
        for product in self:
            url = product.product_tmpl_id.website_url
            if pavs := product.product_template_attribute_value_ids.product_attribute_value_id:
                pav_ids = [str(pav.id) for pav in pavs]
                url = f'{url}#attribute_values={",".join(pav_ids)}'
            product.website_url = url

    #=== CONSTRAINT METHODS ===#

    @api.constrains('base_unit_count')
    def _check_base_unit_count(self):
        if any(product.base_unit_count < 0 for product in self):
            raise ValidationError(_(
                "The value of Base Unit Count must be greater than 0."
                " Use 0 to hide the price per unit on this product."
            ))

    #=== BUSINESS METHODS ===#

    def _prepare_variant_values(self, combination):
        variant_dict = super()._prepare_variant_values(combination)
        variant_dict['base_unit_count'] = self.base_unit_count
        return variant_dict

    def website_publish_button(self):
        self.ensure_one()
        return self.product_tmpl_id.website_publish_button()

    def open_website_url(self):
        self.ensure_one()
        res = self.product_tmpl_id.open_website_url()
        res['url'] = self.website_url
        return res

    def _get_images(self):
        """Return a list of records implementing `image.mixin` to
        display on the carousel on the website for this variant.

        This returns a list and not a recordset because the records might be
        from different models (template, variant and image).

        It contains in this order: the main image of the variant (which will fall back on the main
        image of the template, if unset), the Variant Extra Images, and the Template Extra Images.
        """
        self.ensure_one()
        variant_images = list(self.product_variant_image_ids)
        template_images = list(self.product_tmpl_id.product_template_image_ids)
        return [self] + variant_images + template_images

    def _get_combination_info_variant(self, **kwargs):
        """Return the variant info based on its combination.
        See `_get_combination_info` for more information.
        """
        self.ensure_one()
        return self.product_tmpl_id._get_combination_info(
            combination=self.product_template_attribute_value_ids,
            product_id=self.id,
            **kwargs)

    def _website_show_quick_add(self):
        self.ensure_one()
        # TODO VFE pass website as param and avoid existence check
        website = self.env['website'].get_current_website()
        if not self.sale_ok:
            return False
        combination_info = self.product_tmpl_id._get_combination_info(
            product_id=self.id,
            combination=self.product_template_attribute_value_ids,
        )
        if combination_info.get('uc_wait_sales_price'):
            return True
        return not website.prevent_zero_price_sale or self._get_contextual_price()

    def _is_add_to_cart_allowed(self):
        self.ensure_one()
        is_product_salable = self.active and self.sale_ok and self.website_published
        website = self.env['website'].get_current_website()
        return (is_product_salable and website.has_ecommerce_access()) \
               or self.env.user.has_group('base.group_system')

    @api.onchange('public_categ_ids')
    def _onchange_public_categ_ids(self):
        if self.public_categ_ids:
            self.website_published = True
        else:
            self.website_published = False

    def write(self, vals):
        if 'active' in vals and not vals['active']:
            # unlink draft lines containing the archived product
            self.env['sale_custom.order.line'].sudo().search([
                ('state', '=', 'draft'),
                ('product_id', 'in', self.ids),
                ('order_id', 'any', [('website_id', '!=', False)]),
            ]).unlink()
        return super().write(vals)

    def _get_oms_active_pricelist(self, at_date=None):
        """
        Backward-compatible helper: website pricing now follows the active
        Odoo website pricelist, not the raw OMS import header.
        """
        website = self.env["website"].get_current_website()
        if website:
            try:
                return website.get_current_pricelist()
            except Exception:
                return website.pricelist_id
        return self.env["product.pricelist"].browse()

    def _get_oms_special_pricelist_for_product(self, at_date=None):
        self.ensure_one()
        at_date = at_date or fields.Date.context_today(self)

        ProductPricelist = self.env["product.pricelist"].sudo()
        special_pl = ProductPricelist.search([
            ("name", "=", OMS_SPECIAL_PRICELIST_NAME),
            ("active", "=", True),
        ], limit=1)
        if not special_pl:
            return ProductPricelist.browse()

        items = self._get_oms_candidate_pricelist_items(special_pl, at_date=at_date)
        if items.filtered(lambda item: self._is_oms_special_pricelist_item(item)):
            return special_pl

        return ProductPricelist.browse()

    def _resolve_oms_pricelist_for_product(self, pricelist=None, at_date=None):
        self.ensure_one()
        pl = pricelist or self._get_oms_active_pricelist(at_date=at_date)
        return pl

    def _get_oms_default_pricelist(self):
        return self.env["product.pricelist"].sudo().search([
            ("name", "=", OMS_DEFAULT_PRICELIST_NAME),
            ("active", "=", True),
        ], limit=1)

    def _get_oms_frame_by_price_type(self, category_id, price_type):
        if not category_id or not price_type:
            return self.env["oms.pricelist.frame"].browse()

        Frame = self.env["oms.pricelist.frame"].sudo()
        frames = Frame.search([
            ("active", "=", True),
            ("category_id", "=", int(category_id)),
        ])
        ordered_frames = sorted(
            frames,
            key=lambda frame: (
                frame.min_qty or 0,
                frame.max_qty or 0,
                frame.price_list_name or "",
                frame.id,
            ),
        )
        for idx, frame in enumerate(ordered_frames, start=1):
            if f"BG{idx:02d}" == price_type:
                return frame
        return Frame.browse()

    def _get_oms_frame_by_name(self, category_id, frame_name):
        if not category_id or not frame_name:
            return self.env["oms.pricelist.frame"].browse()

        normalized_name = _oms_norm(frame_name)
        frames = self.env["oms.pricelist.frame"].sudo().search([
            ("active", "=", True),
            ("category_id", "=", int(category_id)),
        ])
        return frames.filtered(
            lambda frame: _oms_norm(frame.price_list_name) == normalized_name
        )[:1]

    def _get_current_oms_frame_for_line(self, line):
        if getattr(line, "price_frame_name", False):
            frame = self._get_oms_frame_by_name(
                line.pricelist_id.category_id,
                line.price_frame_name,
            )
            if frame:
                return frame
        frame = self._get_oms_frame_by_price_type(
            line.pricelist_id.category_id,
            line.price_type,
        )
        return frame or line.price_frame_id

    def _is_oms_special_pricelist(self, pricelist):
        return bool(pricelist and pricelist.name == OMS_SPECIAL_PRICELIST_NAME)

    def _is_oms_default_pricelist(self, pricelist):
        return bool(pricelist and pricelist.name == OMS_DEFAULT_PRICELIST_NAME)

    def _is_oms_target_pricelist(self, pricelist):
        if not pricelist:
            return False
        if (pricelist.name or "").strip().upper().startswith("[OMS]"):
            return True
        if pricelist.name in (OMS_DEFAULT_PRICELIST_NAME, OMS_SPECIAL_PRICELIST_NAME):
            return True

        Frame = self.env["oms.pricelist.frame"].sudo()
        if "publish_pricelist_ids" not in Frame._fields:
            return False

        return bool(Frame.search([
            ("active", "=", True),
            ("publish_pricelist_ids", "in", pricelist.ids),
        ], limit=1))

    def _is_oms_managed_pricelist(self, pricelist):
        if not pricelist:
            return False
        if self._is_oms_target_pricelist(pricelist):
            return True

        return bool(
            self._get_oms_mapped_price_lines(pricelist)
            or self._get_oms_candidate_pricelist_items(pricelist)
        )

    def _is_oms_special_pricelist_item(self, item):
        frame_name = getattr(item, "oms_price_frame_name", False) or item.name or ""
        if _is_oms_special_price_name(frame_name):
            return True

        frame = getattr(item, "oms_price_frame_id", False)
        if frame and _is_oms_special_price_name(frame.price_list_name):
            return True

        return False

    def _get_oms_candidate_pricelist_items(self, pricelist, at_date=None):
        self.ensure_one()
        if not pricelist:
            return self.env["product.pricelist.item"].browse()

        at_date = at_date or fields.Date.context_today(self)
        Item = self.env["product.pricelist.item"].sudo()
        base_domain = [
            ("pricelist_id", "=", pricelist.id),
            ("compute_price", "=", "fixed"),
            "|", ("date_start", "=", False), ("date_start", "<=", at_date),
            "|", ("date_end", "=", False), ("date_end", ">=", at_date),
            ("fixed_price", ">", 0),
        ]
        if "oms_price_frame_name" in Item._fields:
            base_domain += [
                "|", ("oms_price_frame_id", "!=", False),
                     ("oms_price_frame_name", "!=", False),
            ]

        order_by = "min_quantity asc, oms_max_quantity asc, id desc"
        variant_items = Item.search(
            base_domain + [
                ("applied_on", "=", "0_product_variant"),
                ("product_id", "=", self.id),
            ],
            order=order_by,
        )

        if variant_items:
            return variant_items

        return Item.search(
            base_domain + [
                ("applied_on", "=", "1_product"),
                ("product_tmpl_id", "=", self.product_tmpl_id.id),
            ],
            order=order_by,
        )

    def _get_oms_normal_pricelist_items(self, items):
        return items.filtered(lambda item: not self._is_oms_special_pricelist_item(item))

    def _get_oms_first_special_pricelist_item(self, items):
        special_items = items.filtered(lambda item: self._is_oms_special_pricelist_item(item))
        if not special_items:
            return special_items

        def _sort_key(item):
            min_qty = int(item.min_quantity or 1)
            max_qty = int(getattr(item, "oms_max_quantity", 0) or 0)
            return (
                min_qty > 1,
                min_qty,
                max_qty or 10**18,
                -item.id,
            )

        return special_items.sorted(_sort_key)[:1]

    def _is_oms_special_price_line(self, line):
        frame = self._get_current_oms_frame_for_line(line)
        if frame and _is_oms_special_price_name(frame.price_list_name):
            return True
        return _is_oms_special_price_name(line.price_type or "")

    def _get_oms_mapped_price_lines(self, pricelist, at_date=None):
        self.ensure_one()
        if not pricelist:
            return self.env["oms.price.list.line"].browse()

        at_date = at_date or fields.Date.context_today(self)
        Line = self.env["oms.price.list.line"].sudo()
        lines = Line.search([
            ("item_id", "=", self.id),
            ("price", ">", 0),
            ("from_date", "<=", at_date),
            ("to_date", ">=", at_date),
            ("pricelist_id.active", "=", True),
        ])
        return lines.filtered(
            lambda line: pricelist in self._get_current_oms_frame_for_line(line).publish_pricelist_ids
        )

    def _has_oms_price_frame_mapping(self, at_date=None):
        self.ensure_one()
        at_date = at_date or fields.Date.context_today(self)
        Line = self.env["oms.price.list.line"].sudo()
        lines = Line.search([
            ("item_id", "=", self.id),
            ("price", ">", 0),
            ("from_date", "<=", at_date),
            ("to_date", ">=", at_date),
            ("pricelist_id.active", "=", True),
        ])
        return bool(lines.filtered(
            lambda line: self._get_current_oms_frame_for_line(line).publish_pricelist_ids
        ))

    def _get_oms_first_special_price_line(self, lines):
        special_lines = lines.filtered(lambda line: self._is_oms_special_price_line(line))
        if not special_lines:
            return special_lines

        def _sort_key(line):
            min_qty = int(line.min_qty or 1)
            max_qty = int(line.max_qty or 0)
            return (
                min_qty > 1,
                min_qty,
                max_qty or 10**18,
                _oms_bg_priority(line.price_type),
                line.price,
                -line.id,
            )

        return special_lines.sorted(_sort_key)[:1]

    def _get_oms_normal_price_lines(self, lines):
        return lines.filtered(lambda line: not self._is_oms_special_price_line(line))

    def _oms_price_lines_to_tiers(self, lines, price_map=None):
        INF = 10**18
        best_by_range = {}
        price_map = price_map or {}
        for line in lines:
            minq = int(line.min_qty or 1)
            if minq < 1:
                minq = 1

            maxq_raw = int(line.max_qty or 0)
            max_norm = maxq_raw if maxq_raw > 0 and maxq_raw < 999999 else INF
            key = (minq, max_norm)
            frame = self._get_current_oms_frame_for_line(line)
            frame_name = frame.price_list_name if frame else (line.price_type or "")
            priority = _oms_bg_priority(line.price_type)

            cur = best_by_range.get(key)
            if (
                not cur
                or priority < cur["priority"]
                or (priority == cur["priority"] and line.id > cur["id"])
            ):
                best_by_range[key] = {
                    "id": line.id,
                    "priority": priority,
                    "min_qty": minq,
                    "max_norm": max_norm,
                    "price": price_map.get(line.item_id.id, float(line.price or 0.0)),
                    "name": frame_name,
                }

        res = []
        for tier in sorted(best_by_range.values(), key=lambda x: (x["min_qty"], x["max_norm"], x["priority"], x["id"])):
            if tier["max_norm"] < tier["min_qty"]:
                continue
            res.append({
                "min_qty": tier["min_qty"],
                "max_qty": 0 if tier["max_norm"] >= INF else int(tier["max_norm"]),
                "price": tier["price"],
                "name": tier["name"],
                "is_contact": float(tier["price"] or 0.0) <= 1.0,
                "is_special": _is_oms_special_price_name(tier["name"]),
            })
        return res

    def _sync_oms_line_prices_from_items(self, mapped_lines):
        """Build item_id -> fixed_price lookup for mapped_lines.

        Reads the authoritative price from product.pricelist.item (the source
        of truth). Returns a dict {item_id: fixed_price}. Caller passes this
        to _oms_price_lines_to_tiers so tier prices reflect any manual admin
        edits without writing back to oms.price.list.line (which would cause
        RecursionError during website template rendering).
        """
        self.ensure_one()
        if not mapped_lines:
            return {}
        item_ids = [line.item_id.id for line in mapped_lines if line.item_id]
        if not item_ids:
            return {}
        Item = self.env["product.pricelist.item"].sudo()
        return {
            it.id: float(it.fixed_price or 0.0)
            for it in Item.browse(item_ids).exists()
            if float(it.fixed_price or 0.0) > 0
        }

    def _get_oms_tier_price_lines(self, at_date=None, pricelist=None):
        """
        Return tiers from the same product.pricelist.item rules used by cart.
        This keeps PDP display and add-to-cart pricing in sync.
        """
        self.ensure_one()
        at_date = at_date or fields.Date.context_today(self)
    
        pl = self._resolve_oms_pricelist_for_product(pricelist=pricelist, at_date=at_date)
        if not pl:
            return []

        mapped_lines = self._get_oms_mapped_price_lines(pl, at_date=at_date)
        if mapped_lines:
            price_map = self._sync_oms_line_prices_from_items(mapped_lines)
            return self._oms_price_lines_to_tiers(mapped_lines, price_map=price_map)

        if self._is_oms_special_pricelist(pl):
            default_pl = self._get_oms_default_pricelist()
            if default_pl:
                default_mapped_lines = self._get_oms_mapped_price_lines(
                    default_pl,
                    at_date=at_date,
                )
                if default_mapped_lines:
                    price_map = self._sync_oms_line_prices_from_items(default_mapped_lines)
                    return self._oms_price_lines_to_tiers(default_mapped_lines, price_map=price_map)

        items = self._get_oms_candidate_pricelist_items(pl, at_date=at_date)
        force_single_special = False

        if self._is_oms_default_pricelist(pl):
            items = self._get_oms_normal_pricelist_items(items)
        else:
            special_item = self._get_oms_first_special_pricelist_item(items)
            if special_item:
                items = special_item
                force_single_special = True
            else:
                items = self._get_oms_normal_pricelist_items(items)

        if not items and not self._is_oms_default_pricelist(pl):
            default_pl = self._get_oms_default_pricelist()
            if default_pl:
                items = self._get_oms_normal_pricelist_items(
                    self._get_oms_candidate_pricelist_items(default_pl, at_date=at_date)
                )
    
        if not items:
            return []
    
        INF = 10**18
    
        best_by_range = {}
        for item in items:
            minq = int(item.min_quantity or 1)
            if minq < 1:
                minq = 1
            if force_single_special:
                minq = 1
    
            maxq_raw = int(getattr(item, "oms_max_quantity", 0) or 0)
            max_norm = INF if force_single_special else (maxq_raw if maxq_raw > 0 else INF)
            key = (minq, max_norm)
            scope_priority = 0 if item.applied_on == "0_product_variant" else 1
    
            cur = best_by_range.get(key)
            if (
                (not cur)
                or scope_priority < cur["scope_priority"]
                or (scope_priority == cur["scope_priority"] and item.id > cur["id"])
            ):
                best_by_range[key] = {
                    "id": item.id,
                    "scope_priority": scope_priority,
                    "min_qty": minq,
                    "max_norm": max_norm,
                    "price": float(item.fixed_price or 0.0),
                    "name": (
                        getattr(item, "oms_price_frame_name", False)
                        or item.name
                        or ""
                    ),
                }
    
        tiers = sorted(best_by_range.values(), key=lambda x: (x["min_qty"], x["max_norm"], x["id"]))
    
        res = []
        for t in tiers:
            if t["max_norm"] < t["min_qty"]:
                continue
            res.append({
                "min_qty": t["min_qty"],
                "max_qty": 0 if t["max_norm"] >= INF else int(t["max_norm"]),
                "price": t["price"],
                "name": t["name"],
                "is_contact": float(t["price"] or 0.0) <= 1.0,
                "is_special": _is_oms_special_price_name(t["name"]),
            })
    
        return res

    def uc_get_tier_price_unit(self, order=None, qty=1.0, pricelist=None, partner=None):
        """Return the website tier price using the same rules shown on PDP."""
        self.ensure_one()
        qty = float(qty or 1.0)
        tiers = self._get_oms_tier_price_lines(pricelist=pricelist)
        matched = [
            tier for tier in tiers
            if qty >= float(tier.get("min_qty") or 1)
            and (
                not tier.get("max_qty")
                or qty <= float(tier.get("max_qty") or 0)
            )
        ]
        if not matched:
            return None

        matched.sort(
            key=lambda tier: (
                -float(tier.get("min_qty") or 1),
                float(tier.get("max_qty") or 10**18) or 10**18,
            )
        )
        price = float(matched[0].get("price") or 0.0)
        return 0.0 if price <= 1.0 else price
