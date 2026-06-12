# -*- coding: utf-8 -*-
import json
import hmac
import logging

from odoo import http, _
from odoo.http import request
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


def _constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(
        (a or "").encode("utf-8"),
        (b or "").encode("utf-8"),
    )


def _get_payload_from_request(kwargs):
    """
    Compatible payload extraction across Odoo versions:

    Priority:
    1) kwargs (for type="json", Odoo thường parse và đưa vào **kwargs)
    2) request.params (một số bản đưa vào params)
    3) raw request body (request.httprequest.data) parse JSON
    4) if JSON-RPC, payload nằm trong "params"
    """
    # 1) kwargs
    body = kwargs if isinstance(kwargs, dict) else {}

    # 2) if empty, try request.params
    if not body:
        try:
            params = request.params or {}
            if isinstance(params, dict):
                body = params
        except Exception:
            body = {}

    # 3) if still empty, parse raw body
    if not body:
        try:
            raw = request.httprequest.data
            if raw:
                body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

    if not isinstance(body, dict):
        body = {}

    # 4) JSON-RPC: payload inside params
    payload = body.get("params") if isinstance(body.get("params"), dict) else body
    if not isinstance(payload, dict):
        payload = {}

    return payload


class OmsPriceListVersionedApi(http.Controller):

    @http.route(
        "/api/oms/pricelist/versioned_upsert",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def versioned_upsert(self, **kwargs):

        # ==========================================================
        # 0) Load system api_key
        # ==========================================================
        ICP = request.env["ir.config_parameter"].sudo()
        cfg_key = (ICP.get_param("oms.pricelist.api_key") or "").strip()
        if not cfg_key:
            _logger.error("oms.pricelist.api_key is not configured")
            raise AccessError(_("Server chưa cấu hình oms.pricelist.api_key"))

        # ==========================================================
        # 1) Parse payload (NO request.jsonrequest)
        # ==========================================================
        payload = _get_payload_from_request(kwargs)

        # ==========================================================
        # 2) Authenticate api_key (Header > Body)
        # ==========================================================
        header_key = (request.httprequest.headers.get("X-API-Key") or "").strip()
        body_key = (payload.get("api_key") or "").strip()
        in_key = header_key or body_key

        if not in_key or not _constant_time_equal(in_key, cfg_key):
            _logger.warning(
                "versioned_upsert: invalid api_key (header=%s body=%s)",
                bool(header_key),
                bool(body_key),
            )
            raise AccessError("Invalid api_key")

        # ==========================================================
        # 3) Validate data
        # ==========================================================
        data = payload.get("data")
        if data is None:
            raise UserError("Missing required field: data")
        if not isinstance(data, list):
            raise UserError("data must be a list")
        if not data:
            raise UserError("data must be a non-empty list")

        _logger.info("versioned_upsert: accepted %s rows", len(data))

        # ==========================================================
        # 4) Delegate to model
        # ==========================================================
        return request.env["oms.price.list"].sudo().api_versioned_upsert_from_payload(data)
