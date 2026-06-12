# File: controllers/oms_api.py
# -*- coding: utf-8 -*-
import hmac
import json
import logging

from odoo import http, _, fields
from odoo.http import request
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _constant_time_equal(a: str, b: str) -> bool:
    a = (a or "").encode("utf-8")
    b = (b or "").encode("utf-8")
    return hmac.compare_digest(a, b)


class OmsApiController(http.Controller):

    def _normalize_payment_text(self, value):
        return ''.join(ch for ch in str(value or '').upper() if ch.isalnum())

    def _amount_equal(self, left, right):
        try:
            return abs(float(left or 0.0) - float(right or 0.0)) <= 0.01
        except Exception:
            return False

    def _order_paid_amount(self, order):
        return max(
            float(getattr(order, "pay_accumulated", 0.0) or 0.0),
            float(getattr(order, "amount_paid", 0.0) or 0.0),
            float(getattr(order, "uc_amount_paid", 0.0) or 0.0),
        )

    def _order_is_fully_paid(self, order):
        total = float(getattr(order, "amount_total", 0.0) or 0.0)
        if total <= 0.0:
            return False
        paid = self._order_paid_amount(order)
        currency = getattr(order, "currency_id", None)
        if currency:
            return currency.compare_amounts(paid, total) >= 0
        return paid + 0.01 >= total

    def _send_approval_if_fully_paid(self, order, tx=None):
        if not order or not self._order_is_fully_paid(order):
            return False

        try:
            if hasattr(order, "uc_website_finalize_send_approval"):
                return bool(order.sudo().uc_website_finalize_send_approval(reason="paid", payment_tx=tx))
            if hasattr(order, "action_send_for_approval"):
                order.sudo().action_send_for_approval()
                return True
        except Exception:
            _logger.exception(
                "api_oms_payment: cannot send approval for fully paid order=%s tx=%s",
                getattr(order, "id", None),
                getattr(tx, "reference", None),
            )
            raise
        return False

    def _find_sepay_tx_for_oms_payment(self, order, amount, remark):
        Tx = request.env["payment.transaction"].sudo()
        if not order or "payment.transaction" not in request.env.registry.models:
            return Tx.browse([])

        states = ("draft", "pending", "authorized", "done")
        domain = [("provider_code", "=", "sepay"), ("state", "in", states)]

        if "sale_order_ids" in Tx._fields:
            domain.append(("sale_order_ids", "in", [order.id]))
        elif "sale_order_id" in Tx._fields:
            domain.append(("sale_order_id", "=", order.id))
        elif "sale_custom_order_ids" in Tx._fields:
            domain.append(("sale_custom_order_ids", "in", [order.id]))
        elif "sale_custom_order_id" in Tx._fields:
            domain.append(("sale_custom_order_id", "=", order.id))
        else:
            domain.append(("reference", "ilike", order.name or str(order.id)))

        txs = Tx.search(domain, order="state asc, create_date desc, id desc", limit=30)
        if not txs:
            return Tx.browse([])

        remark_key = self._normalize_payment_text(remark)
        amount_float = float(amount or 0.0)

        def _score(tx):
            score = 0
            if tx.state != "done":
                score += 100
            if self._amount_equal(tx.amount, amount_float):
                score += 20
            note = ""
            try:
                if hasattr(tx, "_sepay_build_qr_note"):
                    note = tx._sepay_build_qr_note()
            except Exception:
                note = ""
            note_key = self._normalize_payment_text(note)
            ref_key = self._normalize_payment_text(tx.reference)
            if remark_key and note_key and (remark_key in note_key or note_key in remark_key):
                score += 50
            if remark_key and ref_key and (remark_key in ref_key or ref_key in remark_key):
                score += 10
            return score

        return sorted(txs, key=_score, reverse=True)[0]

    def _mark_sepay_tx_done_from_oms_payment(self, tx):
        if not tx or not tx.exists():
            return False
        if tx.provider_code != "sepay":
            return False
        if tx.state == "done":
            return False
        if tx.state == "draft":
            tx._set_pending()
        if hasattr(tx, "action_sepay_mark_done"):
            tx.action_sepay_mark_done()
        else:
            tx._set_done()
            if hasattr(tx, "_post_process_after_done"):
                tx._post_process_after_done()
        return True

    @http.route('/api/oms/payment', type='json', auth='public', methods=['POST'], csrf=False)
    def api_oms_payment(self, **kwargs):
        """
        Header bắt buộc:
          - X-API-Key: <key cấu hình>

        JSON body:
          {
            "order_id": 513,
            "amount": "2268000",
            "remark": "AUTC029595TT513"
          }
        """
        try:
            # ==================================================================
            # 1) Kiểm tra API key
            # ==================================================================
            ICP = request.env['ir.config_parameter'].sudo()
            cfg_key = (ICP.get_param('oms.api_key', '') or '').strip()
            in_key = (request.httprequest.headers.get('X-API-Key') or '').strip()

            if not cfg_key or not _constant_time_equal(in_key, cfg_key):
                _logger.warning(
                    "api_oms_payment: unauthorized request. in_key=%r", in_key
                )
                return {
                    "ok": False,
                    "error": "unauthorized",
                    "message": _("Sai hoặc thiếu API key."),
                }

            # ==================================================================
            # 2) Đọc & validate payload từ body JSON
            # ==================================================================
            raw_body = request.httprequest.get_data(as_text=True) or ""
            _logger.info("api_oms_payment: raw_body=%r", raw_body)

            try:
                payload = json.loads(raw_body or "{}") or {}
            except Exception:
                _logger.warning(
                    "api_oms_payment: body không phải JSON, dùng request.params=%s",
                    request.params,
                )
                payload = dict(request.params) if request.params else {}

            _logger.info("api_oms_payment: payload=%s", payload)

            order_id = payload.get("order_id")
            amount = payload.get("amount")
            remark = (payload.get("remark") or "").strip()

            # --- order_id bắt buộc ---
            if order_id in (None, ""):
                return {
                    "ok": False,
                    "error": "missing_order_id",
                    "message": _("Thiếu order_id."),
                }

            try:
                order_id_int = int(order_id)
            except (TypeError, ValueError):
                _logger.warning(
                    "api_oms_payment: invalid order_id=%r (không convert được sang int)",
                    order_id,
                )
                return {
                    "ok": False,
                    "error": "invalid_order_id",
                    "message": _("order_id phải là số ID nội bộ của đơn hàng."),
                }

            # --- amount bắt buộc ---
            if amount in (None, ""):
                return {
                    "ok": False,
                    "error": "missing_amount",
                    "message": _("Thiếu số tiền (amount)."),
                }

            try:
                amount_float = float(amount)
            except (TypeError, ValueError):
                _logger.warning(
                    "api_oms_payment: invalid amount=%r (không convert được sang float)",
                    amount,
                )
                return {
                    "ok": False,
                    "error": "invalid_amount",
                    "message": _("Số tiền (amount) không hợp lệ."),
                }

            # ==================================================================
            # 3) Tìm & cập nhật đơn
            # ==================================================================
            Order = request.env["sale_custom.order"].sudo()
            order = Order.browse(order_id_int)

            if not order or not order.exists():
                _logger.warning(
                    "api_oms_payment: order not found. order_id=%s", order_id_int
                )
                return {
                    "ok": False,
                    "error": "order_not_found",
                    "message": _("Không tìm thấy đơn hàng."),
                }

            _logger.info(
                "api_oms_payment: applying payment amount=%s, remark=%r for order %s (%s)",
                amount_float,
                remark,
                order.id,
                order.name,
            )

            tx = self._find_sepay_tx_for_oms_payment(order, amount_float, remark)
            tx_state_before = tx.state if tx else False
            payment_logged = False
            duplicate_payment = (
                bool(remark)
                and (order.pay_last_remark or "").strip() == remark
                and self._amount_equal(order.pay_last_amount, amount_float)
            )

            if duplicate_payment:
                _logger.info(
                    "api_oms_payment: skip duplicate payment log order=%s amount=%s remark=%r tx=%s state=%s",
                    order.id,
                    amount_float,
                    remark,
                    tx.reference if tx else None,
                    tx_state_before,
                )
            else:
                order.apply_incoming_payment(amount=amount_float, remark=remark)
                payment_logged = True

            tx_marked_done = False
            if tx:
                try:
                    tx_marked_done = self._mark_sepay_tx_done_from_oms_payment(tx)
                except Exception:
                    _logger.exception(
                        "api_oms_payment: cannot mark SePay tx done. order=%s tx=%s",
                        order.id,
                        tx.reference,
                    )
                    raise

            # Không cần flush / invalidate_cache trên recordset ở bản Odoo này

            approval_sent = self._send_approval_if_fully_paid(order, tx=tx if tx else None)

            return {
                "ok": True,
                "order_id": order.id,
                "order_name": order.name or "",
                "customer_name": order.partner_id.display_name
                if order.partner_id
                else "",
                "pay_accumulated": order.pay_accumulated,
                "pay_last_amount": order.pay_last_amount,
                "pay_last_remark": order.pay_last_remark,
                "pay_updated_at": fields.Datetime.to_string(order.pay_updated_at)
                if order.pay_updated_at
                else None,
                "payment_logged": payment_logged,
                "duplicate_payment": duplicate_payment,
                "approval_sent": approval_sent,
                "sepay_tx": {
                    "id": tx.id,
                    "reference": tx.reference,
                    "state_before": tx_state_before,
                    "state_after": tx.state,
                    "marked_done": tx_marked_done,
                } if tx else None,
            }

        except UserError as ue:
            _logger.warning("api_oms_payment: UserError: %s", ue)
            return {
                "ok": False,
                "error": "bad_request",
                "message": str(ue),
            }
        except Exception as e:
            _logger.exception("api_oms_payment: unexpected error.")
            return {
                "ok": False,
                "error": "server_error",
                "message": _("Lỗi xử lý thanh toán."),
                "detail": str(e),  # khi stable có thể bỏ field này đi
            }
    @http.route('/api/oms/order_info', type='json', auth='public', methods=['POST'], csrf=False)
    def api_oms_order_info(self, **kwargs):
        """
        Header bắt buộc:
          - X-API-Key: <key cấu hình>

        JSON body:
          {
            "order_id": 513
          }
        """
        try:
            # ==================================================================
            # 1) Kiểm tra API key
            # ==================================================================
            ICP = request.env['ir.config_parameter'].sudo()
            cfg_key = (ICP.get_param('oms.api_key', '') or '').strip()
            in_key = (request.httprequest.headers.get('X-API-Key') or '').strip()

            if not cfg_key or not _constant_time_equal(in_key, cfg_key):
                _logger.warning("api_oms_order_info: unauthorized request. in_key=%r", in_key)
                return {
                    "ok": False,
                    "error": "unauthorized",
                    "message": _("Sai hoặc thiếu API key."),
                }

            # ==================================================================
            # 2) Đọc & validate payload
            # ==================================================================
            raw_body = request.httprequest.get_data(as_text=True) or ""
            _logger.info("api_oms_order_info: raw_body=%r", raw_body)

            try:
                payload = json.loads(raw_body or "{}") or {}
            except Exception:
                _logger.warning(
                    "api_oms_order_info: body không phải JSON, dùng request.params=%s",
                    request.params,
                )
                payload = dict(request.params) if request.params else {}

            _logger.info("api_oms_order_info: payload=%s", payload)

            order_id = payload.get("order_id")
            if order_id in (None, ""):
                return {
                    "ok": False,
                    "error": "missing_order_id",
                    "message": _("Thiếu order_id."),
                }

            try:
                order_id_int = int(order_id)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "invalid_order_id",
                    "message": _("order_id phải là số ID nội bộ của đơn hàng."),
                }

            # ==================================================================
            # 3) Tìm đơn + build response
            # ==================================================================
            Order = request.env["sale_custom.order"].sudo()
            order = Order.browse(order_id_int)
            if not order or not order.exists():
                return {
                    "ok": False,
                    "error": "order_not_found",
                    "message": _("Không tìm thấy đơn hàng."),
                }

            # --- SlpCode: ưu tiên dùng hàm chuẩn trên model nếu có ---
            slp_code_int = None
            if hasattr(order, "_get_slp_code"):
                try:
                    slp_code_int = int(order._get_slp_code() or 0)
                except Exception:
                    slp_code_int = None

            # --- Thông tin user bán hàng (user_id) ---
            user = getattr(order, "user_id", False)
            user_info = None
            if user:
                # cố gắng lấy các thuộc tính có thể chứa slp_code trên user
                user_slp_raw = None
                for attr in ("slp_code", "sap_slpcode", "x_sap_slpcode", "sales_code", "sap_code", "code"):
                    if hasattr(user, attr):
                        v = getattr(user, attr)
                        if v not in (None, "", False):
                            user_slp_raw = v
                            break

                user_info = {
                    "id": user.id,
                    "name": user.name or "",
                    "login": getattr(user, "login", "") or "",
                    "slp_code_raw": str(user_slp_raw) if user_slp_raw not in (None, False) else None,
                }

            # --- Nếu order có field SlpCode (có thể là recordset) thì trả thêm ---
            slp_obj = getattr(order, "SlpCode", False)
            slp_obj_info = None
            if slp_obj:
                slp_obj_info = {
                    "id": getattr(slp_obj, "id", None),
                    "name": getattr(slp_obj, "name", None) or getattr(slp_obj, "display_name", "") or "",
                    "branch": getattr(slp_obj, "branch", None),
                }

            # --- Trả thêm thông tin đơn hàng thường dùng ---
            return {
                "ok": True,
                "order": {
                    "id": order.id,
                    "name": order.name or "",
                    "state": getattr(order, "state", None),
                    "date_order": fields.Datetime.to_string(getattr(order, "date_order", False)) if getattr(order, "date_order", False) else None,
                    "amount_total": getattr(order, "amount_total", 0.0),
                    "currency": getattr(getattr(order, "currency_id", False), "name", None),
                    "partner": {
                        "id": getattr(getattr(order, "partner_id", False), "id", None),
                        "name": getattr(getattr(order, "partner_id", False), "display_name", "") if getattr(order, "partner_id", False) else "",
                        "phone": getattr(getattr(order, "partner_id", False), "phone", None),
                        "email": getattr(getattr(order, "partner_id", False), "email", None),
                    },
                },
                "sales": {
                    "slp_code": slp_code_int,
                    "user": user_info,
                    "slp_object": slp_obj_info,
                },
                # nếu bạn muốn kèm luôn các field thanh toán custom của bạn:
                "payment": {
                    "pay_accumulated": getattr(order, "pay_accumulated", None),
                    "pay_last_amount": getattr(order, "pay_last_amount", None),
                    "pay_last_remark": getattr(order, "pay_last_remark", None),
                    "pay_updated_at": fields.Datetime.to_string(getattr(order, "pay_updated_at", False)) if getattr(order, "pay_updated_at", False) else None,
                },
            }

        except UserError as ue:
            _logger.warning("api_oms_order_info: UserError: %s", ue)
            return {"ok": False, "error": "bad_request", "message": str(ue)}
        except Exception as e:
            _logger.exception("api_oms_order_info: unexpected error.")
            return {
                "ok": False,
                "error": "server_error",
                "message": _("Lỗi lấy thông tin đơn hàng."),
                "detail": str(e),
            }
