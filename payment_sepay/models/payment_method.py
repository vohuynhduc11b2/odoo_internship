# models/payment_method.py
from odoo import models


class PaymentMethod(models.Model):
    _inherit = "payment.method"

    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        res["sepay"] = {
            "mode": "unique",
            "domain": [("provider_id.code", "=", "sepay")],
        }
        return res
