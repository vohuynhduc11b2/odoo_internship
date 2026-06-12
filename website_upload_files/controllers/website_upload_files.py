# -*- coding: utf-8 -*-
import base64
import logging

from odoo import http, _
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)


class WebsiteSaleFileUpload(WebsiteSale):
    """OMS Payment modes:
    - full / deposit: đi payment bình thường (SePay)
    - unc: upload chứng từ (multi) + gửi duyệt
    - debt: gửi duyệt (bypass payment)
    """

    @http.route(['/shop/payment'], type='http', auth='public', website=True, sitemap=False)
    def shop_payment(self, **post):
        # Render trang payment bình thường
        res = super().shop_payment(**post)

        order = request.website.sale_get_order()
        if not order:
            return res

        # Đọc mode để QWeb/JS dùng hiển thị (không xử lý upload ở đây)
        payment_mode = (post.get("oms_payment_mode_hidden") or post.get("oms_payment_mode") or "").strip()

        attachments = request.env['ir.attachment'].sudo().search([
            ('res_model', '=', order._name),
            ('res_id', '=', order.id),
        ], order='id desc')

        if getattr(res, 'qcontext', None) is not None:
            res.qcontext.update({
                'attachments': attachments,
                'oms_payment_mode': payment_mode,
            })
        return res

    @http.route(['/shop/oms/send_for_approval'], type='http', auth='public', website=True,
                methods=['POST'], csrf=False, sitemap=False)
    def shop_oms_send_for_approval(self, **post):
        """POST endpoint cho 2 mode bypass:
        - unc: attach nhiều file (multi) + gửi duyệt
        - debt: gửi duyệt luôn
        """
        order = request.website.sale_get_order()
        if not order:
            return request.redirect('/shop/cart')

        payment_mode = (post.get("oms_payment_mode_hidden") or post.get("oms_payment_mode") or "").strip()

        # Chỉ xử lý 2 mode này
        if payment_mode not in ("unc", "debt"):
            return request.redirect('/shop/payment')

        # =========================================================
        # UNC: upload nhiều file
        # QWeb input phải là:
        #   <input type="file" name="attachments" multiple="multiple" .../>
        # =========================================================
        if payment_mode == "unc":
            uploads = request.httprequest.files.getlist('attachments') or []

            # fallback support field cũ 'attachment' (1 file)
            single = request.httprequest.files.get('attachment')
            if single:
                uploads.append(single)

            if uploads:
                Attachment = request.env['ir.attachment'].sudo()
                for upload in uploads:
                    if not upload:
                        continue
                    data = upload.read()
                    if not data:
                        continue
                    Attachment.create({
                        'name': upload.filename,
                        'res_name': upload.filename,
                        'type': 'binary',
                        'res_model': order._name,
                        'res_id': order.id,
                        'mimetype': upload.mimetype,
                        'datas': base64.b64encode(data),
                    })

        # =========================================================
        # GỬI DUYỆT (UNC hoặc Công nợ)
        # =========================================================
        try:
            if hasattr(order, "action_send_for_approval"):
                order.sudo().action_send_for_approval()
            else:
                # Nếu hệ bạn chưa có approval, có thể fallback:
                # order.sudo().action_confirm()
                pass
        except Exception as e:
            _logger.exception("[OMS] Send for approval failed SO=%s mode=%s: %s", order.name, payment_mode, e)
            return request.redirect('/shop/payment?error=approval_failed')

        # Redirect sau khi gửi duyệt
        return request.redirect('/my/orders/%s' % order.id)

    @http.route('/shop/attachments/delete', type='json', auth='public', website=True, sitemap=False)
    def shop_attachments_delete(self, attachment_id=None, **kwargs):
        order = request.website.sale_get_order()
        if not order or not attachment_id:
            return {'ok': False}

        try:
            attachment_id = int(attachment_id)
        except Exception:
            return {'ok': False}

        att = request.env['ir.attachment'].sudo().browse(attachment_id)

        # Security: chỉ cho xóa attachment của đúng đơn hiện tại
        if not att.exists() or att.res_model != order._name or att.res_id != order.id:
            return {'ok': False}

        att.unlink()
        return {'ok': True}
