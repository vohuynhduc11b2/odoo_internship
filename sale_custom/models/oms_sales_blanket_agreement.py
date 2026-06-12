from odoo import models, fields, api

class SalesBlanketAgreement(models.Model):
    _name = 'oms.sales.blanket.agreement'
    _description = 'Sales Blanket Agreement (Hợp đồng nguyên tắc bán hàng)'
    _order = 'id desc'

    bp_code = fields.Char("Mã khách hàng (BpCode)")
    card_name = fields.Char("Tên khách hàng")
    abs_id = fields.Integer("AbsID")
    method = fields.Selection([('M', 'M'), ('D', 'D')], string="Method")
    start_date = fields.Datetime("Ngày bắt đầu")
    end_date = fields.Datetime("Ngày kết thúc")
    sign_date = fields.Datetime("Ngày ký")
    num_at_card = fields.Char("Số hợp đồng (NumAtCard)")
    bp_curr = fields.Char("Loại tiền")
    descript = fields.Char("Mô tả")
    voucher_type = fields.Char("Loại hợp đồng (VoucherType)")
    status = fields.Char("Trạng thái")
    u_slp_code = fields.Char("Mã NVKD (U_SlpCode)")
    slp_name = fields.Char("Tên NVKD")
    u_bplid = fields.Char("Chi nhánh (U_BPLid)")
    u_detail_entry = fields.Integer("Detail Entry")
    u_total_value = fields.Float("Tổng giá trị hợp đồng")
    u_check_limit_amount = fields.Selection([('Y', 'Có'), ('N', 'Không')], string="Kiểm tra hạn mức")
    u_check_by = fields.Char("Check By")
    u_debt_line = fields.Float("Công nợ hợp đồng")
    store = fields.Char("Store")
    group_num = fields.Integer("GroupNum")
    pymnt_group = fields.Char("Điều khoản thanh toán")
    # -----------------------
    # Display name
    # -----------------------
    def _fmt_date(self, dt):
        if not dt:
            return "..."
        # hiển thị theo timezone user, dạng dd/mm/YYYY
        ts = fields.Datetime.context_timestamp(self, dt)
        return ts.strftime("%d/%m/%Y")

    def name_get(self):
        """
        Mặc định ngắn gọn: "<NumAtCard | CardName | [BpCode]> | <PymntGroup>"
        Bật tên đầy đủ qua context: with_context(ba_show_full_name=True)
        """
        res = []
        full = bool(self.env.context.get('ba_show_full_name'))
        for rec in self:
            core = rec.num_at_card or rec.card_name or rec.bp_code or _("HĐ #%s") % rec.id
            short_tail = rec.pymnt_group or False

            if not full:
                name = core if not short_tail else f"{core} | {short_tail}"
                res.append((rec.id, name))
                continue

            parts = []
            if rec.num_at_card:
                parts.append(rec.num_at_card)
            label = []
            if rec.card_name:
                label.append(rec.card_name)
            if rec.bp_code:
                label.append(f"[{rec.bp_code}]")
            if label:
                parts.append(" ".join(label))
            # khoảng thời gian hiệu lực
            if rec.start_date or rec.end_date:
                parts.append(f"{self._fmt_date(rec.start_date)}→{self._fmt_date(rec.end_date)}")
            if rec.pymnt_group:
                parts.append(f"PTTT: {rec.pymnt_group}")
            if rec.slp_name:
                parts.append(f"NVKD: {rec.slp_name}")

            name = " | ".join([p for p in parts if p]) or core
            res.append((rec.id, name))
        return res

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=80):
        """
        Tìm theo NumAtCard, BpCode, CardName, PymntGroup, hoặc AbsID (số).
        """
        args = args or []
        dom = []
        if name:
            dom = [
                '|', '|', '|',
                ('num_at_card', operator, name),
                ('bp_code', operator, name),
                ('card_name', operator, name),
                ('pymnt_group', operator, name),
            ]
            # tìm đúng AbsID nếu nhập số
            if name.isdigit():
                dom = ['|'] + dom + [('abs_id', '=', int(name))]
        recs = self.search(dom + args, limit=limit)
        return recs.name_get()