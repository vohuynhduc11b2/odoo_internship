# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class OmsPriceListAPI(http.Controller):

    @http.route(
        '/oms/price_list/sync',
        type='json',
        auth='user',       # tuỳ bạn: có thể đổi sang 'public' + token
        methods=['POST'],
        csrf=False
    )
    def sync_price_list(self, **kwargs):
        """
        API nhận JSON chuẩn để tạo/cập nhật bảng giá OMS.

        - Dùng chung cho tất cả brand/file Excel (GOODWE, SOLAX, PYLONTECH, SUNGROW, TỦ ĐIỆN, ...).
        - Lớp ngoài (n8n/script) chịu trách nhiệm đọc Excel, convert về JSON theo format
          mà model `OmsPriceList.api_sync_price_list` mô tả.
        """
        payload = request.jsonrequest or {}
        env = request.env

        try:
            pricelist_id = env['oms.price.list'].sudo().api_sync_price_list(payload)
            return {
                'success': True,
                'pricelist_id': pricelist_id,
            }
        except ValidationError as e:
            # Lỗi validate (thiếu item_code, sai price_type, không tìm thấy product, ...)
            _logger.warning("Sync price list ValidationError: %s", e)
            return {
                'success': False,
                'error': str(e),
            }
        except Exception as e:
            env.cr.rollback()
            _logger.exception("Sync price list internal error")
            return {
                'success': False,
                'error': "Internal error: %s" % e,
            }
