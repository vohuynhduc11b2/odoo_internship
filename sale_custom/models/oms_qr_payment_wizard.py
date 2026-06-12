# -*- coding: utf-8 -*-
import re
import base64
from urllib.parse import urlencode

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class OmsQrPaymentWizard(models.TransientModel):
    _name = "oms.qr.payment.wizard"
    _description = "Tạo QR thanh toán Vietcombank"

    # --- Helpers --------------------------------------------------------------
    def _cfg(self, key, default=''):
        """Đọc tham số hệ thống với giá trị mặc định."""
        param = self.env['ir.config_parameter'].sudo().get_param(key)
        return (param or default).strip()

    def _default_bank_account(self):
        """
        Chọn 1 trong 2 STK Vietcombank theo chi nhánh user tạo Wizard:

        - HNI : dùng oms.vcb.hni      (mặc định 1051318386)
        - Khác: dùng oms.vcb.ky_dong  (mặc định 1036936868)
        """
        branch = (self.env.user.branch or '').upper()

        acc_hni = self._cfg('oms.vcb.hni', '1051318386')
        acc_hcm = self._cfg('oms.vcb.ky_dong', '1036936868')

        if branch == 'HNI':
            return acc_hni
        return acc_hcm

    def _default_bank_name(self):
        """
        Tên chi nhánh ngân hàng theo chi nhánh user:

        - HNI  -> Vietcombank – CN Hà Nội
        - Khác -> Vietcombank – CN Kỳ Đồng – HCM
        """
        branch = (self.env.user.branch or '').upper()
        if branch == 'HNI':
            return "Vietcombank – CN Hà Nội"
        return "Vietcombank – CN Kỳ Đồng – HCM"

    def _qr_desc(self):
        """
        Nội dung QR:
          - AUT: AUT + partner.ref + (COC/TT)
          - ELE: MMYY + partner.ref + OMS + (COC/THANHTOAN)

        Ví dụ:
          AUTC12345COC
          AUTC12345TT
          1125C12345OMSCOC
          1125C12345OMSTHANHTOAN
        """
        mmyy = fields.Date.context_today(self).strftime('%m%y')
        bu = re.sub(r'[^0-9A-Za-z]', '', self.business_area or 'AUT').upper()
        if bu not in ('AUT', 'ELE'):
            bu = 'AUT'

        # lấy ref của KH, chỉ giữ chữ & số, rỗng thì 00000
        pref_raw = (self.partner_id.ref or '').strip()
        pref = re.sub(r'[^0-9A-Za-z]', '', pref_raw) or "00000"

        if bu == 'ELE':
            suffix = "COC" if self.is_deposit else "THANHTOAN"
            return f"{mmyy}{pref}OMS{suffix}"

        suffix = "COC" if self.is_deposit else "TT"
        return f"AUT{pref}{suffix}"

    # --- Fields ---------------------------------------------------------------
    partner_id = fields.Many2one(
        'res.partner',
        string="Khách hàng",
        required=True,
    )

    amount = fields.Monetary(
        string="Số tiền",
        required=True,
        help="Số tiền cần thanh toán / cọc."
    )

    currency_id = fields.Many2one(
        'res.currency',
        string="Tiền tệ",
        required=True,
        default=lambda self: self.env.company.currency_id.id,
    )

    is_deposit = fields.Boolean(
        string="Là tiền cọc?",
        help="Tick nếu đây là QR cho tiền cọc."
    )

    business_area = fields.Selection(
        [('AUT', 'AUT'), ('ELE', 'ELE')],
        string="Mảng",
        required=True,
        default=lambda self: (
            (self.env.user.business_area or 'AUT').strip().upper()
            if (self.env.user.business_area or '').strip().upper() in ('AUT', 'ELE')
            else 'AUT'
        ),
    )

    # nội dung: tự sinh, readonly
    description = fields.Char(
        string="Nội dung chuyển khoản",
        readonly=True,
    )

    bank_account = fields.Char(
        string="Số tài khoản",
        readonly=True,
        default=lambda self: self._default_bank_account(),
        help=(
            "HNI : 1051318386 – Vietcombank (DAT - CN Hà Nội)\n"
            "Khác: 1036936868 – Vietcombank – CN Kỳ Đồng – HCM"
        ),
    )

    bank_name = fields.Char(
        string="Ngân hàng",
        readonly=True,
        default=lambda self: self._default_bank_name(),
    )

    sepay_qr_url = fields.Char(
        string="Link QR (Sepay)",
        compute="_compute_sepay_qr_url",
        store=False,
    )

    qr_image = fields.Binary(
        string="QR Code",
        readonly=True,
        help="Ảnh QR tải từ Sepay."
    )

    qr_filename = fields.Char(
        string="Tên file QR",
        default="qr_payment.png"
    )

    # --- Onchange & Compute ---------------------------------------------------
    @api.onchange('partner_id', 'is_deposit', 'business_area')
    def _onchange_set_default_description(self):
        """Mỗi lần đổi KH / cọc -> luôn sinh lại description (readonly)."""
        for w in self:
            w.description = w._qr_desc()

    @api.onchange('amount')
    def _onchange_amount_clear_qr_image(self):
        """Đổi số tiền -> clear ảnh QR cũ, để user bấm lại nút tải."""
        for w in self:
            w.qr_image = False

    @api.depends('amount', 'description', 'bank_account')
    def _compute_sepay_qr_url(self):
        """
        Tạo link hình QR của Sepay:
        https://qr.sepay.vn/img?acc=...&bank=Vietcombank&amount=...&des=...
        """
        for w in self:
            acc = (w.bank_account or '').strip()
            if not acc:
                w.sepay_qr_url = False
                continue

            params = {
                'acc': acc,
                'bank': 'Vietcombank',
            }

            # amount: làm tròn, >0 mới gửi
            try:
                amt = int(round(float(w.amount or 0)))
                if amt > 0:
                    params['amount'] = str(amt)
            except Exception:
                pass

            desc = (w.description or '').strip()
            if desc:
                params['des'] = desc

            w.sepay_qr_url = "https://qr.sepay.vn/img?" + urlencode(params, safe='')

    # --- Internal: tải ảnh QR -------------------------------------------------
    def _fetch_qr_image(self):
        self.ensure_one()
        if not self.sepay_qr_url:
            raise UserError(_("Chưa có link QR, vui lòng kiểm tra số tiền / nội dung."))

        try:
            resp = requests.get(self.sepay_qr_url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            raise UserError(_("Không tải được ảnh QR: %s") % e)

        self.qr_image = base64.b64encode(resp.content)
        if not self.qr_filename:
            self.qr_filename = "qr_%s.png" % (self.partner_id.ref or 'payment')

    # --- Action: tải file QR --------------------------------------------------
    def action_download_qr(self):
        """
        - Gọi Sepay lấy ảnh PNG
        - Lưu vào Binary + attachment
        - Trả về act_url download file (không mở tab mới)
        """
        self.ensure_one()
        self._fetch_qr_image()

        if not self.qr_image:
            raise UserError(_("Không có dữ liệu ảnh QR."))

        # ---- Đặt tên file theo KH + số tiền ----
        partner_name = (self.partner_id.name or 'customer').strip()
        # bỏ ký tự lạ trong tên cho an toàn
        partner_name = re.sub(r'[^0-9A-Za-zÀ-ỹà-ỹ\s_-]', '', partner_name)

        try:
            amt_int = int(round(float(self.amount or 0)))
        except Exception:
            amt_int = 0

        if amt_int > 0:
            filename = f"QR_{partner_name}_{amt_int}_VND.png"
        else:
            filename = f"QR_{partner_name}.png"

        # lưu lại vào field để lần sau còn dùng
        self.qr_filename = filename

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': self.qr_image,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'image/png',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=1',
            'target': 'self',  # tải về ngay trong tab hiện tại
        }
