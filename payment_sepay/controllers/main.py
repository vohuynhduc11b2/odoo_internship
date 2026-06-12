# -*- coding: utf-8 -*-
import base64
import json
import hmac
from html import escape
import logging
import os
from urllib.parse import quote
from werkzeug.utils import secure_filename

from odoo import http
from odoo.http import request
import requests

_logger = logging.getLogger(__name__)


def _constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def _q(s: str) -> str:
    return quote((s or "").strip())


def _format_vnd(amount):
    try:
        return f"{int(round(float(amount or 0.0))):,}".replace(",", ".") + " VND"
    except Exception:
        return "0 VND"


def _sepay_bank_info_for_order(provider, order):
    branch_code = ((getattr(getattr(order, "user_id", None), "branch", "") or "") if order else "").upper().strip()

    if branch_code == "HNI" or branch_code.startswith("HN"):
        return {
            "bank": "Vietcombank",
            "account": "1051318386",
            "branch": "Vietcombank - CN Hà Nội",
            "account_name": (provider.sepay_account_name or "CÔNG TY CỔ PHẦN TẬP ĐOÀN DAT").strip(),
        }

    return {
        "bank": "Vietcombank",
        "account": "1036936868",
        "branch": "Vietcombank - CN Kỳ Đồng",
        "account_name": (provider.sepay_account_name or "CÔNG TY CỔ PHẦN TẬP ĐOÀN DAT").strip(),
    }


