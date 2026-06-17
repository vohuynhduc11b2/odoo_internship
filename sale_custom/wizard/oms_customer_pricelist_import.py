# -*- coding: utf-8 -*-
"""
Wizard để import hàng loạt gán bảng giá cho khách hàng từ Excel.
"""
import base64
import io
from odoo import fields, models, _
from odoo.exceptions import UserError, ValidationError


class OmsCustomerPricelistImport(models.TransientModel):
    _name = 'oms.customer.pricelist.import'
    _description = 'Import Gán Bảng giá Khách hàng'

    file_name = fields.Char('Tên file')
    file_import = fields.Binary('File Import', required=True)
    overwrite = fields.Boolean(
        string='Ghi đè nếu đã tồn tại',
        default=True,
        help='Nếu check, các assignment đã tồn tại sẽ bị ghi đè. '
             'Nếu không check, các assignment đã tồn tại sẽ bị bỏ qua.'
    )

    def _parse_excel(self):
        """Parse file Excel và trả về danh sách dòng."""
        import xlrd
        
        if not self.file_import:
            raise UserError(_('Vui lòng chọn file Excel để import.'))

        file_data = base64.b64decode(self.file_import)
        try:
            workbook = xlrd.open_workbook(file_contents=file_data)
        except Exception as e:
            raise UserError(_('Không thể đọc file Excel: %s') % str(e))

        sheet = workbook.sheet_by_index(0)
        
        # Đọc header
        headers = []
        for col in range(sheet.ncols):
            headers.append(sheet.cell_value(0, col) if sheet.nrows > 0 else '')
        
        rows = []
        for row_idx in range(1, sheet.nrows):
            row_data = {}
            for col_idx, header in enumerate(headers):
                if col_idx < sheet.ncols:
                    row_data[header] = sheet.cell_value(row_idx, col_idx)
            rows.append(row_data)
        
        return headers, rows

    def _get_partner_by_code(self, card_code):
        """Tìm khách hàng theo Card Code."""
        Partner = self.env['res.partner']
        
        # Thử tìm theo x_oms_card_code
        partner = Partner.search([
            ('x_oms_card_code', '=', str(card_code).strip())
        ], limit=1)
        
        if not partner:
            # Thử tìm theo ref
            partner = Partner.search([
                ('ref', '=', str(card_code).strip())
            ], limit=1)
        
        return partner

    def _get_pricelist_by_name(self, pricelist_name):
        """Tìm bảng giá theo tên."""
        Pricelist = self.env['product.pricelist']
        
        # Thử tìm chính xác
        pricelist = Pricelist.search([
            ('name', '=', str(pricelist_name).strip())
        ], limit=1)
        
        if not pricelist:
            # Thử tìm chứa
            pricelist = Pricelist.search([
                ('name', 'ilike', str(pricelist_name).strip())
            ], limit=1)
        
        return pricelist

    def action_import(self):
        """Thực hiện import."""
        self.ensure_one()
        
        headers, rows = self._parse_excel()
        
        if not rows:
            raise UserError(_('File Excel không có dữ liệu.'))
        
        # Map header về lowercase để so sánh
        headers_lower = {h.lower().strip(): h for h in headers}
        
        # Kiểm tra các cột bắt buộc
        required_cols = ['card_code', 'pricelist_name']
        missing_cols = []
        for col in required_cols:
            if col not in headers_lower and col.replace('_', ' ') not in headers_lower:
                missing_cols.append(col)
        
        if missing_cols:
            raise UserError(_('Thiếu các cột bắt buộc: %s') % ', '.join(missing_cols))
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        error_lines = []
        
        CustomerPricelist = self.env['oms.customer.pricelist']
        
        for idx, row in enumerate(rows, start=2):
            try:
                # Lấy giá trị từ row
                card_code = str(row.get(headers_lower.get('card_code', ''), '')).strip()
                pricelist_name = str(row.get(headers_lower.get('pricelist_name', ''), '')).strip()
                
                if not card_code:
                    error_lines.append(f"Dòng {idx}: Thiếu Card Code")
                    skipped_count += 1
                    continue
                
                if not pricelist_name:
                    error_lines.append(f"Dòng {idx}: Thiếu Pricelist Name")
                    skipped_count += 1
                    continue
                
                # Tìm partner
                partner = self._get_partner_by_code(card_code)
                if not partner:
                    error_lines.append(f"Dòng {idx}: Không tìm thấy khách hàng với Card Code '{card_code}'")
                    skipped_count += 1
                    continue
                
                # Tìm pricelist
                pricelist = self._get_pricelist_by_name(pricelist_name)
                if not pricelist:
                    error_lines.append(f"Dòng {idx}: Không tìm thấy bảng giá '{pricelist_name}'")
                    skipped_count += 1
                    continue
                
                # Parse các trường optional
                is_default = bool(row.get(headers_lower.get('is_default', ''), ''))
                priority = int(float(row.get(headers_lower.get('priority', '10') or '10'))) if row.get(headers_lower.get('priority', '')) else 10
                
                valid_from = None
                if row.get(headers_lower.get('valid_from', '')):
                    try:
                        # Thử parse ngày
                        date_val = row.get(headers_lower.get('valid_from', ''))
                        if isinstance(date_val, float):
                            import xlrd
                            valid_from = xlrd.xldate_as_datetime(date_val, workbook.datemode).date()
                        elif isinstance(date_val, str):
                            valid_from = fields.Date.from_string(date_val)
                    except:
                        pass
                
                valid_to = None
                if row.get(headers_lower.get('valid_to', '')):
                    try:
                        date_val = row.get(headers_lower.get('valid_to', ''))
                        if isinstance(date_val, float):
                            import xlrd
                            valid_to = xlrd.xldate_as_datetime(date_val, workbook.datemode).date()
                        elif isinstance(date_val, str):
                            valid_to = fields.Date.from_string(date_val)
                    except:
                        pass
                
                note = row.get(headers_lower.get('note', ''), '') or ''
                
                # Kiểm tra xem đã tồn tại chưa
                domain = [
                    ('partner_id', '=', partner.id),
                    ('pricelist_id', '=', pricelist.id),
                ]
                
                existing = CustomerPricelist.search(domain, limit=1)
                
                vals = {
                    'partner_id': partner.id,
                    'pricelist_id': pricelist.id,
                    'is_default': is_default,
                    'priority': priority,
                    'state': 'active',
                    'active': True,
                    'note': note,
                }
                
                if valid_from:
                    vals['valid_from'] = valid_from
                if valid_to:
                    vals['valid_to'] = valid_to
                
                if existing:
                    if self.overwrite:
                        existing.write(vals)
                        updated_count += 1
                    else:
                        skipped_count += 1
                else:
                    CustomerPricelist.create(vals)
                    created_count += 1
                    
            except Exception as e:
                error_lines.append(f"Dòng {idx}: Lỗi - {str(e)}")
                skipped_count += 1
        
        # Tạo thông báo kết quả
        message = f"Import hoàn tất!\n"
        message += f"- Tạo mới: {created_count}\n"
        message += f"- Cập nhật: {updated_count}\n"
        message += f"- Bỏ qua: {skipped_count}\n"
        
        if error_lines:
            message += f"\nLỗi ({len(error_lines)}):\n" + "\n".join(error_lines[:20])
            if len(error_lines) > 20:
                message += f"\n... và {len(error_lines) - 20} lỗi khác"
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Import Gán Bảng giá',
                'message': message,
                'type': 'success' if not error_lines else 'warning',
                'sticky': False,
            }
        }
