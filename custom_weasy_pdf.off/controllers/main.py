from odoo import http
from odoo.http import request
from odoo.modules.module import get_module_resource
from weasyprint import HTML
from weasyprint.document import Stream
import io
import pydyf
from PIL import Image
import requests
from io import BytesIO
import re
import base64
from urllib.parse import quote
from odoo.http import content_disposition

# —— Preload header.png as base64 to embed directly —— 
_header_path = get_module_resource('custom_weasy_pdf', 'static', 'img', 'header.png')
with open(_header_path, 'rb') as f:
    HEADER_SRC = f"data:image/png;base64,{ base64.b64encode(f.read()).decode('ascii') }"

# —— Dummy 1×1 PNG fallback —— 
DUMMY_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAAAA1JREFUGFdj+P///38ACfsD/"
    "QGHfFkAAAAASUVORK5CYII="
)

# —— Monkey-patch fallback for JPEG2000 —— 
_original_save_jpeg2000 = Stream._save_jpeg2000
def _save_jpeg2000_fallback(self, pillow_image, optimize):
    try:
        return _original_save_jpeg2000(self, pillow_image, optimize)
    except (KeyError, OSError):
        buf = io.BytesIO()
        pillow_image.save(buf, format='JPEG', optimize=optimize)
        buf.seek(0)
        return buf

Stream._save_jpeg2000 = _save_jpeg2000_fallback

# —— Monkey-patch to force JPEG encoding —— 
def _add_image_force_jpeg(self, pillow_image, image_rendering, optimize_size):
    image_name = f'i{pillow_image.id}'
    self._x_objects[image_name] = None
    if image_name in self._images:
        return image_name

    # Nếu ảnh có alpha channel, ghép lên nền trắng rồi chuyển về RGB
    if pillow_image.mode in ('RGBA', 'LA'):
        background = Image.new('RGB', pillow_image.size, (255, 255, 255))
        alpha = pillow_image.split()[-1]
        background.paste(pillow_image, mask=alpha)
        pillow_image = background
    # Với các mode không hỗ trợ JPEG, ép về RGB
    elif pillow_image.mode not in ('RGB', 'L', 'CMYK'):
        pillow_image = pillow_image.convert('RGB')

    # Xác định color space
    if pillow_image.mode == 'RGB':
        color_space = '/DeviceRGB'
    elif pillow_image.mode == 'L':
        color_space = '/DeviceGray'
    elif pillow_image.mode == 'CMYK':
        color_space = '/DeviceCMYK'
    else:
        color_space = '/DeviceRGB'

    extra = pydyf.Dictionary({
        'Type': '/XObject',
        'Subtype': '/Image',
        'Width': pillow_image.width,
        'Height': pillow_image.height,
        'ColorSpace': color_space,
        'BitsPerComponent': 8,
        'Interpolate': 'true' if image_rendering == 'auto' else 'false',
    })

    # Ép luôn sang JPEG để dùng DCTDecode
    optimize = 'images' in optimize_size
    buf = io.BytesIO()
    pillow_image.save(buf, format='JPEG', optimize=optimize)
    extra['Filter'] = '/DCTDecode'
    buf.seek(0)

    xobj = pydyf.Stream([buf.getvalue()], extra=extra)
    self._images[image_name] = xobj
    return image_name

# Gán monkey-patch
Stream.add_image = _add_image_force_jpeg


Stream.add_image = _add_image_force_jpeg

# —— Utility functions —— 

def _abs_url(u: str) -> str:
    if not u:
        return ''
    if u.startswith('data:image/'):
        return u
    if re.match(r'^https?://', u):
        return u
    base = request.httprequest.host_url.rstrip('/')
    return f"{base}/{u.lstrip('/')}"

def fetch_img_convert(url):
    try:
        url = _abs_url(url)

        # Data URI thì bỏ qua request
        if url.startswith('data:image/'):
            header, b64 = url.split(',', 1)
            fmt = header.split('/')[1].split(';')[0]
            return base64.b64decode(b64), fmt

        resp = requests.get(
            url,
            timeout=10,
            cookies=request.httprequest.cookies,        # mang cookie Odoo
            headers={'User-Agent': 'WeasyPrint/1.0'}
        )
        resp.raise_for_status()
        ctype = resp.headers.get('Content-Type', '')
        if 'image' not in ctype:
            raise ValueError(f'Not an image: {ctype}')

        img = Image.open(BytesIO(resp.content))
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue(), 'png'
    except Exception as e:
        request.env['ir.logging']._logger.error(f"[ERROR IMG] {url}: {e}")
        return base64.b64decode(DUMMY_BASE64), 'png'