class SePayController(http.Controller):

    # ==========================================================
    # SET MODE (JSON)  (FE gọi để lưu session)
    # ==========================================================
    @http.route("/payment/sepay/set_mode", type="json", auth="public", website=True, csrf=False)
    def sepay_set_mode(self, mode=None, deposit=None, percent=None, **kw):
        mode = (mode or "").strip().lower()

        try:
            percent = float(percent) if percent not in (None, "", False) else 30.0
        except Exception:
            percent = 30.0

        dep = 1 if mode == "deposit" else 0
        if deposit not in (None, "", False):
            try:
                dep = 1 if int(deposit) == 1 else 0
            except Exception:
                pass

        request.session["oms_payment_mode"] = mode
        request.session["website_payment_type"] = mode
        request.session["sepay_mode"] = ("unc" if mode == "unc" else ("credit" if mode == "credit" else "qr"))

        request.session["uc_is_deposit"] = dep
        request.session["uc_deposit_percent"] = percent

        request.session["oms_is_deposit"] = dep
        request.session["oms_deposit30"] = dep
        request.session["oms_deposit_percent"] = percent

        order_total = False
        order_pricelist_id = False
        pricelist_changed = False
        try:
            order = request.website.sale_get_order(force_create=False)
            if order and hasattr(order, "_oms_apply_strategic_payment_pricelist"):
                before_pl = order.pricelist_id.id if order.pricelist_id else False
                order.sudo()._oms_apply_strategic_payment_pricelist(mode)
                order_total = order.amount_total
                order_pricelist_id = order.pricelist_id.id if order.pricelist_id else False
                pricelist_changed = bool(order_pricelist_id and order_pricelist_id != before_pl)
        except Exception:
            _logger.exception("[SePay] cannot apply strategic pricelist for mode=%s", mode)

        _logger.info(
            "[SePay] set_mode session=%s mode=%s deposit=%s percent=%s",
            getattr(request.session, "sid", None), mode, dep, percent
        )
        return {
            "ok": True,
            "mode": mode,
            "deposit": dep,
            "percent": percent,
            "amount_total": order_total,
            "pricelist_id": order_pricelist_id,
            "pricelist_changed": pricelist_changed,
        }

    # ==========================================================
    # SET COMMENT (POST) (FE gọi để lưu session)
    # ==========================================================
    @http.route("/payment/sepay/set_comment", type="http", auth="public", website=True, methods=["POST"], csrf=False)
    def sepay_set_comment(self, comment=None, **kw):
        comment = (comment or kw.get("comment") or "").strip()
        request.session["sepay_comment"] = comment

        try:
            order = request.website.sale_get_order(force_create=False)
            if order and "client_order_ref" in order._fields and comment:
                order.sudo().write({"client_order_ref": comment})
        except Exception:
            pass

        return request.make_json_response({"ok": True, "comment": comment})

    # ==========================================================
    # UPLOAD UNC FILES (REAL FILE UPLOAD)
    # ==========================================================
    @http.route(
        "/payment/sepay/upload_unc_files",
        type="http",
        auth="public",
        website=True,
        csrf=False,
        methods=["POST"],
    )
    def upload_unc_files(self, **post):
        order = False
        order_id = int(post.get("sale_order_id") or 0)
        access_token = (post.get("access_token") or "").strip()

        # 1) Ưu tiên lấy theo order id truyền lên
        if order_id:
            order = request.env["sale.order"].sudo().browse(order_id).exists()

            # Nếu đi từ portal/public page thì kiểm tra token khi có truyền
            if order and access_token and hasattr(order, "access_token"):
                real_token = (order.access_token or "").strip()
                if real_token and real_token != access_token:
                    return request.make_json_response(
                        {
                            "ok": False,
                            "message": "Token đơn hàng không hợp lệ.",
                        },
                        status=403,
                    )

        # 2) Fallback về cart session hiện tại
        if not order:
            order = request.website.sale_get_order()

        if not order:
            _logger.warning("[SePay][UPLOAD_UNC] order not found | post=%s", post)
            return request.make_json_response(
                {
                    "ok": False,
                    "message": "Không tìm thấy đơn hàng để đính kèm file.",
                },
                status=400,
            )

        files = (
            request.httprequest.files.getlist("oms_unc_files")
            or request.httprequest.files.getlist("oms_unc_files[]")
        )

        valid_files = []
        for f in files:
            if f and getattr(f, "filename", None):
                valid_files.append(f)

        if not valid_files:
            _logger.warning("[SePay][UPLOAD_UNC] no files | order=%s", order.id)
            return request.make_json_response(
                {
                    "ok": False,
                    "message": "Vui lòng chọn file ủy nhiệm chi.",
                },
                status=400,
            )

        Attachment = request.env["ir.attachment"].sudo()
        attachment_ids = []
        filenames = []

        for storage in valid_files:
            raw_name = (storage.filename or "").strip() or "unc_file"
            safe_name = secure_filename(raw_name) or raw_name

            content = storage.read()
            if not content:
                continue

            attachment = Attachment.create({
                "name": safe_name,
                "type": "binary",
                "datas": base64.b64encode(content),
                "res_model": order._name,
                "res_id": order.id,
                "mimetype": storage.mimetype or "application/octet-stream",
            })

            attachment_ids.append(attachment.id)
            filenames.append(safe_name)

        if not attachment_ids:
            return request.make_json_response(
                {
                    "ok": False,
                    "message": "File tải lên rỗng hoặc không hợp lệ.",
                },
                status=400,
            )

        # Gắn luôn vào chatter để kế toán / sale nhìn thấy
        if hasattr(order, "message_post"):
            try:
                order.message_post(
                    body="Khách hàng đã tải lên file ủy nhiệm chi từ website.",
                    attachment_ids=attachment_ids,
                )
            except Exception:
                _logger.exception(
                    "[SePay][UPLOAD_UNC] message_post failed | order=%s | attachment_ids=%s",
                    order.id, attachment_ids
                )

        _logger.info(
            "[SePay][UPLOAD_UNC] success | order=%s | attachment_ids=%s | files=%s",
            order.id, attachment_ids, filenames
        )

        return request.make_json_response(
            {
                "ok": True,
                "message": "Tải file ủy nhiệm chi thành công.",
                "order_id": order.id,
                "attachment_ids": attachment_ids,
                "files": filenames,
            },
            status=200,
        )
    # ==========================================================
    # INTERNAL: load tx + validate token
    # ==========================================================
    def _uc_get_tx(self, reference: str):
        reference = (reference or "").strip()
        if not reference:
            return None
        tx = request.env["payment.transaction"].sudo().search(
            [("reference", "=", reference), ("provider_code", "=", "sepay")],
            limit=1,
        )
        return tx or None

    def _uc_require_token(self, tx, token: str):
        """Bắt buộc token match uc_access_token (token riêng cho website)."""
        token = (token or "").strip()
        tx_token = (getattr(tx, "uc_access_token", "") or "").strip()
        return bool(token) and bool(tx_token) and _constant_time_equal(token, tx_token)

    def _uc_get_order_for_unc_upload(self, order_id=None, order_model=None, reference=None, access_token=None):
        """
        Ưu tiên:
        1) explicit order_id/order_model từ frontend
        2) session sale_order_id / website_sale_order_id
        3) website current order
        4) tx theo reference
        """
        reference = (reference or "").strip()
        token = (access_token or "").strip()
        order_id = order_id or ""
        order_model = order_model or ""
    
        def _resolve_explicit(model_name, rec_id):
            if not model_name or not rec_id:
                return None
            if model_name not in request.env.registry.models:
                return None
    
            try:
                rec_id = int(rec_id)
            except Exception:
                return None
    
            rec = request.env[model_name].sudo().browse(rec_id).exists()
            if not rec:
                return None
    
            if token:
                rec_tok = (getattr(rec, "access_token", "") or "").strip()
                if rec_tok and not _constant_time_equal(rec_tok, token):
                    _logger.warning(
                        "[SePay][UPLOAD_UNC] explicit order token mismatch | model=%s | id=%s",
                        model_name, rec_id
                    )
                    return None
    
            return rec.sudo()
    
        # 1) explicit order từ frontend
        rec = _resolve_explicit(order_model, order_id)
        if rec:
            _logger.info(
                "[SePay][UPLOAD_UNC] use explicit order | model=%s | id=%s | name=%s",
                rec._name, rec.id, rec.name
            )
            return rec
    
        # 2) session fallback
        sid = request.session.get("sale_order_id") or request.session.get("website_sale_order_id")
        try:
            sid = int(sid) if sid else None
        except Exception:
            sid = None
    
        if sid:
            model_candidates = []
            if "sale_custom.order" in request.env.registry.models:
                model_candidates.append("sale_custom.order")
            model_candidates.append("sale.order")
    
            for model_name in model_candidates:
                rec = request.env[model_name].sudo().browse(sid).exists()
                if rec:
                    _logger.info(
                        "[SePay][UPLOAD_UNC] use session order | model=%s | id=%s | name=%s",
                        rec._name, rec.id, rec.name
                    )
                    return rec.sudo()
    
        # 3) current website order
        try:
            order = request.website.sale_get_order(force_create=False)
            if order:
                _logger.info(
                    "[SePay][UPLOAD_UNC] use current website order | model=%s | id=%s | name=%s",
                    order._name, order.id, order.name
                )
                return order.sudo()
        except Exception:
            pass
        
        # 4) tx fallback
        if reference:
            tx = self._uc_get_tx(reference)
            if tx:
                if token and not self._uc_require_token(tx, token):
                    _logger.warning("[SePay][UPLOAD_UNC] tx token invalid for reference=%s", reference)
                    return request.env["sale.order"].browse([])
    
                try:
                    order = tx._uc_get_order_for_amount()
                    if order:
                        _logger.info(
                            "[SePay][UPLOAD_UNC] use tx order | model=%s | id=%s | name=%s | tx=%s",
                            order._name, order.id, order.name, tx.reference
                        )
                        return order.sudo()
                except Exception:
                    _logger.exception("[SePay][UPLOAD_UNC] cannot resolve order from tx reference=%s", reference)
    
        return request.env["sale.order"].browse([])

    def _uc_search_unc_attachments(self, order):
        """
        Chỉ lấy file UNC do khách upload.
        Không tính CreateSO-payload / response / error / PDF report hệ thống.
        """
        if not order:
            return request.env["ir.attachment"].sudo().browse([])

        Attachment = request.env["ir.attachment"].sudo()

        model_names = {order._name}
        if order._name != "sale.order":
            model_names.add("sale.order")
        if "sale_custom.order" in request.env.registry.models:
            model_names.add("sale_custom.order")

        atts = Attachment.search([
            ("res_id", "=", order.id),
            ("res_model", "in", list(model_names)),
            ("type", "=", "binary"),
            ("name", "ilike", "UNC-"),
        ], order="id desc")

        _logger.info(
            "[SePay][UNC_ATTACH_SEARCH] order=%s id=%s model=%s count=%s names=%s",
            getattr(order, "name", ""),
            order.id,
            order._name,
            len(atts),
            atts.mapped("name"),
        )
        return atts

    # ==========================================================
    # INTERNAL: resolve mode thật sự (chống mất sepay_mode hidden input)
    # ==========================================================
    def _uc_resolve_mode(self, tx, sepay_mode=None, **kw):
        """
        Ưu tiên:
        1) tham số route (sepay_mode/kw)
        2) request.params (GET+POST merged)
        3) session
        4) tx.sepay_mode
        """
        candidates = [
            sepay_mode,
            kw.get("sepay_mode"),
            kw.get("mode"),
            kw.get("oms_payment_mode"),
            kw.get("website_payment_type"),
            kw.get("payment_type"),
        ]

        try:
            params = getattr(request, "params", None) or {}
            if isinstance(params, dict):
                candidates += [
                    params.get("sepay_mode"),
                    params.get("oms_payment_mode"),
                    params.get("website_payment_type"),
                    params.get("payment_type"),
                ]
        except Exception:
            pass

        try:
            sess = getattr(request, "session", None)
            if sess:
                candidates += [
                    sess.get("sepay_mode"),
                    sess.get("oms_payment_mode"),
                    sess.get("website_payment_type"),
                ]
        except Exception:
            pass

        candidates.append(getattr(tx, "sepay_mode", None))

        for c in candidates:
            try:
                if hasattr(tx, "_sepay_parse_mode"):
                    m = tx._sepay_parse_mode(c)
                else:
                    m = (c or "").strip().lower()
            except Exception:
                m = None
            if m in ("qr", "unc", "credit"):
                return m

        return "qr"

    # ==========================================================
    # CONTINUE PAYMENT FROM PORTAL ORDER
    # ==========================================================
    @http.route("/my/orders/<int:order_id>/pay", type="http", auth="public", website=True, methods=["GET"], csrf=False)
    def portal_continue_payment(self, order_id, access_token=None, **kw):
        tok = (access_token or kw.get("access_token") or "").strip()

        _logger.info(
            "HIT /my/orders/%s/pay tok=%s user=%s public=%s",
            order_id, (tok[:6] + "..." if tok else ""), request.env.user.id, request.env.user._is_public()
        )

        model_candidates = []
        if "sale_custom.order" in request.env.registry.models:
            model_candidates.append("sale_custom.order")
        model_candidates.append("sale.order")

        order = None
        for m in model_candidates:
            rec = request.env[m].sudo().browse(order_id).exists()
            if not rec:
                continue

            rec_tok = (getattr(rec, "access_token", "") or "").strip()
            if tok and rec_tok and tok == rec_tok:
                order = rec
                break

            if order is None:
                order = rec

        if not order:
            _logger.info("PAY: order_id=%s not found in %s", order_id, model_candidates)
            return request.not_found()

        _logger.info(
            "PAY: picked model=%s id=%s name=%s order_tok=%s",
            order._name, order.id, getattr(order, "name", ""),
            ((getattr(order, "access_token", "") or "")[:6] + "...")
        )

        if request.env.user._is_public():
            order_tok = (getattr(order, "access_token", "") or "").strip()
            if not tok or not order_tok or tok != order_tok:
                _logger.info("PAY: public token mismatch order_tok=%s", (order_tok[:6] + "..." if order_tok else ""))
                return request.not_found()
        else:
            same_cp = (order.partner_id.commercial_partner_id == request.env.user.partner_id.commercial_partner_id)
            order_tok = (getattr(order, "access_token", "") or "").strip()
            token_ok = tok and order_tok and tok == order_tok
            if not (same_cp or token_ok):
                _logger.info("PAY: user access denied same_cp=%s token_ok=%s", same_cp, token_ok)
                return request.not_found()

        pay_kind = (kw.get("pay_kind") or kw.get("payment_kind") or kw.get("kind") or "").strip().lower()
        explicit_payment_kind = pay_kind in ("deposit", "coc", "downpayment", "remaining", "balance", "full")
        desired_is_deposit = pay_kind in ("deposit", "coc", "downpayment")
        if explicit_payment_kind:
            request.session["sepay_mode"] = "qr"
            request.session["oms_payment_mode"] = "deposit" if desired_is_deposit else "full"
            request.session["website_payment_type"] = "deposit" if desired_is_deposit else "full"
            request.session["uc_is_deposit"] = 1 if desired_is_deposit else 0
            request.session["oms_is_deposit"] = 1 if desired_is_deposit else 0
            request.session["oms_deposit30"] = 1 if desired_is_deposit else 0
            try:
                pct = float(getattr(order, "prepayment_percent", 0.0) or 0.0) * 100.0
                if pct > 0:
                    request.session["uc_deposit_percent"] = pct
                    request.session["oms_deposit_percent"] = pct
            except Exception:
                pass

        Tx = request.env["payment.transaction"].sudo()
        dom = [("provider_code", "=", "sepay"), ("state", "in", ("draft", "pending", "authorized"))]

        if "sale_order_ids" in Tx._fields:
            dom += [("sale_order_ids", "in", [order.id])]
        elif "sale_order_id" in Tx._fields:
            dom += [("sale_order_id", "=", order.id)]
        elif "sale_custom_order_id" in Tx._fields:
            dom += [("sale_custom_order_id", "=", order.id)]
        elif "sale_custom_order_ids" in Tx._fields:
            dom += [("sale_custom_order_ids", "in", [order.id])]
        else:
            if getattr(order, "name", False):
                dom += [("reference", "ilike", order.name)]

        txs = Tx.search(dom, order="create_date desc, id desc", limit=10)
        tx = txs[:1]
        if explicit_payment_kind:
            tx = txs.filtered(lambda t: bool(getattr(t, "uc_is_deposit", False)) == desired_is_deposit)[:1]
        if tx:
            tx_token = (getattr(tx, "uc_access_token", "") or "").strip()
            if not tx_token and hasattr(tx, "_uc_get_access_token"):
                tx_token = tx._uc_get_access_token()

            sess_mode = (
                request.session.get("sepay_mode")
                or request.session.get("website_payment_type")
                or request.session.get("oms_payment_mode")
                or ""
            ).strip().lower()

            if sess_mode in ("unc", "credit") and (getattr(tx, "sepay_mode", "qr") or "qr") != sess_mode:
                try:
                    if hasattr(tx, "_sepay_sync_mode_if_needed"):
                        tx._sepay_sync_mode_if_needed(sess_mode)
                    else:
                        tx.sudo().write({"sepay_mode": sess_mode})
                except Exception:
                    _logger.exception("[SePay][PAY] cannot sync tx mode tx=%s sess_mode=%s", tx.reference, sess_mode)

            mode = (getattr(tx, "sepay_mode", "qr") or "qr").strip().lower()

            # CHỈ CHẶN UNC
            if mode == "unc" and not self._uc_has_order_attachment(order):
                _logger.warning(
                    "[SePay][PAY] blocked because missing UNC attachment | order=%s id=%s",
                    order.name, order.id
                )
                return self._uc_redirect_order_missing_attachment(order)

            _logger.info(
                "[SePay][PAY] redirect existing tx | order=%s tx=%s mode=%s",
                order.name, tx.reference, mode
            )

            if mode in ("unc", "credit"):
                return request.redirect(
                    f"/payment/sepay/unc?reference={_q(tx.reference)}&access_token={_q(tx_token)}&sepay_mode={_q(mode)}"
                )

            return request.redirect(
                f"/payment/sepay/pay?reference={_q(tx.reference)}&access_token={_q(tx_token)}&sepay_mode={_q(mode)}"
            )

        # Không có tx → tạo mới và redirect thẳng đến SePay QR
        provider_sudo = request.env['payment.provider'].sudo().search(
            [('code', '=', 'sepay'), ('state', 'in', ('enabled', 'test'))],
            limit=1,
        )

        if not provider_sudo:
            _logger.warning("[SePay][PAY] no SePay provider | order=%s", order.name)
            order_tok = (getattr(order, 'access_token', '') or '').strip()
            return request.redirect(f"/my/orders/{order.id}?access_token={_q(order_tok)}")

        logged_in = not request.env.user._is_public()
        partner_sudo = (
            request.env.user.partner_id if logged_in
            else getattr(order, 'partner_invoice_id', None) or order.partner_id
        )

        if explicit_payment_kind and desired_is_deposit:
            try:
                amount = float(order._get_prepayment_required_amount())
            except Exception:
                amount = float(order.amount_total or 0.0)
        else:
            try:
                paid = float(getattr(order, 'amount_paid', 0.0) or 0.0)
                amount = max(float(order.amount_total or 0.0) - paid, 0.0)
            except Exception:
                amount = float(order.amount_total or 0.0)

        if amount <= 0.0:
            amount = float(order.amount_total or 0.0)

        ref_base = getattr(order, 'name', '') or str(order.id)
        try:
            reference = request.env['payment.transaction'].sudo()._compute_reference(
                provider_sudo.code, prefix=ref_base
            )
        except Exception:
            import time as _time
            reference = f"{ref_base}-{int(_time.time())}"

        payment_method_sudo = request.env['payment.method'].sudo().search(
            [('provider_ids', 'in', [provider_sudo.id])],
            limit=1,
        )

        tx_vals = {
            'provider_id': provider_sudo.id,
            'reference': reference,
            'amount': amount,
            'currency_id': order.currency_id.id,
            'partner_id': partner_sudo.id,
            'operation': 'online_direct',
        }
        if payment_method_sudo:
            tx_vals['payment_method_id'] = payment_method_sudo.id

        if 'sale_order_ids' in request.env['payment.transaction']._fields:
            from odoo.fields import Command as _Cmd
            tx_vals['sale_order_ids'] = [_Cmd.set([order.id])]

        tx_sudo = request.env['payment.transaction'].sudo().create(tx_vals)

        if explicit_payment_kind and 'uc_is_deposit' in tx_sudo._fields:
            tx_sudo.write({'uc_is_deposit': 1 if desired_is_deposit else 0})

        tx_tok = (getattr(tx_sudo, 'uc_access_token', '') or '').strip()
        if not tx_tok and hasattr(tx_sudo, '_uc_get_access_token'):
            tx_tok = tx_sudo._uc_get_access_token()

        _logger.info(
            "[SePay][PAY] created new tx | order=%s tx=%s amount=%.0f",
            order.name, tx_sudo.reference, amount
        )
        return request.redirect(
            f"/payment/sepay/pay?reference={_q(tx_sudo.reference)}&access_token={_q(tx_tok)}&sepay_mode=qr"
        )

    # ==========================================================
    # QR PAGE
    # ==========================================================
    @http.route("/payment/sepay/pay", type="http", auth="public", website=True, methods=["GET", "POST"], csrf=False)
    def sepay_pay_page(self, reference=None, access_token=None, sepay_mode=None, sepay_description=None, **kw):
        reference = (reference or kw.get("reference") or "").strip()
        token = (access_token or kw.get("access_token") or "").strip()

        tx = self._uc_get_tx(reference)
        if not tx:
            return request.not_found()

        if not self._uc_require_token(tx, token):
            return request.not_found()

        provider = tx.provider_id.sudo()
        if not provider:
            return request.not_found()

        mode = self._uc_resolve_mode(tx, sepay_mode=sepay_mode, **kw)
        try:
            if hasattr(tx, "_sepay_sync_mode_if_needed"):
                tx._sepay_sync_mode_if_needed(mode)
            else:
                if (getattr(tx, "sepay_mode", "qr") or "qr") != mode:
                    tx.sudo().write({"sepay_mode": mode})
        except Exception:
            pass

        if mode in ("unc", "credit"):
            tx_token = (getattr(tx, "uc_access_token", "") or "").strip()
            return request.redirect(
                f"/payment/sepay/unc?reference={_q(tx.reference)}&access_token={_q(tx_token)}&sepay_mode={_q(mode)}"
            )

        if tx.state == "draft":
            tx._set_pending()

        orders = request.env["sale.order"].sudo().browse([])
        try:
            if "sale_order_ids" in tx._fields:
                orders = tx.sale_order_ids.sudo().exists()
            elif "sale_order_id" in tx._fields:
                orders = tx.sale_order_id.sudo().exists()
        except Exception:
            orders = request.env["sale.order"].sudo().browse([])

        if not orders:
            try:
                cur = request.website.sale_get_order(force_create=False)
                if cur:
                    orders = cur.sudo()
            except Exception:
                orders = request.env["sale.order"].sudo().browse([])

        self._uc_clear_cart_session()

        description = (sepay_description or kw.get("sepay_description") or "").strip()
        if not description and hasattr(tx, "_sepay_build_qr_note"):
            description = tx._sepay_build_qr_note()

        bank_info = _sepay_bank_info_for_order(provider, orders[:1])
        sepay_qr_url = provider._sepay_qr_image_url(
            amount=tx.amount,
            description=description,
            bank=bank_info["bank"],
            account=bank_info["account"],
        )
        tx_token = getattr(tx, "uc_access_token", "") or ""
        sepay_qr_download_url = (
            f"/payment/sepay/qr-download?reference={_q(tx.reference)}"
            f"&access_token={_q(tx_token)}"
        )

        values = {
            "tx": tx,
            "reference": tx.reference,
            "amount": tx.amount,
            "currency": tx.currency_id.name if tx.currency_id else "",
            "provider_display": provider.display_name,
            "sepay_description": description,
            "sepay_qr_url": sepay_qr_url,
            "sepay_qr_download_url": sepay_qr_download_url,
            "sepay_mode": mode,
            "access_token": tx_token,
        }
        return request.render("payment_sepay.sepay_payment_page", values)

    @http.route("/payment/sepay/qr-download", type="http", auth="public", website=True, methods=["GET"], csrf=False)
    def sepay_qr_download(self, reference=None, access_token=None, **kw):
        reference = (reference or "").strip()
        token = (access_token or "").strip()

        tx = self._uc_get_tx(reference)
        if not tx:
            return request.not_found()
        if not self._uc_require_token(tx, token):
            return request.not_found()

        provider = tx.provider_id.sudo()
        order = tx._uc_get_order_for_amount() if hasattr(tx, "_uc_get_order_for_amount") else False
        bank_info = _sepay_bank_info_for_order(provider, order)
        description = tx._sepay_build_qr_note() if hasattr(tx, "_sepay_build_qr_note") else tx.reference
        qr_url = provider._sepay_qr_image_url(
            amount=tx.amount,
            description=description,
            template="qronly",
            bank=bank_info["bank"],
            account=bank_info["account"],
        )

        qr_data_uri = ""
        try:
            res = requests.get(qr_url, timeout=10)
            res.raise_for_status()
            content_type = res.headers.get("Content-Type") or "image/png"
            qr_data_uri = f"data:{content_type};base64,{base64.b64encode(res.content).decode('ascii')}"
        except Exception:
            _logger.exception("[SePay] Cannot fetch QR image for download tx=%s", tx.reference)
            qr_data_uri = escape(qr_url)

        partner = (order.partner_id if order else tx.partner_id) or request.env["res.partner"].sudo().browse([])
        customer_name = partner.display_name or partner.name or ""
        account = bank_info["account"]
        account_name = bank_info["account_name"]
        branch = bank_info["branch"]

        qr_src = qr_data_uri if qr_data_uri.startswith("data:") else qr_url
        html = f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <style>
    @page {{ size: A4; margin: 12mm; }}
    body {{
      margin: 0;
      background: #f4f7fb;
      font-family: Arial, DejaVu Sans, sans-serif;
      color: #0f172a;
    }}
    .page {{
      width: 148mm;
      margin: 0 auto;
      background: #fff;
      border: 1px solid #d8e2ef;
      border-radius: 22px;
      overflow: hidden;
    }}
    .header {{
      background: #1765ad;
      color: #fff;
      text-align: center;
      padding: 22px 20px 20px;
    }}
    .header h1 {{
      margin: 0;
      font-size: 26px;
      line-height: 1.2;
      letter-spacing: .2px;
    }}
    .header div {{
      margin-top: 6px;
      font-size: 13px;
      color: #eaf3ff;
    }}
    .content {{
      padding: 18px 34px 28px;
      text-align: center;
    }}
    .bank {{
      font-size: 20px;
      font-weight: 700;
      margin-top: 0;
    }}
    .account-name {{
      margin-top: 4px;
      font-size: 13px;
      color: #3f5b8c;
      text-transform: uppercase;
    }}
    .qr-box {{
      width: 245px;
      height: 245px;
      margin: 28px auto 12px;
      border: 1px solid #d8e2ef;
      border-radius: 18px;
      background: #f8fafc;
      padding: 18px;
      box-sizing: border-box;
    }}
    .qr-box img {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }}
    .account {{
      font-size: 26px;
      font-weight: 700;
      margin-top: 8px;
    }}
    .hint {{
      font-size: 13px;
      color: #64748b;
      margin-top: 5px;
    }}
    .info {{
      margin: 22px auto 0;
      width: 420px;
      border: 1px solid #d8e2ef;
      border-radius: 14px;
      background: #f8fafc;
      padding: 12px 20px;
      box-sizing: border-box;
      text-align: left;
    }}
    .row {{
      display: table;
      width: 100%;
      min-height: 34px;
      border-bottom: 1px solid #d8e2ef;
      padding: 8px 0;
    }}
    .row:last-child {{ border-bottom: 0; }}
    .label {{
      display: table-cell;
      width: 105px;
      color: #536482;
      font-size: 13px;
      vertical-align: top;
    }}
    .value {{
      display: table-cell;
      font-size: 14px;
      font-weight: 700;
      vertical-align: top;
      word-break: break-word;
    }}
    .note {{
      margin: 14px auto 0;
      width: 420px;
      border: 1px solid #d8e2ef;
      border-radius: 12px;
      padding: 12px 20px;
      box-sizing: border-box;
      text-align: left;
      font-size: 12px;
      line-height: 1.45;
      color: #334155;
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="header">
      <h1>QR THANH TOÁN</h1>
      <div>Dùng camera ngân hàng hoặc ví điện tử để quét mã</div>
    </div>
    <div class="content">
      <div class="bank">{escape(branch)}</div>
      <div class="account-name">{escape(account_name)}</div>
      <div class="qr-box"><img src="{qr_src}" alt="QR"/></div>
      <div class="account">{escape(account)}</div>
      <div class="hint">Quét mã để chuyển khoản nhanh</div>
      <div class="info">
        <div class="row">
          <div class="label">Khách hàng:</div>
          <div class="value">{escape(customer_name)}</div>
        </div>
        <div class="row">
          <div class="label">Số tiền:</div>
          <div class="value">{escape(_format_vnd(tx.amount))}</div>
        </div>
        <div class="row">
          <div class="label">Nội dung:</div>
          <div class="value">{escape(description)}</div>
        </div>
      </div>
      <div class="note">
        Nếu thanh toán bằng Internet Banking hoặc Web ngân hàng, vui lòng nhập đúng nội dung chuyển khoản bên trên.
      </div>
    </div>
  </div>
</body>
</html>'''

        pdf = request.env["ir.actions.report"].sudo()._run_wkhtmltopdf(
            [html],
            specific_paperformat_args={
                "margin_top": 12,
                "margin_bottom": 12,
                "margin_left": 12,
                "margin_right": 12,
            },
        )

        filename = f"QR-{description or tx.reference}.pdf"
        headers = [
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
        ]
        return request.make_response(pdf, headers=headers)

    # ==========================================================
    # UNC/CREDIT PENDING PAGE
    # ==========================================================
    def _uc_save_unc_files(self, order):
        files = request.httprequest.files.getlist("oms_unc_files")
        attachment_ids = []

        if not order:
            _logger.warning("[SePay][UNC_UPLOAD] no order to attach files")
            return attachment_ids

        if not files:
            _logger.warning("[SePay][UNC_UPLOAD] no files received | order=%s", order.display_name)
            return attachment_ids

        Attachment = request.env["ir.attachment"].sudo()

        for f in files:
            if not f:
                continue

            filename = (getattr(f, "filename", "") or "").strip() or "unc_file"
            content = f.read()
            if not content:
                continue

            att = Attachment.create({
                "name": f"UNC-{order.name}-{filename}",
                "type": "binary",
                "datas": base64.b64encode(content),
                "res_model": order._name,
                "res_id": order.id,
                "mimetype": getattr(f, "mimetype", False) or "application/octet-stream",
            })
            attachment_ids.append(att.id)

        if attachment_ids:
            order.sudo().message_post(
                body="Khách hàng đã tải lên phiếu ủy nhiệm chi.",
                attachment_ids=attachment_ids,
            )

        _logger.info(
            "[SePay][UNC_UPLOAD] saved | order=%s | attachment_ids=%s",
            order.display_name,
            attachment_ids,
        )
        return attachment_ids
    @http.route("/payment/sepay/unc", type="http", auth="public", website=True, methods=["GET", "POST"], csrf=False)
    def sepay_unc_page(self, reference=None, access_token=None, sepay_mode=None, sepay_description=None, **kw):
        reference = (reference or "").strip()
        access_token = (access_token or "").strip()

        _logger.info(
            "[SePay][UNC_PAGE] reference=%s token=%s mode=%s method=%s kw=%s",
            reference,
            (access_token[:6] + "...") if access_token else "",
            sepay_mode,
            request.httprequest.method,
            kw,
        )

        tx = request.env["payment.transaction"].sudo().search([
            ("reference", "=", reference),
            ("provider_code", "=", "sepay"),
        ], order="id desc", limit=1)

        if not tx:
            _logger.warning("[SePay][UNC_PAGE] transaction not found reference=%s", reference)
            return request.redirect("/shop/payment")

        token_ok = _constant_time_equal(tx.uc_access_token or "", access_token or "")
        if not token_ok:
            _logger.warning("[SePay][UNC_PAGE] invalid token for tx=%s", tx.reference)
            return request.redirect("/shop/payment")

        mode = self._uc_resolve_mode(tx, sepay_mode=sepay_mode, **kw)
        if mode not in ("unc", "credit"):
            mode = "unc"

        try:
            if hasattr(tx, "_sepay_sync_mode_if_needed"):
                tx._sepay_sync_mode_if_needed(mode)
            else:
                if (getattr(tx, "sepay_mode", "qr") or "qr") != mode:
                    tx.sudo().write({"sepay_mode": mode})
        except Exception:
            _logger.exception("[SePay][UNC_PAGE] cannot sync mode tx=%s mode=%s", tx.reference, mode)

        try:
            if tx.state == "draft":
                tx._set_pending()
        except Exception:
            _logger.exception("[SePay][UNC_PAGE] cannot set pending tx=%s", tx.reference)

        order = tx._uc_get_order_for_amount()

        # ==========================================================
        # NEW: nếu là POST + mode UNC thì nhận file và lưu luôn tại đây
        # ==========================================================
        if order and mode == "unc" and request.httprequest.method == "POST":
            try:
                received_files = request.httprequest.files.getlist("oms_unc_files")
                _logger.info(
                    "[SePay][UNC_PAGE] POST upload attempt | order=%s | file_count=%s",
                    order.display_name,
                    len(received_files or []),
                )
                self._uc_save_unc_files(order)
            except Exception:
                _logger.exception(
                    "[SePay][UNC_PAGE] failed to save uploaded files | order=%s | tx=%s",
                    order.display_name if order else None,
                    tx.reference,
                )

        att_ok = True
        att_count = 0
        if order and mode == "unc":
            atts = self._uc_search_unc_attachments(order)
            att_count = len(atts)
            att_ok = bool(att_count)

            if not att_ok:
                _logger.warning(
                    "[SePay][UNC_PAGE] missing UNC attachment | order=%s tx=%s",
                    order.display_name, tx.reference
                )
                return self._uc_redirect_order_missing_attachment(order)

        try:
            if order:
                self._uc_finalize_pending_approval_once(order, tx=tx, mode=mode)
                _logger.info(
                    "[SePay][UNC_PAGE] approval flow triggered | SO=%s | mode=%s | approval_state=%s",
                    order.display_name,
                    mode,
                    getattr(order, "approval_state", False),
                )
            else:
                _logger.warning("[SePay][UNC_PAGE] no order resolved from tx=%s", tx.reference)
        except Exception:
            _logger.exception(
                "[SePay][UNC_PAGE] failed to trigger approval | tx=%s | mode=%s",
                tx.reference, mode
            )

        values = {
            "tx": tx,
            "order": order,
            "reference": tx.reference,
            "access_token": access_token,
            "sepay_mode": mode,
            "sepay_description": sepay_description or "",
            "is_deposit": bool(tx.uc_is_deposit),
            "deposit_percent": float(tx.uc_deposit_percent or 30.0),
            "att_ok": att_ok,
            "att_count": att_count,
        }
        return request.render("payment_sepay.sepay_unc_page", values)

    # ==========================================================
    # RETURN
    # ==========================================================
    @http.route("/payment/sepay/return", type="http", auth="public", website=True, methods=["GET"], csrf=False)
    def sepay_return(self, reference=None, access_token=None, **kw):
        reference = (reference or kw.get("reference") or "").strip()
        token = (access_token or kw.get("access_token") or "").strip()

        tx = self._uc_get_tx(reference)
        if not tx:
            return request.redirect("/shop/payment")

        if token and not self._uc_require_token(tx, token):
            return request.not_found()

        mode = (getattr(tx, "sepay_mode", "qr") or "qr").strip().lower()
        tx_token = _q(getattr(tx, "uc_access_token", "") or "")

        if tx.state == "done":
            orders = request.env["sale.order"].sudo().browse([])
            try:
                if "sale_order_ids" in tx._fields:
                    orders = tx.sale_order_ids.sudo().exists()
                elif "sale_order_id" in tx._fields:
                    orders = tx.sale_order_id.sudo().exists()
            except Exception:
                orders = request.env["sale.order"].sudo().browse([])

            for so in orders:
                self._uc_finalize_order_if_match_session(so, reason="paid", tx=tx)
            return request.redirect("/shop/confirmation")

        if mode in ("unc", "credit"):
            return request.redirect(f"/payment/sepay/unc?reference={_q(tx.reference)}&access_token={tx_token}&sepay_mode={_q(mode)}")

        return request.redirect(f"/payment/sepay/pay?reference={_q(tx.reference)}&access_token={tx_token}&sepay_mode=qr")

    # ==========================================================
    # STATUS (polling)
    # ==========================================================
    @http.route("/payment/sepay/status", type="json", auth="public", methods=["POST"], csrf=False)
    def sepay_status(self, reference=None, access_token=None, **kw):
        reference = (reference or "").strip()
        token = (access_token or kw.get("access_token") or "").strip()
        tx = self._uc_get_tx(reference)
        if not tx:
            return {"ok": False, "state": "not_found"}
        tx_token = (getattr(tx, "uc_access_token", "") or "").strip()
        if tx_token and not self._uc_require_token(tx, token):
            return {"ok": False, "state": "forbidden"}
        return {
            "ok": True,
            "state": tx.state,
            "reference": tx.reference,
            "is_done": tx.state == "done",
        }

    # ==========================================================
    # WEBHOOK (chỉ finalize QR)
    # ==========================================================
    def _uc_sepay_webhook_payload(self, payload):
        payload = dict(payload or {})
        if payload:
            return payload

        req = request.httprequest
        try:
            if req.is_json:
                data = req.get_json(silent=True) or {}
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

        try:
            data = json.loads((req.get_data(as_text=True) or "").strip() or "{}")
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        return dict(req.form or {})

    def _uc_sepay_reference_candidates(self, payload):
        candidates = [
            payload.get("reference"),
            payload.get("remark"),
            payload.get("code"),
            payload.get("content"),
            payload.get("description"),
        ]
        return [str(c or "").strip() for c in candidates if str(c or "").strip()]

    def _uc_find_sepay_tx_from_payload(self, payload):
        Tx = request.env["payment.transaction"].sudo()
        candidates = self._uc_sepay_reference_candidates(payload)

        for value in candidates:
            tx = self._uc_get_tx(value)
            if tx:
                return tx

        domain = [("provider_code", "=", "sepay"), ("state", "in", ("draft", "pending", "authorized"))]
        for value in candidates:
            tx = Tx.search(domain + [("reference", "ilike", value)], order="id desc", limit=1)
            if tx:
                return tx

        content = " ".join(candidates)
        if content:
            txs = Tx.search(domain, order="id desc", limit=80)
            for tx in txs:
                note = tx._sepay_build_qr_note() if hasattr(tx, "_sepay_build_qr_note") else ""
                if note and note in content:
                    return tx
        return Tx.browse([])

    def _uc_sepay_webhook_amount(self, payload):
        for key in ("transferAmount", "amount", "money", "value"):
            val = payload.get(key)
            if val not in (None, "", False):
                return val
        return None

    @http.route("/payment/sepay/webhook", type="http", auth="public", methods=["POST"], csrf=False)
    def sepay_webhook(self, **payload):
        payload = self._uc_sepay_webhook_payload(payload)
        api_key = (
            request.httprequest.headers.get("X-API-Key", "")
            or request.httprequest.headers.get("Authorization", "").replace("Apikey", "").strip()
        )

        transfer_type = (payload.get("transferType") or "").strip().lower()
        if transfer_type and transfer_type != "in":
            return request.make_json_response({"success": True, "status": "ignored", "message": "not_inbound"})

        tx = self._uc_find_sepay_tx_from_payload(payload)
        if not tx:
            return request.make_json_response({"success": True, "status": "ignored", "message": "tx_not_found"})

        status = (payload.get("status") or "success").strip().lower()
        amount = self._uc_sepay_webhook_amount(payload)


        mode = (getattr(tx, "sepay_mode", "qr") or "qr").strip().lower()
        if mode in ("unc", "credit"):
            return request.make_json_response({"success": True, "status": "ignored", "message": "not_qr_flow"})

        provider_key = (tx.provider_id.sepay_api_key or "").strip()
        if provider_key and not _constant_time_equal(api_key, provider_key):
            return request.make_json_response({"success": False, "status": "unauthorized"}, status=401)

        if status not in ("success", "paid", "done", "completed"):
            return request.make_json_response({"success": True, "status": "ignored", "message": f"status={status}"})

        if amount not in (None, "", False):
            try:
                if float(amount) + 0.01 < float(tx.amount):
                    return request.make_json_response({"success": False, "status": "error", "message": "amount_mismatch"}, status=400)
            except Exception:
                pass

        if tx.state == "draft":
            tx._set_pending()

        if tx.state != "done":
            if hasattr(tx, "action_sepay_mark_done"):
                tx.action_sepay_mark_done()
            else:
                tx._set_done()
                if hasattr(tx, "_post_process_after_done"):
                    tx._post_process_after_done()

        return request.make_json_response({"success": True, "status": "ok", "reference": tx.reference})

    # ==========================================================
    # HELPERS: CLEAR CART & FINALIZE
    # ==========================================================
    def _uc_clear_cart_session(self):
        """Clear cart session để khách tiếp tục đặt đơn mới."""
        try:
            if hasattr(request.website, "sale_reset"):
                request.website.sale_reset()
        except Exception:
            pass

        for k in ("sale_order_id", "website_sale_order_id", "website_sale_cart_quantity"):
            request.session.pop(k, None)
        request.session["website_sale_cart_quantity"] = 0

    def _uc_clear_cart_if_match_session(self, order):
        """Clear cart nếu session đang trỏ đúng order này."""
        if not order:
            return False

        oid = order.id

        current = None
        try:
            current = request.website.sale_get_order(force_create=False)
        except Exception:
            current = None

        if current and current.id == oid:
            self._uc_clear_cart_session()
            return True

        sid = request.session.get("sale_order_id")
        try:
            sid = int(sid) if sid else None
        except Exception:
            sid = None

        if sid and sid == oid:
            self._uc_clear_cart_session()
            return True

        return False

    def _uc_finalize_pending_approval_once(self, order, tx=None, mode="unc"):
        """UNC/Credit: gửi duyệt 1 lần, tránh refresh trang gọi lặp."""
        if not order:
            return False

        order = order.sudo()
        mode = (mode or "unc").strip().lower()

        approval_state = str(getattr(order, "approval_state", "") or "").strip().lower()

        sent_states = {
            "sent",
            "submitted",
            "to_approve",
            "waiting_approval",
            "waiting_credit",
            "waiting_credit_approval",
            "credit_review",
            "pending_approval",
        }

        if approval_state in sent_states:
            _logger.info(
                "[SePay] skip finalize pending approval | SO=%s | approval_state=%s | mode=%s",
                order.display_name, approval_state, mode
            )
            if tx and "sepay_sent_for_approval" in tx._fields and not tx.sepay_sent_for_approval:
                try:
                    tx.sudo().write({"sepay_sent_for_approval": True})
                except Exception:
                    _logger.exception("[SePay] cannot set sepay_sent_for_approval for tx=%s", tx.reference)
            self._uc_clear_cart_if_match_session(order)
            return True

        if tx and "sepay_sent_for_approval" in tx._fields and tx.sepay_sent_for_approval:
            _logger.info(
                "[SePay] skip finalize pending approval by tx flag | SO=%s | tx=%s | mode=%s",
                order.display_name, tx.reference, mode
            )
            self._uc_clear_cart_if_match_session(order)
            return True

        if "uc_website_finalized" in order._fields and order.uc_website_finalized:
            _logger.info(
                "[SePay] skip finalize pending approval by uc_website_finalized | SO=%s | mode=%s",
                order.display_name, mode
            )
            if tx and "sepay_sent_for_approval" in tx._fields and not tx.sepay_sent_for_approval:
                try:
                    tx.sudo().write({"sepay_sent_for_approval": True})
                except Exception:
                    _logger.exception("[SePay] cannot set sepay_sent_for_approval for tx=%s", tx.reference)
            self._uc_clear_cart_if_match_session(order)
            return True

        _logger.info(
            "[SePay] finalize pending approval | SO=%s | mode=%s | tx=%s",
            order.display_name, mode, getattr(tx, "reference", None)
        )

        ok = False

        try:
            if hasattr(order, "uc_website_finalize_send_approval"):
                ok = bool(order.uc_website_finalize_send_approval(
                    reason="other",
                    payment_tx=tx
                ))
            elif hasattr(order, "action_send_for_approval"):
                order.action_send_for_approval()
                ok = True
            else:
                _logger.warning("[SePay] order=%s has no approval method", order.display_name)
                ok = False
        except Exception:
            _logger.exception(
                "[SePay] send approval failed | SO=%s | mode=%s | tx=%s",
                order.display_name, mode, getattr(tx, "reference", None)
            )
            ok = False

        if ok:
            if "uc_website_finalized" in order._fields and not order.uc_website_finalized:
                try:
                    order.write({"uc_website_finalized": True})
                except Exception:
                    _logger.exception("[SePay] cannot set uc_website_finalized for SO=%s", order.display_name)

            if tx and "sepay_sent_for_approval" in tx._fields and not tx.sepay_sent_for_approval:
                try:
                    tx.sudo().write({"sepay_sent_for_approval": True})
                except Exception:
                    _logger.exception("[SePay] cannot set sepay_sent_for_approval for tx=%s", tx.reference)

            self._uc_clear_cart_if_match_session(order)

        return ok

    def _uc_has_order_attachment(self, order):
        """Chỉ tính file UNC khách upload, không tính log/pdf hệ thống."""
        if not order:
            return False

        atts = self._uc_search_unc_attachments(order)

        _logger.info(
            "[SePay][ATTACH_UNC] order=%s id=%s has_unc=%s att_ids=%s",
            getattr(order, "name", ""),
            order.id,
            bool(atts),
            atts.ids,
        )
        return bool(atts)

    def _uc_redirect_order_missing_attachment(self, order):
        """Redirect về trang đơn khi chưa có file UNC."""
        order_token = (getattr(order, "access_token", "") or "").strip()
        if order_token:
            return request.redirect(
                f"/my/orders/{order.id}?access_token={_q(order_token)}&missing_attachment=1"
            )
        return request.redirect(f"/my/orders/{order.id}?missing_attachment=1")
