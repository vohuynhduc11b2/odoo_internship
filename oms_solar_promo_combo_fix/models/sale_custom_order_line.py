from odoo import fields, models


class SaleCustomOrderLine(models.Model):
    _inherit = 'sale_custom.order.line'

    oms_promo_rule_price = fields.Float(string='Giá CTKM áp dụng', digits='Product Price', copy=False)

    def _oms_is_accessory_line(self):
        self.ensure_one()
        if bool('accessory_parent_line_id' in self._fields and self.accessory_parent_line_id):
            return True
        return bool(
            'linked_line_id' in self._fields
            and self.linked_line_id
            and not getattr(self, 'is_gift', False)
            and not getattr(self, 'is_bundle', False)
        )

    def _oms_get_bundle_root_line(self):
        self.ensure_one()
        if 'accessory_parent_line_id' in self._fields and self.accessory_parent_line_id:
            return self.accessory_parent_line_id
        if (
            'linked_line_id' in self._fields
            and self.linked_line_id
            and not getattr(self, 'is_gift', False)
            and not getattr(self, 'is_bundle', False)
        ):
            return self.linked_line_id
        return self

    def _oms_get_bundle_lines(self):
        self.ensure_one()
        root = self._oms_get_bundle_root_line()
        lines = root
        if 'accessory_child_line_ids' in root._fields and root.accessory_child_line_ids:
            lines |= root.accessory_child_line_ids
        if 'linked_line_ids' in root._fields and root.linked_line_ids:
            lines |= root.linked_line_ids.filtered(lambda l: not getattr(l, 'is_gift', False) and not getattr(l, 'is_bundle', False))
        return lines.filtered(lambda l: getattr(l, 'product_id', False) and not getattr(l, 'display_type', False))

    def _oms_get_promo_bundle_purchased_qty(self, promo):
        self.ensure_one()
        order = self._oms_get_order()
        if not order or not promo:
            return float(getattr(self, 'product_uom_qty', 0.0) or 0.0)

        sale_lines = order.order_line.filtered(
            lambda l: getattr(l, 'product_id', False)
            and not getattr(l, 'display_type', False)
            and not getattr(l, 'is_gift', False)
            and not getattr(l, 'is_bundle', False)
        )
        if not sale_lines:
            return 0.0

        def _sum_qty_for_templates(templates):
            templates = templates.exists()
            if not templates:
                return 0.0
            matched = sale_lines.filtered(lambda l, tids=set(templates.ids): getattr(l.product_id, 'product_tmpl_id', False).id in tids)
            return sum(float(getattr(l, 'product_uom_qty', 0.0) or 0.0) for l in matched)

        main_templates = getattr(promo, 'apply_product_line_ids', self.env['oms.promotion.apply.product.line']).mapped('product_tmpl_id').exists()
        main_qty = _sum_qty_for_templates(main_templates)
        if main_qty <= 0:
            main_qty = float(getattr(self._oms_get_bundle_root_line(), 'product_uom_qty', 0.0) or 0.0)

        candidate_qtys = []
        for combo in getattr(promo, 'bundle_combo_ids', self.env['oms.promotion.bundle.combo']):
            combo_qtys = []
            for product_tmpl in combo.product_tmpl_ids:
                combo_qtys.append(_sum_qty_for_templates(product_tmpl))
            combo_qtys = [qty for qty in combo_qtys if qty > 0]
            if combo_qtys:
                candidate_qtys.append(min([main_qty] + combo_qtys))

        if candidate_qtys:
            return max(candidate_qtys)
        return main_qty

    def _oms_apply_group_promo_prices(self, promotions):
        for line in self:
            is_accessory = line._oms_is_accessory_line()
            price = False
            for promo in promotions:
                rule = getattr(promo, '_oms_find_product_price_rule', lambda *a, **k: self.env['oms.promotion.product.price'])(line, is_accessory=is_accessory)
                if not rule and not is_accessory:
                    # Website cart can lose parent-child linkage for accessory products.
                    # Fall back to configured accessory rules when the product belongs to a bundle combo.
                    product_tmpl = getattr(line, 'product_template_id', False) or getattr(getattr(line, 'product_id', False), 'product_tmpl_id', False)
                    if product_tmpl and any(product_tmpl in combo.product_tmpl_ids for combo in getattr(promo, 'bundle_combo_ids', self.env['oms.promotion.bundle.combo'])):
                        rule = getattr(promo, '_oms_find_product_price_rule', lambda *a, **k: self.env['oms.promotion.product.price'])(line, is_accessory=True)
                if rule:
                    price = float(rule.promo_price or 0.0)
                    break
            if not price and not is_accessory:
                for promo in promotions:
                    getter = getattr(promo, '_oms_get_effective_main_price', False)
                    if getter:
                        price = float(getter() or 0.0)
                    if price:
                        break
            vals = {}
            if price > 0:
                if abs(float(getattr(line, 'price_unit', 0.0) or 0.0) - price) > 1e-9:
                    vals['price_unit'] = price
                if 'technical_price_unit' in line._fields and abs(float(getattr(line, 'technical_price_unit', 0.0) or 0.0) - price) > 1e-9:
                    vals['technical_price_unit'] = price
                if abs(float(line.oms_promo_rule_price or 0.0) - price) > 1e-9:
                    vals['oms_promo_rule_price'] = price
                if 'oms_promotion_main_price' in line._fields and not is_accessory and abs(float(getattr(line, 'oms_promotion_main_price', 0.0) or 0.0) - price) > 1e-9:
                    vals['oms_promotion_main_price'] = price
            elif line.oms_promo_rule_price:
                vals['oms_promo_rule_price'] = 0.0
            if vals:
                super(SaleCustomOrderLine, line.with_context(oms_skip_gift_sync=True, oms_skip_main_price_sync=True)).write(vals)

    def _oms_sync_gift_combo_promotions(self):
        if self.env.context.get('oms_skip_gift_sync'):
            return
        Selection = self.env['oms.solar.gift.combo.selection'].sudo()
        processed = self.browse()
        for line in self:
            if not getattr(line, 'product_id', False) or getattr(line, 'display_type', False):
                continue
            root = line._oms_get_bundle_root_line()
            if root in processed:
                continue
            processed |= root
            order = root._oms_get_order()
            if not order:
                continue
            bundle_lines = root._oms_get_bundle_lines()
            promotions = self.env['oms.promotion'].browse()
            for bline in bundle_lines:
                promotions |= bline._oms_get_applied_promotions()
            promotions = promotions.exists()
            current = Selection.search([('order_id', '=', order.id), ('line_id', '=', root.id)])
            keep = Selection.browse()
            for promo in promotions:
                combos = promo._oms_get_gift_combo_records().exists()
                has_combo_logic = bool(combos)
                has_price_logic = bool(promo.oms_product_price_ids) or bool(getattr(promo, '_oms_get_effective_main_price', lambda: False)())
                if not has_combo_logic and not has_price_logic:
                    continue
                purchased_qty = root._oms_get_promo_bundle_purchased_qty(promo)
                allowed_qty = promo._oms_get_effective_gift_qty(purchased_qty) if hasattr(promo, '_oms_get_effective_gift_qty') else purchased_qty
                selection = current.filtered(lambda s, pid=promo.id: s.promotion_id.id == pid)[:1]
                current_combo = selection.combo_id if selection else self.env['product.combo']
                default_combo = self.env['product.combo']
                allow_select = bool(getattr(promo, 'oms_allow_customer_select_gift_combo', False))
                if current_combo and current_combo in combos:
                    default_combo = current_combo
                elif combos and (len(combos) == 1 or not allow_select):
                    default_combo = combos[:1]
                vals = {
                    'order_id': order.id,
                    'line_id': root.id,
                    'promotion_id': promo.id,
                    'purchased_qty': purchased_qty,
                    'allowed_qty': allowed_qty,
                    'selected_qty': min(float(selection.selected_qty if selection else (allowed_qty if default_combo else 0.0)), allowed_qty) if allowed_qty else 0.0,
                    'main_product_price': float(getattr(promo, '_oms_get_effective_main_price', lambda: False)() or 0.0),
                    'note': getattr(promo, 'oms_gift_combo_note', False) or False,
                    'active': True,
                    'combo_id': default_combo.id if default_combo else False,
                }
                if selection:
                    selection.write(vals)
                else:
                    selection = Selection.create(vals)
                keep |= selection
            stale = current - keep
            if stale:
                stale.unlink()
            bundle_lines._oms_apply_group_promo_prices(promotions)
