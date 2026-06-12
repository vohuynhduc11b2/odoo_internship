from odoo import api, fields, models


class SaleCustomOrderLine(models.Model):
    _inherit = "sale_custom.order.line"

    oms_promotion_main_price = fields.Float(string="Giá CTKM áp cho SP chính", digits="Product Price", copy=False)
    gift_combo_id = fields.Many2one("oms.promotion.gift.combo", string="Combo Quà tặng", index=True)

    def _oms_get_order(self):
        self.ensure_one()
        for fname in ("order_id", "sale_order_id"):
            if fname in self._fields and self[fname]:
                return self[fname]
        return self.env["sale_custom.order"]

    def _oms_get_applied_promotions(self):
        self.ensure_one()
        Promotion = self.env["oms.promotion"]
        promotions = Promotion.browse()
        for fname in ("promotion_ids", "oms_promotion_ids", "auto_promotion_ids"):
            if fname in self._fields and self[fname]:
                promotions |= self[fname]
        if "promotion_id" in self._fields and self.promotion_id:
            promotions |= self.promotion_id
        return promotions.exists()

    @api.model
    def _oms_registry_has_model(self, model_name):
        try:
            return bool(model_name and self.env.registry.get(model_name))
        except Exception:
            return False

    @api.model
    def _oms_is_unknown_record(self, value):
        try:
            return getattr(value, '_name', None) == '_unknown'
        except Exception:
            return False

    @api.model
    def _oms_sanitize_relational_val(self, field, value):
        if value in (False, None, '', []):
            return value

        comodel_name = getattr(field, 'comodel_name', False)
        if comodel_name and not self._oms_registry_has_model(comodel_name):
            return False if field.type == 'many2one' else []

        if field.type == 'many2one':
            if self._oms_is_unknown_record(value):
                return False
            if hasattr(value, 'id'):
                try:
                    return value.id or False
                except Exception:
                    return False
            return value

        if field.type not in ('many2many', 'one2many'):
            return value

        if self._oms_is_unknown_record(value):
            return []

        if hasattr(value, 'ids'):
            try:
                ids = [int(x) for x in value.ids if x]
            except Exception:
                ids = []
            return [(6, 0, ids)] if field.type == 'many2many' else []

        if not isinstance(value, (list, tuple)):
            return value

        commands = []
        for item in value:
            if not isinstance(item, (list, tuple)):
                continue
            cmd = list(item)
            if len(cmd) >= 2 and self._oms_is_unknown_record(cmd[1]):
                cmd[1] = False
            if len(cmd) >= 3:
                payload = cmd[2]
                if self._oms_is_unknown_record(payload):
                    continue
                if hasattr(payload, 'ids'):
                    try:
                        payload_ids = [int(x) for x in payload.ids if x]
                    except Exception:
                        payload_ids = []
                    cmd[2] = payload_ids if field.type == 'many2many' else []
                elif isinstance(payload, dict) and comodel_name and self._oms_registry_has_model(comodel_name):
                    # keep nested create vals as-is; only ensure bare recordsets are not leaked
                    sanitized_payload = {}
                    for sub_key, sub_val in payload.items():
                        if self._oms_is_unknown_record(sub_val):
                            continue
                        if hasattr(sub_val, 'id'):
                            try:
                                sanitized_payload[sub_key] = sub_val.id or False
                            except Exception:
                                sanitized_payload[sub_key] = False
                        elif hasattr(sub_val, 'ids'):
                            try:
                                sanitized_payload[sub_key] = [(6, 0, [int(x) for x in sub_val.ids if x])]
                            except Exception:
                                sanitized_payload[sub_key] = []
                        else:
                            sanitized_payload[sub_key] = sub_val
                    cmd[2] = sanitized_payload
            commands.append(tuple(cmd))
        return commands

    @api.model
    def _oms_sanitize_create_vals(self, vals):
        vals = dict(vals or {})
        clean = {}
        for key, value in vals.items():
            field = self._fields.get(key)
            if not field:
                continue
            if field.type in ('many2one', 'many2many', 'one2many'):
                sanitized = self._oms_sanitize_relational_val(field, value)
                if sanitized in (False, [], None) and field.type in ('many2many', 'one2many'):
                    continue
                clean[key] = sanitized
            else:
                clean[key] = value
        return clean

    def _oms_sync_gift_combo_promotions(self):
        if self.env.context.get('oms_skip_gift_sync'):
            return
        Selection = self.env["oms.solar.gift.combo.selection"].sudo()
        for line in self:
            order = line._oms_get_order()
            if not order:
                continue
            promotions = line._oms_get_applied_promotions().filtered(
                lambda p: bool(p._oms_get_gift_combo_records()) or bool(p._oms_get_effective_main_price())
            )
            current = Selection.search([('order_id', '=', order.id), ('line_id', '=', line.id)])
            keep = Selection.browse()
            for promo in promotions:
                allowed_qty = promo._oms_get_effective_gift_qty(getattr(line, "product_uom_qty", 0.0))
                combos = promo._oms_get_gift_combo_records()
                default_combo = combos[:1] if combos else self.env["product.combo"].browse()
                vals = {
                    "order_id": order.id,
                    "line_id": line.id,
                    "promotion_id": promo.id,
                    "purchased_qty": float(getattr(line, "product_uom_qty", 0.0) or 0.0),
                    "allowed_qty": allowed_qty,
                    "main_product_price": promo._oms_get_effective_main_price() or 0.0,
                    "note": promo.oms_gift_combo_note or False,
                    "active": True,
                    "combo_id": default_combo.id if default_combo else False,
                    "selected_qty": allowed_qty if default_combo and allowed_qty > 0 else 0.0,
                }
                selection = current.filtered(lambda s, pid=promo.id: s.promotion_id.id == pid)[:1]
                if selection:
                    selection.write(vals)
                else:
                    selection = Selection.create(vals)
                keep |= selection
            stale = current - keep
            if stale:
                stale.unlink()
            line._oms_apply_main_price_from_promotion()

    def _oms_apply_main_price_from_promotion(self):
        if self.env.context.get("oms_skip_main_price_sync"):
            return
        for line in self:
            promo = line._oms_get_applied_promotions().filtered(lambda p: bool(p._oms_get_effective_main_price()))[:1]
            price = promo._oms_get_effective_main_price() if promo else False
            order = line._oms_get_order()
            preserve_sales_price = (
                not self.env.context.get("oms_force_main_price_sync")
                and getattr(order, "website_id", False)
                and float(getattr(line, "price_unit", 0.0) or 0.0) > 1.0
            )
            if not price:
                if line.oms_promotion_main_price:
                    super(SaleCustomOrderLine, line.with_context(oms_skip_gift_sync=True, oms_skip_main_price_sync=True)).write({"oms_promotion_main_price": 0.0})
                continue
            write_vals = {}
            if not preserve_sales_price and abs(float(getattr(line, "price_unit", 0.0) or 0.0) - float(price)) > 1e-9:
                write_vals["price_unit"] = price
            if not preserve_sales_price and "technical_price_unit" in line._fields and abs(float(getattr(line, "technical_price_unit", 0.0) or 0.0) - float(price)) > 1e-9:
                write_vals["technical_price_unit"] = price
            if abs(float(line.oms_promotion_main_price or 0.0) - float(price)) > 1e-9:
                write_vals["oms_promotion_main_price"] = price
            if write_vals:
                super(SaleCustomOrderLine, line.with_context(oms_skip_gift_sync=True, oms_skip_main_price_sync=True)).write(write_vals)

    @api.model
    def _oms_prepare_create_vals_fallback(self, vals):
        vals = dict(vals or {})
        fallback = {}
        for key, value in vals.items():
            field = self._fields.get(key)
            if not field:
                continue
            if field.type == "many2one":
                sanitized = self._oms_sanitize_relational_val(field, value)
                if isinstance(sanitized, int) and sanitized:
                    fallback[key] = sanitized
                elif sanitized in (False, None):
                    fallback[key] = False
            elif field.type in ("many2many", "one2many"):
                # Drop x2many payloads entirely in fallback path to avoid `_unknown.id`
                # during `new(vals)` precompute when voucher logic builds reward lines.
                continue
            else:
                fallback[key] = value
        return fallback

    @api.model_create_multi
    def create(self, vals_list):
        clean_vals_list = [self._oms_sanitize_create_vals(vals) for vals in vals_list]
        try:
            lines = super().create(clean_vals_list)
        except AttributeError as e:
            if "_unknown" not in str(e):
                raise
            fallback_vals_list = [self._oms_prepare_create_vals_fallback(vals) for vals in vals_list]
            lines = super().create(fallback_vals_list)
        try:
            lines._oms_sync_gift_combo_promotions()
        except Exception:
            # Never block core line creation / voucher apply because of OMS Solar gift sync.
            pass
        return lines

    def write(self, vals):
        clean_vals = self._oms_sanitize_create_vals(vals)
        res = super().write(clean_vals)
        watched = {"product_id", "product_uom_qty", "price_unit", "promotion_ids", "promotion_id", "oms_promotion_ids", "auto_promotion_ids"}
        if watched.intersection(clean_vals.keys()) and not self.env.context.get('oms_skip_gift_sync'):
            try:
                self._oms_sync_gift_combo_promotions()
            except Exception:
                pass
        return res
