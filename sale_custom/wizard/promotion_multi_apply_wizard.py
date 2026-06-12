# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models
from odoo.exceptions import UserError   # <-- THÊM

_logger = logging.getLogger(__name__)

class PromotionMultiApplyWizard(models.TransientModel):
    _name = 'promotion.multi.apply.wizard'
    _description = 'Áp dụng nhiều khuyến mãi'

    line_ids = fields.One2many(
        'promotion.multi.apply.line.wizard',
        'wizard_id',
        string="Chi tiết khuyến mãi",
        copy=False,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        promo_ids = self.env.context.get('allowed_promotion_ids') or []
        promos = self.env['oms.promotion'].browse(promo_ids).exists()
        lines = []
        for promo in promos:
            lines.append((0, 0, {
                'promotion_id': promo.id,
                'checked': True,
            }))
            _logger.debug("INIT PROMO %s", promo.display_name)
        res['line_ids'] = lines
        return res

    def action_apply(self):
        # === BẮT BUỘC CHỌN COMBO NẾU PROMO CÓ COMBO ===
        sel = self.line_ids.filtered('checked')
        missing_combo = sel.filtered(lambda l: l.promotion_has_combo and not l.gift_combo_id)
        if missing_combo:
            names = ", ".join(missing_combo.mapped(lambda r: r.promotion_id.display_name or r.promotion_id.code or str(r.promotion_id.id)))
            raise UserError(
                "Các khuyến mãi sau có combo quà tặng, vui lòng chọn combo trước khi áp dụng:\n- %s" % names
            )

        active_id    = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        if not active_id or not active_model:
            return {'type': 'ir.actions.act_window_close'}

        today  = fields.Date.today()
        gifts  = []
        cache  = {}
        Promo  = self.env['oms.promotion']
        HAS_OLD_GIFT = 'gift_product_tmpl_id' in Promo._fields

        def get_variant(tmpl_id):
            if tmpl_id not in cache:
                cache[tmpl_id] = self.env['product.product'].search(
                    [('product_tmpl_id', '=', tmpl_id)], limit=1
                )
            return cache[tmpl_id]

        if active_model == 'sale_custom.order':
            order = self.env['sale_custom.order'].browse(active_id)
            mains = order.order_line.filtered(lambda l: l.product_id and not l.is_gift and not l.is_bundle)
            old   = order.order_line.filtered(lambda l: (l.is_gift or l.is_bundle) and l.linked_line_id in mains)
            if old:
                old.unlink()

            for line in mains:
                tmpl_id = line.product_id.product_tmpl_id.id
                categ_id = line.product_id.categ_id.id
                rel = sel.mapped('promotion_id').filtered(lambda p: (
                    p.valid_from <= today <= p.valid_to and (
                        p.apply_product_line_ids.filtered(lambda apl: apl.product_tmpl_id.id == tmpl_id) or
                        p.apply_product_line_ids.filtered(lambda apl: apl.product_category_id.id == categ_id) or
                        p.gift_combo_ids.filtered(lambda c: tmpl_id in c.product_tmpl_ids.ids) or
                        # Khi apply_scope = main_and_bundle: sp bán kèm cũng được giảm giá
                        (p.apply_scope == 'main_and_bundle' and
                         p.bundle_combo_ids.filtered(lambda c: tmpl_id in c.product_tmpl_ids.ids))
                    )
                ))

                total_percent = 0.0
                total_fixed   = 0.0

                for promo in rel:
                    wiz   = sel.filtered(lambda w: w.promotion_id.id == promo.id)[:1]
                    combo = wiz.gift_combo_id

                    if promo.discount_type == 'percent':
                        total_percent += (promo.discount_percent or 0.0)
                    elif promo.discount_type == 'fixed':
                        total_fixed   += (promo.discount_value   or 0.0)

                    if combo:
                        for prod in combo.product_tmpl_ids:
                            v = get_variant(prod.id)
                            if v:
                                gifts.append({
                                    'order_id':        order.id,
                                    'product_id':      v.id,
                                    'product_uom_qty': 1,
                                    'price_unit':      0.0,
                                    'is_gift':         True,
                                    'linked_line_id':  line.id,
                                    'name':            f'Quà [{combo.name}] từ KM [{promo.code}]',
                                })
                    elif HAS_OLD_GIFT and promo.gift_product_tmpl_id:
                        v = get_variant(promo.gift_product_tmpl_id.id)
                        if v:
                            gifts.append({
                                'order_id':        order.id,
                                'product_id':      v.id,
                                'product_uom_qty': 1,
                                'price_unit':      0.0,
                                'is_gift':         True,
                                'linked_line_id':  line.id,
                                'name':            f'Quà từ KM [{promo.code}]',
                            })

                line.discount       = min(total_percent, 100.0)
                line.fixed_discount = total_fixed
                if 'promotion_ids' in line._fields:
                    line.promotion_ids = [(6, 0, rel.ids)]
                line._onchange_set_oms_price_and_promotion()

            order.applied_promotion_ids = [(6, 0, sel.mapped('promotion_id').ids)]
            order.promotion_is_applied  = True
            if hasattr(order, 'update_applied_promotions'):
                order.update_applied_promotions()

        elif active_model == 'sale_custom.order.line':
            line = self.env['sale_custom.order.line'].browse(active_id)
            old  = line.order_id.order_line.filtered(lambda l: l.linked_line_id == line and (l.is_gift or l.is_bundle))
            if old:
                old.unlink()

            total_percent = 0.0
            total_fixed   = 0.0
            applied_promo_ids = []

            for wiz in sel:
                promo = wiz.promotion_id
                combo = wiz.gift_combo_id

                applied_promo_ids.append(promo.id)

                if promo.discount_type == 'percent':
                    total_percent += (promo.discount_percent or 0.0)
                elif promo.discount_type == 'fixed':
                    total_fixed   += (promo.discount_value   or 0.0)

                if combo:
                    for prod in combo.product_tmpl_ids:
                        v = get_variant(prod.id)
                        if v:
                            gifts.append({
                                'order_id':        line.order_id.id,
                                'product_id':      v.id,
                                'product_uom_qty': 1,
                                'price_unit':      0.0,
                                'is_gift':         True,
                                'linked_line_id':  line.id,
                                'name':            f'Quà [{combo.name}] từ KM [{promo.code}]',
                            })
                elif HAS_OLD_GIFT and promo.gift_product_tmpl_id:
                    v = get_variant(promo.gift_product_tmpl_id.id)
                    if v:
                        gifts.append({
                            'order_id':        line.order_id.id,
                            'product_id':      v.id,
                            'product_uom_qty': 1,
                            'price_unit':      0.0,
                            'is_gift':         True,
                            'linked_line_id':  line.id,
                            'name':            f'Quà từ KM [{promo.code}]',
                        })

            line.discount       = min(total_percent, 100.0)
            line.fixed_discount = total_fixed
            if 'promotion_ids' in line._fields:
                line.promotion_ids = [(6, 0, applied_promo_ids)]
            line._onchange_set_oms_price_and_promotion()

        if gifts:
            self.env['sale_custom.order.line'].create(gifts)

        return {'type': 'ir.actions.act_window_close'}


class PromotionMultiApplyLineWizard(models.TransientModel):
    _name = 'promotion.multi.apply.line.wizard'
    _description = 'Dòng chọn khuyến mãi và combo'

    wizard_id    = fields.Many2one('promotion.multi.apply.wizard', ondelete='cascade', required=True)
    checked      = fields.Boolean(string="Áp dụng?", default=True)
    promotion_id = fields.Many2one('oms.promotion', string="Khuyến mãi", required=True, ondelete='cascade')
    gift_combo_id= fields.Many2one(
        'oms.promotion.gift.combo',
        string="Chọn combo",
        domain="[('promotion_id','=',promotion_id)]",
    )

    # <-- THÊM: cờ xác định promo có combo
    promotion_has_combo = fields.Boolean(compute='_compute_promo_has_combo', store=False)

    @api.depends('promotion_id')
    def _compute_promo_has_combo(self):
        for r in self:
            r.promotion_has_combo = bool(getattr(r.promotion_id, 'gift_combo_ids', False))

    @api.onchange('checked')
    def _onchange_checked_clear_combo(self):
        if not self.checked:
            self.gift_combo_id = False
