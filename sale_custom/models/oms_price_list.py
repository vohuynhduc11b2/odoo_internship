# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError, AccessError, UserError
import logging
import re
import unicodedata
from datetime import date
from collections import defaultdict

MAX_BG_PRICE_TYPE = 99
PRICE_TYPE_SELECTION = [
    (f"BG{idx:02d}", f"BG{idx:02d}")
    for idx in range(1, MAX_BG_PRICE_TYPE + 1)
]
BG_PRIORITY = {f"BG{idx:02d}": idx for idx in range(1, MAX_BG_PRICE_TYPE + 1)}
STRATEGIC_FRAME_MARKERS = ("dac biet", "chien luoc", "chien luot")

_logger = logging.getLogger(__name__)


def _norm_key(s: str) -> str:
    if not s:
        return ""
    if not isinstance(s, str):
        s = str(s)

    # normalize newlines -> space
    s = s.replace("\r", "\n")
    s = re.sub(r"\n+", " ", s)

    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # normalize currency bracket spacing variants
    s = s.replace("( VND)", "(VND)").replace("(VND )", "(VND)").replace("( vnd)", "(vnd)")

    # normalize compare operator spacing: ">= 3" -> ">=3"
    s = re.sub(r"(>=|<=|>|<)\s+", r"\1", s)

    # normalize hyphen spacing: "6 - 19" -> "6-19"
    s = re.sub(r"\s*-\s*", "-", s)

    # remove accents
    s_nfkd = unicodedata.normalize("NFKD", s)
    s = "".join([c for c in s_nfkd if not unicodedata.combining(c)])

    # IMPORTANT: normalize Vietnamese đ/Đ (unicodedata won't remove it)
    s = s.replace("đ", "d").replace("Đ", "D")

    s = s.lower().strip()

    # IMPORTANT: de-dup "truoc vat truoc vat" -> "truoc vat"
    s = re.sub(r"\btruoc vat\b(?:\s+\btruoc vat\b)+", "truoc vat", s)

    # OPTIONAL: ignore (vnd) in matching (nếu muốn tolerant hơn)
    # nếu bạn muốn strict theo frame có/không có (VND) thì comment 2 dòng dưới
    s = re.sub(r"\(\s*vnd\s*\)", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()

    return s




def _parse_price(v):
    """
    Parse number values:
    - accepts int/float
    - accepts strings like "1,234,000" or "1.234.000" or " 1 234 000 "
    - returns float or 0.0
    """
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip()
    if not s:
        return 0.0

    # remove currency symbols and spaces
    s = s.replace("₫", "").replace("vnd", "").replace("VND", "")
    s = s.replace(" ", "")

    # handle thousand separators:
    # - if both '.' and ',' appear: assume ',' is thousand or decimal is tricky; treat as thousand by removing all non-digits except last separator
    # practical approach: keep digits only
    digits = re.sub(r"[^\d\-]", "", s)
    if digits in ("", "-"):
        return 0.0

    try:
        return float(digits)
    except Exception:
        return 0.0

def _sanitize_payload_row_keys(row: dict) -> dict:
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if isinstance(k, str):
            kk = k.replace("\r", "\n")
            kk = re.sub(r"\n+", " ", kk)
            kk = re.sub(r"\s+", " ", kk).strip()
        else:
            kk = k
        out[kk] = v
    return out


def _is_strategic_price_frame_name(name: str) -> bool:
    normalized_name = _norm_key(name or "")
    return any(marker in normalized_name for marker in STRATEGIC_FRAME_MARKERS)



class OmsPriceList(models.Model):
    _name = "oms.price.list"
    _description = "OMS Price List"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # -------------------------
    # Header fields (existing)
    # -------------------------
    name = fields.Char(string="Tên bảng giá", required=True)
    business_unit = fields.Char(string="Business Unit")
    level_code = fields.Char(string="Level Code")
    group_code_solar = fields.Char(string="Group Code Solar")
    note = fields.Char(string="Note")

    from_date = fields.Date(string="Ngày bắt đầu", required=True)
    to_date = fields.Date(string="Ngày kết thúc", required=True)

    # -------------------------
    # Added for versioned API
    # -------------------------
    active = fields.Boolean(default=True, tracking=True)
    category_id = fields.Integer(string="Category Id", index=True, tracking=True)
    category_name = fields.Char(string="Category Name", tracking=True)
    version = fields.Integer(string="Version", default=1, tracking=True)

    # -------------------------
    # Relations
    # -------------------------
    line_ids = fields.One2many(
        "oms.price.list.line", "pricelist_id", string="Price Lines"
    )
    card_group_ids = fields.One2many(
        "oms.price.list.card.group", "pricelist_id", string="Card Group Mappings"
    )
    base_product_price_ids = fields.One2many(
        "oms.price.list.product.base",
        "pricelist_id",
        string="Giá theo SP gốc",
    )

    odoo_pricelist_id = fields.Many2one(
        "product.pricelist",
        string="Odoo Pricelist",
        copy=False,
        tracking=True,
    )


    @api.onchange('from_date', 'to_date')
    def _onchange_date_sync_lines(self):
        """
        Tự động cập nhật ngày cho các dòng nếu chỉnh ở bảng giá tổng.
        Không ghi đè các dòng đã sửa khác giá trị bảng chính (chỉ update khi chưa sửa tay).
        """
        for rec in self:
            for line in rec.line_ids:
                if not line.manual_from_date:
                    line.from_date = rec.from_date
                if not line.manual_to_date:
                    line.to_date = rec.to_date

    def get_price_for_item(self, item_code, quantity, order_date=None):
        """
        Lấy giá 1 dòng theo item_code + quantity + order_date trên 1 bảng giá cụ thể.
        """
        self.ensure_one()
        order_date = order_date or fields.Date.today()
        PriceLine = self.env['oms.price.list.line']
        price_line = PriceLine.search([
            ('pricelist_id', '=', self.id),
            ('item_code', '=', item_code),
            ('min_qty', '<=', quantity),
            ('max_qty', '>=', quantity),
            ('from_date', '<=', order_date),
            ('to_date', '>=', order_date),
            ('price', '>', 0),
        ], order="price desc", limit=1)
        return price_line.price if price_line else False

    def _get_base_price_child_products(self, product):
        self.ensure_one()
        if not product:
            return self.env["product.product"]
        mappings = self.base_product_price_ids.filtered(
            lambda mapping: mapping.base_product_id == product
        )
        return mappings.mapped("child_product_ids").exists()

    # ======================================================================
    # API: Sync bảng giá từ JSON chuẩn (giữ nguyên)
    # ======================================================================

    @api.model
    def api_sync_price_list(self, payload):
        """
        API cũ: nhận payload chuẩn (đã convert trước), validate + ghi DB.
        """
        if not payload:
            raise ValidationError("Payload rỗng.")

        name = payload.get('name')
        from_date = payload.get('from_date')
        to_date = payload.get('to_date')
        if not name or not from_date or not to_date:
            raise ValidationError("Thiếu 'name', 'from_date' hoặc 'to_date'.")

        business_unit = payload.get('business_unit')
        level_code = payload.get('level_code')
        group_code_solar = payload.get('group_code_solar')
        note = payload.get('note')

        lines_data = payload.get('lines') or []
        card_groups = payload.get('card_groups') or []

        Pricelist = self.sudo()
        Product = self.env['product.product'].sudo()
        Line = self.env['oms.price.list.line'].sudo()

        pricelist = Pricelist.search([
            ('name', '=', name),
            ('from_date', '=', from_date),
            ('to_date', '=', to_date),
        ], limit=1)

        vals_header = {
            'name': name,
            'business_unit': business_unit,
            'level_code': level_code,
            'group_code_solar': group_code_solar,
            'note': note,
            'from_date': from_date,
            'to_date': to_date,
        }

        errors = []
        prepared_lines = []

        valid_price_types = dict(self.env['oms.price.list.line']._fields['price_type'].selection)

        for item_idx, item in enumerate(lines_data, start=1):
            item_code = (item.get('item_code') or "").strip()
            if not item_code:
                errors.append(f"Dòng {item_idx}: thiếu item_code.")
                continue

            product = Product.search([('default_code', '=', item_code)], limit=1)
            if not product:
                errors.append(f"Dòng {item_idx}: không tìm thấy product với ItemCode '{item_code}'.")
                continue

            prices = item.get('prices') or []
            if not prices and 'price' in item:
                prices = [item]

            if not prices:
                errors.append(f"Dòng {item_idx}: không có dữ liệu 'prices'.")
                continue

            for price_idx, p in enumerate(prices, start=1):
                price_type = p.get('price_type') or 'BG01'
                if price_type not in valid_price_types:
                    errors.append(
                        f"Dòng {item_idx}, giá {price_idx}: price_type '{price_type}' không hợp lệ "
                        f"(cho phép: {', '.join(valid_price_types.keys())})."
                    )
                    continue

                try:
                    min_qty = int(p.get('min_qty') or 1)
                    max_qty = int(p.get('max_qty') or 9999999)
                except Exception:
                    errors.append(f"Dòng {item_idx}, giá {price_idx}: min_qty/max_qty không phải số nguyên.")
                    continue

                if min_qty <= 0 or min_qty > max_qty:
                    errors.append(
                        f"Dòng {item_idx}, giá {price_idx}: min_qty/max_qty không hợp lệ "
                        f"(min_qty={min_qty}, max_qty={max_qty})."
                    )
                    continue

                try:
                    price_val = float(p.get('price') or 0.0)
                except Exception:
                    errors.append(f"Dòng {item_idx}, giá {price_idx}: price không phải số.")
                    continue

                if price_val <= 0:
                    errors.append(f"Dòng {item_idx}, giá {price_idx}: price phải > 0.")
                    continue

                prepared_lines.append({
                    'product_id': product.id,
                    'min_qty': min_qty,
                    'max_qty': max_qty,
                    'from_date': p.get('from_date') or from_date,
                    'to_date': p.get('to_date') or to_date,
                    'price_type': price_type,
                    'price': price_val,
                    'is_invoice': p.get('is_invoice', False),
                    'group_code_solar': p.get('group_code_solar') or group_code_solar,
                    'level_code': p.get('level_code') or level_code,
                })

        if errors:
            raise ValidationError("\n".join(errors))

        if pricelist:
            pricelist.write(vals_header)
            pricelist.line_ids.unlink()
            pricelist.card_group_ids.unlink()
        else:
            pricelist = Pricelist.create(vals_header)

        for line_vals in prepared_lines:
            Line.create({
                'pricelist_id': pricelist.id,
                'item_id': line_vals['product_id'],
                'min_qty': line_vals['min_qty'],
                'max_qty': line_vals['max_qty'],
                'from_date': line_vals['from_date'],
                'to_date': line_vals['to_date'],
                'price_type': line_vals['price_type'],
                'price': line_vals['price'],
                'is_invoice': line_vals['is_invoice'],
                'group_code_solar': line_vals['group_code_solar'],
                'level_code': line_vals['level_code'],
            })

        if card_groups:
            CG = self.env['oms.price.list.card.group'].sudo()
            for cg in card_groups:
                CG.create({
                    'pricelist_id': pricelist.id,
                    'card_group_from': cg.get('card_group_from'),
                    'card_group_to': cg.get('card_group_to'),
                })

        return pricelist.id

    # ======================================================================
    # API NEW: Versioned update from payload(api_key + data wide json)
    # ======================================================================

    @api.model
    def api_versioned_upsert_from_payload(self, data_rows):
        """
        NEW:
        - Accept payload containing multiple CategoryId.
        - Auto-group by CategoryId and upsert each group independently.
        """
        if not isinstance(data_rows, list) or not data_rows:
            raise ValidationError("data phải là list và không rỗng.")
    
        # 1) sanitize keys: bỏ \n trong header keys
        cleaned = []
        for i, r in enumerate(data_rows, start=1):
            if not isinstance(r, dict):
                raise ValidationError(f"Dòng {i}: row không phải object.")
            cleaned.append(_sanitize_payload_row_keys(r))
    
        # 2) group by CategoryId
        groups = defaultdict(list)
        for i, r in enumerate(cleaned, start=1):
            cid = r.get("CategoryId")
            if not cid:
                raise ValidationError(f"Dòng {i}: thiếu CategoryId.")
            try:
                cid = int(cid)
            except Exception:
                raise ValidationError(f"Dòng {i}: CategoryId không hợp lệ: {cid}")
            groups[cid].append(r)
    
        # 3) upsert từng nhóm
        results = {}
        for cid, rows in groups.items():
            results[str(cid)] = self._api_versioned_upsert_one_category(cid, rows)
    
        return {
            "ok": True,
            "categories": list(results.keys()),
            "results": results,
        }
    @api.model
    def _api_versioned_upsert_one_category(self, category_id: int, data_rows: list):
        """
        Upsert versioned pricelist for ONE category only.
        data_rows: list[dict] (đã sanitize key)

        Behavior:
        - Skip rows missing ItemCode / missing product
        - Skip price columns that don't match frame_map
        - Deduplicate lines by uniq constraint key (pricelist_id,item_id,min_qty,max_qty,price_type,from_date,to_date)
        - Create all lines (not only first row)
        """
        if not data_rows:
            raise ValidationError(f"CategoryId={category_id}: data rỗng.")

        # đảm bảo tất cả row cùng CategoryId
        for i, r in enumerate(data_rows, start=1):
            try:
                if int((r or {}).get("CategoryId") or 0) != int(category_id):
                    raise ValidationError(
                        f"CategoryId={category_id}: Dòng {i} có CategoryId khác ({(r or {}).get('CategoryId')})."
                    )
            except Exception:
                raise ValidationError(f"CategoryId={category_id}: Dòng {i} CategoryId không hợp lệ.")

        first = data_rows[0] or {}
        warnings = []

        # ==========================================================
        # 1) Load frames theo Category
        # ==========================================================
        Frame = self.env["oms.pricelist.frame"].sudo().with_context(active_test=False)
        frames = Frame.search([
            ("category_id", "=", category_id),
            ("active", "=", True),
        ])
        if not frames:
            raise ValidationError(f"Không có oms.pricelist.frame active cho CategoryId={category_id}.")

        category_name = first.get("CategoryName") or frames[0].category_name or str(category_id)

        # Map normalized frame column -> frame record
        frame_map = {}
        for f in frames:
            if not f.price_list_name:
                continue
            nk = _norm_key(f.price_list_name)
            if not nk:
                continue
            if nk in frame_map and frame_map[nk].id != f.id:
                _logger.warning(
                    "CategoryId=%s: frame normalize key duplicated '%s' (frame_id %s vs %s).",
                    category_id, nk, frame_map[nk].id, f.id
                )
                # giữ frame đầu tiên
                continue
            frame_map[nk] = f

        # ==========================================================
        # 2) Detect dynamic price columns
        # ==========================================================
        FIXED_KEYS_NORM = {_norm_key(x) for x in {
            "CategoryItemCodeId", "CategoryId", "CategoryName",
            "ItemCode", "ItemName", "Des", "Note", "ClearStockPrice",
        }}

        dynamic_norms = set()
        per_row_keymap = []

        for r in data_rows:
            r = r or {}
            row_map = {}
            for raw_k in r.keys():
                nk = _norm_key(raw_k)
                if not nk:
                    continue

                row_map.setdefault(nk, raw_k)

                if nk in FIXED_KEYS_NORM:
                    continue
                if nk.startswith("gm-"):
                    continue

                # chỉ coi là cột giá nếu có giá > 0 ở ít nhất 1 row
                if _parse_price(r.get(raw_k)) > 0:
                    dynamic_norms.add(nk)

            per_row_keymap.append(row_map)

        if not dynamic_norms:
            # category này không có cột giá hợp lệ => bỏ qua category (không raise để batch chạy tiếp)
            return {
                "ok": True,
                "category_id": int(category_id),
                "category_name": category_name,
                "skipped_category": True,
                "reason": "no_dynamic_price_columns",
                "warnings": warnings,
                "lines_created": 0,
            }

        unknown = [c for c in sorted(dynamic_norms) if c not in frame_map]
        if unknown:
            _logger.warning(
                "[CategoryId=%s] Unknown price columns (no frame). Will SKIP: %s",
                category_id, unknown
            )
            warnings.append({
                "type": "unknown_columns",
                "category_id": int(category_id),
                "columns": unknown,
            })
            dynamic_norms = set(dynamic_norms) - set(unknown)

        if not dynamic_norms:
            # sau khi skip unknown mà hết => bỏ qua category
            return {
                "ok": True,
                "category_id": int(category_id),
                "category_name": category_name,
                "skipped_category": True,
                "reason": "no_valid_price_columns_after_skip_unknown",
                "warnings": warnings,
                "lines_created": 0,
            }

        # ==========================================================
        # 3) Disable old active pricelist
        # ==========================================================
        old_active = self.sudo().search([
            ("category_id", "=", category_id),
            ("active", "=", True),
        ])
        if old_active:
            old_active.write({"active": False})

        # ==========================================================
        # 4) Create new pricelist header (version++)
        # ==========================================================
        last = self.sudo().search(
            [("category_id", "=", category_id)],
            order="version desc",
            limit=1,
        )
        new_version = (last.version or 0) + 1 if last else 1

        header_from = fields.Date.today()
        header_to = date(2099, 12, 31)

        pl = self.sudo().create({
            "name": f"{category_name} - v{new_version}",
            "category_id": int(category_id),
            "category_name": category_name,
            "version": new_version,
            "active": True,
            "from_date": header_from,
            "to_date": header_to,
            "note": "API versioned import",
        })

        # ==========================================================
        # 5) BG01..BG99 mapping theo frame min/max
        # ==========================================================
        ordered_frames = frames.sorted(lambda f: (f.min_qty or 0, f.max_qty or 0, f.price_list_name or "", f.id))
        if len(ordered_frames) > MAX_BG_PRICE_TYPE:
            raise ValidationError(
                f"CategoryId={category_id} có hơn {MAX_BG_PRICE_TYPE} khung giá, "
                f"BG01-BG{MAX_BG_PRICE_TYPE:02d} không đủ."
            )

        bg_map = {fr.id: f"BG{idx:02d}" for idx, fr in enumerate(ordered_frames, start=1)}

        # ==========================================================
        # 6) Build raw line vals
        # ==========================================================
        Product = self.env["product.product"].sudo()
        Line = self.env["oms.price.list.line"].sudo()

        MAX_DEFAULT = 999999
        raw_vals = []

        skipped_missing_itemcode = 0
        skipped_missing_product = 0
        skipped_zero_price = 0

        for i, r in enumerate(data_rows, start=1):
            r = r or {}
            item_code = (r.get("ItemCode") or "").strip()
            if not item_code:
                skipped_missing_itemcode += 1
                _logger.warning("[CategoryId=%s] Skip row %s: missing ItemCode", category_id, i)
                continue

            product = Product.search([("default_code", "=", item_code)], limit=1)
            if not product:
                skipped_missing_product += 1
                _logger.warning(
                    "[CategoryId=%s] Skip row %s: product not found default_code='%s'",
                    category_id, i, item_code
                )
                continue

            row_map = per_row_keymap[i - 1] if (i - 1) < len(per_row_keymap) else {}

            for col_norm in dynamic_norms:
                raw_key = row_map.get(col_norm)
                if not raw_key:
                    continue

                price_val = _parse_price(r.get(raw_key))
                if not price_val or price_val <= 0:
                    skipped_zero_price += 1
                    continue

                fr = frame_map.get(col_norm)
                if not fr:
                    continue

                minq = int(fr.min_qty or 1)
                maxq = int(fr.max_qty or MAX_DEFAULT)
                if maxq <= 0 or maxq > MAX_DEFAULT:
                    maxq = MAX_DEFAULT

                raw_vals.append({
                    "pricelist_id": pl.id,
                    "item_id": product.id,
                    "price_frame_id": fr.id,
                    "price_frame_name": fr.price_list_name,
                    "min_qty": minq,
                    "max_qty": maxq,
                    "from_date": header_from,
                    "to_date": header_to,
                    "price_type": bg_map[fr.id],
                    "price": float(price_val),
                    "is_invoice": False,
                    "group_code_solar": pl.group_code_solar,
                    "level_code": pl.level_code,
                })

        # nếu không có dòng nào => vẫn tạo header nhưng báo lines_created=0
        if not raw_vals:
            warnings.append({
                "type": "no_lines_created",
                "category_id": int(category_id),
                "reason": "all_prices_empty_or_products_missing",
            })
            return {
                "ok": True,
                "category_id": int(category_id),
                "category_name": category_name,
                "old_disabled": len(old_active),
                "new_version": new_version,
                "pricelist_id": pl.id,
                "lines_created": 0,
                "warnings": warnings,
                "skipped_missing_itemcode": skipped_missing_itemcode,
                "skipped_missing_product": skipped_missing_product,
                "skipped_zero_price": skipped_zero_price,
            }

        # ==========================================================
        # 7) Dedup by UNIQUE constraint key
        #     uniq key: (pricelist_id,item_id,min_qty,max_qty,price_type,from_date,to_date)
        #     last wins
        # ==========================================================
        before = len(raw_vals)
        dedup = {}
        for v in raw_vals:
            k = (
                v["pricelist_id"],
                v["item_id"],
                v["min_qty"],
                v["max_qty"],
                v["price_type"],
                v["from_date"],
                v["to_date"],
            )
            dedup[k] = v

        create_vals = list(dedup.values())
        dropped = before - len(create_vals)
        if dropped:
            _logger.warning(
                "CategoryId=%s: dedup dropped %s duplicated lines (before=%s after=%s).",
                category_id, dropped, before, len(create_vals)
            )
            warnings.append({
                "type": "dedup_dropped",
                "category_id": int(category_id),
                "dropped": dropped,
                "before": before,
                "after": len(create_vals),
            })

        Line.create(create_vals)

        return {
            "ok": True,
            "category_id": int(category_id),
            "category_name": category_name,
            "old_disabled": len(old_active),
            "new_version": new_version,
            "pricelist_id": pl.id,
            "lines_created": len(create_vals),
            "warnings": warnings,
            "skipped_missing_itemcode": skipped_missing_itemcode,
            "skipped_missing_product": skipped_missing_product,
            "skipped_zero_price": skipped_zero_price,
        }

    def action_publish_to_odoo_pricelist(self):
        """
        Publish selected OMS price lists into Odoo pricelists.

        If UC PriceList Frame has "Bảng giá lấy" configured, each frame pushes
        only to those target pricelists. Frames without a configured target are
        intentionally skipped.
        """
        ProductPricelist = self.env["product.pricelist"].sudo()
        PricelistItem = self.env["product.pricelist.item"].sudo()
        Frame = self.env["oms.pricelist.frame"].sudo().with_context(active_test=False)

        def _get_or_create_pricelist(name):
            pricelist = ProductPricelist.search([("name", "=", name)], limit=1)
            if not pricelist:
                pricelist = ProductPricelist.create({
                    "name": name,
                    "currency_id": self.env.company.currency_id.id,
                    "active": True,
                })
            return pricelist

        def _line_key(line, product=None):
            return (
                product.id if product else line.item_id.id,
                int(line.min_qty or 1),
                int(line.max_qty or 0),
                line.from_date,
                line.to_date,
            )

        def _keep_best_line(chosen_lines, line, product=None):
            key = _line_key(line, product=product)
            cur = chosen_lines.get(key)
            if not cur:
                chosen_lines[key] = line
                return

            p_new = BG_PRIORITY.get(line.price_type or "", 9999)
            p_cur = BG_PRIORITY.get(cur.price_type or "", 9999)
            if p_new < p_cur:
                chosen_lines[key] = line
            elif p_new == p_cur and float(line.price) < float(cur.price):
                chosen_lines[key] = line

        def _sync_to_pricelist(target_pricelist, chosen_lines, *, update_existing, cleanup_product_ids=None):
            created = 0
            updated = 0

            chosen_keys = set(chosen_lines)
            product_ids = set(cleanup_product_ids or []) | {key[0] for key in chosen_keys}

            if product_ids:
                cleanup_domain = [
                    ("pricelist_id", "=", target_pricelist.id),
                    ("applied_on", "=", "0_product_variant"),
                    ("product_id", "in", list(product_ids)),
                    ("compute_price", "=", "fixed"),
                ]
                for item in PricelistItem.search(cleanup_domain):
                    item_key = (
                        item.product_id.id,
                        int(item.min_quantity or 1),
                        int(getattr(item, "oms_max_quantity", 0) or 0),
                        item.date_start,
                        item.date_end,
                    )
                    if item_key not in chosen_keys:
                        item.unlink()

            for (product_id, min_qty, max_qty, date_start, date_end), line in chosen_lines.items():
                frame = line.price_frame_id
                frame_name = frame.price_list_name if frame else (line.price_type or "")
                domain = [
                    ("pricelist_id", "=", target_pricelist.id),
                    ("applied_on", "=", "0_product_variant"),
                    ("product_id", "=", product_id),
                    ("compute_price", "=", "fixed"),
                    ("min_quantity", "=", min_qty),
                    ("date_start", "=", date_start),
                    ("date_end", "=", date_end),
                ]
                if "oms_max_quantity" in PricelistItem._fields:
                    domain.append(("oms_max_quantity", "=", max_qty))
                existing = PricelistItem.search(domain)
                vals = {
                    "fixed_price": float(line.price),
                    "min_quantity": min_qty,
                    "date_start": date_start,
                    "date_end": date_end,
                }
                if "oms_price_frame_id" in PricelistItem._fields:
                    vals["oms_price_frame_id"] = frame.id if frame else False
                if "oms_price_frame_name" in PricelistItem._fields:
                    vals["oms_price_frame_name"] = frame_name
                if "oms_max_quantity" in PricelistItem._fields:
                    vals["oms_max_quantity"] = max_qty

                if existing:
                    if update_existing:
                        existing.write(vals)
                        updated += len(existing)
                    continue

                PricelistItem.create({
                    "pricelist_id": target_pricelist.id,
                    "applied_on": "0_product_variant",
                    "product_id": product_id,
                    "compute_price": "fixed",
                    "fixed_price": float(line.price),
                    "min_quantity": min_qty,
                    "date_start": date_start,
                    "date_end": date_end,
                    **({"oms_price_frame_id": frame.id if frame else False} if "oms_price_frame_id" in PricelistItem._fields else {}),
                    **({"oms_price_frame_name": frame_name} if "oms_price_frame_name" in PricelistItem._fields else {}),
                    **({"oms_max_quantity": max_qty} if "oms_max_quantity" in PricelistItem._fields else {}),
                })
                created += 1

            return created, updated

        def _get_frame_lookup():
            price_types_by_category = defaultdict(set)
            frame_by_category_price_type = {}
            frame_by_category_name = {}
            category_has_publish_config = defaultdict(bool)
            frame_ids = set()
            frames = Frame.search([("active", "=", True)])
            grouped = defaultdict(list)
            for frame in frames:
                grouped[int(frame.category_id or 0)].append(frame)
                frame_by_category_name[
                    (int(frame.category_id or 0), _norm_key(frame.price_list_name or ""))
                ] = frame

            for category_id, cat_frames in grouped.items():
                ordered_frames = sorted(
                    cat_frames,
                    key=lambda f: (f.min_qty or 0, f.max_qty or 0, f.price_list_name or "", f.id),
                )
                for idx, frame in enumerate(ordered_frames, start=1):
                    price_type = f"BG{idx:02d}"
                    frame_by_category_price_type[(category_id, price_type)] = frame
                    if frame.publish_pricelist_ids:
                        category_has_publish_config[category_id] = True
                    if _is_strategic_price_frame_name(frame.price_list_name):
                        price_types_by_category[category_id].add(price_type)
                        frame_ids.add(frame.id)
            return (
                price_types_by_category,
                frame_ids,
                frame_by_category_price_type,
                frame_by_category_name,
                category_has_publish_config,
            )

        def _clear_special_base_rule(special_pricelist):
            domain = [
                ("pricelist_id", "=", special_pricelist.id),
                ("applied_on", "=", "3_global"),
            ]
            PricelistItem.search(domain).unlink()

        default_pl = _get_or_create_pricelist("[OMS] DEFAULT")
        special_pl = _get_or_create_pricelist("[OMS] DAC BIET")

        (
            _strategic_price_types_by_category,
            _strategic_frame_ids,
            frame_by_category_price_type,
            frame_by_category_name,
            _category_has_publish_config,
        ) = _get_frame_lookup()

        configured_target_ids = set(Frame.search([("active", "=", True)]).mapped("publish_pricelist_ids").ids)
        configured_target_ids.update([default_pl.id, special_pl.id])

        target_lines = defaultdict(dict)
        touched_product_ids = set()
        source_names = []
        valid_line_count = 0

        for oms_pricelist in self:
            category_id = int(oms_pricelist.category_id or 0)
            source_names.append(oms_pricelist.display_name)

            for line in oms_pricelist.line_ids:
                if not line.item_id or not line.price or line.price <= 0:
                    continue
                valid_line_count += 1

                mapped_frame = False
                if line.price_frame_name:
                    mapped_frame = frame_by_category_name.get(
                        (category_id, _norm_key(line.price_frame_name))
                    )
                if not mapped_frame:
                    mapped_frame = frame_by_category_price_type.get(
                        (category_id, line.price_type)
                    )
                mapped_frame = mapped_frame or line.price_frame_id
                if mapped_frame and (
                    line.price_frame_id != mapped_frame
                    or line.price_frame_name != mapped_frame.price_list_name
                ):
                    line.sudo().write({
                        "price_frame_id": mapped_frame.id,
                        "price_frame_name": mapped_frame.price_list_name,
                    })

                target_pricelists = (
                    mapped_frame.publish_pricelist_ids
                    if mapped_frame
                    else ProductPricelist.browse()
                )

                for target_pricelist in target_pricelists:
                    _keep_best_line(target_lines[target_pricelist.id], line)

                publish_products = line.item_id | oms_pricelist._get_base_price_child_products(line.item_id)
                touched_product_ids.update(publish_products.ids)
                for child_product in publish_products - line.item_id:
                    for target_pricelist in target_pricelists:
                        _keep_best_line(target_lines[target_pricelist.id], line, product=child_product)

        if not valid_line_count:
            raise UserError("Không có dòng giá hợp lệ để publish.")

        if not target_lines:
            raise UserError(
                "Không có bảng giá nào để publish. Hãy chọn 'Bảng giá lấy' trên UC PriceList Frame."
            )

        _clear_special_base_rule(special_pl)

        publish_results = {}
        sync_target_ids = configured_target_ids | set(target_lines)
        for target_id in sync_target_ids:
            target_pricelist = ProductPricelist.browse(target_id).exists()
            if not target_pricelist:
                continue
            chosen_lines = target_lines.get(target_id, {})

            created, updated = _sync_to_pricelist(
                target_pricelist,
                chosen_lines,
                update_existing=True,
                cleanup_product_ids=touched_product_ids,
            )
            if not chosen_lines:
                continue
            publish_results[target_pricelist.display_name] = {
                "lines": len(chosen_lines),
                "created": created,
                "updated": updated,
            }

        if not publish_results:
            raise UserError("Không tìm thấy bảng giá đích còn hoạt động để publish.")

        self.write({"odoo_pricelist_id": default_pl.id})

        _logger.info(
            "Publish OMS->Odoo aggregated done sources=%s results=%s",
            source_names,
            publish_results,
        )
        result_parts = [
            f"{name}: {vals['lines']} dòng"
            for name, vals in publish_results.items()
        ]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Publish OMS Pricelist",
                "message": "Đã publish " + "; ".join(result_parts),
                "type": "success",
                "sticky": False,
            },
        }


