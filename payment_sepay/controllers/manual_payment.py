# -*- coding: utf-8 -*-
import logging
import re
import uuid
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def _mask(s: str, keep: int = 6) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s if len(s) <= keep else f"{s[:keep]}..."


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    # =========================================================
    # Fields
    # =========================================================
    sepay_mode = fields.Selection(
        selection=[("qr", "QR"), ("unc", "UNC"), ("credit", "Công nợ")],
        string="SePay Mode",
        default="qr",
        copy=False,
        readonly=False,
    )

    sepay_sent_for_approval = fields.Boolean(
        string="SePay Sent For Approval",
        default=False,
        copy=False,
        readonly=True,
    )

    # Token riêng cho website redirect
    uc_access_token = fields.Char(
        string="SePay Access Token",
        copy=False,
        readonly=True,
        default=lambda self: uuid.uuid4().hex,
    )

    # Deposit tracking
    uc_is_deposit = fields.Boolean(copy=False, default=False)
    uc_deposit_percent = fields.Float(copy=False, default=30.0)
    uc_deposit_applied = fields.Boolean(copy=False, default=False)  # chống chạy lặp

    # =========================================================
    # Debug switch (System Parameter)
    # key: payment_sepay.debug = 1
    # =========================================================
    def _sepay_debug(self) -> bool:
        try:
            v = self.env["ir.config_parameter"].sudo().get_param("payment_sepay.debug", "0")
            return str(v).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            return False

    # =========================================================
    # Token helper
    # =========================================================
    def _uc_get_access_token(self):
        self.ensure_one()
        token = (self.uc_access_token or "").strip()
        if not token:
            token = uuid.uuid4().hex
            try:
                self.sudo().write({"uc_access_token": token})
            except Exception:
                pass
        return token

    # =========================================================
    # Mode helpers
    # =========================================================
    def _sepay_parse_mode(self, raw):
        raw = (raw or "").strip().lower()
        if not raw:
            return None

        # CREDIT / DEBT variants
        if raw in ("credit", "debt", "congno", "cong_no", "công nợ", "pay_later"):
            return "credit"
        if ("công nợ" in raw) or ("cong no" in raw) or ("congno" in raw) or ("credit" in raw) or ("debt" in raw):
            return "credit"

        # UNC variants
        if raw in ("unc", "uy_nhiem_chi", "phieu_uy_nhiem_chi", "phiếu ủy nhiệm chi"):
            return "unc"
        if ("ủy" in raw) or ("uy" in raw) or ("nhiem" in raw) or ("unc" in raw):
            return "unc"

        # QR variants
        if raw in ("qr", "qrcode", "qr_code"):
            return "qr"
        if "qr" in raw:
            return "qr"

        return None

    def _sepay_get_http_request(self):
        try:
            from odoo import http
        except Exception:
            return None
        req = getattr(http, "request", None)
        if not req or not getattr(req, "httprequest", None):
            return None
        return req

    def _sepay_get_session_value(self, req, key):
        try:
            sess = getattr(req, "session", None)
            if not sess:
                return None
            getter = getattr(sess, "get", None)
            if callable(getter):
                return getter(key)
        except Exception:
            return None
        return None

    def _sepay_get_mode_from_request(self):
        self.ensure_one()
        req = self._sepay_get_http_request()
        if not req:
            return None

        candidates = []

        # 1) params
        if hasattr(req, "params") and isinstance(req.params, dict):
            candidates += [
                req.params.get("oms_payment_mode"),
                req.params.get("sepay_mode"),
                req.params.get("payment_type"),
                req.params.get("website_payment_type"),
            ]

        # 2) session
        candidates += [
            self._sepay_get_session_value(req, "oms_payment_mode"),
            self._sepay_get_session_value(req, "website_payment_type"),
            self._sepay_get_session_value(req, "sepay_mode"),
        ]

        # 3) form (POST)
        try:
            form = req.httprequest.form
            candidates += [
                form.get("oms_payment_mode"),
                form.get("sepay_mode"),
                form.get("payment_type"),
                form.get("website_payment_type"),
            ]
        except Exception:
            pass

        # 4) query string (GET)
        try:
            args = req.httprequest.args
            candidates += [
                args.get("oms_payment_mode"),
                args.get("sepay_mode"),
                args.get("payment_type"),
                args.get("website_payment_type"),
            ]
        except Exception:
            pass

        for c in candidates:
            mode = self._sepay_parse_mode(c)
            if mode in ("qr", "unc", "credit"):
                return mode
        return None

    def _sepay_resolve_mode(self, processing_values=None):
        self.ensure_one()

        # 1) from request/session
        mode = self._sepay_get_mode_from_request()

        # 2) fallback from processing_values
        if not mode and isinstance(processing_values, dict):
            mode = (
                self._sepay_parse_mode(processing_values.get("oms_payment_mode"))
                or self._sepay_parse_mode(processing_values.get("sepay_mode"))
                or self._sepay_parse_mode(processing_values.get("payment_type"))
                or self._sepay_parse_mode(processing_values.get("website_payment_type"))
            )

        # 3) fallback from field
        mode = mode or (self.sepay_mode or "qr")
        return self._sepay_parse_mode(mode) or "qr"

    def _sepay_sync_mode_if_needed(self, mode):
        self.ensure_one()
        mode = (mode or "").strip().lower()
        if mode not in ("qr", "unc", "credit"):
            return
        if (self.sepay_mode or "qr") == mode:
            return
        try:
            self.sudo().write({"sepay_mode": mode})
        except Exception:
            pass

    # =========================================================
    # Related SO helpers (robust)
    # =========================================================
    def _uc_get_related_sale_orders(self):
        self.ensure_one()
        if "sale_order_ids" in self._fields:
            return self.sale_order_ids
        if "sale_order_id" in self._fields:
            return self.sale_order_id
        if "sale_custom_order_ids" in self._fields:
            return self.sale_custom_order_ids
        if "sale_custom_order_id" in self._fields:
            return self.sale_custom_order_id
        return self.env["sale.order"].browse([])

    def _uc_get_order_for_amount(self):
        self.ensure_one()
        orders = self._uc_get_related_sale_orders()
        return orders[:1].sudo() if orders else self.env["sale.order"].browse([])

    # =========================================================
    # QR note helper (build text only)
    # =========================================================
    def _sepay_build_qr_note(self):
        self.ensure_one()
        now = fields.Datetime.context_timestamp(self, datetime.utcnow())
        mmyy = now.strftime("%m%y")
    
        so = self._uc_get_related_sale_orders()[:1]
        business_area = "AUT"
        if so:
            business_area = (
                getattr(so, "BusinessArea", None)
                or getattr(so, "business_area", None)
                or getattr(so, "u_business_unit", None)
                or getattr(getattr(so, "user_id", None), "business_area", None)
                or "AUT"
            )
        business_area = re.sub(r"[^0-9A-Za-z]", "", str(business_area or "AUT")).upper()
        if business_area not in ("AUT", "ELE"):
            business_area = "AUT"
    
        partner_ref = "00000"
    
        # Ưu tiên lấy mã khách hàng từ commercial partner của SO
        if so and so.partner_id and so.partner_id.commercial_partner_id:
            cp = so.partner_id.commercial_partner_id
            if cp.ref:
                partner_ref = str(cp.ref).strip()
    
        # Fallback từ transaction partner
        if partner_ref == "00000" and self.partner_id and self.partner_id.commercial_partner_id:
            cp = self.partner_id.commercial_partner_id
            if cp.ref:
                partner_ref = str(cp.ref).strip()
    
        partner_ref = re.sub(r"[^0-9A-Za-z]", "", partner_ref) or "00000"
        order_id = str((so.id if so else self.id) or 0)
        order_id = re.sub(r"\D", "", order_id) or "0"
        if business_area == "ELE":
            return f"{mmyy}{partner_ref}OMS{order_id}THANHTOAN"
        suffix = "COC" if self.uc_is_deposit else "TT"
        return f"AUT{partner_ref}{suffix}{order_id}"

    # =========================================================
    # Deposit helpers
    # =========================================================
    def _uc_get_deposit_percent_from_ctx(self, processing_values=None, default=30.0):
        self.ensure_one()

        def _to_float(v):
            try:
                return float(v)
            except Exception:
                return None

        # 1) request/session
        try:
            from odoo import http
            req = getattr(http, "request", None)
            if req and getattr(req, "params", None):
                v = req.params.get("uc_deposit_percent") or req.params.get("percent") or req.params.get("oms_deposit_percent")
                f = _to_float(v)
                if f is not None:
                    return f

            if req and getattr(req, "session", None):
                v = req.session.get("uc_deposit_percent") or req.session.get("percent") or req.session.get("oms_deposit_percent")
                f = _to_float(v)
                if f is not None:
                    return f
        except Exception:
            pass

        # 2) processing_values
        if isinstance(processing_values, dict):
            v = processing_values.get("uc_deposit_percent") or processing_values.get("percent") or processing_values.get("oms_deposit_percent")
            f = _to_float(v)
            if f is not None:
                return f

        return float(default or 30.0)

    def _uc_is_deposit_flow(self, processing_values=None):
        self.ensure_one()

        def _truthy(v):
            return str(v or "").strip().lower() in ("1", "true", "yes", "on")

        def _is_deposit_word(v):
            s = str(v or "").strip().lower()
            return s in ("deposit", "downpayment", "down_payment", "prepayment", "coc", "cọc")

        # 1) request params/session
        try:
            from odoo import http
            req = getattr(http, "request", None)

            if req and getattr(req, "params", None):
                v = req.params.get("uc_is_deposit") or req.params.get("deposit") or req.params.get("oms_is_deposit") or req.params.get("oms_deposit30") or req.params.get("oms_deposit")
                if _truthy(v):
                    return True
                if _is_deposit_word(req.params.get("oms_payment_mode")) or _is_deposit_word(req.params.get("website_payment_type")):
                    return True

            if req and getattr(req, "session", None):
                v = req.session.get("uc_is_deposit") or req.session.get("deposit") or req.session.get("oms_is_deposit") or req.session.get("oms_deposit30") or req.session.get("oms_deposit")
                if _truthy(v):
                    return True
                if _is_deposit_word(req.session.get("oms_payment_mode")) or _is_deposit_word(req.session.get("website_payment_type")):
                    return True
        except Exception:
            pass

        # 2) processing_values
        if isinstance(processing_values, dict):
            v = processing_values.get("uc_is_deposit") or processing_values.get("deposit") or processing_values.get("oms_is_deposit") or processing_values.get("oms_deposit30") or processing_values.get("oms_deposit")
            if _truthy(v):
                return True
            if _is_deposit_word(processing_values.get("oms_payment_mode")) or _is_deposit_word(processing_values.get("website_payment_type")):
                return True

        return False

    def _uc_compute_charge_amount(self, order, is_deposit, percent):
        """Tính amount cần thu cho tx theo cọc / còn lại."""
        self.ensure_one()
        if not order:
            return float(self.amount or 0.0)

        total = float(getattr(order, "amount_total", 0.0) or 0.0)
        pct = float(percent or 0.0)
        if pct <= 0:
            pct = float(getattr(order, "uc_deposit_percent", 30.0) or 30.0)
        if pct <= 0:
            pct = 30.0

        cur = getattr(order, "currency_id", None)
        paid_candidates = [
            float(getattr(order, "pay_accumulated", 0.0) or 0.0),
            float(getattr(order, "amount_paid", 0.0) or 0.0),
            float(getattr(order, "uc_amount_paid", 0.0) or 0.0),
        ]
        paid = max(paid_candidates)

        if is_deposit:
            expected_deposit = total * pct / 100.0
            if cur:
                expected_deposit = cur.round(expected_deposit)
            amt = max(expected_deposit - paid, 0.0)
        else:
            amt = max(total - paid, 0.0)

        if cur:
            amt = cur.round(amt)

        return float(amt)

    # =========================================================
    # FORCE operation everywhere (create + create_values)
    # =========================================================
    @api.model_create_multi
    def create(self, vals_list):
        txs = super().create(vals_list)
        for tx in txs.sudo():
            if tx.provider_code == "sepay" and getattr(tx, "operation", None) not in ("online_redirect", "validation"):
                try:
                    tx.write({"operation": "online_redirect"})
                except Exception:
                    pass
        return txs

    @api.model
    def _get_specific_create_values(self, provider_code, values):
        res = super()._get_specific_create_values(provider_code, values)
        if provider_code == "sepay":
            res["operation"] = "online_redirect"
        return res

    # =========================================================
    # Specific processing values (deposit + pending + mode sync)
    # =========================================================
    def _get_specific_processing_values(self, processing_values):
        res = super()._get_specific_processing_values(processing_values)
        self.ensure_one()

        if self.provider_code != "sepay":
            return res

        mode = self._sepay_resolve_mode(processing_values)
        self._sepay_sync_mode_if_needed(mode)

        # set pending cho cả QR/UNC/CREDIT
        try:
            if self.state == "draft":
                self._set_pending()
        except Exception:
            pass

        # deposit logic: update tx.amount trước khi render/redirect
        try:
            order = self._uc_get_order_for_amount()
            percent = self._uc_get_deposit_percent_from_ctx(processing_values, default=30.0)
            is_deposit = self._uc_is_deposit_flow(processing_values)

            # fallback: nếu tx.amount đã ~ đúng amount cọc thì coi là deposit
            try:
                total = float(getattr(order, "amount_total", 0.0) or 0.0) if order else 0.0
                dep_amt = (total * float(percent or 30.0) / 100.0) if total else 0.0
                cur = getattr(order, "currency_id", None)
                if cur:
                    dep_amt = cur.round(dep_amt)
                if not is_deposit and total and dep_amt and dep_amt < total:
                    if abs(float(self.amount or 0.0) - float(dep_amt)) <= 0.01:
                        is_deposit = True
            except Exception:
                pass

            charge_amount = self._uc_compute_charge_amount(order, is_deposit, percent)

            vals = {"uc_is_deposit": bool(is_deposit), "uc_deposit_percent": float(percent or 30.0)}
            if self.state not in ("done", "cancel", "error"):
                if abs(float(self.amount or 0.0) - float(charge_amount or 0.0)) > 0.00001:
                    vals["amount"] = charge_amount

            self.sudo().write(vals)

            _logger.info(
                "[SePay][AMOUNT] tx=%s state=%s op=%s mode=%s deposit=%s pct=%s amount=%s",
                self.reference, self.state, getattr(self, "operation", None), mode,
                vals["uc_is_deposit"], vals["uc_deposit_percent"], self.amount
            )
        except Exception:
            _logger.exception("[SePay] Cannot apply deposit amount update tx=%s", self.reference)

        return res

    # =========================================================
    # ALWAYS build a redirect form with <form> (template -> hard fallback)
    # =========================================================
    def _sepay_build_redirect_form_html(self, base_processing_values: dict) -> str:
        self.ensure_one()
        rendering_vals = self._get_specific_rendering_values(base_processing_values or {})

        # 1) Template render
        try:
            html = self.env["ir.ui.view"]._render_template("payment_sepay.sepay_redirect_form", rendering_vals)
            if html and "<form" in str(html).lower():
                return html
            _logger.error("[SePay][REDIRECT_HTML] template rendered but missing <form> tx=%s", self.reference)
        except Exception:
            _logger.exception("[SePay][REDIRECT_HTML] template render failed tx=%s", self.reference)

        # 2) HARD fallback (never fail)
        import html as _h

        def esc(x):
            return _h.escape(str(x or ""), quote=True)

        action = rendering_vals.get("api_url") or "/payment/sepay/unc"
        ref = rendering_vals.get("reference") or self.reference
        tok = rendering_vals.get("access_token") or self._uc_get_access_token()
        mode = rendering_vals.get("sepay_mode") or (self.sepay_mode or "unc")
        desc = rendering_vals.get("sepay_description") or ""

        return (
            f'<form action="{esc(action)}" method="post" class="o_payment_redirect_form">'
            f'<input type="hidden" name="reference" value="{esc(ref)}"/>'
            f'<input type="hidden" name="access_token" value="{esc(tok)}"/>'
            f'<input type="hidden" name="sepay_mode" value="{esc(mode)}"/>'
            f'<input type="hidden" name="sepay_description" value="{esc(desc)}"/>'
            f"</form>"
        )

    # =========================================================
    # Rendering values (redirect form values)
    # =========================================================
    def _get_specific_rendering_values(self, processing_values):
        self.ensure_one()
        values = super()._get_specific_rendering_values(processing_values)

        if self.provider_code != "sepay":
            return values

        provider = self.provider_id
        if not provider:
            raise ValidationError(_("Missing payment provider."))

        mode = self._sepay_resolve_mode(processing_values)
        self._sepay_sync_mode_if_needed(mode)

        if self.state == "draft":
            self._set_pending()

        description = self._sepay_build_qr_note() if mode == "qr" else ""
        api_url = "/payment/sepay/pay" if mode == "qr" else "/payment/sepay/unc"

        access_token = self._uc_get_access_token()

        values.update({
            "api_url": api_url,
            "reference": self.reference,
            "access_token": access_token,
            "sepay_mode": mode,
            "sepay_description": description,
            "provider_display": provider.display_name,

            # keys tương thích
            "oms_payment_mode": mode,
            "website_payment_type": mode,

            # debug
            "uc_is_deposit": bool(self.uc_is_deposit),
            "uc_deposit_percent": float(self.uc_deposit_percent or 30.0),
        })

        if self._sepay_debug():
            _logger.info(
                "[SePay][RENDER] ref=%s mode=%s api_url=%s token=%s",
                self.reference, mode, api_url, _mask(access_token)
            )

        # persist last tx params in session
        try:
            req = self._sepay_get_http_request()
            if req and getattr(req, "session", None) is not None:
                req.session["sepay_last_reference"] = self.reference
                req.session["sepay_last_access_token"] = access_token
                req.session["sepay_last_mode"] = mode
                req.session["sepay_mode"] = mode
                req.session["oms_payment_mode"] = mode
                req.session["website_payment_type"] = mode
        except Exception:
            pass

        return values

    # =========================================================
    # Accounting create payment
    # =========================================================
    def _create_payment(self):
        self.ensure_one()
        if self.provider_code == "sepay":
            _logger.info("[SePay] Skip auto account.payment for tx=%s", self.reference)
            return False
        return super()._create_payment()

    # =========================================================
    # Manual mark done for QR
    # =========================================================
    def action_sepay_mark_done(self):
        for tx in self.sudo():
            if tx.provider_code != "sepay":
                continue
            if tx.state in ("done", "cancel", "error"):
                continue
            if (tx.sepay_mode or "qr") != "qr":
                _logger.info("[SePay] Skip mark_done because mode=%s tx=%s", tx.sepay_mode, tx.reference)
                continue

            _logger.info("[SePay] Manual confirm tx=%s", tx.reference)
            try:
                tx._set_done()
            except Exception:
                _logger.exception("[SePay] _set_done failed tx=%s", tx.reference)
                continue

            try:
                if hasattr(tx, "_post_process_after_done"):
                    tx._post_process_after_done()
            except Exception:
                _logger.exception("[SePay] _post_process_after_done failed tx=%s", tx.reference)

    # =========================================================
    # Finalize post processing
    # - apply deposit
    # - send approval
    # =========================================================
    def _finalize_post_processing(self):
        res = super()._finalize_post_processing()

        for tx in self.sudo():
            if tx.provider_code != "sepay":
                continue

            orders = tx._uc_get_related_sale_orders().sudo()

            # ---- Apply deposit (DONE only) ----
            if tx.state == "done" and tx.uc_is_deposit and not tx.uc_deposit_applied:
                try:
                    for order in orders:
                        if not getattr(order, "website_id", False):
                            continue
                        if "uc_deposit_paid_amount" in order._fields:
                            order.sudo().write({
                                "uc_deposit_paid_amount": (order.uc_deposit_paid_amount or 0.0) + (tx.amount or 0.0),
                            })
                    tx.sudo().write({"uc_deposit_applied": True})
                except Exception:
                    _logger.exception("[SePay] Cannot apply deposit for tx=%s", tx.reference)

            # ---- Send approval (DONE/AUTHORIZED) ----
            if tx.state in ("done", "authorized") and not getattr(tx, "sepay_sent_for_approval", False):
                mode = (tx.sepay_mode or "qr").strip().lower()
                if mode == "credit":
                    reason = "debt_paynow"
                elif mode == "unc":
                    reason = "other"
                else:
                    reason = "paid"

                ok_any = False
                try:
                    for order in orders.filtered(lambda o: getattr(o, "website_id", False)):
                        if mode == "qr":
                            paid = max(
                                float(getattr(order, "pay_accumulated", 0.0) or 0.0),
                                float(getattr(order, "amount_paid", 0.0) or 0.0),
                                float(getattr(order, "uc_amount_paid", 0.0) or 0.0),
                            )
                            total = float(getattr(order, "amount_total", 0.0) or 0.0)
                            cur = getattr(order, "currency_id", None)
                            if total and cur and cur.compare_amounts(paid, total) < 0:
                                _logger.info(
                                    "[SePay] skip approval because QR payment is not fully paid order=%s paid=%s total=%s tx=%s",
                                    order.display_name, paid, total, tx.reference,
                                )
                                continue
                        try:
                            if hasattr(order, "uc_website_finalize_send_approval"):
                                ok = bool(order.uc_website_finalize_send_approval(reason=reason, payment_tx=tx))
                            elif hasattr(order, "action_send_for_approval"):
                                order.action_send_for_approval()
                                ok = True
                            else:
                                ok = False
                            ok_any = ok_any or ok
                        except Exception as e:
                            _logger.exception("[SePay] send approval failed order=%s tx=%s: %s", order.display_name, tx.reference, e)

                    tx.sudo().write({"sepay_sent_for_approval": True})
                    _logger.info("[SePay] finalize_post_processing approval ok_any=%s tx=%s mode=%s", ok_any, tx.reference, mode)
                except Exception:
                    _logger.exception("[SePay] finalize_post_processing overall failed tx=%s", tx.reference)
                    try:
                        tx.sudo().write({"sepay_sent_for_approval": True})
                    except Exception:
                        pass

        return res
