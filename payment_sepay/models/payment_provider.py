# models/payment_provider.py
# -*- coding: utf-8 -*-

import logging
from urllib.parse import urlencode

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("sepay", "SePay")],
        ondelete={"sepay": "set default"},
    )

    # ====== QR config (BẮT BUỘC nếu dùng SePay QR) ======
    sepay_bank_code = fields.Char(
        string="SePay Bank Code",
        default="VCB",
        groups="base.group_system",
        copy=False,
        help="Mã ngân hàng (ví dụ: VCB, ACB, TCB...) theo VietQR/SePay.",
    )
    sepay_bank_account = fields.Char(
        string="SePay Bank Account",
        default="1051318386",
        groups="base.group_system",
        copy=False,
        help="Số tài khoản nhận tiền.",
    )
    sepay_account_name = fields.Char(
        string="Account Name (optional)",
        groups="base.group_system",
        copy=False,
    )

    # ====== API config (optional) ======
    sepay_use_api = fields.Boolean(
        string="Use SePay API (optional)",
        default=False,
        groups="base.group_system",
        help="Bật nếu bạn muốn dùng API để poll trạng thái / webhook verify, v.v.",
    )
    sepay_api_key = fields.Char(
        string="SePay API Token (optional)",
        groups="base.group_system",
        password=True,
        copy=False,
    )
    sepay_api_secret = fields.Char(
        string="SePay API Secret (optional)",
        groups="base.group_system",
        password=True,
        copy=False,
    )
    sepay_base_url = fields.Char(
        string="SePay API Base URL (optional)",
        default="https://api.sepay.vn",
        groups="base.group_system",
        help="Base API URL của SePay (chỉ cần khi bật Use SePay API).",
    )
    sepay_webhook_secret = fields.Char(
        string="Webhook Secret (optional)",
        groups="base.group_system",
        password=True,
        copy=False,
        help="Secret để verify webhook (nếu có). Không có thì để trống.",
    )
    sepay_return_url = fields.Char(
        string="Return URL (optional)",
        default="/payment/sepay/return",
        groups="base.group_system",
        help="URL khách quay lại sau khi thanh toán (nếu bạn dùng).",
    )

    # Public QR image generator base URL
    sepay_qr_base_url = fields.Char(
        string="SePay QR Base URL",
        default="https://qr.sepay.vn",
        groups="base.group_system",
        help="Public QR image generator base URL.",
    )

    # =====================================================
    # Website payment flow helpers (QUAN TRỌNG để hết “Oops”)
    # =====================================================
    def _should_build_inline_form(self, is_validation=False):
        self.ensure_one()
        if self.code == "sepay":
            return False
        return super()._should_build_inline_form(is_validation=is_validation)
    
    def _get_redirect_form_view(self, is_validation=False):
        self.ensure_one()
        if self.code == "sepay":
            view = self.env.ref("payment_sepay.sepay_redirect_form", raise_if_not_found=False)
            if view:
                return view
            _logger.error("[SePay] Missing xmlid: payment_sepay.sepay_redirect_form. Check __manifest__.py data.")
            return super()._get_redirect_form_view(is_validation=is_validation)
        return super()._get_redirect_form_view(is_validation=is_validation)


    # =========================
    # Config validation
    # =========================
    def _sepay_check_required_fields(self):
        for p in self:
            if p.code != "sepay":
                continue
            # chỉ enforce khi provider đang bật
            if p.state not in ("enabled", "test"):
                continue

            missing = []
            if not (p.sepay_bank_code or "").strip():
                missing.append(_("SePay Bank Code"))
            if not (p.sepay_bank_account or "").strip():
                missing.append(_("SePay Bank Account"))

            if p.sepay_use_api:
                if not (p.sepay_api_key or "").strip():
                    missing.append(_("SePay API Token"))
                if not (p.sepay_base_url or "").strip():
                    missing.append(_("SePay API Base URL"))

            if missing:
                raise ValidationError(
                    _("Missing SePay configuration: %s") % ", ".join(missing)
                )

    @api.constrains(
        "state",
        "code",
        "sepay_bank_code",
        "sepay_bank_account",
        "sepay_use_api",
        "sepay_api_key",
        "sepay_base_url",
    )
    def _constrains_sepay_config(self):
        self._sepay_check_required_fields()

    # =========================
    # QR URL builder
    # =========================
    def _sepay_qr_image_url(self, *, amount, description, template="compact", download=False, bank=None, account=None):
        """Sinh URL ảnh QR public (không gọi API)."""
        self.ensure_one()

        bank = (bank or self.sepay_bank_code or "").strip()
        acc = (account or self.sepay_bank_account or "").strip()
        if not bank or not acc:
            raise ValidationError(
                _("SePay requires Bank Code and Bank Account to generate QR.")
            )

        base = (self.sepay_qr_base_url or "https://qr.sepay.vn").strip().rstrip("/")
        des = (description or "").strip()
        if len(des) > 50:
            des = des[:50]

        try:
            amt = int(round(float(amount or 0.0)))
        except Exception:
            amt = 0

        params = {"bank": bank, "acc": acc, "amount": amt, "des": des}
        template = (template or "").strip()
        if template:
            params["template"] = template
        if download:
            params["download"] = "true"

        query = urlencode(params, doseq=False)
        return f"{base}/img?{query}"
