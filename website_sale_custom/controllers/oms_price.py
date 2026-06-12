# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request

class OmsPriceController(http.Controller):

    @http.route("/oms/price_tiers", type="json", auth="public", website=True, csrf=False)
    def oms_price_tiers(self, product_id=None, product_template_id=None, **kw):
        if not product_id and not product_template_id:
            return []

        product_id = int(product_id or 0)
        template_id = int(product_template_id or kw.get("template_id") or 0)

        Product = request.env["product.product"].sudo()
        Template = request.env["product.template"].sudo()
        product = Product.browse(product_id).exists() if product_id else Product.browse()
        template = Template.browse(template_id).exists() if template_id else Template.browse()

        if template and product and product.product_tmpl_id != template:
            product = Product.browse()
        if not template:
            template = product.product_tmpl_id if product else Template.browse(product_id).exists()

        try:
            order = request.website.sale_get_order(force_create=False, update_pricelist=False)
        except TypeError:
            order = request.website.sale_get_order(force_create=False)

        pricelist = order.pricelist_id if order and order.pricelist_id else request.website.get_current_pricelist()
        if product:
            tiers = product._get_oms_tier_price_lines(pricelist=pricelist)
            if tiers:
                return tiers
            return []

        if template:
            for variant in template.product_variant_ids.sorted("id"):
                tiers = variant._get_oms_tier_price_lines(pricelist=pricelist)
                if tiers:
                    return tiers

        return []