class OmsPriceListProductBase(models.Model):
    _name = "oms.price.list.product.base"
    _description = "OMS Price List Base Product Mapping"
    _order = "base_product_id, id"

    pricelist_id = fields.Many2one(
        "oms.price.list",
        string="Price List",
        ondelete="cascade",
        required=True,
        index=True,
    )
    base_product_id = fields.Many2one(
        "product.product",
        string="SP gốc",
        required=True,
        index=True,
    )
    base_item_code = fields.Char(
        string="Mã SP gốc",
        related="base_product_id.default_code",
        store=True,
        readonly=True,
    )
    child_product_ids = fields.Many2many(
        "product.product",
        "oms_price_list_product_base_child_rel",
        "mapping_id",
        "product_id",
        string="SP con dùng giá SP gốc",
        required=True,
    )

    @api.constrains("base_product_id", "child_product_ids")
    def _check_child_products(self):
        for rec in self:
            if rec.base_product_id and rec.base_product_id in rec.child_product_ids:
                raise ValidationError("SP con không được trùng với SP gốc.")

    _sql_constraints = [
        (
            "uniq_pricelist_base_product",
            "unique(pricelist_id, base_product_id)",
            "Mỗi SP gốc chỉ được cấu hình một lần trên cùng bảng giá OMS.",
        ),
    ]


class ProductPricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    oms_price_frame_id = fields.Many2one(
        "oms.pricelist.frame",
        string="OMS Price Frame",
        index=True,
        readonly=True,
    )
    oms_price_frame_name = fields.Char(string="OMS Price Frame Name", readonly=True)
    oms_max_quantity = fields.Float(string="OMS Max Quantity", readonly=True)


class OmsPriceListLine(models.Model):
    _name = "oms.price.list.line"
    _description = "OMS Price List Line"

    pricelist_id = fields.Many2one(
        "oms.price.list", string="Price List", ondelete="cascade", required=True
    )
    item_id = fields.Many2one('product.product', string="Product", required=True)
    item_code = fields.Char(
        string="Item Code",
        related="item_id.default_code",
        store=True,
        readonly=True
    )
    manual_from_date = fields.Boolean(string="Sửa ngày bắt đầu", default=False)
    manual_to_date = fields.Boolean(string="Sửa ngày kết thúc", default=False)
    from_date = fields.Date(string="From Date", required=True)
    to_date = fields.Date(string="To Date", required=True)
    min_qty = fields.Integer(string="Min Qty", default=1)
    max_qty = fields.Integer(string="Max Qty", default=9999999)

    price_type = fields.Selection(PRICE_TYPE_SELECTION, string="Bảng Giá", required=True)
    price_frame_id = fields.Many2one(
        "oms.pricelist.frame",
        string="Price Frame",
        index=True,
        readonly=True,
    )
    api_price_list_id = fields.Integer(
        string="PriceList",
        related="price_frame_id.api_id",
        store=True,
        readonly=True,
    )

    price = fields.Float(string="Giá", required=True)
    price_frame_name = fields.Char(string="Price Frame Name", index=True, readonly=True)
    is_invoice = fields.Boolean(string="Is Invoice")
    group_code_solar = fields.Char(string="Group Code Solar")
    level_code = fields.Char(string="Level Code")

    @api.model
    def create(self, vals):
        """
        Khi tạo dòng mới, nếu from_date/to_date chưa có thì lấy từ bảng giá tổng.
        """
        pricelist_id = vals.get('pricelist_id')
        pricelist = self.env['oms.price.list'].browse(pricelist_id) if pricelist_id else False
        if vals.get("price_frame_id") and not vals.get("price_frame_name"):
            frame = self.env["oms.pricelist.frame"].browse(vals["price_frame_id"])
            vals["price_frame_name"] = frame.price_list_name
        if not vals.get('from_date') and pricelist and pricelist.from_date:
            vals['from_date'] = pricelist.from_date
        if not vals.get('to_date') and pricelist and pricelist.to_date:
            vals['to_date'] = pricelist.to_date
        return super(OmsPriceListLine, self).create(vals)

    @api.onchange('pricelist_id')
    def _onchange_pricelist_id(self):
        if self.pricelist_id:
            if not self.from_date:
                self.from_date = self.pricelist_id.from_date
            if not self.to_date:
                self.to_date = self.pricelist_id.to_date

    @api.onchange('from_date')
    def _onchange_manual_from_date(self):
        for rec in self:
            rec.manual_from_date = (rec.from_date != rec.pricelist_id.from_date)

    @api.onchange('to_date')
    def _onchange_manual_to_date(self):
        for rec in self:
            rec.manual_to_date = (rec.to_date != rec.pricelist_id.to_date)

    @api.model
    def get_price_for_product(self, item_code, quantity, at_date=None, pricelist_id=None):
        at_date = at_date or fields.Date.today()
        domain = [
            ('item_code', '=', item_code),
            ('min_qty', '<=', quantity),
            ('max_qty', '>=', quantity),
            ('from_date', '<=', at_date),
            ('to_date', '>=', at_date),
            ('price', '>', 0),
        ]
        if pricelist_id:
            domain.append(('pricelist_id', '=', pricelist_id))
        line = self.search(domain, order="price desc", limit=1)
        return line.price if line else False

    _sql_constraints = [
        (
            'uniq_item_per_qty_price_type_date',
            'unique(pricelist_id, item_id, min_qty, max_qty, price_type, from_date, to_date)',
            'Không được trùng sản phẩm, khoảng số lượng, loại bảng giá, khung ngày trên cùng 1 bảng giá!'
        )
    ]


class OmsPriceListCommission(models.Model):
    _name = "oms.price.list.commission"
    _description = "OMS Price List Commission"

    business_unit = fields.Char(string="Business Unit")
    level_code = fields.Char(string="Level Code")
    pricelist_code = fields.Char(string="Price List Code")
    commission = fields.Float(string="Commission (%)")
    valid_from = fields.Date(string="Valid From")
    valid_to = fields.Date(string="Valid To")


class OmsPriceListCardGroup(models.Model):
    _name = "oms.price.list.card.group"
    _description = "OMS Price List Card Group"

    pricelist_id = fields.Many2one(
        "oms.price.list", string="Price List", ondelete="cascade"
    )
    card_group_from = fields.Char(string="Card Group From")
    card_group_to = fields.Char(string="Card Group To")