def replace_img_tag_with_base64(html):
    for full_tag, url in re.findall(r'(<img[^>]+src="([^"]+)"[^>]*>)', html):
        if url.startswith('data:image/'):
            continue
        img_bytes, fmt = fetch_img_convert(url)
        data = base64.b64encode(img_bytes).decode('ascii')
        new_src = f'data:image/{fmt};base64,{data}'
        html = html.replace(full_tag, full_tag.replace(url, new_src))
    return html

def replace_css_url_with_base64(html):
    for url in re.findall(r'url\([\'"]?([^\'")]+)[\'"]?\)', html):
        if url.startswith('data:image/'):
            continue
        img_bytes, fmt = fetch_img_convert(url if re.match(r'^https?://|^/', url) else _abs_url(url))
        data = base64.b64encode(img_bytes).decode('ascii')
        new_src = f'data:image/{fmt};base64,{data}'
        html = html.replace(url, new_src)
    return html

def safe(val):
    return '' if not val or str(val) == 'False' else val


def selection_label(record, field_name):
    val = record[field_name]
    if not val:
        return ''
    # fields_get trả về label đã dịch theo context lang của user
    sel = record.fields_get([field_name])[field_name]['selection']  # list of (value, label)
    return dict(sel).get(val, val)

INVALID_FS_CHARS = r'\/:*?"<>|'

def clean_filename_part(s: str) -> str:
    if not s:
        return ''
    for ch in INVALID_FS_CHARS:
        s = s.replace(ch, ' ')
    s = ' '.join(str(s).split())
    return s[:80]

def build_quote_filename(order) -> str:
    """
    Output format (ưu tiên):
      <ABBR/Customer> - <Doctor> - <Stops Stop> - <SO>.pdf

    Vẫn an toàn khi thiếu trường (tự bỏ qua phần trống).
    """

    def part(val: str) -> str:
        return clean_filename_part(val or "")

    # SO: ví dụ "SQ-HCM-2509 S00057"
    so = part(getattr(order, "name", "") or "SO")

    # Tên KH/abbr: ưu tiên company_ref -> partner.abbr -> partner.name
    abbr = part(
        getattr(order, "company_ref", "")
        or getattr(getattr(order, "partner_id", None), "abbr", "")
        or getattr(getattr(order, "partner_id", None), "name", "")
    )

    # Bác sĩ/người liên hệ (đặt nhiều phương án để tự khớp nếu field khác tên)
    doctor = part(
        getattr(order, "EL_DoctorName", "")
        or getattr(order, "doctor_name", "")
        or getattr(order, "contact_name", "")
        or getattr(getattr(order, "partner_id", None), "contact_name", "")
    )

    # Mã công trình (nếu muốn giữ trong tên file — đặt giữa Doctor và Stops)
    cong_trinh = part(getattr(order, "EL_Construction_Code", ""))

    # Số điểm dừng: thêm hậu tố 'Stop' nếu có
    raw_stops = part(getattr(order, "EL_QuantityStops", ""))
    stops = f"{raw_stops} Stop" if raw_stops and "stop" not in raw_stops.lower() else raw_stops

    # Ghép theo thứ tự mới: ABBR -> Doctor -> (Công trình) -> Stops -> SO
    parts = [p for p in [abbr, doctor, cong_trinh, stops, so] if p]
    base = " - ".join(parts) if parts else "SO"
    return f"{base}.pdf"



# ===== Controller ===== 

