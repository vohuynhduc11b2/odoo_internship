# -*- coding: utf-8 -*-
from odoo import http, fields
from odoo.http import request, Response
from datetime import datetime, date, time as dtime
import json, pytz, unicodedata


def _json(data, status=200):
    return Response(
        json.dumps(data, ensure_ascii=False),
        status=status,
        content_type='application/json; charset=utf-8',
    )


def _norm(s):
    """Chuẩn hóa chuỗi: trim, lower, bỏ dấu tiếng Việt."""
    s = (s or '').strip().lower()
    # Bỏ dấu tiếng Việt
    s = ''.join(
        ch for ch in unicodedata.normalize('NFD', s)
        if unicodedata.category(ch) != 'Mn'
    )
    return s


# --- Timezone helpers ---------------------------------------------------------
def _tz():
    """Ưu tiên: Header X-TZ -> Body tz -> web.base.tz -> fallback VN."""
    ICP = request.env['ir.config_parameter'].sudo()
    body_tz = None
    try:
        if request.httprequest.method == 'POST':
            body = json.loads(request.httprequest.get_data(as_text=True) or '{}')
            body_tz = (body.get('tz') or '').strip()
    except Exception:
        pass
    hdr = (request.httprequest.headers.get('X-TZ') or '').strip()
    cfg = (ICP.get_param('web.base.tz') or '').strip()
    name = hdr or body_tz or cfg or 'Asia/Ho_Chi_Minh'
    try:
        return pytz.timezone(name)
    except Exception:
        return pytz.timezone('Asia/Ho_Chi_Minh')


def _to_utc_range(tz, dfrom: str | None, dto: str | None):
    """date_from/date_to (YYYY-MM-DD, local tz) -> UTC strings cho domain search."""
    utc_from = utc_to = None
    if dfrom:
        lf = tz.localize(
            datetime.combine(datetime.strptime(dfrom, "%Y-%m-%d").date(), dtime.min)
        )
        utc_from = fields.Datetime.to_string(lf.astimezone(pytz.UTC))
    if dto:
        lt = tz.localize(
            datetime.combine(datetime.strptime(dto, "%Y-%m-%d").date(), dtime.max)
        )
        utc_to = fields.Datetime.to_string(lt.astimezone(pytz.UTC))
    return utc_from, utc_to


def _to_local_pair(tz, dt_utc):
    """Odoo datetime (UTC naive) -> (utc_aware, local_aware)."""
    if not dt_utc:
        return None, None
    # Odoo lưu naive-UTC
    aware_utc = pytz.UTC.localize(
        fields.Datetime.from_string(fields.Datetime.to_string(dt_utc))
    )
    local = aware_utc.astimezone(tz)
    return aware_utc, local


def _fmt_local(dt_local, fmt="%d/%m/%Y %H:%M"):
    return dt_local.strftime(fmt) if dt_local else None


# -----------------------------------------------------------------------------


STEP_ALIASES = {
    "sm": ["sm duyệt", "sales manager", "sales manager leader"],
    "bu": ["bu duyệt", "business unit", "business unit leader"],
    "bod": ["bod duyệt", "ban giám đốc"],
    "congno": ["duyệt công nợ", "công nợ", "kế toán"],
}


