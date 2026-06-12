# -*- coding: utf-8 -*-
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _set_done(self):
        res = super()._set_done()

        for tx in self:
            # Odoo core website_sale link đơn qua sale_order_ids (sale.order)
            sale_orders = tx.sale_order_ids
            if not sale_orders:
                continue

            for so in sale_orders:
                # Chỉ xử lý đơn website (để không ảnh hưởng sale nội bộ)
                if not so.website_id:
                    continue

                # 1) Confirm sale.order nếu chưa confirm (để portal/core hoạt động đúng)
                if so.state in ("draft", "sent"):
                    try:
                        so.sudo().with_context(from_website_payment=True).action_confirm()
                    except Exception:
                        _logger.exception("Failed to confirm sale.order %s after payment done", so.name)

                # 2) Tạo / cập nhật sale_custom.order (để backend custom của bạn thấy)
                custom = self.env["sale_custom.order"].sudo().search([("origin", "=", so.name)], limit=1)
                vals = {
                    "origin": so.name,  # dùng origin làm “key” cầu nối
                    "partner_id": so.partner_id.id,
                    "partner_invoice_id": so.partner_invoice_id.id,
                    "partner_shipping_id": so.partner_shipping_id.id,
                    "pricelist_id": so.pricelist_id.id,
                    "currency_id": so.currency_id.id,
                    "company_id": so.company_id.id,
                    "website_id": so.website_id.id,
                    "note": so.note,
                }
                if custom:
                    custom.write(vals)
                else:
                    custom = self.env["sale_custom.order"].sudo().create(vals)

                # 3) Copy lines (tối thiểu)
                # Xoá lines cũ để tránh lệch (sửa nhanh; tối ưu sau)
                if custom.order_line:
                    custom.order_line.unlink()

                for line in so.order_line:
                    self.env["sale_custom.order.line"].sudo().create({
                        "order_id": custom.id,
                        "product_id": line.product_id.id,
                        "name": line.name,
                        "product_uom_qty": line.product_uom_qty,
                        "product_uom": line.product_uom.id,
                        "price_unit": line.price_unit,
                        "discount": line.discount,
                        "tax_id": [(6, 0, line.tax_id.ids)],
                    })

                # 4) Clear cart session (giỏ hàng mất)
                try:
                    from odoo.http import request
                    if request and request.session:
                        request.session.pop("sale_order_id", None)
                        request.session.pop("website_sale_cart_quantity", None)
                except Exception:
                    pass

        return res
