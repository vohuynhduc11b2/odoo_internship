from odoo import fields, models


class OmsPromotion(models.Model):
    _inherit = 'oms.promotion'

    oms_product_price_ids = fields.One2many(
        'oms.promotion.product.price',
        'promotion_id',
        string='Giá CTKM theo sản phẩm',
        help='PM nhập giá CTKM cho sản phẩm chính và/hoặc bán kèm.',
    )

    def _oms_collect_price_rule_candidates(self):
        self.ensure_one()
        candidates = []
        seen = set()

        def _push(product_tmpl, scope):
            if not product_tmpl:
                return
            key = (int(product_tmpl.id), scope)
            if key in seen:
                return
            seen.add(key)
            candidates.append({
                'product_tmpl_id': int(product_tmpl.id),
                'apply_scope': scope,
            })

        for line in getattr(self, 'apply_product_line_ids', self.env['oms.promotion.apply.product.line']):
            _push(line.product_tmpl_id, 'main')

        for combo in getattr(self, 'bundle_combo_ids', self.env['oms.promotion.bundle.combo']):
            for product_tmpl in combo.product_tmpl_ids:
                _push(product_tmpl, 'accessory')

        return candidates

    def action_sync_product_price_lines(self):
        Price = self.env['oms.promotion.product.price']
        for promo in self:
            existing = {
                (int(line.product_tmpl_id.id), line.apply_scope)
                for line in promo.oms_product_price_ids
                if line.product_tmpl_id
            }
            to_create = []
            for candidate in promo._oms_collect_price_rule_candidates():
                key = (candidate['product_tmpl_id'], candidate['apply_scope'])
                if key in existing:
                    continue
                to_create.append({
                    'promotion_id': promo.id,
                    'product_tmpl_id': candidate['product_tmpl_id'],
                    'apply_scope': candidate['apply_scope'],
                    'promo_price': 0.0,
                })
            if to_create:
                Price.create(to_create)
        return True

    def _oms_get_gift_combo_records(self):
        self.ensure_one()
        combos = super()._oms_get_gift_combo_records()
        if not getattr(self, 'oms_allow_customer_select_gift_combo', False):
            return combos[:1]
        if 'gift_combo_ids' not in self._fields:
            return combos
        Combo = self.env['product.combo'].sudo()
        raw_ids = [int(c.id) for c in self.gift_combo_ids if c and c.id]
        if not raw_ids:
            return Combo.browse()
        return Combo.search([('id', 'in', raw_ids)], order='id')

    def _oms_find_product_price_rule(self, line, is_accessory=False):
        self.ensure_one()
        product_tmpl = getattr(line, 'product_template_id', False) or getattr(getattr(line, 'product_id', False), 'product_tmpl_id', False)
        if not product_tmpl:
            return self.env['oms.promotion.product.price']
        rules = self.oms_product_price_ids.filtered(lambda r: r.active and r.product_tmpl_id == product_tmpl)
        if is_accessory:
            exact = rules.filtered(lambda r: r.apply_scope == 'accessory')[:1]
        else:
            exact = rules.filtered(lambda r: r.apply_scope == 'main')[:1]
        return exact or rules.filtered(lambda r: r.apply_scope == 'all')[:1]