class CustomWeasyPdfController(http.Controller):
    @http.route('/my/weasy_pdf', type='http', auth='user')
    def export_pdf(self, **kwargs):
        order_id = int(request.params.get('order_id', 0))
        report_type = request.params.get('report_type', '')
        order = request.env['sale_custom.order'].browse(order_id)
        report_type = report_type or order.report_type
        order.report_type = report_type
        lines = order.order_line
        show_prices = (report_type != "san_xuat")
        show_terms  = (report_type != "san_xuat")
        cols = 6 if show_prices else 4

        # ==== GROUP LINES THEO PHẦN (Section Odoo) ====  # REPLACED
        sections = []
        current = None
        running_no = 1  # số thứ tự chỉ cho dòng sản phẩm thực

        # 3 mã hoa hồng cần ẩn (gộp vào dòng trước)
        hidden_codes = {'HOAHONGKEGIA', 'HOAHONGKEGIACANHAN', 'HOAHONGKHACHHANG','HOAHONGMGCANHAN'}

        def ensure_section():
            nonlocal current
            if current is None:
                current = {'name': '', 'rows': [], 'subtotal': 0.0}
                sections.append(current)

        # dùng để nhớ dòng sản phẩm thực gần nhất trong section hiện tại
        last_real_row = None

        for l in lines:
            dt = getattr(l, 'display_type', False)

            if dt == 'line_section':
                current = {'name': l.name or '', 'rows': [], 'subtotal': 0.0}
                sections.append(current)
                last_real_row = None
                continue

            if dt == 'line_note':
                ensure_section()
                current['rows'].append({
                    'type': 'note',
                    'text': safe(l.name),
                })
                last_real_row = None
                continue

            # ----- dòng thường / hoa hồng -----
            ensure_section()

            code = safe(getattr(getattr(l, 'product_id', None), 'default_code', '')).upper()
            name_up = safe(l.name).upper()
            is_commission = (code in hidden_codes) or ('HOA HỒNG' in name_up)

            # nếu là hoa hồng: cộng vào dòng thực ngay trước đó
            if is_commission:
                if last_real_row:
                    add_sub = float(l.price_subtotal or 0.0)
                    last_real_row['subtotal'] += add_sub

                    qty_val = last_real_row.get('_qty_val', 0.0) or 0.0
                    last_real_row['unit_price'] = (last_real_row['subtotal'] / qty_val) if qty_val else 0.0

                    current['subtotal'] += add_sub
                # nếu không có dòng thực trước đó thì bỏ qua
                continue

            # dòng sản phẩm thực
            qty_val = float(l.product_uom_qty or 0.0)
            subtotal_val = float(l.price_subtotal or 0.0)
            unit_price_val = (subtotal_val / qty_val) if qty_val else 0.0

            row = {
                'type': 'line',
                'no': running_no,
                'code': safe(getattr(getattr(l, 'product_id', None), 'default_code', '')),
                'product': safe(getattr(getattr(l, 'product_id', None), 'name', '')),
                'desc': safe(l.name),
                'qty': f'{qty_val:.1f}',
                'unit_price': unit_price_val,   # đơn giá = thành tiền/qty (sau sẽ cộng thêm hoa hồng)
                'subtotal': subtotal_val,       # thành tiền ban đầu (sẽ tăng nếu có hoa hồng phía sau)
                '_qty_val': qty_val,            # giữ lại để tính lại đơn giá khi gộp hoa hồng
            }
            current['rows'].append(row)
            current['subtotal'] += subtotal_val
            last_real_row = row
            running_no += 1

        # Dùng cùng tên biến như cũ để khỏi sửa phía dưới
        grouped_lines = sections
        

        # Tiền bằng chữ
        currency = order.currency_id
        money_text = ''
        if currency and order.amount_total:
            money_text = currency.amount_to_text(order.amount_total).replace('\n', '').replace('Dong', 'Đồng').strip()
        
        # ===== PAYMENT (vẫn là <li> có bullet, layout 2 cột, anti page-break) =====
        qr_url = safe(getattr(order, "sepay_qr_url", "")) or "/custom_weasy_pdf/static/img/qr.png"

        def _bank_default():
            return (
                '<div>Khách hàng thanh toán bằng chuyển khoản vào tài khoản sau:</div>'
                '<div><b>CÔNG TY CỔ PHẦN TẬP ĐOÀN DAT</b></div>'
                'STK: <b>103 693 6868</b> – Vietcombank – CN Kỳ Đồng – HCM<br/>'
            )

        branch = safe(getattr(order.user_id, 'branch', ''))
        partner_ref = safe(order.partner_ref)

        if branch == 'HNI':
            bank_text = (
                '<div>Khách hàng thanh toán bằng chuyển khoản vào tài khoản sau:</div>'
                '<div><b>CÔNG TY CỔ PHẦN TẬP ĐOÀN DAT – CHI NHÁNH HÀ NỘI</b></div>'
                'STK: <b>105 131 8386</b> – Vietcombank – CN Kỳ Đồng<br/>'
            )
        elif branch == 'CTH':
            bank_text = (
                '<div>Khách hàng thanh toán bằng chuyển khoản vào tài khoản sau:</div>'
                '<div><b>CÔNG TY CỔ PHẦN TẬP ĐOÀN DAT – CHI NHÁNH CẦN THƠ</b></div>'
                'STK: <b>103 693 6868</b> – Vietcombank – CN Kỳ Đồng – HCM<br/>'
            )
        else:
            bank_text = _bank_default()
        qr_desc = safe(order._qr_desc() if hasattr(order, "_qr_desc") else "")

        payment_html = f"""
        <li style="page-break-inside:avoid;">
          <table style="width:100%; border-collapse:collapse; page-break-inside:avoid;">
            <colgroup>
              <col style="width:70%"/>
              <col style="width:30%"/>
            </colgroup>
            <tr style="vertical-align:top; page-break-inside:avoid;">
              <!-- Cột trái: chữ thường, không khung -->
              <td style="border:none; padding:0 12px 0 0;">
                <div style="font-weight:600; margin:0 0 4px 0;">Phương thức thanh toán</div>
                <div style="line-height:1.55;">{bank_text}</div>
                <div style="margin-top:4px;">
                  <b>Nội dung chuyển khoản (bắt buộc):</b> {qr_desc}
                </div>
              </td>

              <!-- Cột phải: QR nhỏ gọn, không viền -->
              <td style="border:none; padding:0; text-align:center;">
                <img src="{qr_url}" alt="QR chuyển khoản"
                     style="width:22mm; height:22mm; object-fit:contain; display:block; margin:0 auto;"/>
                <div style="font-size:10px; color:#6b7280; margin-top:4px;">Quét QR để thanh toán</div>
              </td>
            </tr>
          </table>
        </li>
        """

        

        contact = getattr(order, 'cntct_code', False) or order.partner_id
        phone = safe(getattr(contact, 'cellolar', '')) or safe(getattr(contact, 'tel1', '')) or safe(getattr(contact, 'tel2', ''))
        email = safe(getattr(contact, 'email', ''))
        address = safe(getattr(order, 'EL_Construction_Address', order.partner_id.street)) or safe(getattr(order, 'Address', order.partner_id.street))
        address_2 = safe(getattr(order, 'Address', order.partner_id.street)) or safe(getattr(order, 'EL_Construction_Address', order.partner_id.street))
        # --- Build body cho bảng sản phẩm: hỗ trợ cả 'line' và 'note' ---
        rows_html = []
        for section in grouped_lines:
            if not section.get('rows'):
                continue
            
            if section['name']:
                rows_html.append(
                    f"<tr class='section-title-row'><td colspan='{cols}'>{section['name']}</td></tr>"
                )

            for row in section['rows']:
                if row['type'] == 'line':
                    price_cells = "" if not show_prices else (
                        f"<td style='text-align:right;'>{row['unit_price']:,.0f}</td>"
                        f"<td style='text-align:right;'>{row['subtotal']:,.0f}</td>"
                    )
                    rows_html.append(
                        "<tr>"
                        f"<td style='text-align:center;'>{row['no']}</td>"
                        f"<td style='text-align:left;vertical-align:top;'>{row['product']}</td>"
                        f"<td>{row['desc']}</td>"
                        f"<td style='text-align:center;'>{row['qty']}</td>"
                        f"{price_cells}"
                        "</tr>"
                    )
                else:  # note
                    rows_html.append(
                        "<tr>"
                        f"<td colspan='{cols}' style='font-style:italic;background:#fff;color:#7a7a7a;"
                        "border:1px solid #96bfe7;padding:7px 10px;'>"
                        f"{row['text']}"
                        "</td>"
                        "</tr>"
                    )

            # chỉ cộng phần khi có giá
            if section['name'] and show_prices:
                rows_html.append(
                    "<tr>"
                    f"<td colspan='{cols-1}' style='text-align:right;font-weight:bold;color:#135abe;'>"
                    f"Tổng cộng {section['name']}:</td>"
                    f"<td style='text-align:right;font-weight:bold;color:#135abe;'>"
                    f"{section['subtotal']:,.0f}</td>"
                    "</tr>"
                )

        products_table_body = ''.join(rows_html)

        price_heads = "" if not show_prices else (
            "<th style='background:#e4eefc;color:#0f0000;text-align:center;min-width:120px'>"
            "Unit Price<br>Đơn giá (VNĐ)</th>"
            "<th style='background:#e4eefc;color:#0f0000;text-align:center;min-width:140px'>"
            "Total Amount<br>Thành tiền (VNĐ)</th>"
        )

        html = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @page {{
                    size: A4;
                    margin: 0.5cm 3cm 2cm 2cm;
                    line-height: 3;
                }}
                body {{
                    font-family: "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", sans-serif;
                    font-size: 12px;
                    color: #222;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    font-size: 12px;
                }}
                th, td {{
                    border: 1px solid #96bfe7;
                    padding: 4px 7px;
                }}
                th {{
                    background: #e4eefc;
                    color: #0f0000;
                    font-weight: bold;
                    text-align: center;
                }}
                .tbl-title-row th {{
                    background: #2370b7;
                    color: #fff;
                    text-align: left;
                    font-size: 13px;
                    border-color: #2370b7;
                    padding:7px 8px;
                }}
                .section-title-row td {{
                    background: #f2f6fa;
                    color: #1a6fa8;
                    font-weight: bold;
                    border-color: #96bfe7;
                    text-align:left;
                }}
                .no-border {{
                    border: none !important;
                }}
                /* Bảng thông số */
                .spec-table {{ table-layout: fixed; width: 100%; }}
                /* Set tỉ lệ từng cột (tổng = 100%) */
                .spec-table col.c1 {{ width: 6%; }}   /* No.  -> nhỏ */
                .spec-table col.c2 {{ width: 42%; }}  /* Mô tả */
                .spec-table col.c3 {{ width: 9%; }}  /* Giá trị -> nhỏ */
                .spec-table col.c4 {{ width: 8%; }}   /* Đơn vị -> nhỏ */
                .spec-table col.c5 {{ width: 19%; }}  /* Ghi chú 1 */
                .spec-table col.c6 {{ width: 16%; }}  /* Ghi chú 2 */

            </style>
        </head>
        <body style="width: 17cm; line-height:1.5;">
        <!-- ========== HEADER ========== -->
        <div style="display:flex;align-items:flex-start;margin-bottom:4px;">
            <img src="{HEADER_SRC}" alt="DAT" style="width:100%;height:auto;display:block;"/>
        </div>
        <!-- ========== TIÊU ĐỀ BÁO GIÁ ========== -->
        <div style="text-align:center; margin-bottom:11px;">
            <div style="font-weight:bold;font-size:1.15em;letter-spacing:0.5px;">BÁO GIÁ ĐƠN HÀNG</div>
            <div style="margin-top:2px;font-size:1.02em;color:#0f0000">
                Số báo giá: <span style="font-weight:bold;">{safe(order.name)}</span>
            </div>
        </div>
        <!-- ========== KHÁCH HÀNG ========== -->
        <div style="margin-bottom:7px;">
            <div style="font-weight:bold;">
                Kính gửi: <span>{safe(contact.name)}</span>
                <span style="font-weight:normal;color:#0f0000;">({phone} / {email})</span>
            </div>
            <div style="margin:2px 0 2px 0;">
                <span style="font-weight:bold;">{safe(order.partner_id.parent_id.name) or safe(order.partner_id.name)}</span>
                <span style="font-weight:normal;color:#0f0000;">({address_2})</span>
            </div>
        </div>
        <!-- ========== LỜI MỞ ĐẦU ========== -->
        <div style="margin-bottom:6px;font-size:12px;">
            Công ty Cổ Phần Tập Đoàn DAT chuyên cung cấp các sản phẩm, thiết bị và giải pháp kỹ thuật tại Việt Nam, chúng tôi xin chân thành cảm ơn Quý Doanh nghiệp đã quan tâm đến sản phẩm và dịch vụ của DAT. Công ty DAT trân trọng gửi đến Quý doanh nghiệp báo giá hàng hóa tốt nhất như sau:
        </div>
        <!-- ========== BẢNG SẢN PHẨM CHIA PHẦN ========== -->
        <table style="margin-top:10px;">
          <tr class="tbl-title-row">
            <th colspan="{cols}" style="text-align:left;">I. SẢN PHẨM CUNG CẤP</th>
          </tr>
          <tr>
            <th style="background:#e4eefc;color:#0f0000;text-align:center;">No.</th>
            <th style="background:#e4eefc;color:#0f0000;text-align:center;">Model<br>Mã hàng</th>
            <th style="background:#e4eefc;color:#0f0000;text-align:center;">Description<br>Mô tả</th>
            <th style="background:#e4eefc;color:#0f0000;text-align:center;">Quality<br>Số lượng</th>
            {price_heads}
          </tr>
          {products_table_body}
        </table>

        <!-- ========== TỔNG TIỀN ========== -->
        {"" if report_type == "san_xuat" else f'''
        <div style="margin-top:8px; font-size:12px; line-height:1.55;">
            <div><b>Tổng giá trị (tạm tính, đã bao gồm thuế GTGT):</b> {order.amount_total:,.0f} đ</div>
            <div style="margin-top:2px; font-style:italic;">
                <b>Thành tiền (bằng chữ):</b> {money_text}
            </div>
        </div>
        '''}
        <!-- ========== ĐIỀU KIỆN THƯƠNG MẠI ========== -->
        <div style="font-size:13px;font-weight:bold;margin-top:14px;">II. ĐIỀU KIỆN THƯƠNG MẠI</div>
        <ul style="margin:6px 0 0 18px; padding:0; line-height:1.5;">
            <li>Chất lượng hàng hóa: Hàng mới 100% từ nhà sản xuất.</li>
            <li>Địa điểm giao hàng: {address or address_2 or ''}</li>
        </ul>

        <div style="margin:8px 0 8px 18px; page-break-inside:avoid;">
          <table style="width:100%; border-collapse:collapse; page-break-inside:avoid;">
            <colgroup>
              <col style="width:75%"/>
              <col style="width:25%"/>
            </colgroup>
            <tr style="vertical-align:top; page-break-inside:avoid;">
              <td style="border:none; padding:0 12px 0 0;">
                <div style="font-weight:600; margin:0 0 4px 0;">Phương thức thanh toán</div>
                <div style="line-height:1.55;">{bank_text}</div>
                <div style="margin-top:4px;">
                  <b>Nội dung chuyển khoản (bắt buộc):</b> {qr_desc}
                </div>
              </td>
              <td style="border:none; padding:0; text-align:center;">
                <img src="{qr_url}" alt="QR chuyển khoản"
                     style="width:28mm; height:28mm; object-fit:contain; display:block; margin:0 auto;"/>
                <div style="font-size:10px; color:#6b7280; margin-top:4px;">Quét QR để thanh toán</div>
              </td>
            </tr>
          </table>
        </div>

        <ul style="margin:0 0 0 18px; padding:0; line-height:1.5;">
            <li>Chứng từ: Hóa đơn, CO, CQ (bản sao) &amp; Phiếu xuất kho (kiêm bảo hành).</li>
            <li>Hiệu lực báo giá: Có hiệu lực trong vòng 15 ngày kể từ ngày báo giá.</li>
        </ul>

        <div style="margin-top:10px;font-size:12px; line-height:1.6;">
            Công ty DAT chân thành cảm ơn Quý Công Ty đã cho phép chúng tôi được phục vụ. Chúng tôi cam kết luôn mang đến sản phẩm, giải pháp và dịch vụ tốt nhất cho Quý Công ty.<br/>
            <b>Trân trọng kính chào!</b>
        </div>
        <!-- ========== CHỮ KÝ ========== -->
        <table style="width:100%; margin-top:24px; border:none; table-layout:fixed;">
          <colgroup>
            <col style="width:50%">
            <col style="width:50%">
          </colgroup>
          <tr>
            <td style="border:none; text-align:center; padding-top:8px; vertical-align:top;">
              <div><b>Xác nhận của bên mua</b></div>
              <div>(Ký tên và đóng dấu)</div>
              <div style="height:88px;"></div>
            </td>

            <td style="border:none; text-align:center; padding-top:8px; vertical-align:top;">
              <div><b>Xác nhận của bên bán</b></div>
              <div>(Ký tên và đóng dấu)</div>
              <div style="height:88px;"></div>
              <div style="margin-top:4px;font-weight:bold;">{safe(order.user_id.name)}</div>
            </td>
          </tr>
        </table>
        </body>
        </html>
        """
        # Nhúng hình ảnh base64
        html = replace_img_tag_with_base64(html)
        html = replace_css_url_with_base64(html)

        pdf = HTML(string=html).write_pdf()

        filename = build_quote_filename(order)
        # Khuyến nghị thêm filename* cho UTF-8 để trình duyệt hiển thị đúng dấu:
        return request.make_response(pdf, [
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', content_disposition(filename)),
        ])