# -*- coding: utf-8 -*-
import base64
import logging

from odoo import models
from odoo.tools.misc import file_open

_logger = logging.getLogger(__name__)


def _read_logo_b64():
    """
    Đọc logo từ static file và trả về bytes base64.
    NÊN dùng ảnh nguồn lớn (vd: 512x128 hoặc 1024x256) để lên web nét và phóng to không vỡ.
    """
    path = "payment_sepay/static/src/img/sepay_logo.png"
    with file_open(path, "rb") as f:
        return base64.b64encode(f.read())


def _write_logo(rec, img_b64):
    """
    Odoo 18 ưu tiên ghi vào image_1920 (ảnh gốc) để hệ thống tự tạo các size nhỏ hơn.
    Fallback theo thứ tự: image_1920 -> image -> image_128
    """
    vals = {}

    if "image_1920" in rec._fields:
        vals["image_1920"] = img_b64
    elif "image" in rec._fields:
        vals["image"] = img_b64
    elif "image_128" in rec._fields:
        vals["image_128"] = img_b64

    if vals:
        rec.write(vals)
        return True
    return False


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    def _sepay_apply_checkout_logo(self):
        img_b64 = None
        try:
            img_b64 = _read_logo_b64()
        except Exception as e:
            _logger.exception("[SePay] Cannot read logo file: %s", e)
            return

        for provider in self.sudo():
            if _write_logo(provider, img_b64):
                _logger.info("[SePay] Applied provider logo: %s (%s)", provider.display_name, provider.code)


class PaymentMethod(models.Model):
    _inherit = "payment.method"

    def _sepay_apply_checkout_logo(self):
        img_b64 = None
        try:
            img_b64 = _read_logo_b64()
        except Exception as e:
            _logger.exception("[SePay] Cannot read logo file: %s", e)
            return

        for method in self.sudo():
            if _write_logo(method, img_b64):
                _logger.info("[SePay] Applied method logo: %s (%s)", method.display_name, method.code)
