# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    def _uc_get_tier_breakpoints(self, product, partner=None):
        """Lấy danh sách mốc min_quantity có thể ảnh hưởng đến giá."""
        self.ensure_one()
        product = product.with_context(pricelist=self.id)

        items = self.env['product.pricelist.item'].sudo().search([
            ('pricelist_id', '=', self.id),
        ])

        tmpl = product.product_tmpl_id
        categ = product.categ_id
        today = fields.Date.context_today(self)
        breakpoints = {1}

        for item in items:
            if item.date_start and item.date_start > today:
                continue
            if item.date_end and item.date_end < today:
                continue

            applied_on = item.applied_on or ''
            ok = False
            if applied_on == '3_global':
                ok = True
            elif applied_on == '2_product_category' and item.categ_id:
                ok = categ == item.categ_id or bool(
                    categ.parent_path and item.categ_id.parent_path and categ.parent_path.startswith(item.categ_id.parent_path)
                )
            elif applied_on == '1_product' and item.product_tmpl_id:
                ok = item.product_tmpl_id == tmpl
            elif applied_on == '0_product_variant' and item.product_id:
                ok = item.product_id == product

            if ok:
                breakpoints.add(max(int(item.min_quantity or 1), 1))

        return sorted(breakpoints)

    def uc_get_effective_tier_table(self, product, partner=None):
        """Tính bảng giá hiệu lực thật sự theo từng khoảng số lượng."""
        self.ensure_one()
        breakpoints = self._uc_get_tier_breakpoints(product, partner=partner)
        if not breakpoints:
            breakpoints = [1]
        if 1 not in breakpoints:
            breakpoints = [1] + breakpoints

        rows = []
        for idx, qty_from in enumerate(breakpoints):
            qty_to = breakpoints[idx + 1] - 1 if idx + 1 < len(breakpoints) else None
            price = self._get_product_price(product, qty_from, partner=partner)
            rows.append({
                'min_qty': qty_from,
                'max_qty': qty_to,
                'price': price,
            })

        merged = []
        for row in rows:
            if not merged:
                merged.append(row)
                continue
            prev = merged[-1]
            same_price = abs((prev['price'] or 0.0) - (row['price'] or 0.0)) < 0.000001
            contiguous = prev['max_qty'] is not None and prev['max_qty'] + 1 == row['min_qty']
            if same_price and contiguous:
                prev['max_qty'] = row['max_qty']
            else:
                merged.append(row)

        return [row for row in merged if row['max_qty'] is None or row['max_qty'] >= row['min_qty']]
