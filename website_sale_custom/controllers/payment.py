# -*- coding: utf-8 -*-
from html import escape

from psycopg2.errors import LockNotAvailable

from odoo import _
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.fields import Command
from odoo.http import request, route
from odoo.tools import SQL

from odoo.addons.payment.controllers import portal as payment_portal


class PaymentPortal(payment_portal.PaymentPortal):
    """
    - SePay + QR: giữ luồng redirect chuẩn qua tx._get_processing_values()
    - UNC: tạo tx, set pending, gửi duyệt, nhưng vẫn trả redirect_form_html chuẩn
      để frontend Odoo không crash.
    """

    def _validate_transaction_for_order(self, transaction, sale_order):
        return

    def _get_payment_type_from_kwargs_or_order(self, order_sudo, kwargs):
        raw = (
            (kwargs.get("payment_type"))
            or (kwargs.get("pay_type"))
            or (kwargs.get("payment_mode"))
            or (kwargs.get("uc_payment_type"))
            or (kwargs.get("website_payment_type"))
            or (kwargs.get("oms_payment_mode_hidden"))
            or (kwargs.get("oms_payment_mode"))
            or (kwargs.get("sepay_payment_type"))
            or (kwargs.get("sepay_mode"))
            or ""
        )
        raw = (raw or "").strip().lower()
        if raw:
            return raw

        sess_raw = (
            (request.session.get("payment_type"))
            or (request.session.get("sepay_payment_type"))
            or (request.session.get("website_payment_type"))
            or (request.session.get("oms_payment_mode"))
            or (request.session.get("sepay_mode"))
            or ""
        )
        sess_raw = (sess_raw or "").strip().lower()
        if sess_raw:
            return sess_raw

        for fname in ("payment_type", "x_payment_type", "uc_payment_type", "website_payment_type", "sepay_mode"):
            if hasattr(order_sudo, fname):
                v = (getattr(order_sudo, fname) or "").strip().lower()
                if v:
                    return v

        return ""

    def _is_unc(self, payment_type: str) -> bool:
        payment_type = (payment_type or "").strip().lower()
        return payment_type in ("unc", "uy_nhiem_chi", "phiếu ủy nhiệm chi", "phieu_uy_nhiem_chi")

    def _is_credit(self, payment_type: str) -> bool:
        payment_type = (payment_type or "").strip().lower()
        return payment_type in ("credit", "debt", "congno", "cong_no", "công nợ", "pay_later")

    def _send_for_approval(self, order_sudo, tx_sudo=None):
        try:
            if hasattr(order_sudo, "action_send_for_approval"):
                order_sudo.sudo().action_send_for_approval()
            else:
                order_sudo.sudo().message_post(
                    body=_("UNC: Khách đã gửi phiếu ủy nhiệm chi. Vui lòng kiểm tra và duyệt theo quy trình."),
                    subtype_xmlid="mail.mt_note",
                )
        except Exception as e:
            order_sudo.sudo().message_post(
                body=_("UNC: Không thể tự động gửi duyệt. Lỗi: %s") % (str(e)),
                subtype_xmlid="mail.mt_note",
            )

        if tx_sudo and hasattr(tx_sudo, "sepay_sent_for_approval"):
            try:
                tx_sudo.sudo().write({"sepay_sent_for_approval": True})
            except Exception:
                pass

    def _build_redirect_form_html(self, action, fields=None, method="get"):
        fields = fields or {}
        inputs = []
        for name, value in fields.items():
            if value in (None, False, ""):
                continue
            inputs.append(
                '<input type="hidden" name="%s" value="%s"/>'
                % (escape(str(name), quote=True), escape(str(value), quote=True))
            )

        return """
            <form id="o_payment_redirect_form" action="%s" method="%s">
                %s
            </form>
        """ % (
            escape(action, quote=True),
            escape(method, quote=True),
            "".join(inputs),
        )

    @route("/shop/payment/transaction/<int:order_id>", type="json", auth="public", website=True)
    def shop_payment_transaction(self, order_id, access_token=None, **kwargs):
        try:
            order_sudo = self._document_check_access("sale_custom.order", order_id, access_token)

            request.env.cr.execute(
                SQL(
                    "SELECT 1 FROM %s WHERE id = %s FOR NO KEY UPDATE NOWAIT",
                    SQL.identifier(order_sudo._table),
                    order_id,
                )
            )
        except MissingError:
            raise
        except AccessError as e:
            raise ValidationError(_("The access token is invalid.")) from e
        except LockNotAvailable:
            raise UserError(_("Payment is already being processed."))

        if order_sudo.state == "cancel":
            raise ValidationError(_("The order has been cancelled."))

        if hasattr(order_sudo, "_uc_wait_sales_price_lines") and order_sudo._uc_wait_sales_price_lines():
            raise ValidationError(_(
                "Đơn hàng đang có sản phẩm chờ Sales nhập giá. "
                "Vui lòng quay lại thanh toán sau khi Sales cập nhật giá."
            ))

        order_sudo._check_cart_is_ready_to_be_paid()
        self._validate_transaction_kwargs(kwargs)

        payment_type = self._get_payment_type_from_kwargs_or_order(order_sudo, kwargs)
        is_unc = self._is_unc(payment_type)
        is_credit = self._is_credit(payment_type)

        if hasattr(order_sudo, "_oms_apply_strategic_payment_pricelist"):
            order_sudo._oms_apply_strategic_payment_pricelist(payment_type)

        partner = None
        if hasattr(order_sudo, "partner_invoice_id") and order_sudo.partner_invoice_id:
            partner = order_sudo.partner_invoice_id
        elif hasattr(order_sudo, "partner_id") and order_sudo.partner_id:
            partner = order_sudo.partner_id
        partner_id = partner.id if partner else False

        currency = getattr(order_sudo, "currency_id", None)
        currency_id = currency.id if currency else (request.website.currency_id.id if request.website.currency_id else False)

        create_kwargs = dict(kwargs)
        if not create_kwargs.get("amount"):
            create_kwargs["amount"] = order_sudo.amount_total

        compare_amounts = order_sudo.currency_id.compare_amounts
        if compare_amounts(create_kwargs["amount"], order_sudo.amount_total):
            raise ValidationError(_("The cart has been updated. Please refresh the page."))
        if compare_amounts(order_sudo.amount_paid, order_sudo.amount_total) == 0:
            raise UserError(_("The cart has already been paid. Please refresh the page."))

        delay_payment_request = (create_kwargs.get("flow") == "token")
        if delay_payment_request:
            request.update_context(delay_payment_request=True)

        create_kwargs.pop("partner_id", None)
        create_kwargs.pop("currency_id", None)

        tx_sudo = self._create_transaction(
            partner_id=partner_id,
            currency_id=currency_id,
            custom_create_values={"sale_order_ids": [Command.set([order_id])]},
            **create_kwargs,
        )

        request.session["__website_sale_last_tx_id"] = tx_sudo.id
        request.session["sale_transaction_id"] = tx_sudo.id

        if hasattr(tx_sudo, "sepay_mode"):
            try:
                if is_unc:
                    tx_sudo.sudo().write({"sepay_mode": "unc"})
                elif is_credit:
                    tx_sudo.sudo().write({"sepay_mode": "credit"})
                else:
                    tx_sudo.sudo().write({"sepay_mode": "qr"})
            except Exception:
                pass

        self._validate_transaction_for_order(tx_sudo, order_sudo)

        if delay_payment_request:
            tx_sudo._send_payment_request()

        request.session["sale_last_order_id"] = int(order_id)
        request.session.pop("sale_order_id", None)
        request.session["website_sale_cart_quantity"] = 0
        request.session.pop("uc_voucher_msg", None)
        request.session.pop("uc_cart_msg", None)

        if is_unc:
            try:
                if tx_sudo.state == "draft":
                    tx_sudo._set_pending()
            except Exception:
                pass

            self._send_for_approval(order_sudo, tx_sudo=tx_sudo)

            processing_values = tx_sudo._get_processing_values()
            processing_values["redirect_url"] = "/shop/unc/submitted"
            processing_values["redirect_form_html"] = self._build_redirect_form_html(
                "/shop/unc/submitted",
                {
                    "order_id": order_id,
                    "access_token": access_token or "",
                    "reference": tx_sudo.reference,
                },
                method="get",
            )
            return processing_values

        return tx_sudo._get_processing_values()

    @route("/shop/unc/submitted", type="http", auth="public", website=True, methods=["GET", "POST"], csrf=False)
    def shop_unc_submitted(self, order_id=None, access_token=None, reference=None, **kw):
        if not order_id or not access_token:
            return request.redirect("/shop/payment")

        order_sudo = self._document_check_access("sale_custom.order", int(order_id), access_token)

        tx = None
        if reference:
            tx = request.env["payment.transaction"].sudo().search([("reference", "=", reference)], limit=1)

        amount_total = getattr(order_sudo, "amount_total", 0.0)
        amount_paid = getattr(order_sudo, "amount_paid", 0.0)
        try:
            amount_due = amount_total - amount_paid
        except Exception:
            amount_due = None

        approval_state = None
        for fname in ("approval_state", "x_approval_state", "state_approval", "uc_approval_state"):
            if hasattr(order_sudo, fname):
                approval_state = getattr(order_sudo, fname)
                break

        values = {
            "order": order_sudo,
            "tx": tx,
            "amount_total": amount_total,
            "amount_paid": amount_paid,
            "amount_due": amount_due,
            "approval_state": approval_state,
        }
        return request.render("payment_sepay.sepay_unc_page", values)