class ApprovalOrderApi(http.Controller):

    @http.route(
        '/_dat/api/orders/approval',
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
    )
    def approval_list(self, **kw):
        ICP = request.env['ir.config_parameter'].sudo()

        # ===== API KEY =====
        need = (ICP.get_param('oms.api_key') or '').strip()
        got = (request.httprequest.headers.get('X-Api-Key') or '').strip()
        if not need or got != need:
            return _json({"status": False, "message": "Unauthorized"}, 401)

        # ===== Params =====
        if request.httprequest.method == 'POST':
            try:
                payload = json.loads(
                    request.httprequest.get_data(as_text=True) or '{}'
                )
            except Exception:
                return _json(
                    {"status": False, "message": "Body phải là JSON"},
                    400,
                )
        else:
            payload = kw

        try:
            # có thể chỉnh default limit tuỳ nhu cầu (500 cho ELE)
            limit = int(payload.get('limit', 500) or 500)
            offset = int(payload.get('offset', 0) or 0)
        except Exception:
            return _json(
                {"status": False, "message": "limit/offset phải là số nguyên"},
                400,
            )

        # KHÔNG GIỚI HẠN NGÀY NỮA, CHỈ DÙNG date_field ĐỂ SẮP XẾP + HIỂN THỊ
        date_field = (payload.get('date_field') or 'date_order').strip()
        if date_field not in ('date_order', 'create_date'):
            date_field = 'date_order'

        tz = _tz()  # vẫn cần timezone để format created_at_local

        # ===== EPOCH (đếm lần gọi) + idempotent theo X-Poll-Id =====
        poll_id = (request.httprequest.headers.get('X-Poll-Id') or '').strip()
        last_poll_id = (ICP.get_param('oms.approval_last_poll_id') or '').strip()
        try:
            current_epoch = int(ICP.get_param('oms.approval_poll_epoch') or 0)
        except Exception:
            current_epoch = 0

        if poll_id and poll_id == last_poll_id:
            # retry cùng poll_id: giữ nguyên epoch -> không cộng đếm
            pass
        else:
            current_epoch += 1
            ICP.set_param('oms.approval_poll_epoch', str(current_epoch))
            if poll_id:
                ICP.set_param('oms.approval_last_poll_id', poll_id)

        # gom keywords cho 4 bước
        kw_all = []
        for arr in STEP_ALIASES.values():
            kw_all += arr
        kw_all = [_norm(x) for x in kw_all]

        # KHÔNG THÊM DOMAIN THEO NGÀY NỮA
        dom = []

        Order = request.env['sale_custom.order'].sudo()
        # Lấy full danh sách theo domain + order
        ids_all = Order.search(dom, order=f'{date_field} desc, id desc').ids
        recs_all = Order.browse(ids_all)

        def is_target(o):
            step = _norm(
                (o.current_step_name or o._get_current_step_name() or '').strip()
            )
            return any(k in step for k in kw_all)

        def _get_current_step_rec(o):
            wf = getattr(o, 'workflow_id', False)
            cur_seq = int(getattr(o, 'current_sequence', 0) or 0)
            if not wf or not cur_seq:
                return request.env['oms.workflow.step']
            return wf.step_ids.filtered(
                lambda s: int(getattr(s, 'sequence', 0) or 0) == cur_seq
            )[:1]

        items_all = []

        for o in recs_all:
            if not is_target(o):
                continue

            step_rec = _get_current_step_rec(o)
            seq = int(getattr(step_rec, 'sequence', 0) or 0)

            # ===== ĐẾM LIÊN TIẾP: (order_id, sequence) qua các lần gọi =====
            cnt_key = f"oms.approval_poll_count.{o.id}.{seq}"
            seen_key = f"oms.approval_last_seen.{o.id}.{seq}"
            try:
                last_seen = int(ICP.get_param(seen_key) or 0)
            except Exception:
                last_seen = 0
            try:
                call_count = int(ICP.get_param(cnt_key) or 0)
            except Exception:
                call_count = 0

            if last_seen == (current_epoch - 1):
                # xuất hiện liên tiếp so với lần gọi trước
                call_count += 1
            else:
                # bắt đầu chuỗi mới
                call_count = 1

            ICP.set_param(cnt_key, str(call_count))
            ICP.set_param(seen_key, str(current_epoch))

            try:
                store_code = o._store_from_user_branch()
            except Exception:
                store_code = None

            # ===== Tên hiển thị khách/đối tác =====
            partner = o.partner_id
            partner_short = (getattr(partner, 'short_name', '') or '').strip()
            partner_name = (
                partner.commercial_company_name or partner.name or ''
            ).strip()
            display_name = partner_short or partner_name

            # ===== Nhân viên bán hàng (ưu tiên SlpCode, fallback user_id) =====
            sp_user = getattr(o, 'SlpCode', None) or getattr(o, 'user_id', None)
            sp_name = getattr(sp_user, 'name', '') if sp_user else ''
            sp_id = getattr(sp_user, 'slp_code', False) if sp_user else False

            dt_utc, dt_loc = _to_local_pair(tz, getattr(o, date_field))

            items_all.append(
                {
                    "id": o.id,
                    "code": o.name,
                    "bu": "AUT",
                    "voucher_type_name": "Phiếu bán hàng",
                    "store_code": store_code,
                    # Thời gian
                    "created_at_utc": dt_utc.isoformat().replace("+00:00", "Z")
                    if dt_utc
                    else None,
                    "created_at_local": dt_loc.isoformat() if dt_loc else None,
                    "created_at_text": _fmt_local(dt_loc, "%d/%m/%Y %H:%M"),
                    "tz": str(tz),
                    # Bước duyệt
                    "current_step": (
                        o.current_step_name or o._get_current_step_name() or ''
                    ).strip(),
                    "current_step_id": step_rec.id or None,
                    "current_step_sequence": seq or None,
                    # Khách hàng/Công ty
                    "card_name": display_name or '',
                    "short_name_company": getattr(o, 'company_ref', '') or '',
                    "name": getattr(o, 'company_ref', '') or display_name,
                    # Nhân viên bán hàng
                    "salesperson_id": sp_id or None,
                    "salesperson_name": sp_name or '',
                    # Số lần liên tiếp xuất hiện ở cùng bước
                    "call_count": call_count,
                    "date_field": date_field,
                }
            )

        # ===== Áp dụng offset/limit SAU KHI LỌC =====
        total = len(items_all)
        if limit:
            items = items_all[offset: offset + limit]
        else:
            items = items_all[offset:]

        return _json(
            {
                "status": True,
                "count": len(items),
                "total": total,  # nếu mobile app không cần có thể bỏ
                "items": items,
            }
        )
