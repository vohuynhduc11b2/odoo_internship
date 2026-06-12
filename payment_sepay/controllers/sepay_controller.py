# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, MissingError


class SePayApprovalController(http.Controller):

    @http.route("/shop/approval/<int:order_id>", type="http", auth="public", website=True, methods=["GET"], csrf=False)
    def shop_approval_page(self, order_id, access_token=None, **kw):
        access_token = (access_token or "").strip()
        if not access_token:
            return request.redirect("/shop/payment")

        # 1) check access theo access_token (public portal)
        try:
            order_sudo = request.env["ir.http"].sudo()._document_check_access(
                "sale_custom.order", order_id, access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/shop/payment")

        # 2) tìm tx sepay theo field link thật sự đang có trên payment.transaction
        Tx = request.env["payment.transaction"].sudo()
        dom = [("provider_code", "=", "sepay")]

        if "sale_custom_order_id" in Tx._fields:
            dom.append(("sale_custom_order_id", "=", order_sudo.id))
        elif "sale_custom_order_ids" in Tx._fields:
            dom.append(("sale_custom_order_ids", "in", [order_sudo.id]))
        elif "sale_order_id" in Tx._fields:
            dom.append(("sale_order_id", "=", order_sudo.id))
        elif "sale_order_ids" in Tx._fields:
            dom.append(("sale_order_ids", "in", [order_sudo.id]))
        else:
            # fallback cuối cùng (nếu không có field link)
            if getattr(order_sudo, "name", False):
                dom.append(("reference", "ilike", order_sudo.name))

        txs = Tx.search(dom, order="id desc")

        # 3) best-effort approval state
        approval_state = None
        for cand in ("approval_state", "x_approval_state", "state_approval"):
            if hasattr(order_sudo, cand):
                approval_state = getattr(order_sudo, cand)
                break

        values = {
            "order": order_sudo,
            "txs": txs,
            "approval_state": approval_state,
            "partner_debit": getattr(order_sudo.partner_id, "debit", None),
            "partner_credit": getattr(order_sudo.partner_id, "credit", None),
            "access_token": access_token,
        }
        return request.render("payment_sepay.sepay_approval_page", values)