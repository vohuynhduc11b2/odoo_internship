# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import html
import logging
import pytz
from collections import defaultdict
from datetime import timedelta,date, datetime, time
from itertools import groupby
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from markupsafe import Markup
import requests
import re
from odoo.tools.float_utils import float_is_zero, float_compare
from decimal import Decimal, ROUND_HALF_UP
import base64
from urllib.parse import urlencode
import uuid
import unicodedata
from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import (
    AccessError,
    RedirectWarning,
    UserError,
    ValidationError,
)
from odoo.fields import Command
from odoo.http import request
from odoo.osv import expression
from odoo.tools import (
    create_index,
    float_is_zero,
    format_amount,
    format_date,
    is_html_empty,
    SQL,
)
from odoo.tools.mail import html_keep_url

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)

INVOICE_STATUS = [
    ('upselling', 'Upselling Opportunity'),
    ('invoiced', 'Fully Invoiced'),
    ('to invoice', 'To Invoice'),
    ('no', 'Nothing to Invoice')
]


COMMISSION_SKUS = {'HOAHONGKEGIA', 'HOAHONGKEGIACANHAN', 'HOAHONGKHACHHANG'}
SALE_ORDER_STATE = [
    ('draft', "Quotation"),
    ('sent', "Quotation Sent"),
    ('sale', "Sales Order"),
    ('cancel', "Cancelled"),
    ('approved', "Approved"),   # Thêm
    ('rejected', "Rejected"),   # Thêm
]


class ApprovalWorkflow(models.Model):
    _name = 'approval.workflow'
    _description = 'Quy trình duyệt động'

    name = fields.Char(string="Tên quy trình", required=True)
    steps_json = fields.Json(string='Các bước duyệt (JSON)', default=list)

class SaleOrder(models.Model):
    _name = 'sale_custom.order'
    _inherit = ['portal.mixin', 'product.catalog.mixin', 'mail.thread', 'mail.activity.mixin', 'utm.mixin']
    _description = "Sales Order"
    _order = 'date_order desc, id desc'
    _check_company_auto = True

    _sql_constraints = [
        ('date_order_conditional_required',
         "CHECK((state = 'sale' AND date_order IS NOT NULL) OR state != 'sale')",
         "A confirmed sales order requires a confirmation date."),
    ]

    @property
    def _rec_names_search(self):
        if self._context.get('sale_show_partner_name'):
            return ['name', 'partner_id.name']
        return ['name']

    #=== FIELDS ===#

    name = fields.Char(
        string="Order Reference",
        required=True, copy=False, readonly=False,
        index='trigram',
        default=lambda self: _('New'))

    company_id = fields.Many2one(
        comodel_name='res.company',
        required=True, index=True,
        default=lambda self: self.env.company)
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Customer",
        required=True, change_default=True, index=True,
        tracking=1,
        check_company=True)
    state = fields.Selection(
        selection=SALE_ORDER_STATE,
        string="Status",
        readonly=True, copy=False, index=True,
        tracking=3,
        default='draft')

    def _get_current_step_name(self):
        self.ensure_one()
        wf = getattr(self, "workflow_id", False)
        cur_seq = int(getattr(self, "current_sequence", 0) or 0)
        if not wf or not cur_seq:
            return _("Báo giá")
        step = wf.step_ids.filtered(lambda s: int(s.sequence) == cur_seq)[:1]
        return step.name if step else _("Báo giá")

    approval_state = fields.Selection([
        ('draft',    'Báo giá'),
        ('waiting', 'Chờ'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancel', 'Đã hủy'),
    ], compute="_compute_approval_state", store=False)

    approval_step = fields.Char(compute="_compute_approval_state", store=False)

    @api.depends('state', 'workflow_id', 'current_sequence')
    def _compute_approval_state(self):
        for o in self:
            s = (o.state or "").lower()
            stepname = o._get_current_step_name()
            if s in ("approved", "sale"):
                o.approval_state = "approved"
                o.approval_step = "Approved"
            elif s in ("rejected",):
                o.approval_state = "rejected"
                o.approval_step = "Rejected"
            elif s in ("cancel",):
                o.approval_state = "cancel"
                o.approval_step = "Đã hủy"
            elif s in ('draft',) and stepname in ('Báo giá',):
                o.approval_state = 'draft'
                o.approval_step = _('Báo giá')
            else:
                o.approval_state = "waiting"
                o.approval_step = f"Chờ {stepname}"

    sap_u_sino       = fields.Char(string="U_SINo (SAP)", readonly=True, copy=False)
    sap_opty_type    = fields.Char(string="OptyType (SAP)", readonly=True, copy=False)
    sap_voucher_no   = fields.Char(string="Số chứng từ SAP", readonly=True, copy=False, index=True)
    sap_status_name  = fields.Char(string="Trạng thái SAP", readonly=True, copy=False, index=True, tracking=True)
    sap_stt          = fields.Integer(string="STT (SAP)", readonly=True, copy=False)

    locked = fields.Boolean(
        help="Locked orders cannot be modified.",
        default=False,
        copy=False,
        tracking=True)
    has_archived_products = fields.Boolean(compute="_compute_has_archived_products")

    client_order_ref = fields.Char(string="Customer Reference", copy=False)
    create_date = fields.Datetime(  # Override of default create_date field from ORM
        string="Creation Date", index=True, readonly=True)
    commitment_date = fields.Datetime(
        string="Delivery Date", copy=False,
        help="This is the delivery date promised to the customer. "
             "If set, the delivery order will be scheduled based on "
             "this date rather than product lead times.")
    date_order = fields.Datetime(
        string="Order Date",
        required=True, copy=False,
        help="Creation date of draft/sent orders,\nConfirmation date of confirmed orders.",
        default=fields.Datetime.now)
    origin = fields.Char(
        string="Source Document",
        help="Reference of the document that generated this sales order request")
    reference = fields.Char(
        string="Payment Ref.",
        help="The payment communication of this sale order.",
        copy=False)

    require_signature = fields.Boolean(
        string="Online signature",
        compute='_compute_require_signature',
        store=True, readonly=False, precompute=True,
        help="Request a online signature from the customer to confirm the order.")
    require_payment = fields.Boolean(
        string="Online payment",
        compute='_compute_require_payment',
        store=True, readonly=False, precompute=True,
        help="Request a online payment from the customer to confirm the order.")
    prepayment_percent = fields.Float(
        string="Prepayment percentage",
        compute='_compute_prepayment_percent',
        store=True, readonly=False, precompute=True,
        help="The percentage of the amount needed that must be paid by the customer to confirm the order.")

    signature = fields.Image(
        string="Signature",
        copy=False, attachment=True, max_width=1024, max_height=1024)
    signed_by = fields.Char(
        string="Signed By", copy=False)
    signed_on = fields.Datetime(
        string="Signed On", copy=False)

    validity_date = fields.Datetime(
        string="Expiration",
        help="Validity of the order, after that you will not able to sign & pay the quotation.",
        compute='_compute_validity_date',
        store=True, readonly=False, copy=False, precompute=True,
        states={'draft': [('readonly', False)]})
    journal_id = fields.Many2one(
        'account.journal', string="Invoicing Journal",
        compute="_compute_journal_id", store=True, readonly=False, precompute=True,
        domain=[('type', '=', 'sale')], check_company=True,
        help="If set, the SO will invoice in this journal; "
             "otherwise the sales journal with the lowest sequence is used.")

    # Partner-based computes
    note = fields.Html(
        string="Terms and conditions",
        compute='_compute_note',
        store=True, readonly=False, precompute=True)

    partner_invoice_id = fields.Many2one(
        comodel_name='res.partner',
        string="Invoice Address",
        compute='_compute_partner_invoice_id',
        store=True, readonly=False, required=True, precompute=True,
        check_company=True,
        index='btree_not_null')
    partner_shipping_id = fields.Many2one(
        comodel_name='res.partner',
        string="Delivery Address",
        compute='_compute_partner_shipping_id',
        store=True, readonly=False, required=True, precompute=True,
        check_company=True,
        index='btree_not_null')

    fiscal_position_id = fields.Many2one(
        comodel_name='account.fiscal.position',
        string="Fiscal Position",
        compute='_compute_fiscal_position_id',
        store=True, readonly=False, precompute=True, check_company=True,
        help="Fiscal positions are used to adapt taxes and accounts for particular customers or sales orders/invoices."
            "The default value comes from the customer.",
    )
    payment_term_id = fields.Many2one(
        comodel_name='account.payment.term',
        string="Payment Terms",
        compute='_compute_payment_term_id',
        store=True, readonly=False, precompute=True, check_company=True,  # Unrequired company
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    pricelist_id = fields.Many2one(
        comodel_name='product.pricelist',
        string="Pricelist",
        compute='_compute_pricelist_id',
        store=True, readonly=False, precompute=True, check_company=True,  # Unrequired company
        tracking=1,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="If you change the pricelist, only newly added lines will be affected.")
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        compute='_compute_currency_id',
        store=True,
        precompute=True,
        ondelete='restrict'
    )
    currency_rate = fields.Float(
        string="Currency Rate",
        compute='_compute_currency_rate',
        digits=0,
        store=True, precompute=True)
    user_id = fields.Many2one(
        comodel_name='res.users',
        string="Salesperson",
        compute='_compute_user_id',
        store=True, readonly=False, precompute=True, index=True,
        tracking=2,
        domain=lambda self: "[('groups_id', '=', {}), ('share', '=', False), ('company_ids', '=', company_id)]".format(
            self.env.ref("sales_team.group_sale_salesman").id
        ))
    u_is_issue_invoice = fields.Selection(
        [
            ('Y', 'PHHD ngay'),
            ('N', 'Không lấy hóa đơn'),
            ('A', 'PHHD sau'),
            ('B', 'Giá có VAT - không PH'),
            ('T', 'PHHD sau bằng tay'),
            ('C', 'PHHD ngay bằng tay'),
        ],
        string="Chế độ hóa đơn",
        required=True,
        default=lambda self: (
            (self.env['ir.config_parameter'].sudo()
             .get_param('oms.default_invoice_mode', 'Y') or 'Y').strip().upper()
        ),
        help="Mã 1 ký tự đẩy qua API: Y/N/A/B/T/C",
        # optional:
        tracking=True,   # muốn log thay đổi trên chatter thì bật
    )


    team_id = fields.Many2one(
        comodel_name='crm.team',
        string="Sales Team",
        compute='_compute_team_id',
        store=True, readonly=False, precompute=True, ondelete="set null",
        change_default=True, check_company=True,  # Unrequired company
        tracking=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    # Lines and line based computes
    order_line = fields.One2many(
        comodel_name='sale_custom.order.line',
        inverse_name='order_id',
        string="Order Lines",
        copy=True, auto_join=True)

    amount_untaxed = fields.Monetary(string="Untaxed Amount", store=True, compute='_compute_amounts', tracking=5)
    amount_tax = fields.Monetary(string="Taxes", store=True, compute='_compute_amounts')
    amount_total = fields.Monetary(string="Total", store=True, compute='_compute_amounts', tracking=4)
    amount_to_invoice = fields.Monetary(string="Un-invoiced Balance", compute='_compute_amount_to_invoice')
    amount_invoiced = fields.Monetary(string="Already invoiced", compute='_compute_amount_invoiced')

     # Currency để format các số tiền ở Portal (theo công ty của SO)
    portal_company_currency_id = fields.Many2one(
        "res.currency",
        string="Portal Currency",
        related="company_id.currency_id",
        store=False,
        readonly=True,
    )

    # 1) Tên khách hàng
    portal_customer_name = fields.Char(
        string="Tên khách hàng (Portal)",
        compute="_compute_portal_partner_info",
        store=False,
        readonly=True,
    )

    # 2) Ngày hiện tại đang xem
    portal_view_date = fields.Date(
        string="Ngày đang xem (Portal)",
        compute="_compute_portal_view_date",
        store=False,
        readonly=True,
    )

    # 3) Hạn mức tín dụng (để Monetary nhưng lấy qua compute -> không bị lỗi type related)
    portal_credit_limit = fields.Monetary(
        string="Hạn mức tín dụng (Portal)",
        compute="_compute_portal_credit_info",
        currency_field="portal_company_currency_id",
        store=False,
        readonly=True,
    )

    # 4) Tổng công nợ
    portal_total_debt = fields.Monetary(
        string="Tổng công nợ (Portal)",
        compute="_compute_portal_credit_info",
        currency_field="portal_company_currency_id",
        store=False,
        readonly=True,
    )

    # 5) Tổng thu ứng
    portal_total_advance = fields.Monetary(
        string="Tổng thu ứng (Portal)",
        compute="_compute_portal_credit_info",
        currency_field="portal_company_currency_id",
        store=False,
        readonly=True,
    )

    # 6) Tổng doanh số
    portal_total_sales = fields.Monetary(
        string="Tổng doanh số (Portal)",
        compute="_compute_portal_credit_info",
        currency_field="portal_company_currency_id",
        store=False,
        readonly=True,
    )


    @api.depends("partner_id")
    def _compute_portal_partner_info(self):
        for rec in self:
            partner = rec.partner_id.commercial_partner_id if rec.partner_id and rec.partner_id.commercial_partner_id else rec.partner_id
            rec.portal_customer_name = partner.display_name if partner else ""

    @api.depends_context("tz")
    def _compute_portal_view_date(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.portal_view_date = today

    @api.depends("partner_id", "company_id")
    def _compute_portal_credit_info(self):
        """
        Lấy thông tin hạn mức/nợ/thu ứng từ commercial partner.
        Dùng compute để tránh lỗi 'related field type inconsistent'.
        """
        for rec in self:
            commercial = rec.partner_id.commercial_partner_id if rec.partner_id and rec.partner_id.commercial_partner_id else rec.partner_id
            if not commercial:
                rec.portal_credit_limit = 0.0
                rec.portal_total_debt = 0.0
                rec.portal_total_advance = 0.0
                continue

            # credit_limit / credit có thể là Float hoặc Monetary tuỳ DB/module -> ép float an toàn
            credit_limit = commercial.credit_limit if hasattr(commercial, "credit_limit") else 0.0
            credit = commercial.credit if hasattr(commercial, "credit") else 0.0

            # total_advance_amount là ví dụ; đổi sang field thật của anh nếu khác
            advance = commercial.total_advance_amount if hasattr(commercial, "total_advance_amount") else 0.0

            rec.portal_credit_limit = float(credit_limit or 0.0)
            rec.portal_total_debt = float(credit or 0.0)
            rec.portal_total_advance = float(advance or 0.0)


    approval_steps_html = fields.Html(string="Các bước duyệt", compute='_compute_approval_steps_html')

    @api.depends('workflow_id', 'current_sequence')
    def _compute_approval_steps_html(self):
        def _num(x, default=None):
            """Chuẩn hoá về float; hỗ trợ '12,345', '-7.2%', None, int/float."""
            if x is None:
                return default
            if isinstance(x, (int, float)):
                return float(x)
            s = str(x).strip()
            if s.endswith('%'):
                s = s[:-1]
            s = s.replace(',', '')
            try:
                return float(s)
            except Exception:
                return default

        for order in self:
            html = '<div class="oe_approval_steps_bar oe_approval_steps_bar_mini">'
            steps = []
            step_states = {}

            context_vals = order.get_approval_context() if order.workflow_id else {}
            all_steps = order.workflow_id.step_ids.sorted('sequence') if order.workflow_id else []
            cur_seq = all_steps and int(all_steps[0].sequence) or 0
            current_sequence = int(order.current_sequence or 0)
            sequence_set = set()
            _logger.info(f"[APPROVAL][HTML] context_vals: {context_vals}")

            while cur_seq and cur_seq not in sequence_set:
                sequence_set.add(cur_seq)
                cur_step = all_steps.filtered(lambda s: int(s.sequence) == cur_seq)
                if not cur_step:
                    _logger.info(f"[APPROVAL][HTML] Không tìm thấy step có sequence={cur_seq}, dừng.")
                    break
                cur_step = cur_step[0]
                steps.append(cur_step)

                # Trạng thái hiển thị theo current_sequence hiện tại
                if cur_seq < current_sequence:
                    step_states[cur_seq] = 'done'
                elif cur_seq == current_sequence:
                    step_states[cur_seq] = 'current'
                else:
                    step_states[cur_seq] = 'todo'

                _logger.info(f"[APPROVAL][HTML] Đang ở bước {cur_seq} - {cur_step.name}")

                # Xét điều kiện để quyết định bước tiếp theo
                next_seq = 0
                conds = order._safe_parse_conditions(cur_step.conditions_json)
                found = False

                for idx, cond in enumerate(conds):
                    cond_if = cond.get("if", {}) or {}
                    match = True
                    log_detail = []
                    for field, rule in cond_if.items():
                        val_raw = context_vals.get(field)
                        # Hỗ trợ so sánh dạng số
                        if isinstance(rule, dict):
                            for op, rule_val in rule.items():
                                rv = _num(rule_val, rule_val)
                                vv = _num(val_raw, None)
                                try:
                                    if vv is None or rv is None:
                                        result = False
                                    elif op == "$lte": result = (vv <= rv)
                                    elif op == "$lt":  result = (vv <  rv)
                                    elif op == "$gte": result = (vv >= rv)
                                    elif op == "$gt":  result = (vv >  rv)
                                    elif op == "$eq":  result = (vv == rv)
                                    elif op == "$ne":  result = (vv != rv)
                                    else:
                                        result = False
                                    log_detail.append(f"{field}: {val_raw}->{vv} {op} {rule_val}->{rv} ? {result}")
                                    if not result:
                                        match = False
                                except Exception:
                                    match = False
                                    log_detail.append(f"{field}: {val_raw} {op} {rule_val} ? EXC")
                        else:
                            # So sánh bằng tuyệt đối, vẫn chuẩn hoá số nếu có thể
                            left  = _num(val_raw, val_raw)
                            right = _num(rule, rule)
                            result = (left == right)
                            log_detail.append(f"{field}: {val_raw}->{left} == {rule}->{right} ? {result}")
                            if not result:
                                match = False

                    _logger.info(f"[APPROVAL][HTML]    - Cond[{idx}] {cond_if} | {log_detail} | Match={match}")
                    if match:
                        next_seq = int(cond.get('next_sequence', 0) or 0)
                        _logger.info(f"[APPROVAL][HTML]    -> Điều kiện match, next_seq = {next_seq}")
                        found = True
                        break

                if not found:
                    _logger.info("[APPROVAL][HTML]    -> Không có điều kiện match, dừng theo luồng.")
                    break

                # Chống vòng lặp vô hạn (ví dụ next_seq trỏ về chính nó)
                if next_seq in sequence_set:
                    _logger.warning(f"[APPROVAL][HTML] next_seq={next_seq} đã đi qua trước đó, dừng để tránh lặp.")
                    break

                cur_seq = next_seq

            # Render trạng thái
            # Nếu đã approved hết (current_sequence = 0),
            # thì chỉ bước CUỐI CÙNG là 'done', còn lại là 'todo'
            if current_sequence == 0 and steps:
                last_seq = int(steps[-1].sequence)
                for s in steps:
                    step_states[int(s.sequence)] = 'todo'
                step_states[last_seq] = 'done'

            for step in steps:
                step_seq = int(step.sequence)
                state = step_states.get(step_seq, 'todo')
                if state == 'done':
                    css = 'oe_approval_step_done'
                    icon = '<span class="step-icon">&#10003;</span>'
                elif state == 'current':
                    css = 'oe_approval_step_current'
                    icon = '<span class="step-icon step-icon-current"></span>'
                else:
                    css = 'oe_approval_step_todo'
                    icon = '<span class="step-icon"></span>'
                html += f'<span class="oe_approval_step {css}">{icon}<span class="step-label">{step.name}</span></span>'

            html += '</div>'
            order.approval_steps_html = html



    invoice_count = fields.Integer(string="Invoice Count", compute='_get_invoiced')
    invoice_ids = fields.Many2many(
        comodel_name='account.move',
        string="Invoices",
        compute='_get_invoiced',
        search='_search_invoice_ids',
        copy=False)
    invoice_status = fields.Selection(
        selection=INVOICE_STATUS,
        string="Invoice Status",
        compute='_compute_invoice_status',
        store=True)

    # Payment fields
    transaction_ids = fields.Many2many(
        comodel_name='payment.transaction',
        relation='sale_custom_order_transaction_rel', column1='sale_order_id', column2='transaction_id',
        string="Transactions",
        copy=False, readonly=True)
    authorized_transaction_ids = fields.Many2many(
        comodel_name='payment.transaction',
        string="Authorized Transactions",
        compute='_compute_authorized_transaction_ids',
        copy=False,
        compute_sudo=True)
    amount_paid = fields.Float(
        string="Payment Transactions Amount",
        help="Sum of transactions made in through the online payment form that are in the state"
             " 'done' or 'authorized' and linked to this order.",
        compute='_compute_amount_paid',
        compute_sudo=True,
    )

    # UTMs - enforcing the fact that we want to 'set null' when relation is unlinked
    campaign_id = fields.Many2one(ondelete='set null')
    medium_id = fields.Many2one(ondelete='set null')
    source_id = fields.Many2one(ondelete='set null')

    # Followup ?
    tag_ids = fields.Many2many(
        comodel_name='crm.tag',
        relation='sale_custom_order_tag_rel', column1='order_id', column2='tag_id',
        string="Tags")

    # Remaining non stored computed fields (hide/make fields readonly, ...)
    amount_undiscounted = fields.Float(
        string="Amount Before Discount",
        compute='_compute_amount_undiscounted', digits=0)
    country_code = fields.Char(related='company_id.account_fiscal_country_id.code', string="Country code")
    company_price_include = fields.Selection(related='company_id.account_price_include')
    duplicated_order_ids = fields.Many2many(comodel_name='sale_custom.order', compute='_compute_duplicated_order_ids')
    expected_date = fields.Datetime(
        string="Expected Date",
        compute='_compute_expected_date', store=False,  # Note: can not be stored since depends on today()
        help="Delivery date you can promise to the customer, computed from the minimum lead time of the order lines.")
    is_expired = fields.Boolean(string="Is Expired", compute='_compute_is_expired')
    partner_credit_warning = fields.Text(
        compute='_compute_partner_credit_warning')
    tax_calculation_rounding_method = fields.Selection(
        related='company_id.tax_calculation_rounding_method',
        depends=['company_id'])
    tax_country_id = fields.Many2one(
        comodel_name='res.country',
        compute='_compute_tax_country_id',
        # Avoid access error on fiscal position when reading a sale order with company != user.company_ids
        compute_sudo=True)  # used to filter available taxes depending on the fiscal country and position
    tax_totals = fields.Binary(compute='_compute_tax_totals', exportable=False)
    terms_type = fields.Selection(related='company_id.terms_type')
    type_name = fields.Char(string="Type Name", compute='_compute_type_name')
    
    # Remaining ux fields (not computed, not stored)

    show_update_fpos = fields.Boolean(
        string="Has Fiscal Position Changed", store=False)  # True if the fiscal position was changed
    has_active_pricelist = fields.Boolean(
        compute='_compute_has_active_pricelist')
    show_update_pricelist = fields.Boolean(
        string="Has Pricelist Changed", store=False)  # True if the pricelist was changed
    applied_promotion_ids = fields.Many2many(
        'oms.promotion', 'sale_order_applied_promotion_rel', 'order_id', 'promotion_id',
        string='Khuyến mãi đã áp dụng'
    )
    promotion_ids = fields.Many2many('oms.promotion', string="Khuyến mãi đang chọn (tạm thời)", copy=False)
    promotion_is_applied = fields.Boolean("Khuyến mãi đã được áp dụng", default=False)

    current_step_name = fields.Char(string="Bước duyệt hiện tại", compute="_compute_current_step_name")


    is_promotion_line = fields.Boolean(
        compute='_is_promotion_line')
    
    workflow_id = fields.Many2one('approval.workflow', string='Quy trình duyệt')
    current_sequence = fields.Integer(string='Bước hiện tại', default=1, store=True)
    max_discount = fields.Float('Mức giảm giá tối đa (%)', compute='_compute_max_discount', store=True)
    

    # ================= CUSTOM FIELDS MAPPING =================
    VoucherTypeID = fields.Selection(
        selection=[('1310', '1310 - Phiếu bán hàng')],
        string="VoucherTypeID",
        default='1310',
        readonly=True,
        
    )

    docnumber = fields.Char(
        string="Số lệnh SAP",
        readonly=True,
        copy=False,
        tracking=True,
        help="Giá trị 'docnumber' trả về từ API CreateSO (ví dụ: HCM-25-040899).",
    )

    # --- ETA: mặc định 17:00 hôm nay ---
    def _default_eta_17h_today(self):
        today = fields.Date.context_today(self)
        dt = datetime.combine(today, time(17, 0))
        return fields.Datetime.to_string(dt)

    eta_datetime = fields.Datetime(
        string='ETA (Dự kiến giao)',
        default=_default_eta_17h_today,
        tracking=True,
    )

    # --- Checkbox: Đã đủ hàng ---
    is_fully_stocked = fields.Boolean(
        string='Đã đủ hàng',
        compute='_compute_is_fully_stocked',
        store=True,
        readonly=True,
        tracking=True,
    )

    @api.depends(
        'order_line.is_prepared',
        'order_line.display_type',
        'order_line.is_downpayment',
    )
    def _compute_is_fully_stocked(self):
        for order in self:
            # chỉ tính các dòng sản phẩm thực sự
            lines = order.order_line.filtered(
                lambda l: not l.display_type and not getattr(l, 'is_downpayment', False)
            )
            # Nếu không có dòng hàng => False; có dòng và tất cả đều prepared => True
            order.is_fully_stocked = bool(lines) and all(l.is_prepared for l in lines)

    CardCode = fields.Char(
        string="Mã khách hàng",
        related='partner_id.ref',
        store=True,
        readonly=True
    )
    # --- THAY field SlpCode: bỏ readonly, thêm default động
    SlpCode = fields.Many2one(
        'res.users',
        string='Nhân viên bán hàng',
        default=lambda self: self._default_slpcode(),
        tracking=True,
    )

    # Danh sách user cho domain của SlpCode
    slpcode_domain_ids = fields.Many2many(
        'res.users',
        compute='_compute_slpcode_domain_ids',
        compute_sudo=True,
        store=False,
    )

    # -----------------------------
    # Helpers về quyền
    # -----------------------------
    def _user_can_see_all(self, user=None):
        user = user or self.env.user
        has_sales_all = user.has_group('sales_team.group_sale_salesman_all_leads')
        has_logistics = user.has_group('sale_custom.group_logistics')

        # Đúng nếu chỉ thuộc MỘT trong hai nhóm (XOR), sai nếu thuộc cả hai hoặc không nhóm nào
        return has_sales_all and not has_logistics
        # hoặc ngắn gọn: return bool(has_sales_all ^ has_logistics)


    # -----------------------------
    # Default & Onchange giữ nguyên
    # -----------------------------
    def _default_slpcode(self):
        User = self.env['res.users'].sudo()
        partner = None
        pid = self.env.context.get('default_partner_id')
        if pid:
            base_partner = self.env['res.partner'].sudo().browse(pid)
            partner = base_partner.commercial_partner_id if base_partner and base_partner.commercial_partner_id else base_partner
        if partner and partner.ref:
            cust = self.env['oms.customer'].sudo().search([('card_code', '=', partner.ref)], limit=1)
            if cust and cust.slp_code:
                u = User.search([('slp_code', '=', cust.slp_code), ('active', '=', True)], limit=1)
                if u:
                    return u.id
        if partner and partner.user_id:
            return partner.user_id.id
        return self.env.user.id

    @api.onchange('partner_id')
    def _onchange_partner_set_slpcode(self):
        for rec in self:
            user_id = False
            partner = rec.partner_id.commercial_partner_id if rec.partner_id and rec.partner_id.commercial_partner_id else rec.partner_id
            if partner and partner.ref:
                cust = self.env['oms.customer'].sudo().search([('card_code', '=', partner.ref)], limit=1)
                if cust and cust.slp_code:
                    u = self.env['res.users'].sudo().search(
                        [('slp_code', '=', cust.slp_code), ('active', '=', True)], limit=1
                    )
                    user_id = u.id or False
            if not user_id and partner and partner.user_id:
                user_id = partner.user_id.id
            rec.SlpCode = user_id or self.env.user.id

    # -----------------------------
    # Domain cho SlpCode:
    # - Sales thường: chỉ thấy chính họ
    # - Quản lý/all-leads: thấy toàn bộ user active (có thể lọc thêm slp_code nếu muốn)
    # -----------------------------
    @api.depends_context('uid')
    def _compute_slpcode_domain_ids(self):
        User = self.env['res.users'].sudo()
        user = self.env.user.sudo()
        if self._user_can_see_all(user=user):
            domain_users = User.search([('active', '=', True)])
            # Nếu chỉ muốn user có slp_code: thêm ('slp_code','!=',False)
            # domain_users = User.search([('active', '=', True), ('slp_code', '!=', False)])
        else:
            domain_users = user
        ids = domain_users.ids
        for order in self:
            order.slpcode_domain_ids = [(6, 0, ids)]

    GroupNum = fields.Many2one(
        'oms.payment.terms',
        string="Nhóm khách hàng",
        domain="[]"
    )

    @api.onchange('partner_id')
    def _onchange_partner_id_set_groupnum(self):
        for rec in self:
            card_code = rec.partner_id.ref or ''
            customer = self.env['oms.customer'].search([('card_code', '=', card_code)], limit=1)
            if customer and customer.group_num:
                # Giả sử group_num là mã số/mã nhóm điều khoản (Char/Int)
                group_term = self.env['oms.payment.terms'].search([('group_num', '=', customer.group_num)], limit=1)
                rec.GroupNum = group_term.id if group_term else False
            else:
                rec.GroupNum = False


    TrnspCode = fields.Selection([
        ('30', 'Giao tận nơi'),
    ], string="Phương thức vận chuyển", default='30')

    trnsp_id = fields.Many2one(
        'oms.transport',
        string="Phương thức vận chuyển",
        tracking=True,
        default=lambda self: self._default_transport()
    )

    @api.model
    def _default_transport(self):
        """Lấy bản ghi transport có trnsp_code = 3 làm mặc định"""
        return self.env['oms.transport'].search([('trnsp_code', '=', 3)], limit=1)


    IsIssueInvoice = fields.Boolean(string="Phát hành hóa đơn", default=True, readonly=True)

    WhsCode = fields.Many2one(
        'oms.warehouse',
        string="Kho (WhsCode)",
        default=lambda self: self._get_default_whscode(),
        tracking=True,
    )

    def _get_default_whscode(self):
        branch = self.env.user.branch or ''
        branch_map = {
            'HCM': 'HCMVP201',
            'CTH': 'CTHVP101',
            'HNI': 'HNIVP101'
        }
        code = branch_map.get(branch)
        if code:
            whs = self.env['oms.warehouse'].search([('whs_code', '=', code)], limit=1)
            return whs and whs.id or False
        return False

    @api.onchange('WhsCode')
    def _onchange_whscode_branch(self):
        branch = self.env.user.branch or ''
        return {
            'domain': {
                'WhsCode': [('active', '=', True), ('store_name', '=', branch), ('u_whs_type', '=', 'BD')]
            }
        }


    IsCOD = fields.Boolean(string="COD", tracking=True, default=False)
    IsCOCQ = fields.Boolean(string="CO-CQ", tracking=True, default=False)
    IsInstall = fields.Boolean(string="Lắp đặt", tracking=True, default=False)
    IsSetup = fields.Boolean(string="Cài đặt", tracking=True, default=False)


    BPLId = fields.Selection([
        ('1', 'CN HCM'),
        ('2', 'CN Hà Nội'),
    ], string="BPLId", readonly=True, tracking=True, default='1')

    Store = fields.Selection([
        ('1', 'HCM'),
        ('2', 'CTH'),
        ('3', 'HNI'),
    ], string="Store", readonly=True, tracking=True, default='1')

    InvStore = fields.Selection([
        ('1', 'HCM'),
        ('2', 'CTH'),
        ('3', 'HNI'),
    ], string="InvStore", readonly=True, tracking=True, default='1')

    Project = fields.Many2one(
        'oms.prjinfo', 
        string="Dự án", 
        tracking=True,
        domain="[('u_card_code', '=', partner_ref)]"
    )

    marketing_campaign_id = fields.Many2one(
        'oms.marketing.campaign', 
        string="Chiến dịch Marketing",
        domain="[('u_active', '=', 'Y')]" 
    )

    blanket_agreement_id = fields.Many2one(
        'oms.sales.blanket.agreement',
        string="Hợp đồng nguyên tắc",
        domain="""[('bp_code', '=', partner_ref), ('start_date', '<=', date_order),
            '|', ('end_date', '=', False), ('end_date', '>=', date_order)
        ]""",
        tracking=True,
    )


    BusinessArea = fields.Selection([
        ('AUT', 'AUT'),
    ], string="BusinessArea", readonly=True, default='AUT')

    PostingDate = fields.Date(string="PostingDate", tracking=True,)
    DocDueDate = fields.Date(string="DocDueDate",tracking=True,)
    quote_date = fields.Date(string="Ngày báo giá", tracking=True, default=fields.Date.context_today, readonly=True, states={'draft': [('readonly', False)]})
    
    quote_date_formatted = fields.Char(
        string="Ngày báo giá (định dạng)",
        compute='_compute_quote_date_formatted',
        store=False,
        tracking=True,
        readonly=True
    )

    @api.depends('quote_date')
    def _compute_quote_date_formatted(self):
        for rec in self:
            rec.quote_date_formatted = self.format_vn_date(rec.quote_date, city="Hồ Chí Minh")

    @staticmethod
    def format_vn_date(date_obj, city="Hồ Chí Minh"):
        if not date_obj:
            return ""
        if isinstance(date_obj, str):
            date_obj = datetime.fromisoformat(date_obj)
        return f"{city}, Ngày {date_obj.day} tháng {date_obj.month} năm {date_obj.year}"

    order_date = fields.Date(string="Ngày đặt hàng", readonly=True, tracking=True, states={'draft': [('readonly', False)]})
    expected_delivery_date = fields.Date(
        string="Ngày giao hàng dự kiến",
        tracking=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: fields.Date.context_today(self) + relativedelta(days=30),
    )
    actual_delivery_date = fields.Date(string="Ngày giao hàng thực tế", tracking=True,)

    S1No = fields.Char(string="S1No", readonly=True)
    VoucherNo = fields.Char(string="VoucherNo", readonly=True)

    StatusID = fields.Selection([
        ('0', 'Draft'),
        ('1', 'Open'),
        ('2', 'Approved'),
        ('3', 'Rejected')
    ], string="StatusID", default='1', readonly=True)

    cntct_code = fields.Many2one(
        'oms.contact',
        string="Người liên hệ",
        domain="[('card_code', '=', partner_ref)]",
    )

    # =========================
    # Helpers
    # =========================
    def _uc_pick_contact(self, card_code):
        """Pick oms.contact theo CardCode.
        Ưu tiên:
          1) contact active (nếu có field active), mới nhất -> id desc
          2) fallback: bất kể active, lấy contact CUỐI CÙNG (id desc / update_date desc)
        """
        card_code = (card_code or "").strip()
        Contact = self.env["oms.contact"].sudo()
        if not card_code:
            return Contact.browse()

        base_domain = [("card_code", "=", card_code)]

        # ưu tiên field thời gian nếu có, và luôn id desc để "lấy cái cuối cùng"
        if "update_date" in Contact._fields:
            order_by = "update_date desc, id desc"
        elif "write_date" in Contact._fields:
            order_by = "write_date desc, id desc"
        else:
            order_by = "id desc"

        # 1) thử active trước
        if "active" in Contact._fields:
            c = Contact.search(base_domain + [("active", "=", True)], order=order_by, limit=1)
            if c:
                return c

        # 2) fallback: lấy contact cuối cùng, kể cả inactive
        return Contact.search(base_domain, order=order_by, limit=1)
    def _uc_autofill_contact(self):
        """Set cntct_code nếu đang trống hoặc sai card_code."""
        for order in self:
            card = (order.partner_ref or "").strip()
            if not card:
                order.cntct_code = False
                continue

            # nếu đang đúng card_code thì giữ
            if order.cntct_code and (order.cntct_code.card_code or "").strip() == card:
                continue

            c = order._uc_pick_contact(card)
            order.cntct_code = c or False

            _logger.info(
                "[UC_CONTACT] autofill order=%s partner=%s card=%s picked=%s",
                order.id, order.partner_id.id if order.partner_id else None,
                card, c.id if c else None
            )

    # =========================
    # UI onchange
    # =========================
    @api.onchange("partner_id")
    def _onchange_partner_id_reset_contact(self):
        for order in self:
            order._uc_autofill_contact()
            # nếu muốn cảnh báo khi không có contact:
            if order.partner_id and order.partner_ref and not order.cntct_code:
                return {
                    "warning": {
                        "title": "Không tìm thấy người liên hệ",
                        "message": f"CardCode {order.partner_ref} chưa có oms.contact để tự chọn.",
                    }
                }

    # =========================
    # Server-side: create/write (quan trọng!)
    # =========================
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order, vals in zip(orders, vals_list):
            if self.env.context.get("uc_skip_autocontact"):
                continue
            # nếu user không truyền cntct_code -> tự fill
            if not vals.get("cntct_code") and order.partner_id:
                order.with_context(uc_skip_autocontact=True)._uc_autofill_contact()
        return orders

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("uc_skip_autocontact"):
            return res

        # đổi partner mà không set cntct_code -> tự fill lại
        if "partner_id" in vals and "cntct_code" not in vals:
            self.with_context(uc_skip_autocontact=True)._uc_autofill_contact()
        return res
        
    u_carcodecommission = fields.Many2one(
        'oms.contact',
        string="Người nhận HH"
    )

    partner_ref = fields.Char(compute='_compute_partner_ref', store=False)

    company_ref = fields.Char(string="Tên công ty viết tắt")

    def _uc_partner_ref_from_partner(self, partner):
        if not partner:
            return ""
        commercial = partner.commercial_partner_id if partner.commercial_partner_id else partner
        return (getattr(commercial, "ref", False) or "").strip()

    @api.depends('partner_id')
    def _compute_partner_ref(self):
        for rec in self:
            rec.partner_ref = rec._uc_partner_ref_from_partner(rec.partner_id) or False

    def _uc_pick_contact_by_card(self, card_code):
        card_code = (card_code or "").strip()
        if not card_code:
            return False
        return self.env["oms.contact"].sudo().search(
            [("card_code", "=", card_code), ("active", "=", True)],
            order="id asc",
            limit=1,
        )

    CntctName = fields.Char(related='cntct_code.name', string="Tên liên hệ", readonly=True)
    vat = fields.Char(string="Mã số thuế (MST)", related='partner_id.vat', store=True, readonly=True)

    Address = fields.Char(
        string="Address",
        readonly=True,
        compute='_compute_billing_address'
    )

    @api.depends('partner_ref')
    def _compute_billing_address(self):
        for rec in self:
            value = ''
            if rec.partner_ref:
                addr = rec.env['oms.address'].search([
                    ('card_code', '=', rec.partner_ref),
                    ('adres_type', '=', 'B')
                ], limit=1)
                value = addr.name or ''
            rec.Address = value

    ShipToCode = fields.Many2one(
        'oms.address',
        string="ShipToCode",
        domain="[('card_code', '=', partner_ref), ('adres_type', '=', 'S')]",
        default=lambda self: self._default_ship_to_code(),
        tracking=True,
    )

    # Helper: lấy địa chỉ giao hàng đầu tiên theo domain
    def _default_ship_to_code(self):
        ICP = self.env['ir.config_parameter'].sudo()
        # Lấy partner_ref từ context nếu có, hoặc suy từ partner mặc định
        partner_ref = (self.env.context.get('default_partner_ref') or '') or ''
        if not partner_ref:
            partner_id = self.env.context.get('default_partner_id')
            if partner_id:
                partner = self.env['res.partner'].browse(partner_id)
                partner_ref = self._uc_partner_ref_from_partner(partner)

        domain = [('adres_type', '=', 'S')]
        if partner_ref:
            domain.append(('card_code', '=', partner_ref))

        # Ưu tiên theo 'sequence' nếu model có, rồi theo id tăng dần
        Order = self.env['oms.address']
        try:
            rec = Order.search(domain, order='sequence asc, id asc', limit=1)
        except Exception:
            rec = Order.search(domain, order='id asc', limit=1)
        return rec.id or False

    # Khi đổi khách hàng/partner_ref thì tự set lại ShipToCode là bản ghi đầu tiên hợp lệ
    @api.onchange('partner_id', 'partner_invoice_id', 'partner_ref')
    def _onchange_partner_set_shipto_first(self):
        for order in self:
            # Nếu đã có ShipToCode và vẫn còn nằm trong domain thì giữ nguyên
            if order.ShipToCode:
                ok = True
                if order.partner_ref and order.ShipToCode.card_code != order.partner_ref:
                    ok = False
                if getattr(order.ShipToCode, 'adres_type', '') != 'S':
                    ok = False
                if ok:
                    continue
            # Chọn lại bản ghi đầu tiên theo domain
            partner_ref = (order.partner_ref or '').strip()
            domain = [('adres_type', '=', 'S')]
            if partner_ref:
                domain.append(('card_code', '=', partner_ref))
            try:
                rec = self.env['oms.address'].search(domain, order='sequence asc, id asc', limit=1)
            except Exception:
                rec = self.env['oms.address'].search(domain, order='id asc', limit=1)
            order.ShipToCode = rec or False

    def _find_oms_ship_to_from_partner_shipping(self):
        """Return the OMS shipping address selected in partner_shipping_id."""
        self.ensure_one()
        shipto = self.partner_shipping_id
        address_code = (getattr(shipto, 'x_oms_address_code', False) or '').strip()
        if not address_code:
            return self.env['oms.address']

        card_code = (
            getattr(shipto, 'x_oms_card_code', False)
            or self.partner_ref
            or self._uc_partner_ref_from_partner(self.partner_id)
            or ''
        ).strip()

        domain = [('address', '=', address_code), ('adres_type', '=', 'S')]
        if card_code:
            domain.append(('card_code', '=', card_code))
        return self.env['oms.address'].search(domain, limit=1)

    @api.onchange('partner_shipping_id')
    def _onchange_partner_shipping_set_shipto(self):
        for order in self:
            addr = order._find_oms_ship_to_from_partner_shipping()
            if addr:
                order.ShipToCode = addr

    Address2 = fields.Char(related='ShipToCode.address', string="Address2", readonly=True, tracking=True,)
    CardCode2 = fields.Many2one(
        'res.partner',
        string="Khách hàng",
        tracking=True,
    )



    NoteInternal = fields.Text(string="Ghi chú nội bộ", tracking=True, size=240)

    Comments = fields.Text(string="Comments", tracking=True, size=240)
    NoteForAct = fields.Text(string="NoteForAct", tracking=True, size=240)

    # --- Elevator Technical Fields (Thang máy) ---
    EL_Construction_Code = fields.Char(string="Mã công trình", tracking=True)
    EL_Construction_Address = fields.Char(string="Địa chỉ công trình", tracking=True)  # field mới

    EL_PowerGearlessCabinet = fields.Char(string="Công suất tủ điều khiển máy kéo", tracking=True)
    EL_PowerGearlessCabinet_Note = fields.Selection(
        [('Có phòng máy', 'Có phòng máy'), ('Không phòng máy', 'Không phòng máy')],
        string="Machine Room", tracking=True,
    )

    EL_QuantityStops = fields.Char(string="Số điểm dừng", tracking=True)
    EL_Speed = fields.Char(string="Tốc độ", tracking=True)
    EL_Speed_Note = fields.Selection(
        [('Cửa tự động', 'Cửa tự động'), ('Cửa mở tay', 'Cửa mở tay')],
        string="Ghi chú Tốc độ", tracking=True,)

    EL_QuantityGroup = fields.Selection([(str(i), str(i)) for i in range(1, 9)],
        string="Số thang máy/group", tracking=True)

    # ⬇️ ĐỔI: dùng làm "Đóng gói" (không tạo field mới)
    EL_QuantityGroup_Note = fields.Selection(
        [('Thùng gỗ', 'Thùng gỗ')],
        string="Ghi chú Điện áp thắng máy kéo", tracking=True,
    )

    EL_TractorBrakeVoltage = fields.Selection(
        [('110','110'), ('24','24'), ('200','200')],
        string="Điện áp thắng máy kéo", tracking=True,
    )
    EL_TractorBrakeVoltage_Note = fields.Selection(
        [('01 cửa car','01 cửa car'), ('02 cửa car','02 cửa car')],
        string="Ghi chú Điện áp thắng máy kéo", tracking=True,
    )

    EL_Encoder = fields.Selection(
        [('1024','1024'), ('1387','1387'), ('1313','1313')],
        string="Encoder", tracking=True,
    )

    EL_Trademark = fields.Char(
        string="Thương hiệu",
        default="Thương hiệu GEAT - Ý (Date 2025)",
        tracking=True,
    )

    def _norm(self, s): 
        return (s or "").strip().lower()

    def _vnkey(self, s: str) -> str:
        """lower + bỏ dấu để nhận nhiều biến thể nhập liệu."""
        s = (s or "").strip().lower()
        s = unicodedata.normalize('NFD', s)
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
        return s

    def _map_elevator_inputs(self, vals: dict):
        out = dict(vals or {})

        # 1) Địa chỉ công trình (Char)
        addr = (out.get('EL_Construction_Address')
                or vals.get('construction_address')
                or vals.get('EL_Construction_Address_Note')
                or vals.get('construction_address_note'))
        if addr:
            out['EL_Construction_Address'] = str(addr).strip()

        # 2) Encoder (Selection: 1024/1387/1313) — mặc định 1024 nếu có text lạ
        enc_raw = out.get('EL_Encoder') or vals.get('encoder') or vals.get('EL_Encoder')
        k = self._vnkey(enc_raw)
        if k:
            if '1387' in k:
                out['EL_Encoder'] = '1387'
            elif '1313' in k:
                out['EL_Encoder'] = '1313'
            elif '1024' in k:
                out['EL_Encoder'] = '1024'
            else:
                out['EL_Encoder'] = '1024'
        else:
            out.pop('EL_Encoder', None)

        # 3) Machine Room (Selection: 'CÓ PHÒNG MÁY' / 'KHÔNG PHÒNG MÁY')
        mr_raw = out.get('EL_PowerGearlessCabinet_Note') or vals.get('machine_room') or ''
        mr = self._vnkey(mr_raw)
        if any(x in mr for x in ['co phong may', 'co', 'cophongmay', 'cpm', 'with']):
            out['EL_PowerGearlessCabinet_Note'] = 'Có phòng máy'
        elif any(x in mr for x in ['khong phong may', 'khong', 'ko', 'kpm', 'without']):
            out['EL_PowerGearlessCabinet_Note'] = 'Không phòng máy'
        else:
            out.pop('EL_PowerGearlessCabinet_Note', None)

        # 4) Ghi chú Tốc độ (Selection: 'Cửa tự động' / 'Cửa mở tay')
        spn_raw = out.get('EL_Speed_Note') or vals.get('EL_Speed_Note') or ''
        spn = self._vnkey(spn_raw)
        if any(x in spn for x in ['auto', 'tu dong', 'tu-dong']):
            out['EL_Speed_Note'] = 'Cửa tự động'
        elif any(x in spn for x in ['manual', 'mo tay', 'mo-tay', 'mo thu cong', 'thu cong']):
            out['EL_Speed_Note'] = 'Cửa mở tay'
        else:
            out.pop('EL_Speed_Note', None)

        # 5) Điện áp thắng (Selection: '110'/'24'/'200')
        volt_raw = (out.get('EL_TractorBrakeVoltage') or vals.get('tractor_voltage')
                    or vals.get('EL_TractorBrakeVoltage') or '')
        vv = self._vnkey(volt_raw)
        if '110' in vv:
            out['EL_TractorBrakeVoltage'] = '110'
        elif '200' in vv:
            out['EL_TractorBrakeVoltage'] = '200'
        elif '24' in vv:
            out['EL_TractorBrakeVoltage'] = '24'
        else:
            out.pop('EL_TractorBrakeVoltage', None)

        # 6) Ghi chú điện áp thắng (Selection: '01 cửa car' / '02 cửa car')
        vnote_raw = out.get('EL_TractorBrakeVoltage_Note') or vals.get('EL_TractorBrakeVoltage_Note') or ''
        vn = self._vnkey(vnote_raw)
        if any(x in vn for x in ['02', '2', 'two']):
            out['EL_TractorBrakeVoltage_Note'] = '02 cửa car'
        elif any(x in vn for x in ['01', '1', 'one']):
            out['EL_TractorBrakeVoltage_Note'] = '01 cửa car'
        else:
            out.pop('EL_TractorBrakeVoltage_Note', None)

        # 7) Đóng gói → dùng EL_QuantityGroup_Note (Selection: 'Thùng gỗ' / 'None')
        pk_raw = (out.get('EL_QuantityGroup_Note') or vals.get('packing')
                  or vals.get('EL_Packing') or '')
        pk = self._vnkey(pk_raw)
        if any(x in pk for x in ['go', 'thung go', 'wood', 'wooden', 'thunggo']):
            out['EL_QuantityGroup_Note'] = 'Thùng gỗ'
        elif any(x in pk for x in ['none', 'khong', 'ko', 'no']):
            out['EL_QuantityGroup_Note'] = 'None'
        else:
            out.pop('EL_QuantityGroup_Note', None)

        # 8) Số thang/group (Selection '1'..'8'): nhận cả số int/str
        grp_raw = out.get('EL_QuantityGroup') or vals.get('EL_QuantityGroup') or vals.get('group_qty')
        if grp_raw is not None:
            try:
                n = int(str(grp_raw).strip())
                if 1 <= n <= 8:
                    out['EL_QuantityGroup'] = str(n)
                else:
                    out.pop('EL_QuantityGroup', None)
            except Exception:
                out.pop('EL_QuantityGroup', None)

        return out


    def create(self, vals):
        vals = self._map_elevator_inputs(vals)
        return super().create(vals)



    report_type = fields.Selection([
        ('co_tu', 'Có tủ'),
        ('khong_tu', 'Không tủ'),
        ('san_xuat', 'Sản xuất'),
    ], string="Loại báo giá", default='co_tu')

    partner_ids_filtered = fields.Many2many(
        'res.partner',
        compute='_compute_partner_ids_filtered',
        compute_sudo=True,
        store=False
    )

    

    @api.depends('partner_id')
    def _compute_partner_ids_filtered(self):
        Partner = self.env['res.partner'].sudo()
        OmsCustomer = self.env['oms.customer'].sudo()
        user = self.env.user.sudo()

        # 3 nhóm được “xem tất cả”
        see_all = (
            (user.has_group('sales_team.group_sale_salesman_all_leads') or
            user.has_group('sales_team.group_sale_manager') or
            user.has_group('sale.group_sale_manager')) 
            and not user.has_group('sale_custom.group_logistics')
        )

        slp_code = getattr(user, 'slp_code', False)

        if see_all:
            # Admin có slp_code -> thấy hết
            partner_ids = Partner.search([]).ids
        else:
            # Còn lại: theo slp_code
            partner_ids = []
            if slp_code:
                partner_ids = OmsCustomer.search([
                    ('slp_code', '=', slp_code),
                    ('res_partner_id', '!=', False),
                ]).mapped('res_partner_id').ids

        for order in self:
            order.partner_ids_filtered = [(6, 0, partner_ids)]
    
    def init(self):
        # Nếu model mới đặt tên là sale_custom.order, bảng sẽ là sale_custom_order
        create_index(self._cr, 'sale_order_date_order_id_idx', 'sale_custom_order', ["date_order desc", "id desc"])

    #=== COMPUTE METHODS ===#

    @api.depends('partner_id')
    @api.depends_context('sale_show_partner_name')
    def _compute_display_name(self):
        if not self._context.get('sale_show_partner_name'):
            return super()._compute_display_name()
        for order in self:
            name = order.name
            if order.partner_id.name:
                name = f'{name} - {order.partner_id.name}'
            order.display_name = name

    @api.depends('order_line.product_id')
    def _compute_has_archived_products(self):
        for order in self:
            order.has_archived_products = any(
                not product.active for product in order.order_line.product_id
            )

    @api.depends('company_id')
    def _compute_require_signature(self):
        for order in self:
            order.require_signature = order.company_id.portal_confirmation_sign

    @api.depends('company_id')
    def _compute_require_payment(self):
        for order in self:
            order.require_payment = order.company_id.portal_confirmation_pay

    @api.depends('require_payment')
    def _compute_prepayment_percent(self):
        for order in self:
            order.prepayment_percent = order.company_id.prepayment_percent

    @api.depends('company_id')
    def _compute_validity_date(self):
        for order in self:
            # Lấy ngày hôm nay theo timezone user
            today = fields.Date.context_today(order)

            # Lấy timezone của user (hoặc context, hoặc UTC)
            tz_name = order.env.user.tz or order.env.context.get('tz') or 'UTC'
            tz = pytz.timezone(tz_name)

            # 17:00 theo giờ địa phương
            local_dt = tz.localize(datetime.combine(today, time(17, 0, 0)))
            # Convert sang UTC để lưu DB
            utc_dt = local_dt.astimezone(pytz.UTC)

            order.validity_date = fields.Datetime.to_string(utc_dt)

    @api.constrains('validity_date')
    def _check_validity_date_not_past(self):
        for order in self:
            if not order.validity_date:
                continue
            
            # (khuyến nghị) đừng chặn add-to-cart / draft website
            if order.state == 'draft' and getattr(order, 'website_id', False):
                continue
            
            today = fields.Date.context_today(order)
    
            # validity_date có thể là date hoặc datetime -> đưa về date theo TZ user
            field = order._fields.get('validity_date')
            if field and field.type == 'datetime':
                valid_date = fields.Date.to_date(
                    fields.Datetime.context_timestamp(order, order.validity_date)
                )
            else:
                valid_date = fields.Date.to_date(order.validity_date)
    
            if valid_date < today:
                raise ValidationError(_("Ngày giao hàng dự kiến phải lớn hơn hoặc bằng ngày hiện tại."))


    def _compute_journal_id(self):
        self.journal_id = False

    @api.depends('partner_id')
    def _compute_note(self):
        use_invoice_terms = self.env['ir.config_parameter'].sudo().get_param('account.use_invoice_terms')
        if not use_invoice_terms:
            return
        for order in self:
            order = order.with_company(order.company_id)
            if order.terms_type == 'html' and self.env.company.invoice_terms_html:
                baseurl = html_keep_url(order._get_note_url() + '/terms')
                context = {'lang': order.partner_id.lang or self.env.user.lang}
                order.note = _('Terms & Conditions: %s', baseurl)
                del context
            elif not is_html_empty(self.env.company.invoice_terms):
                if order.partner_id.lang:
                    order = order.with_context(lang=order.partner_id.lang)
                order.note = order.env.company.invoice_terms

    @api.model
    def _get_note_url(self):
        return self.env.company.get_base_url()

    @api.depends('partner_id')
    def _compute_partner_invoice_id(self):
        for order in self:
            order.partner_invoice_id = order.partner_id.address_get(['invoice'])['invoice'] if order.partner_id else False

    @api.depends('partner_id')
    def _compute_partner_shipping_id(self):
        for order in self:
            order.partner_shipping_id = order.partner_id.address_get(['delivery'])['delivery'] if order.partner_id else False

    @api.depends('partner_shipping_id', 'partner_id', 'company_id')
    def _compute_fiscal_position_id(self):
        """
        Trigger the change of fiscal position when the shipping address is modified.
        """
        cache = {}
        for order in self:
            if not order.partner_id:
                order.fiscal_position_id = False
                continue
            fpos_id_before = order.fiscal_position_id.id
            key = (order.company_id.id, order.partner_id.id, order.partner_shipping_id.id)
            if key not in cache:
                cache[key] = self.env['account.fiscal.position'].with_company(
                    order.company_id
                )._get_fiscal_position(order.partner_id, order.partner_shipping_id).id
            if fpos_id_before != cache[key] and order.order_line:
                order.show_update_fpos = True
            order.fiscal_position_id = cache[key]

    @api.depends('partner_id')
    def _compute_payment_term_id(self):
        for order in self:
            order = order.with_company(order.company_id)
            order.payment_term_id = order.partner_id.property_payment_term_id

    def _uc_is_shop_request(self):
        try:
            return bool(getattr(request, "httprequest", None) and (request.httprequest.path or "").startswith("/shop"))
        except Exception:
            return False

    def _uc_get_website_pricelist(self):
        Pricelist = self.env["product.pricelist"].sudo()
        Website = self.env["website"].sudo() if "website" in self.env else False
        ctx = self.env.context or {}

        pl = False
        pl_id = ctx.get("website_sale_current_pl") or ctx.get("website_pricelist_id")
        if pl_id:
            pl = Pricelist.browse(int(pl_id)).exists()

        if not pl and Website and ctx.get("website_id"):
            try:
                w = Website.browse(int(ctx["website_id"])).exists()
                if w and getattr(w, "pricelist_id", False):
                    pl = w.pricelist_id
            except Exception:
                pass

        if not pl:
            try:
                if getattr(request, "website", None) and getattr(request.website, "pricelist_id", False):
                    pl = request.website.pricelist_id
                else:
                    sid = getattr(request, "session", {}).get("website_sale_current_pl")
                    if sid:
                        pl = Pricelist.browse(int(sid)).exists()
            except Exception:
                pass

        return pl

    def _uc_force_web_pricelist_and_recompute(self):
        self.ensure_one()
        if not self._uc_is_shop_request():
            return False

        pl = self._uc_get_website_pricelist()
        if not pl:
            return False

        if self.pricelist_id and self.pricelist_id.id == pl.id:
            return pl

        # set đúng pricelist
        self.sudo().write({"pricelist_id": pl.id})

        # recompute giá line theo tier (qty-based)
        try:
            self.with_context(force_price_recomputation=True)._recompute_prices()
        except Exception:
            # fallback nếu instance bạn dùng action_update_prices
            if hasattr(self, "action_update_prices"):
                self.with_context(force_price_recomputation=True).action_update_prices()

        return pl

    @api.depends('partner_id', 'company_id', 'state')
    def _compute_pricelist_id(self):
        Pricelist = self.env['product.pricelist'].sudo()
        Website = self.env['website'].sudo() if 'website' in self.env else False

        for order in self:
            if order.state != 'draft':
                continue

            pl = False
            ctx = order.env.context or {}

            # 1) Ưu tiên lấy từ context/session web
            pl_id = ctx.get('website_sale_current_pl') or ctx.get('website_pricelist_id')
            if pl_id:
                pl = Pricelist.browse(int(pl_id)).exists()

            # 2) Nếu có website_id trong context -> lấy pricelist của website
            if not pl and Website and ctx.get('website_id'):
                try:
                    w = Website.browse(int(ctx['website_id'])).exists()
                    if w and getattr(w, 'pricelist_id', False):
                        pl = w.pricelist_id
                except Exception:
                    pass

            # 3) Nếu đang trong HTTP request -> lấy request.website / request.session
            if not pl:
                try:
                    if getattr(request, 'website', None) and getattr(request.website, 'pricelist_id', False):
                        pl = request.website.pricelist_id
                    else:
                        sid = getattr(request, 'session', {}).get('website_sale_current_pl')
                        if sid:
                            pl = Pricelist.browse(int(sid)).exists()
                except Exception:
                    # không có request context (upgrade/init) -> bỏ qua
                    pass

            # Nếu lấy được pricelist từ web -> dùng luôn
            if pl:
                order.pricelist_id = pl
                continue

            # 4) Lấy từ oms.customer.pricelist (theo tài liệu AUT)
            if order.partner_id:
                CustomerPricelist = self.env['oms.customer.pricelist'].sudo()
                at_date = order.date_order.date() if order.date_order else fields.Date.today()
                pl_from_cust = CustomerPricelist.get_pricelist_for_partner(
                    order.partner_id, at_date=at_date
                )
                if pl_from_cust and pl_from_cust.active:
                    order.pricelist_id = pl_from_cust
                    continue

            # 5) Fallback: theo partner property
            if not order.partner_id:
                order.pricelist_id = False
                continue
            order = order.with_company(order.company_id)
            order.pricelist_id = order.partner_id.property_product_pricelist

    @api.depends('pricelist_id', 'company_id')
    def _compute_currency_id(self):
        for order in self:
            order.currency_id = order.pricelist_id.currency_id or order.company_id.currency_id

    @api.depends('currency_id', 'date_order', 'company_id')
    def _compute_currency_rate(self):
        for order in self:
            order.currency_rate = self.env['res.currency']._get_conversion_rate(
                from_currency=order.company_id.currency_id,
                to_currency=order.currency_id,
                company=order.company_id,
                date=(order.date_order or fields.Datetime.now()).date(),
            )

    @api.depends('company_id')
    def _compute_has_active_pricelist(self):
        for order in self:
            order.has_active_pricelist = bool(self.env['product.pricelist'].search(
                [('company_id', 'in', (False, order.company_id.id)), ('active', '=', True)],
                limit=1,
            ))

    @api.depends('partner_id', 'SlpCode')
    def _compute_user_id(self):
        for order in self:
            if order.SlpCode:
                order.user_id = order.SlpCode
                continue

            if order.partner_id and not (order._origin.id and order.user_id):
                # Recompute the salesman on partner change
                #   * if partner is set (is required anyway, so it will be set sooner or later)
                #   * if the order is not saved or has no salesman already
                commercial = order.partner_id.commercial_partner_id if order.partner_id.commercial_partner_id else order.partner_id
                order.user_id = (
                    order.partner_id.user_id
                    or commercial.user_id
                    or (self.env.user.has_group('sales_team.group_sale_salesman') and self.env.user)
                )

    @api.depends('partner_id', 'user_id')
    def _compute_team_id(self):
        cached_teams = {}
        for order in self:
            default_team_id = self.env.context.get('default_team_id', False) or order.team_id.id
            user_id = order.user_id.id
            company_id = order.company_id.id
            key = (default_team_id, user_id, company_id)
            if key not in cached_teams:
                cached_teams[key] = self.env['crm.team'].with_context(
                    default_team_id=default_team_id,
                )._get_default_team_id(
                    user_id=user_id,
                    domain=self.env['crm.team']._check_company_domain(company_id),
                )
            order.team_id = cached_teams[key]

    @api.depends('order_line.price_subtotal', 'currency_id', 'company_id', 'payment_term_id')
    def _compute_amounts(self):
        AccountTax = self.env['account.tax']
        for order in self:
            order_lines = order.order_line.filtered(lambda x: not x.display_type)
            base_lines = [line._prepare_base_line_for_taxes_computation() for line in order_lines]
            base_lines += order._add_base_lines_for_early_payment_discount()
            AccountTax._add_tax_details_in_base_lines(base_lines, order.company_id)
            AccountTax._round_base_lines_tax_details(base_lines, order.company_id)
            tax_totals = AccountTax._get_tax_totals_summary(
                base_lines=base_lines,
                currency=order.currency_id or order.company_id.currency_id,
                company=order.company_id,
            )
            order.amount_untaxed = tax_totals['base_amount_currency']
            order.amount_tax = tax_totals['tax_amount_currency']
            order.amount_total = tax_totals['total_amount_currency']

    def _add_base_lines_for_early_payment_discount(self):
        """
        When applying a payment term with an early payment discount, and when said payment term computes the tax on the
        'mixed' setting, the tax computation is always based on the discounted amount untaxed.
        Creates the necessary line for this behavior to be displayed.
        :returns: array containing the necessary lines or empty array if the payment term isn't epd mixed
        """
        self.ensure_one()
        epd_lines = []
        if (
            self.payment_term_id.early_discount
            and self.payment_term_id.early_pay_discount_computation == 'mixed'
            and self.payment_term_id.discount_percentage
        ):
            percentage = self.payment_term_id.discount_percentage
            currency = self.currency_id or self.company_id.currency_id
            for line in self.order_line.filtered(lambda x: not x.display_type):
                line_amount_after_discount = (line.price_subtotal / 100) * percentage
                epd_lines.append(self.env['account.tax']._prepare_base_line_for_taxes_computation(
                    record=self,
                    price_unit=-line_amount_after_discount,
                    quantity=1.0,
                    currency_id=currency,
                    sign=1,
                    special_type='early_payment',
                    tax_ids=line.tax_id,
                ))
                epd_lines.append(self.env['account.tax']._prepare_base_line_for_taxes_computation(
                    record=self,
                    price_unit=line_amount_after_discount,
                    quantity=1.0,
                    currency_id=currency,
                    sign=1,
                    special_type='early_payment',
                ))
        return epd_lines

    @api.depends('order_line.invoice_lines')
    def _get_invoiced(self):
        # The invoice_ids are obtained thanks to the invoice lines of the SO
        # lines, and we also search for possible refunds created directly from
        # existing invoices. This is necessary since such a refund is not
        # directly linked to the SO.
        for order in self:
            invoices = order.order_line.invoice_lines.move_id.filtered(lambda r: r.move_type in ('out_invoice', 'out_refund'))
            order.invoice_ids = invoices
            order.invoice_count = len(invoices)

    def _search_invoice_ids(self, operator, value):
        if operator == 'in' and value:
            self.env.cr.execute("""
                SELECT array_agg(so.id)
                    FROM sale_custom_order so
                    JOIN sale_custom_order_line sol ON sol.order_id = so.id
                    JOIN sale_custom_order_line_invoice_rel soli_rel ON soli_rel.order_line_id = sol.id
                    JOIN account_move_line aml ON aml.id = soli_rel.invoice_line_id
                    JOIN account_move am ON am.id = aml.move_id
                WHERE
                    am.move_type in ('out_invoice', 'out_refund') AND
                    am.id = ANY(%s)
            """, (list(value),))
            so_ids = self.env.cr.fetchone()[0] or []
            return [('id', 'in', so_ids)]
        elif operator == '=' and not value:
            # special case for [('invoice_ids', '=', False)], i.e. "Invoices is not set"
            #
            # We cannot just search [('order_line.invoice_lines', '=', False)]
            # because it returns orders with uninvoiced lines, which is not
            # same "Invoices is not set" (some lines may have invoices and some
            # doesn't)
            #
            # A solution is making inverted search first ("orders with invoiced
            # lines") and then invert results ("get all other orders")
            #
            # Domain below returns subset of ('order_line.invoice_lines', '!=', False)
            order_ids = self._search([
                ('order_line.invoice_lines.move_id.move_type', 'in', ('out_invoice', 'out_refund'))
            ])
            return [('id', 'not in', order_ids)]
        return [
            ('order_line.invoice_lines.move_id.move_type', 'in', ('out_invoice', 'out_refund')),
            ('order_line.invoice_lines.move_id', operator, value),
        ]

    @api.depends('state', 'order_line.invoice_status')
    def _compute_invoice_status(self):
        """
        Compute the invoice status of a SO. Possible statuses:
        - no: if the SO is not in status 'sale' or 'done', we consider that there is nothing to
          invoice. This is also the default value if the conditions of no other status is met.
        - to invoice: if any SO line is 'to invoice', the whole SO is 'to invoice'
        - invoiced: if all SO lines are invoiced, the SO is invoiced.
        - upselling: if all SO lines are invoiced or upselling, the status is upselling.
        """
        confirmed_orders = self.filtered(lambda so: so.state == 'sale')
        (self - confirmed_orders).invoice_status = 'no'
        if not confirmed_orders:
            return
        lines_domain = [('is_downpayment', '=', False), ('display_type', '=', False)]
        line_invoice_status_all = [
            (order.id, invoice_status)
            for order, invoice_status in self.env['sale_custom.order.line']._read_group(
                lines_domain + [('order_id', 'in', confirmed_orders.ids)],
                ['order_id', 'invoice_status']
            )
        ]
        for order in confirmed_orders:
            line_invoice_status = [d[1] for d in line_invoice_status_all if d[0] == order.id]
            if order.state != 'sale':
                order.invoice_status = 'no'
            elif any(invoice_status == 'to invoice' for invoice_status in line_invoice_status):
                if any(invoice_status == 'no' for invoice_status in line_invoice_status):
                    # If only discount/delivery/promotion lines can be invoiced, the SO should not
                    # be invoiceable.
                    invoiceable_domain = lines_domain + [('invoice_status', '=', 'to invoice')]
                    invoiceable_lines = order.order_line.filtered_domain(invoiceable_domain)
                    special_lines = invoiceable_lines.filtered(
                        lambda sol: not sol._can_be_invoiced_alone()
                    )
                    if invoiceable_lines == special_lines:
                        order.invoice_status = 'no'
                    else:
                        order.invoice_status = 'to invoice'
                else:
                    order.invoice_status = 'to invoice'
            elif line_invoice_status and all(invoice_status == 'invoiced' for invoice_status in line_invoice_status):
                order.invoice_status = 'invoiced'
            elif line_invoice_status and all(invoice_status in ('invoiced', 'upselling') for invoice_status in line_invoice_status):
                order.invoice_status = 'upselling'
            else:
                order.invoice_status = 'no'

    @api.depends('transaction_ids')
    def _compute_authorized_transaction_ids(self):
        for trans in self:
            trans.authorized_transaction_ids = trans.transaction_ids.filtered(lambda t: t.state == 'authorized')

    @api.depends('transaction_ids')
    def _compute_amount_paid(self):
        """ Sum of the amount paid through all transactions for this SO. """
        for order in self:
            order.amount_paid = sum(
                tx.amount for tx in order.transaction_ids if tx.state in ('authorized', 'done')
            )

    def _compute_amount_undiscounted(self):
        for order in self:
            total = 0.0
            for line in order.order_line:
                total += (line.price_subtotal * 100)/(100-line.discount) if line.discount != 100 else (line.price_unit * line.product_uom_qty)
            order.amount_undiscounted = total

    @api.depends('client_order_ref', 'date_order', 'origin', 'partner_id')
    def _compute_duplicated_order_ids(self):
        order_to_duplicate_orders = self._fetch_duplicate_orders()
        for order in self:
            order.duplicated_order_ids = [Command.set(order_to_duplicate_orders.get(order.id, []))]

    def _fetch_duplicate_orders(self):
        """ Fectch duplicated orders.

        :return: Dictionary mapping order to it's related duplicated orders.
        :rtype: dict
        """
        orders = self.filtered(lambda order: order.id and order.client_order_ref)
        if not orders:
            return {}

        used_fields = (
            'company_id',
            'partner_id',
            'client_order_ref',
            'origin',
            'date_order',
            'state',
        )
        self.env['sale_custom.order'].flush_model(used_fields)

        result = self.env.execute_query(SQL("""
            SELECT
                sale_custom_order.id AS order_id,
                array_agg(duplicate_order.id) AS duplicate_ids
              FROM sale_custom_order
              JOIN sale_custom_order AS duplicate_order
                ON sale_custom_order.company_id = duplicate_order.company_id
                 AND sale_custom_order.id != duplicate_order.id
                 AND duplicate_order.state != 'cancel'
                 AND sale_custom_order.partner_id = duplicate_order.partner_id
                 AND sale_custom_order.date_order = duplicate_order.date_order
                 AND sale_custom_order.client_order_ref = duplicate_order.client_order_ref
                 AND (
                    sale_custom_order.origin = duplicate_order.origin
                    OR (sale_custom_order.origin IS NULL AND duplicate_order.origin IS NULL)
                )
             WHERE sale_custom_order.id IN %(orders)s
             GROUP BY sale_custom_order.id
            """,
            orders=tuple(orders.ids),
        ))
        return {
            order_id: set(duplicate_ids)
            for order_id, duplicate_ids in result
        }

    @api.depends('order_line.customer_lead', 'date_order', 'state')
    def _compute_expected_date(self):
        """ For service and consumable, we only take the min dates. This method is extended in sale_stock to
            take the picking_policy of SO into account.
        """
        self.mapped("order_line")  # Prefetch indication
        for order in self:
            if order.state == 'cancel':
                order.expected_date = False
                continue
            dates_list = order.order_line.filtered(
                lambda line: not line.display_type and not line._is_delivery()
            ).mapped(lambda line: line and line._expected_date())
            if dates_list:
                order.expected_date = order._select_expected_date(dates_list)
            else:
                order.expected_date = False

    def _select_expected_date(self, expected_dates):
        self.ensure_one()
        return min(expected_dates)

    def _compute_is_expired(self):
        for order in self:
            # dùng context_today để tôn trọng timezone user
            today = fields.Date.context_today(order)
            # ép validity_date về date, an toàn cả khi trường là datetime/string
            vd = fields.Date.to_date(order.validity_date) if order.validity_date else False
            order.is_expired = bool(
                order.state in ('draft', 'sent')
                and vd
                and vd < today
            )

    @api.depends('company_id', 'fiscal_position_id')
    def _compute_tax_country_id(self):
        for record in self:
            if record.fiscal_position_id.foreign_vat:
                record.tax_country_id = record.fiscal_position_id.country_id
            else:
                record.tax_country_id = record.company_id.account_fiscal_country_id

    @api.depends('order_line.amount_to_invoice')
    def _compute_amount_to_invoice(self):
        for order in self:
            order.amount_to_invoice = sum(order.order_line.mapped('amount_to_invoice'))

    @api.depends('order_line.amount_invoiced')
    def _compute_amount_invoiced(self):
        for order in self:
            order.amount_invoiced = sum(order.order_line.mapped('amount_invoiced'))

    @api.depends('company_id', 'partner_id', 'amount_total')
    def _compute_partner_credit_warning(self):
        for order in self:
            order.with_company(order.company_id)
            order.partner_credit_warning = ''
            show_warning = order.state in ('draft', 'sent') and \
                           order.company_id.account_use_credit_limit
            if show_warning:
                order.partner_credit_warning = self.env['account.move']._build_credit_warning_message(
                    order.sudo(),  # ensure access to `credit` & `credit_limit` fields
                    current_amount=(order.amount_total / order.currency_rate),
                )

    @api.depends_context('lang')
    @api.depends('order_line.price_subtotal', 'currency_id', 'company_id', 'payment_term_id')
    def _compute_tax_totals(self):
        AccountTax = self.env['account.tax']
        for order in self:
            order_lines = order.order_line.filtered(lambda x: not x.display_type)
            base_lines = [line._prepare_base_line_for_taxes_computation() for line in order_lines]
            base_lines += order._add_base_lines_for_early_payment_discount()
            AccountTax._add_tax_details_in_base_lines(base_lines, order.company_id)
            AccountTax._round_base_lines_tax_details(base_lines, order.company_id)
            order.tax_totals = AccountTax._get_tax_totals_summary(
                base_lines=base_lines,
                currency=order.currency_id or order.company_id.currency_id,
                company=order.company_id,
            )

    @api.depends('state')
    def _compute_type_name(self):
        for record in self:
            if record.state in ('draft', 'sent', 'cancel'):
                record.type_name = _("Quotation")
            else:
                record.type_name = _("Sales Order")

    # portal.mixin override
    def _compute_access_url(self):
        super()._compute_access_url()
        for order in self:
            order.access_url = f'/my/orders/{order.id}'

    #=== CONSTRAINT METHODS ===#

    @api.constrains('company_id', 'order_line')
    def _check_order_line_company_id(self):
        for order in self:
            invalid_companies = order.order_line.product_id.company_id.filtered(
                lambda c: order.company_id not in c._accessible_branches()
            )
            if invalid_companies:
                bad_products = order.order_line.product_id.filtered(
                    lambda p: p.company_id and p.company_id in invalid_companies
                )
                raise ValidationError(_(
                    "Your quotation contains products from company %(product_company)s whereas your quotation belongs to company %(quote_company)s. \n Please change the company of your quotation or remove the products from other companies (%(bad_products)s).",
                    product_company=', '.join(invalid_companies.sudo().mapped('display_name')),
                    quote_company=order.company_id.display_name,
                    bad_products=', '.join(bad_products.mapped('display_name')),
                ))

    @api.constrains('prepayment_percent')
    def _check_prepayment_percent(self):
        for order in self:
            if order.require_payment and not (0 < order.prepayment_percent <= 1.0):
                raise ValidationError(_("Prepayment percentage must be a valid percentage."))

    #=== ONCHANGE METHODS ===#

    def onchange(self, values, field_names, fields_spec):
        self_with_context = self
        if not field_names: # Some warnings should not be displayed for the first onchange
            self_with_context = self.with_context(sale_onchange_first_call=True)
        return super(SaleOrder, self_with_context).onchange(values, field_names, fields_spec)

    @api.onchange('commitment_date', 'expected_date')
    def _onchange_commitment_date(self):
        """ Warn if the commitment dates is sooner than the expected date """
        if self.commitment_date and self.expected_date and self.commitment_date < self.expected_date:
            return {
                'warning': {
                    'title': _('Requested date is too soon.'),
                    'message': _("The delivery date is sooner than the expected date."
                                 " You may be unable to honor the delivery date.")
                }
            }

    @api.onchange('company_id')
    def _onchange_company_id_warning(self):
        self.show_update_pricelist = True
        if self.env.context.get('sale_onchange_first_call'):
            return
        if self.order_line and self.state == 'draft':
            return {
                'warning': {
                    'title': _("Warning for the change of your quotation's company"),
                    'message': _("Changing the company of an existing quotation might need some "
                                 "manual adjustments in the details of the lines. You might "
                                 "consider updating the prices."),
                }
            }

    @api.onchange('company_id')
    def _onchange_company_id(self):
        for order in self:
            # This can't be caught by a python constraint as it is only triggered at save
            # and a compute methodd needs this data to be set correctly before saving
            if not order.company_id:
                raise ValidationError(_("The company is required, please select one before making any other changes to the sale order."))

    @api.onchange('fiscal_position_id')
    def _onchange_fpos_id_show_update_fpos(self):
        if self.order_line and (
            not self.fiscal_position_id
            or (self.fiscal_position_id and self._origin.fiscal_position_id != self.fiscal_position_id)
        ):
            self.show_update_fpos = True

    @api.onchange('partner_id')
    def _onchange_partner_id_warning(self):
        if not self.partner_id:
            return

        partner = self.partner_id

        # If partner has no warning, check its company
        if partner.sale_warn == 'no-message' and partner.parent_id:
            partner = partner.parent_id

        if partner.sale_warn and partner.sale_warn != 'no-message':
            # Block if partner only has warning but parent company is blocked
            if partner.sale_warn != 'block' and partner.parent_id and partner.parent_id.sale_warn == 'block':
                partner = partner.parent_id

            if partner.sale_warn == 'block':
                self.partner_id = False

            return {
                'warning': {
                    'title': _("Warning for %s", partner.name),
                    'message': partner.sale_warn_msg,
                }
            }

    @api.onchange('pricelist_id')
    def _onchange_pricelist_id_show_update_prices(self):
        self.show_update_pricelist = bool(self.order_line)

    @api.onchange('prepayment_percent')
    def _onchange_prepayment_percent(self):
        if not self.prepayment_percent:
            self.require_payment = False

    @api.onchange('order_line')
    def _onchange_order_line(self):
        for index, line in enumerate(self.order_line):
            if line.product_type == 'combo' and line.selected_combo_items:
                linked_lines = line._get_linked_lines()
                selected_combo_items = json.loads(line.selected_combo_items)
                if (
                    selected_combo_items
                    and len(selected_combo_items) != len(line.product_template_id.combo_ids)
                ):
                    raise ValidationError(_(
                        "The number of selected combo items must match the number of available"
                        " combo choices."
                    ))

                # Delete any existing combo item lines.
                delete_commands = [Command.delete(linked_line.id) for linked_line in linked_lines]
                # Create a new combo item line for each selected combo item.
                create_commands = [Command.create({
                    'product_id': combo_item['product_id'],
                    'product_uom_qty': line.product_uom_qty,
                    'combo_item_id': combo_item['combo_item_id'],
                    'product_no_variant_attribute_value_ids': [
                        Command.set(combo_item['no_variant_attribute_value_ids'])
                    ],
                    'product_custom_attribute_value_ids': [Command.clear()] + [
                        Command.create(attribute_value)
                        for attribute_value in combo_item['product_custom_attribute_values']
                    ],
                    # Combo item lines should come directly after their combo product line.
                    'sequence': line.sequence + item_index + 1,
                    # If the linked line exists in DB, populate linked_line_id, otherwise populate
                    # linked_virtual_id.
                    'linked_line_id': line.id if line._origin else False,
                    'linked_virtual_id': line.virtual_id if not line._origin else False,
                }) for item_index, combo_item in enumerate(selected_combo_items)]
                # Shift any lines coming after the combo product line so that the combo item lines
                # come first.
                update_commands = [Command.update(
                    order_line.id,
                    {'sequence': line.sequence + len(selected_combo_items) + line_index - index},
                ) for line_index, order_line in enumerate(self.order_line) if line_index > index]

                # Clear `selected_combo_items` to avoid applying the same changes multiple times.
                line.selected_combo_items = False
                self.order_line = delete_commands + create_commands + update_commands

    #=== CRUD METHODS ===#

    def _is_logistics_user(self):
        return self.env.user.has_group('sale_custom.group_logistics')

    # --- Tạo số chứng từ theo chi nhánh + chặn nhóm kho
    @api.model_create_multi
    def create(self, vals_list):
        # Chặn nhóm kho tạo đơn
        if self._is_logistics_user():
            raise AccessError(_("Kho/Điều vận không được tạo Sales Order."))

        today = fields.Date.context_today(self)
        yyMM = today.strftime('%y%m')

        for vals in vals_list:
            if self._uc_is_shop_request():
                pl = self._uc_get_website_pricelist()
                if pl:
                    vals["pricelist_id"] = pl.id
        
            # Chỉ gán số khi name rỗng hoặc là "New"
            if not vals.get('name') or vals.get('name') == _("New"):
                # Lấy branch của user (fallback 'CTH' nếu không khớp)
                branch = (getattr(self.env.user, 'branch', '') or '').upper()
                if branch == 'HCM':
                    branch_code = 'HCM'
                elif branch == 'HNI':
                    branch_code = 'HNI'
                else:
                    branch_code = 'CTH'

                prefix = f"SQ-{branch_code}-{yyMM}/"

                # Lấy sequence theo công ty (nếu có), loại bỏ padding ở XML => tự zfill(4)
                company_id = vals.get('company_id') or self.env.company.id
                seq_raw = self.env['ir.sequence'].with_company(company_id).next_by_code('sale_custom.order')
                # Phòng trường hợp chưa cấu hình sequence
                seq_num = (seq_raw or '1')
                # Nếu seq_raw có prefix/suffix ở sequence, cắt lấy phần số cuối
                # (an toàn: rút chuỗi số ở đuôi; nếu không tìm thấy thì dùng seq_raw)
                import re
                m = re.search(r'(\d+)$', seq_num or '')
                seq_digits = m.group(1) if m else seq_num
                seq_4 = str(seq_digits).zfill(4)

                vals['name'] = f"{prefix}{seq_4}"

        return super().create(vals_list)

    def _get_copiable_order_lines(self):
        """Returns the order lines that can be copied to a new order."""
        return self.order_line.filtered(lambda l: not l.is_downpayment)

    def copy_data(self, default=None):
        default = dict(default or {})
        default_has_no_order_line = 'order_line' not in default
        default.setdefault('order_line', [])
        vals_list = super().copy_data(default=default)
        if default_has_no_order_line:
            for order, vals in zip(self, vals_list):
                vals['order_line'] = [
                    Command.create(line_vals)
                    for line_vals in order._get_copiable_order_lines().copy_data()
                ]
        return vals_list
    def copy(self, default=None):
        """Nhân bản báo giá: luôn reset về draft và bước đầu tiên."""
        default = dict(default or {})
        default.update({
            'state': 'draft',
            'current_sequence': 1,
            'so_create_attempted': False,
            'so_create_attempted_at': False,
            'so_create_result': False,
            'so_create_lock_uuid': False,
            'so_idem_key': False,
            'dispatch_send_count': 0,

        })
        return super(SaleOrder, self).copy(default)


    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft_or_cancel(self):
        for order in self:
            if order.state not in ('draft', 'cancel'):
                raise UserError(_(
                    "You can not delete a sent quotation or a confirmed sales order."
                    " You must first cancel it."))

    is_logistics_user = fields.Boolean(compute='_compute_is_logistics_user', store=False)

    def _compute_is_logistics_user(self):
        has = self.env.user.has_group('sale_custom.group_logistics')
        for rec in self:
            rec.is_logistics_user = has

    # --- Ghi: map elevator inputs + chặn nhóm kho + ràng buộc pricelist
    def write(self, vals):
        # BỎ đặc thù is_prepared; giữ nguyên pipeline cũ
        vals = self._map_elevator_inputs(vals)

        # Không cho đổi pricelist nếu đã xác nhận
        if 'pricelist_id' in vals and any(so.state == 'sale' for so in self):
            raise UserError(_("You cannot change the pricelist of a confirmed order !"))

        # Ghi thật
        res = super().write(vals)

        # Auto-subscribe đối tác khi đổi partner_id (giữ như cũ)
        if vals.get('partner_id'):
            self.filtered(lambda so: so.state in ('sent', 'sale')).message_subscribe(
                partner_ids=[vals['partner_id']]
            )
        return res
    # --- Xoá: chặn nhóm kho
    def unlink(self):
        if self._is_logistics_user():
            raise AccessError(_("Kho/Điều vận không được xóa Sales Order."))
        return super().unlink()

    #=== ACTION METHODS ===#

    def action_open_discount_wizard(self):
        self.ensure_one()
        return {
            'name': _("Discount"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale_custom.order.discount',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_draft(self):
        orders = self.filtered(lambda s: s.state in ['cancel', 'sent'])
        return orders.write({
            'state': 'draft',
            'signature': False,
            'signed_by': False,
            'signed_on': False,
        })

    def action_quotation_send(self):
        """ Opens a wizard to compose an email, with relevant mail template loaded by default """
        self.filtered(lambda so: so.state in ('draft', 'sent')).order_line._validate_analytic_distribution()
        lang = self.env.context.get('lang')

        ctx = {
            'default_model': 'sale_custom.order',
            'default_res_ids': self.ids,
            'default_composition_mode': 'comment',
            'default_email_layout_xmlid': 'mail.mail_notification_layout_with_responsible_signature',
            'email_notification_allow_footer': True,
            'proforma': self.env.context.get('proforma', False),
        }

        if len(self) > 1:
            ctx['default_composition_mode'] = 'mass_mail'
        else:
            ctx.update({
                'force_email': True,
                'model_description': self.with_context(lang=lang).type_name,
            })
            if not self.env.context.get('hide_default_template'):
                mail_template = self._find_mail_template()
                if mail_template:
                    ctx.update({
                        'default_template_id': mail_template.id,
                        'mark_so_as_sent': True,
                    })
                if mail_template and mail_template.lang:
                    lang = mail_template._render_lang(self.ids)[self.id]
            else:
                for order in self:
                    order._portal_ensure_token()

        action = {
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(False, 'form')],
            'view_id': False,
            'target': 'new',
            'context': ctx,
        }
        if (
            self.env.context.get('check_document_layout')
            and not self.env.context.get('discard_logo_check')
            and self.env.is_admin()
            and not self.env.company.external_report_layout_id
        ):
            layout_action = self.env['ir.actions.report']._action_configure_external_report_layout(
                action,
            )
            # Need to remove this context for windows action
            action.pop('close_on_report_download', None)
            layout_action['context']['dialog_size'] = 'extra-large'
            return layout_action
        return action

    def _find_mail_template(self):
        """ Get the appropriate mail template for the current sales order based on its state.

        If the SO is confirmed, we return the mail template for the sale confirmation.
        Otherwise, we return the quotation email template.

        :return: The correct mail template based on the current status
        :rtype: record of `mail.template` or `None` if not found
        """
        self.ensure_one()
        if self.env.context.get('proforma') or self.state != 'sale':
            return self.env.ref('sale_custom.email_template_edi_sale', raise_if_not_found=False)
        else:
            return self._get_confirmation_template()

    def _get_confirmation_template(self):
        """ Get the mail template sent on SO confirmation (or for confirmed SO's).

        :return: `mail.template` record or None if default template wasn't found
        """
        self.ensure_one()
        default_confirmation_template_id = self.env['ir.config_parameter'].sudo().get_param(
            'sale_custom.default_confirmation_template'
        )
        default_confirmation_template = default_confirmation_template_id \
            and self.env['mail.template'].browse(int(default_confirmation_template_id)).exists()
        if default_confirmation_template:
            return default_confirmation_template
        else:
            return self.env.ref('sale_custom.mail_template_sale_confirmation', raise_if_not_found=False)

    def action_quotation_sent(self):
        """ Mark the given draft quotation(s) as sent.

        :raise: UserError if any given SO is not in draft state.
        """
        if any(order.state != 'draft' for order in self):
            raise UserError(_("Only draft orders can be marked as sent directly."))

        for order in self:
            order.message_subscribe(partner_ids=order.partner_id.ids)

        self.write({'state': 'sent'})

    def action_confirm(self):
        """ Confirm the given quotation(s) and set their confirmation date.

        If the corresponding setting is enabled, also locks the Sale Order.

        :return: True
        :rtype: bool
        :raise: UserError if trying to confirm cancelled SO's
        """
        for order in self:
            error_msg = order._confirmation_error_message()
            if error_msg:
                raise UserError(error_msg)

        self.order_line._validate_analytic_distribution()

        for order in self:
            if order.partner_id in order.message_partner_ids:
                continue
            order.message_subscribe([order.partner_id.id])

        self.write(self._prepare_confirmation_values())

        # Context key 'default_name' is sometimes propagated up to here.
        # We don't need it and it creates issues in the creation of linked records.
        context = self._context.copy()
        context.pop('default_name', None)

        self.with_context(context)._action_confirm()
        user = self[:1].create_uid
        if user and user.sudo().has_group('sale_custom.group_auto_done_setting'):
            # Public user can confirm SO, so we check the group on any record creator.
            self.action_lock()

        if self.env.context.get('send_email'):
            self._send_order_confirmation_mail()

        return True

    def _should_be_locked(self):
        self.ensure_one()
        # Public user can confirm SO, so we check the group on any record creator.
        user = self[:1].create_uid
        return user and user.sudo().has_group('sale_custom.group_auto_done_setting')

    def _confirmation_error_message(self):
        """ Return whether order can be confirmed or not if not then returm error message. """
        self.ensure_one()
        if self.state not in {'draft', 'sent'}:
            return _("Some orders are not in a state requiring confirmation.")
        if any(
            not line.display_type
            and not line.is_downpayment
            and not line.product_id
            for line in self.order_line
        ):
            return _("A line on these orders missing a product, you cannot confirm it.")

        return False

    def _prepare_confirmation_values(self):
        """ Prepare the sales order confirmation values.

        Note: self can contain multiple records.

        :return: Sales Order confirmation values
        :rtype: dict
        """
        return {
            'state': 'sale',
            'date_order': fields.Datetime.now()
        }

    def _action_confirm(self):
        """ Implementation of additional mechanism of Sales Order confirmation.
            This method should be extended when the confirmation should generated
            other documents. In this method, the SO are in 'sale' state (not yet 'done').
        """
        pass

    def _send_order_confirmation_mail(self):
        """ Send a mail to the SO customer to inform them that their order has been confirmed.

        :return: None
        """
        for order in self:
            mail_template = order._get_confirmation_template()
            order._send_order_notification_mail(mail_template)

    def _send_payment_succeeded_for_order_mail(self):
        """ Send a mail to the SO customer to inform them that a payment has been initiated.

        :return: None
        """
        mail_template = self.env.ref(
            'sale_custom.mail_template_sale_payment_executed', raise_if_not_found=False
        )
        for order in self:
            order._send_order_notification_mail(mail_template)

    def _send_order_notification_mail(self, mail_template):
        self.ensure_one()
        if not mail_template:
            return
    
        try:
            self.with_context(force_send=True).message_post_with_source(
                mail_template,
                email_layout_xmlid='mail.mail_notification_layout_with_responsible_signature',
                subtype_xmlid='mail.mt_comment',
            )
        except UserError as e:
            # Thiếu wkhtmltopdf -> template có report PDF -> bỏ qua để không chặn post-process payment
            msg = str(e)
            if 'wkhtmltopdf' in msg.lower():
                _logger.warning("Skip order notification mail because wkhtmltopdf is missing: %s", msg)
                return
            raise


    def action_lock(self):
        self.locked = True

    def action_unlock(self):
        self.locked = False

    def action_cancel(self):
        """ Cancel SO after showing the cancel wizard when needed. (cfr :meth:`_show_cancel_wizard`)

        For post-cancel operations, please only override :meth:`_action_cancel`.

        note: self.ensure_one() if the wizard is shown.
        """
        if any(order.locked for order in self):
            raise UserError(_("You cannot cancel a locked order. Please unlock it first."))
        cancel_warning = self._show_cancel_wizard()
        if cancel_warning:
            self.ensure_one()
            template_id = self.env['ir.model.data']._xmlid_to_res_id(
                'sale_custom.mail_template_sale_cancellation', raise_if_not_found=False
            )
            lang = self.env.context.get('lang')
            template = self.env['mail.template'].browse(template_id)
            if template.lang:
                lang = template._render_lang(self.ids)[self.id]
            ctx = {
                'default_template_id': template_id,
                'default_order_id': self.id,
                'mark_so_as_canceled': True,
                'default_email_layout_xmlid': "mail.mail_notification_layout_with_responsible_signature",
                'model_description': self.with_context(lang=lang).type_name,
            }
            return {
                'name': _('Cancel %s', self.type_name),
                'view_mode': 'form',
                'res_model': 'sale_custom.order.cancel',
                'view_id': self.env.ref('sale_custom.sale_order_cancel_view_form').id,
                'type': 'ir.actions.act_window',
                'context': ctx,
                'target': 'new'
            }
        else:
            return self._action_cancel()

    def _action_cancel(self):
        inv = self.invoice_ids.filtered(lambda inv: inv.state == 'draft')
        inv.button_cancel()
        return self.write({'state': 'cancel'})

    def _show_cancel_wizard(self):
        """ Decide whether the sale_custom.order.cancel wizard should be shown to cancel specified orders.

        :return: True if there is any non-draft order in the given orders
        :rtype: bool
        """
        if self.env.context.get('disable_cancel_warning'):
            return False
        return any(so.state != 'draft' for so in self)

    def action_preview_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': self.get_portal_url(),
        }

    def action_update_taxes(self):
        self.ensure_one()

        self._recompute_taxes()

        if self.partner_id:
            self.message_post(body=_("Product taxes have been recomputed according to fiscal position %s.",
                self.fiscal_position_id._get_html_link() if self.fiscal_position_id else "")
            )

    def _recompute_taxes(self):
        lines_to_recompute = self.order_line.filtered(lambda line: not line.display_type)
        lines_to_recompute._compute_tax_id()
        self.show_update_fpos = False

    def action_update_prices(self):
        self.ensure_one()

        self._recompute_prices()

        if self.pricelist_id:
            message = _("Product prices have been recomputed according to pricelist %s.",
                self.pricelist_id._get_html_link())
        else:
            message = _("Product prices have been recomputed.")
        self.message_post(body=message)

    def _uc_apply_promo_pipeline(self, where=""):
        """Re-apply KM sau khi recompute (để payment/checkout không nhảy giá)."""
        for order in self:
            if getattr(order, "state", "draft") != "draft":
                continue

            _logger.info("[UC_REPRICE] %s BEFORE promo_pipeline order=%s total=%s auto_promo=%s auto_stage=%s",
                         where, order.id, order.amount_total,
                         getattr(getattr(order, "website_auto_purchase_promo_id", False), "id", False),
                         getattr(order, "website_auto_purchase_stage", False))

            # 1) auto apply promotions (nếu có)
            try:
                if hasattr(order, "_auto_apply_promotions"):
                    try:
                        order.sudo()._auto_apply_promotions(for_website=True)
                    except TypeError:
                        order.sudo()._auto_apply_promotions()
            except Exception:
                _logger.exception("[UC_REPRICE] %s _auto_apply_promotions failed order=%s", where, order.id)

            # 2) apply promotions to lines
            try:
                lines = order.order_line.filtered(lambda l: l.product_id and not getattr(l, "display_type", False))
                if "is_gift" in lines._fields:
                    lines = lines.filtered(lambda l: not l.is_gift)
                if "linked_line_id" in lines._fields:
                    lines = lines.filtered(lambda l: not l.linked_line_id)

                if lines and hasattr(lines, "apply_promotions_to_line"):
                    lines.apply_promotions_to_line()
            except Exception:
                _logger.exception("[UC_REPRICE] %s apply_promotions_to_line failed order=%s", where, order.id)

            # 3) auto-purchase (mua lần đầu/hai) chạy sau cùng
            try:
                if hasattr(order, "website_apply_auto_purchase_promo"):
                    order.website_apply_auto_purchase_promo()
            except Exception:
                _logger.exception("[UC_REPRICE] %s website_apply_auto_purchase_promo failed order=%s", where, order.id)

            # 4) recompute totals
            try:
                if hasattr(order, "_compute_amounts"):
                    order._compute_amounts()
                elif hasattr(order, "_amount_all"):
                    order._amount_all()
            except Exception:
                _logger.exception("[UC_REPRICE] %s compute amounts failed order=%s", where, order.id)

            _logger.info("[UC_REPRICE] %s AFTER  promo_pipeline order=%s total=%s", where, order.id, order.amount_total)

    def _recompute_prices(self):
        """
        FIX DỨT ĐIỂM:
        - Vẫn recompute price_unit theo pricelist.
        - Reset + compute discount theo pricelist bằng context force_pricelist_discount=True
          (để _compute_discount vẫn chạy khi cần).
        - Sau đó chạy lại promo pipeline (apply_promotions_to_line + auto_purchase) để giữ KM.
        """
        if self.env.context.get("uc_skip_recompute_prices"):
            return super()._recompute_prices()

        lines_to_recompute = self._get_update_prices_lines()
        lines_to_recompute.invalidate_recordset(['pricelist_item_id'])

        # 1) recompute price_unit
        lines_to_recompute.with_context(force_price_recomputation=True)._compute_price_unit()

        # 2) recompute discount theo PRICELIST (force)
        #    (bước này có thể reset discount, nhưng promo sẽ được apply lại ngay sau)
        try:
            lines_to_recompute.sudo().write({"discount": 0.0})
        except Exception:
            # fallback recordset assign
            lines_to_recompute.discount = 0.0

        lines_to_recompute.with_context(force_pricelist_discount=True)._compute_discount()

        self.show_update_pricelist = False

        _logger.info("[UC_REPRICE] AFTER recompute_prices order_ids=%s (discount reset + pricelist recomputed)", self.ids)

        # 3) IMPORTANT: re-apply promo pipeline để KM không bị mất (đặc biệt khi qua checkout/payment)
        if not self.env.context.get("uc_skip_reapply_promos_after_reprice"):
            self.with_context(
                uc_skip_reapply_promos_after_reprice=True,
                uc_skip_recompute_prices=True,  # chặn recursion
            )._uc_apply_promo_pipeline(where="recompute_prices")

    def _default_order_line_values(self, child_field=False):
        default_data = super()._default_order_line_values(child_field)
        new_default_data = self.env['sale_custom.order.line']._get_product_catalog_lines_data()
        return {**default_data, **new_default_data}

    def _get_action_add_from_catalog_extra_context(self):
        return {
            **super()._get_action_add_from_catalog_extra_context(),
            'product_catalog_currency_id': self.currency_id.id,
            'product_catalog_digits': self.order_line._fields['price_unit'].get_digits(self.env),
        }

    def _get_product_catalog_domain(self):
        return expression.AND([super()._get_product_catalog_domain(), [('sale_ok', '=', True)]])

    def action_open_business_doc(self):
        self.ensure_one()
        return {
            'name': _("Order"),
            'type': 'ir.actions.act_window',
            'res_model': 'sale_custom.order',
            'res_id': self.id,
            'views': [(False, 'form')],
        }

    # INVOICING #

    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a sales order. This method may be
        overridden to implement custom invoice generation (making sure to call super() to establish
        a clean extension chain).
        """
        self.ensure_one()

        txs_to_be_linked = self.transaction_ids.sudo().filtered(
            lambda tx: (
                tx.state in ('pending', 'authorized')
                or tx.state == 'done' and not (tx.payment_id and tx.payment_id.is_reconciled)
            )
        )

        values = {
            'ref': self.client_order_ref or '',
            'move_type': 'out_invoice',
            'narration': self.note,
            'currency_id': self.currency_id.id,
            'campaign_id': self.campaign_id.id,
            'medium_id': self.medium_id.id,
            'source_id': self.source_id.id,
            'team_id': self.team_id.id,
            'partner_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'fiscal_position_id': (self.fiscal_position_id or self.fiscal_position_id._get_fiscal_position(self.partner_invoice_id)).id,
            'invoice_origin': self.name,
            'invoice_payment_term_id': self.payment_term_id.id,
            'invoice_user_id': self.user_id.id,
            'payment_reference': self.reference,
            'transaction_ids': [Command.set(txs_to_be_linked.ids)],
            'company_id': self.company_id.id,
            'invoice_line_ids': [],
            'user_id': self.user_id.id,
        }
        if self.journal_id:
            values['journal_id'] = self.journal_id.id
        return values

    def action_view_invoice(self, invoices=False):
        if not invoices:
            invoices = self.mapped('invoice_ids')
        action = self.env['ir.actions.actions']._for_xml_id('account.action_move_out_invoice_type')
        if len(invoices) > 1:
            action['domain'] = [('id', 'in', invoices.ids)]
        elif len(invoices) == 1:
            form_view = [(self.env.ref('account.view_move_form').id, 'form')]
            if 'views' in action:
                action['views'] = form_view + [(state,view) for state,view in action['views'] if view != 'form']
            else:
                action['views'] = form_view
            action['res_id'] = invoices.id
        else:
            action = {'type': 'ir.actions.act_window_close'}

        context = {
            'default_move_type': 'out_invoice',
        }
        if len(self) == 1:
            context.update({
                'default_partner_id': self.partner_id.id,
                'default_partner_shipping_id': self.partner_shipping_id.id,
                'default_invoice_payment_term_id': self.payment_term_id.id or self.partner_id.property_payment_term_id.id or self.env['account.move'].default_get(['invoice_payment_term_id']).get('invoice_payment_term_id'),
                'default_invoice_origin': self.name,
            })
        action['context'] = context
        return action

    def _get_invoice_grouping_keys(self):
        return ['company_id', 'partner_id', 'currency_id']

    def _nothing_to_invoice_error_message(self):
        return _(
            "Cannot create an invoice. No items are available to invoice.\n\n"
            "To resolve this issue, please ensure that:\n"
            "   \u2022 The products have been delivered before attempting to invoice them.\n"
            "   \u2022 The invoicing policy of the product is configured correctly.\n\n"
            "If you want to invoice based on ordered quantities instead:\n"
            "   \u2022 For consumable or storable products, open the product, go to the 'General Information' tab and change the 'Invoicing Policy' from 'Delivered Quantities' to 'Ordered Quantities'.\n"
            "   \u2022 For services (and other products), change the 'Invoicing Policy' to 'Prepaid/Fixed Price'.\n"
        )

    def _get_update_prices_lines(self):
        """ Hook to exclude specific lines which should not be updated based on price list recomputation """
        return self.order_line.filtered(lambda line: not line.display_type)

    def _get_invoiceable_lines(self, final=False):
        """Return the invoiceable lines for order `self`."""
        down_payment_line_ids = []
        invoiceable_line_ids = []
        pending_section = None
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        for line in self.order_line:
            if line.display_type == 'line_section':
                # Only invoice the section if one of its lines is invoiceable
                pending_section = line
                continue
            if line.display_type != 'line_note' and float_is_zero(line.qty_to_invoice, precision_digits=precision):
                continue
            if line.qty_to_invoice > 0 or (line.qty_to_invoice < 0 and final) or line.display_type == 'line_note':
                if line.is_downpayment:
                    # Keep down payment lines separately, to put them together
                    # at the end of the invoice, in a specific dedicated section.
                    down_payment_line_ids.append(line.id)
                    continue
                if pending_section:
                    invoiceable_line_ids.append(pending_section.id)
                    pending_section = None
                invoiceable_line_ids.append(line.id)

        return self.env['sale_custom.order.line'].browse(invoiceable_line_ids + down_payment_line_ids)

    def _create_account_invoices(self, invoice_vals_list, final):
        """Small method to allow overriding the behavior right after an invoice is created."""
        # Manage the creation of invoices in sudo because a salesperson must be able to generate an invoice from a
        # sale order without "billing" access rights. However, he should not be able to create an invoice from scratch.
        return self.env['account.move'].sudo().with_context(default_move_type='out_invoice').create(invoice_vals_list)

    def _create_invoices(self, grouped=False, final=False, date=None):
        """ Create invoice(s) for the given Sales Order(s).

        :param bool grouped: if True, invoices are grouped by SO id.
            If False, invoices are grouped by keys returned by :meth:`_get_invoice_grouping_keys`
        :param bool final: if True, refunds will be generated if necessary
        :param date: unused parameter
        :returns: created invoices
        :rtype: `account.move` recordset
        :raises: UserError if one of the orders has no invoiceable lines.
        """
        if not self.env['account.move'].has_access('create'):
            try:
                self.check_access('write')
            except AccessError:
                return self.env['account.move']

        # 1) Create invoices.
        invoice_vals_list = []
        invoice_item_sequence = 0 # Incremental sequencing to keep the lines order on the invoice.
        for order in self:
            if order.partner_invoice_id.lang:
                order = order.with_context(lang=order.partner_invoice_id.lang)
            order = order.with_company(order.company_id)

            invoice_vals = order._prepare_invoice()
            invoiceable_lines = order._get_invoiceable_lines(final)

            if not any(not line.display_type for line in invoiceable_lines):
                continue

            invoice_line_vals = []
            down_payment_section_added = False
            for line in invoiceable_lines:
                if not down_payment_section_added and line.is_downpayment:
                    # Create a dedicated section for the down payments
                    # (put at the end of the invoiceable_lines)
                    invoice_line_vals.append(
                        Command.create(
                            order._prepare_down_payment_section_line(sequence=invoice_item_sequence)
                        ),
                    )
                    down_payment_section_added = True
                    invoice_item_sequence += 1
                invoice_line_vals.append(
                    Command.create(
                        line._prepare_invoice_line(sequence=invoice_item_sequence)
                    ),
                )
                invoice_item_sequence += 1

            invoice_vals['invoice_line_ids'] += invoice_line_vals
            invoice_vals_list.append(invoice_vals)

        if not invoice_vals_list and self._context.get('raise_if_nothing_to_invoice', True):
            raise UserError(self._nothing_to_invoice_error_message())

        # 2) Manage 'grouped' parameter: group by (partner_id, currency_id).
        if not grouped:
            new_invoice_vals_list = []
            invoice_grouping_keys = self._get_invoice_grouping_keys()
            invoice_vals_list = sorted(
                invoice_vals_list,
                key=lambda x: [
                    x.get(grouping_key) for grouping_key in invoice_grouping_keys
                ]
            )
            for _grouping_keys, invoices in groupby(invoice_vals_list, key=lambda x: [x.get(grouping_key) for grouping_key in invoice_grouping_keys]):
                origins = set()
                payment_refs = set()
                refs = set()
                ref_invoice_vals = None
                for invoice_vals in invoices:
                    if not ref_invoice_vals:
                        ref_invoice_vals = invoice_vals
                    else:
                        ref_invoice_vals['invoice_line_ids'] += invoice_vals['invoice_line_ids']
                    origins.add(invoice_vals['invoice_origin'])
                    payment_refs.add(invoice_vals['payment_reference'])
                    refs.add(invoice_vals['ref'])
                ref_invoice_vals.update({
                    'ref': ', '.join(refs)[:2000],
                    'invoice_origin': ', '.join(origins),
                    'payment_reference': len(payment_refs) == 1 and payment_refs.pop() or False,
                })
                new_invoice_vals_list.append(ref_invoice_vals)
            invoice_vals_list = new_invoice_vals_list

        # 3) Create invoices.

        # As part of the invoice creation, we make sure the sequence of multiple SO do not interfere
        # in a single invoice. Example:
        # SO 1:
        # - Section A (sequence: 10)
        # - Product A (sequence: 11)
        # SO 2:
        # - Section B (sequence: 10)
        # - Product B (sequence: 11)
        #
        # If SO 1 & 2 are grouped in the same invoice, the result will be:
        # - Section A (sequence: 10)
        # - Section B (sequence: 10)
        # - Product A (sequence: 11)
        # - Product B (sequence: 11)
        #
        # Resequencing should be safe, however we resequence only if there are less invoices than
        # orders, meaning a grouping might have been done. This could also mean that only a part
        # of the selected SO are invoiceable, but resequencing in this case shouldn't be an issue.
        if len(invoice_vals_list) < len(self):
            SaleOrderLine = self.env['sale_custom.order.line']
            for invoice in invoice_vals_list:
                sequence = 1
                for line in invoice['invoice_line_ids']:
                    line[2]['sequence'] = SaleOrderLine._get_invoice_line_sequence(new=sequence, old=line[2]['sequence'])
                    sequence += 1

        moves = self._create_account_invoices(invoice_vals_list, final)

        # 4) Some moves might actually be refunds: convert them if the total amount is negative
        # We do this after the moves have been created since we need taxes, etc. to know if the total
        # is actually negative or not
        if final and (moves_to_switch := moves.sudo().filtered(lambda m: m.amount_total < 0)):
            with self.env.protecting([moves._fields['team_id']], moves_to_switch):
                moves_to_switch.action_switch_move_type()
                self.invoice_ids._set_reversed_entry(moves_to_switch)

        for move in moves:
            if final:
                # Downpayment might have been determined by a fixed amount set by the user.
                # This amount is tax included. This can lead to rounding issues.
                # E.g. a user wants a 100€ DP on a product with 21% tax.
                # 100 / 1.21 = 82.64, 82.64 * 1,21 = 99.99
                # This is already corrected by adding/removing the missing cents on the DP invoice,
                # but must also be accounted for on the final invoice.

                delta_amount = 0
                for order_line in self.order_line:
                    if not order_line.is_downpayment:
                        continue
                    inv_amt = order_amt = 0
                    for invoice_line in order_line.invoice_lines:
                        sign = 1 if invoice_line.move_id.is_inbound() else -1
                        if invoice_line.move_id == move:
                            inv_amt += invoice_line.price_total * sign
                        elif invoice_line.move_id.state != 'cancel':  # filter out canceled dp lines
                            order_amt += invoice_line.price_total * sign
                    if inv_amt and order_amt:
                        # if not inv_amt, this order line is not related to current move
                        # if no order_amt, dp order line was not invoiced
                        delta_amount += inv_amt + order_amt

                if not move.currency_id.is_zero(delta_amount):
                    receivable_line = move.line_ids.filtered(
                        lambda aml: aml.account_id.account_type == 'asset_receivable')[:1]
                    product_lines = move.line_ids.filtered(
                        lambda aml: aml.display_type == 'product' and aml.is_downpayment)
                    tax_lines = move.line_ids.filtered(
                        lambda aml: aml.tax_line_id.amount_type not in (False, 'fixed'))
                    if tax_lines and product_lines and receivable_line:
                        line_commands = [Command.update(receivable_line.id, {
                            'amount_currency': receivable_line.amount_currency + delta_amount,
                        })]
                        delta_sign = 1 if delta_amount > 0 else -1
                        for lines, attr, sign in (
                            (product_lines, 'price_total', -1 if move.is_inbound() else 1),
                            (tax_lines, 'amount_currency', 1),
                        ):
                            remaining = delta_amount
                            lines_len = len(lines)
                            for line in lines:
                                if move.currency_id.compare_amounts(remaining, 0) != delta_sign:
                                    break
                                amt = delta_sign * max(
                                    move.currency_id.rounding,
                                    abs(move.currency_id.round(remaining / lines_len)),
                                )
                                remaining -= amt
                                line_commands.append(Command.update(line.id, {attr: line[attr] + amt * sign}))
                        move.line_ids = line_commands

            move.message_post_with_source(
                'mail.message_origin_link',
                render_values={'self': move, 'origin': move.line_ids.sale_line_ids.order_id},
                subtype_xmlid='mail.mt_note',
            )
        return moves

    # MAIL #

    def _discard_tracking(self):
        self.ensure_one()
        return (
            self.state == 'draft'
            and request and request.env.context.get('catalog_skip_tracking')
        )

    def _track_finalize(self):
        """ Override of `mail` to prevent logging changes when the SO is in a draft state. """
        if (len(self) == 1
            # The method _track_finalize is sometimes called too early or too late and it
            # might cause a desynchronization with the cache, thus this condition is needed.
            and self.env.cache.contains(self, self._fields['state']) and self._discard_tracking()):
            self.env.cr.precommit.data.pop(f'mail.tracking.{self._name}', {})
            self.env.flush_all()
            return
        return super()._track_finalize()

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        if self.env.context.get('mark_so_as_sent'):
            self.filtered(lambda o: o.state == 'draft').with_context(tracking_disable=True).write({'state': 'sent'})
        so_ctx = {'mail_post_autofollow': self.env.context.get('mail_post_autofollow', True)}
        if self.env.context.get('mark_so_as_sent') and 'mail_notify_author' not in kwargs:
            kwargs['notify_author'] = self.env.user.partner_id.id in (kwargs.get('partner_ids') or [])
        return super(SaleOrder, self.with_context(**so_ctx)).message_post(**kwargs)

    def _notify_get_recipients_groups(self, message, model_description, msg_vals=None):
        """ Give access button to users and portal customer as portal is integrated
        in sale. Customer and portal group have probably no right to see
        the document so they don't have the access button. """
        groups = super()._notify_get_recipients_groups(
            message, model_description, msg_vals=msg_vals
        )
        if not self:
            return groups

        self.ensure_one()
        if self._context.get('proforma'):
            for group in [g for g in groups if g[0] in ('portal_customer', 'portal', 'follower', 'customer')]:
                group[2]['has_button_access'] = False
            return groups
        local_msg_vals = dict(msg_vals or {})

        # portal customers have full access (existence not granted, depending on partner_id)
        try:
            customer_portal_group = next(group for group in groups if group[0] == 'portal_customer')
        except StopIteration:
            pass
        else:
            access_opt = customer_portal_group[2].setdefault('button_access', {})
            is_tx_pending = self.get_portal_last_transaction().state == 'pending'
            if self._has_to_be_signed():
                if self._has_to_be_paid():
                    access_opt['title'] = _("View Quotation") if is_tx_pending else _("Sign & Pay Quotation")
                else:
                    access_opt['title'] = _("Accept & Sign Quotation")
            elif self._has_to_be_paid() and not is_tx_pending:
                access_opt['title'] = _("Accept & Pay Quotation")
            elif self.state in ('draft', 'sent'):
                access_opt['title'] = _("View Quotation")

        # enable followers that have access through portal
        follower_group = next(group for group in groups if group[0] == 'follower')
        follower_group[2]['active'] = True
        follower_group[2]['has_button_access'] = True
        access_opt = follower_group[2].setdefault('button_access', {})
        if self.state in ('draft', 'sent'):
            access_opt['title'] = _("View Quotation")
        else:
            access_opt['title'] = _("View Order")
        access_opt['url'] = self._notify_get_action_link('view', **local_msg_vals)

        return groups

    def _notify_by_email_prepare_rendering_context(self, message, msg_vals=False, model_description=False,
                                                   force_email_company=False, force_email_lang=False):
        render_context = super()._notify_by_email_prepare_rendering_context(
            message, msg_vals, model_description=model_description,
            force_email_company=force_email_company, force_email_lang=force_email_lang
        )
        lang_code = render_context.get('lang')
        record = render_context['record']
        subtitles = [f"{record.name} - {record.partner_id.name}" if record.partner_id else record.name]
        if self.amount_total:
            # Do not show the price in subtitles if zero (e.g. e-commerce orders are created empty)
            subtitles.append(
                format_amount(self.env, self.amount_total, self.currency_id, lang_code=lang_code),
            )

        render_context['subtitles'] = subtitles
        return render_context

    def _phone_get_number_fields(self):
        """ No phone or mobile field is available on sale model. Instead SMS will
        fallback on partner-based computation using ``_mail_get_partner_fields``. """
        return []

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'state' in init_values and self.state == 'sale':
            return self.env.ref('sale_custom.mt_order_confirmed')
        elif 'state' in init_values and self.state == 'sent':
            return self.env.ref('sale_custom.mt_order_sent')
        return super()._track_subtype(init_values)

    def _message_get_suggested_recipients(self):
        recipients = super()._message_get_suggested_recipients()
        if self.partner_id:
            self._message_add_suggested_recipient(
                recipients, partner=self.partner_id, reason=_("Customer")
            )
        return recipients

    # PAYMENT #

    def _force_lines_to_invoice_policy_order(self):
        """Force the qty_to_invoice to be computed as if the invoice_policy
        was set to "Ordered quantities", independently of the product configuration.

        This is needed for the automatic invoice logic, as we want to automatically
        invoice the full SO when it's paid.
        """
        for line in self.order_line:
            if line.state == 'sale':
                # No need to set 0 as it is already the standard logic in the compute method.
                line.qty_to_invoice = line.product_uom_qty - line.qty_invoiced

    def payment_action_capture(self):
        """ Capture all transactions linked to this sale order. """
        self.ensure_one()
        payment_utils.check_rights_on_recordset(self)

        # In sudo mode to bypass the checks on the rights on the transactions.
        return self.transaction_ids.sudo().action_capture()

    def payment_action_void(self):
        """ Void all transactions linked to this sale order. """
        payment_utils.check_rights_on_recordset(self)

        # In sudo mode to bypass the checks on the rights on the transactions.
        self.authorized_transaction_ids.sudo().action_void()

    def get_portal_last_transaction(self):
        self.ensure_one()
        return self.transaction_ids.sudo()._get_last()

    def _get_order_lines_to_report(self):
        down_payment_lines = self.order_line.filtered(lambda line:
            line.is_downpayment
            and not line.display_type
            and not line._get_downpayment_state()
        )

        def show_line(line):
            if not line.is_downpayment:
                return True
            elif line.display_type and down_payment_lines:
                return True  # Only show the down payment section if down payments were posted
            elif line in down_payment_lines:
                return True  # Only show posted down payments
            else:
                return False

        return self.order_line.filtered(show_line)

    def _get_default_payment_link_values(self):
        self.ensure_one()
        amount_max = self.amount_total - self.amount_paid

        # Always default to the minimum value needed to confirm the order:
        # - order is not confirmed yet
        # - can be confirmed online
        # - we have still not paid enough for confirmation.
        prepayment_amount = self._get_prepayment_required_amount()
        if (
            self.state in ('draft', 'sent')
            and self.require_payment
            and self.currency_id.compare_amounts(prepayment_amount, self.amount_paid) > 0
        ):
            amount = prepayment_amount - self.amount_paid
        else:
            amount = amount_max

        return {
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_invoice_id.id,
            'amount': amount,
            'amount_max': amount_max,
            'amount_paid': self.amount_paid,
        }

    # EDI #

    def create_document_from_attachment(self, attachment_ids):
        """ Create the sale orders from given attachment_ids and redirect newly create order view.

        :param list attachment_ids: List of attachments process.
        :return: An action redirecting to related sale order view.
        :rtype: dict
        """
        orders = self._create_order_from_attachment(attachment_ids)
        return orders._get_records_action(name=_("Generated Orders"))

    @api.model
    def _create_order_from_attachment(self, attachment_ids):
        """ Create the sale orders from given attachment_ids and fill data by extracting detail
        from attachments and return generated orders.

        :param list attachment_ids: List of attachments process.
        :return: Recordset of order.
        """
        attachments = self.env['ir.attachment'].browse(attachment_ids)
        if not attachments:
            raise UserError(_("No attachment was provided"))

        orders = self.browse()
        for attachment in attachments:
            order = self.create({
                'partner_id': self.env.user.partner_id.id,
            })
            order._extend_with_attachments(attachment)
            orders |= order
            order.message_post(attachment_ids=attachment.ids)
            attachment.write({'res_model': self._name, 'res_id': order.id})

        return orders

    def _extend_with_attachments(self, attachment):
        """ Main entry point to extend/enhance order with attachment.

        :param attachment: A recordset of ir.attachment.
        :returns: None
        """
        self.ensure_one()

        file_data = attachment._unwrap_edi_attachments()[0]
        decoder = self._get_order_edi_decoder(file_data)
        if decoder:
            try:
                with self.env.cr.savepoint():
                    decoder(self, file_data)
            except RedirectWarning:
                raise
            except Exception:
                message = _(
                    "Error importing attachment '%(file_name)s' as order (decoder=%(decoder)s)",
                    file_name=file_data['filename'],
                    decoder=decoder.__name__,
                )
                self.with_user(SUPERUSER_ID).message_post(body=message)
                _logger.exception(message)

        if file_data.get('on_close'):
            file_data['on_close']()
        return True

    def _get_order_edi_decoder(self, file_data):
        """ To be extended with decoding capabilities of order data from file data.

        :returns:  Function to be later used to import the file.
                   Function' args:
                   - order: sale_custom.order
                   - file_data: attachemnt information / value
                   returns True if was able to process the order
        """
        if file_data['type'] in ('pdf', 'binary'):
            return lambda *args: False
        return

    # PORTAL #

    def _has_to_be_signed(self):
        """A sale order has to be signed when:
        - its state is 'draft' or `sent`
        - it's not expired;
        - it requires a signature;
        - it's not already signed.

        Note: self.ensure_one()

        :return: Whether the sale order has to be signed.
        :rtype: bool
        """
        self.ensure_one()
        return (
            self.state in ['draft', 'sent']
            and not self.is_expired
            and self.require_signature
            and not self.signature
        )

    def _has_to_be_paid(self):
        """A sale order has to be paid when:
        - its state is 'draft' or `sent`;
        - it's not expired;
        - it requires a payment;
        - the last transaction's state isn't `done`;
        - the total amount is strictly positive.
        - confirmation amount is not reached

        Note: self.ensure_one()

        :return: Whether the sale order has to be paid.
        :rtype: bool
        """
        self.ensure_one()
        return (
            self.state in ['draft', 'sent']
            and not self.is_expired
            and self.require_payment
            and self.amount_total > 0
            and not self._is_confirmation_amount_reached()
        )

    def _get_portal_return_action(self):
        """ Return the action used to display orders when returning from customer portal. """
        self.ensure_one()
        return self.env.ref('sale_custom.action_quotations_with_onboarding')

    def _get_name_portal_content_view(self):
        """ This method can be inherited by localizations who want to localize the online quotation view. """
        self.ensure_one()
        return 'sale_custom.sale_order_portal_content'

    def _get_name_tax_totals_view(self):
        """ This method can be inherited by localizations who want to localize the taxes displayed on the portal and sale order report. """
        return 'sale_custom.document_tax_totals'

    def _get_report_base_filename(self):
        self.ensure_one()
        return f'{self.type_name} {self.name}'

    #=== CORE METHODS OVERRIDES ===#

    @api.model
    def get_empty_list_help(self, help_msg):
        self = self.with_context(
            empty_list_help_document_name=_("sale order"),
        )
        return super().get_empty_list_help(help_msg)

    def _compute_field_value(self, field):
        if field.name != 'invoice_status' or self.env.context.get('mail_activity_automation_skip'):
            return super()._compute_field_value(field)

        filtered_self = self.filtered(
            lambda so: so.ids
                and (so.user_id or so.partner_id.user_id)
                and so._origin.invoice_status != 'upselling')
        super()._compute_field_value(field)

        upselling_orders = filtered_self.filtered(lambda so: so.invoice_status == 'upselling')
        upselling_orders._create_upsell_activity()

    #=== BUSINESS METHODS ===#

    def _create_upsell_activity(self):
        if not self:
            return

        self.activity_unlink(['sale_custom.mail_act_sale_upsell'])
        for order in self:
            order_ref = order._get_html_link()
            customer_ref = order.partner_id._get_html_link()
            order.activity_schedule(
                'sale_custom.mail_act_sale_upsell',
                user_id=order.user_id.id or order.partner_id.user_id.id,
                note=_("Upsell %(order)s for customer %(customer)s", order=order_ref, customer=customer_ref))

    def _prepare_analytic_account_data(self, prefix=None):
        """ Prepare SO analytic account creation values.

        :return: `account.analytic.account` creation values
        :rtype: dict
        """
        self.ensure_one()
        name = self.name
        if prefix:
            name = prefix + ": " + self.name
        project_plan, _other_plans = self.env['account.analytic.plan']._get_all_plans()
        return {
            'name': name,
            'code': self.client_order_ref,
            'company_id': self.company_id.id,
            'plan_id': project_plan.id,
            'partner_id': self.partner_id.id,
        }

    def _prepare_down_payment_section_line(self, **optional_values):
        """ Prepare the values to create a new down payment section.

        :param dict optional_values: any parameter that should be added to the returned down payment section
        :return: `account.move.line` creation values
        :rtype: dict
        """
        self.ensure_one()
        context = {'lang': self.partner_id.lang}
        down_payments_section_line = {
            'display_type': 'line_section',
            'name': _("Down Payments"),
            'product_id': False,
            'product_uom_id': False,
            'quantity': 0,
            'discount': 0,
            'price_unit': 0,
            'account_id': False,
            **optional_values
        }
        del context
        return down_payments_section_line

    def _get_prepayment_required_amount(self):
        """ Return the minimum amount needed to confirm automatically the quotation.

        Note: self.ensure_one()

        :return: The minimum amount needed to confirm automatically the quotation.
        :rtype: float
        """
        self.ensure_one()
        if self.prepayment_percent == 1.0 or not self.require_payment:
            return self.amount_total
        else:
            return self.currency_id.round(self.amount_total * self.prepayment_percent)

    def _is_confirmation_amount_reached(self):
        """ Return whether `self.amount_paid` is higher than the prepayment required amount.

        Note: self.ensure_one()

        :return: Whether `self.amount_paid` is higher than the prepayment required amount.
        :rtype: bool
        """
        self.ensure_one()
        amount_comparison = self.currency_id.compare_amounts(
            self._get_prepayment_required_amount(), self.amount_paid,
        )
        return amount_comparison <= 0

    def _generate_downpayment_invoices(self):
        """ Generate invoices as down payments for sale order.

        :return: The generated down payment invoices.
        :rtype: recordset of `account.move`
        """
        generated_invoices = self.env['account.move']

        for order in self:
            downpayment_wizard = order.env['sale_custom.advance.payment.inv'].create({
                'sale_order_ids': order,
                'advance_payment_method': 'fixed',
                'fixed_amount': order.amount_paid,
            })
            generated_invoices |= downpayment_wizard._create_invoices(order)

        return generated_invoices

    def _get_product_catalog_order_data(self, products, **kwargs):
        pricelist = self.pricelist_id._get_products_price(
            quantity=1.0,
            products=products,
            currency=self.currency_id,
            date=self.date_order,
            **kwargs,
        )
        res = super()._get_product_catalog_order_data(products, **kwargs)
        for product in products:
            res[product.id]['price'] = pricelist.get(product.id)
            if product.sale_line_warn != 'no-message' and product.sale_line_warn_msg:
                res[product.id]['warning'] = product.sale_line_warn_msg
            if product.sale_line_warn == "block":
                res[product.id]['readOnly'] = True
        return res

    def _get_product_catalog_record_lines(self, product_ids, **kwargs):
        grouped_lines = defaultdict(lambda: self.env['sale_custom.order.line'])
        for line in self.order_line:
            if line.display_type or line.product_id.id not in product_ids:
                continue
            grouped_lines[line.product_id] |= line
        return grouped_lines

    def _get_product_documents(self):
        self.ensure_one()

        documents = (
            self.order_line.product_id.product_document_ids
            | self.order_line.product_template_id.product_document_ids
        )
        return self._filter_product_documents(documents).sorted()

    def _filter_product_documents(self, documents):
        return documents.filtered(
            lambda document:
                document.attached_on_sale == 'quotation'
                or (self.state == 'sale' and document.attached_on_sale == 'sale_order')
        )

    def _update_order_line_info(self, product_id, quantity, **kwargs):
        """ Update sale order line information for a given product or create a
        new one if none exists yet.
        :param int product_id: The product, as a `product.product` id.
        :return: The unit price of the product, based on the pricelist of the
                 sale order and the quantity selected.
        :rtype: float
        """
        request.update_context(catalog_skip_tracking=True)
        sol = self.order_line.filtered(lambda line: line.product_id.id == product_id)
        if sol:
            if quantity != 0:
                sol.product_uom_qty = quantity
            elif self.state in ['draft', 'sent']:
                price_unit = self.pricelist_id._get_product_price(
                    product=sol.product_id,
                    quantity=1.0,
                    currency=self.currency_id,
                    date=self.date_order,
                    **kwargs,
                )
                sol.unlink()
                return price_unit
            else:
                sol.product_uom_qty = 0
        elif quantity > 0:
            sol = self.env['sale_custom.order.line'].create({
                'order_id': self.id,
                'product_id': product_id,
                'product_uom_qty': quantity,
                'sequence': ((self.order_line and self.order_line[-1].sequence + 1) or 10),
            })
            sol.with_context(force_price_recomputation=True)._compute_price_unit()
        return sol.price_unit * (1-(sol.discount or 0.0)/100.0)

    #=== TOOLING ===#

    def _is_readonly(self):
        """ Return Whether the sale order is read-only or not based on the state or the lock status.

        A sale order is considered read-only if its state is 'cancel' or if the sale order is
        locked.

        :return: Whether the sale order is read-only or not.
        :rtype: bool
        """
        self.ensure_one()
        return self.state == 'cancel' or self.locked

    def _is_paid(self):
        """ Return whether the sale order is paid or not based on the linked transactions.

        A sale order is considered paid if the sum of all the linked transaction is equal to or
        higher than `self.amount_total`.

        :return: Whether the sale order is paid or not.
        :rtype: bool
        """
        self.ensure_one()
        return self.currency_id.compare_amounts(self.amount_paid, self.amount_total) >= 0

    def _get_lang(self):
        self.ensure_one()

        if self.partner_id.lang and not self.partner_id.is_public:
            return self.partner_id.lang

        return self.env.lang

    def _validate_order(self):
        """
        Confirm the sale order and send a confirmation email.

        :return: None
        """
        self.with_context(send_email=True).action_confirm()
        
    @api.onchange('partner_id')
    def _onchange_partner_id_oms_price(self):
        # Khi đổi khách hàng, cập nhật lại giá trên từng dòng
        for order in self:
            for line in order.order_line:
                line._onchange_set_oms_price_and_promotion()
    
            
    @api.depends('product_id', 'product_id.categ_id')
    def _compute_promotions(self):
        today = fields.Date.today()
        for line in self:
            promos = self.env['oms.promotion'].search([
                '|',
                    ('product_reward_tmpl_id', '=', line.product_id.product_tmpl_id.id),  # So template luôn
                    ('product_reward_category_ids.product_categ_id', '=', line.product_id.categ_id.id),
                ('valid_from', '<=', today),
                ('valid_to', '>=', today),
            ])
            line.promotion_ids = promos

    @api.depends('promotion_ids.discount_value')
    def _compute_discount(self):
        for line in self:
            total_discount = 0.0
            for promo in line.promotion_ids:
                if promo.discount_type == 'percent' and promo.discount_value:
                    total_discount += promo.discount_value
            line.discount = min(total_discount, 100)

    def _check_lines_price_on_submit(self):
        """Chỉ gọi khi gửi duyệt: 
           - BT: price_unit > 0
           - BK/KM: price_unit == 0
        """
        sel = self.env['sale_custom.order.line']._fields['line_kind'].selection
        sel_map = dict(sel) if sel else {}

        for o in self:
            errors = []
            for l in o.order_line:
                if l.display_type:
                    continue
                # SO có currency khác nhau? Lấy rounding theo đơn hàng.
                rounding = o.currency_id.rounding or 0.01

                if l.line_kind == 'BT':
                    # Có sản phẩm và số lượng > 0 thì đơn giá phải > 0
                    if l.product_id and (l.product_uom_qty or 0) > 0:
                        # compare <= 0
                        if float_compare(l.price_unit or 0.0, 0.0, precision_rounding=rounding) <= 0:
                            errors.append(_(
                                "Dòng 'Bình thường' phải có đơn giá > 0: %s (SL=%s, Đơn giá=%s)"
                            ) % (l.display_name, l.product_uom_qty, l.price_unit))
                elif l.line_kind in ('BK', 'KM'):
                    # Đơn giá phải == 0
                    if not float_is_zero(l.price_unit or 0.0, precision_rounding=rounding):
                        errors.append(_(
                            "Dòng '%s' phải có đơn giá = 0: %s (Đơn giá=%s)"
                        ) % (sel_map.get(l.line_kind, l.line_kind), l.display_name, l.price_unit))
                # Các loại khác (nếu có) thì bỏ qua hoặc bổ sung rule sau

            if errors:
                # Gộp lỗi theo đơn
                raise ValidationError(
                    _("Không thể gửi duyệt vì có lỗi ở dòng: \n- ") + "\n- ".join(errors)
                )

    def action_send_for_approval(self):
        """Gửi duyệt:
        - Luôn check công nợ trước.
        - Nếu bước kế tiếp là Kế toán:
            + công nợ OK  -> tự động bỏ qua bước Kế toán
            + công nợ KO  -> vẫn dừng ở bước Kế toán cho KT duyệt.
        """
        self._check_lines_price_on_submit()
    
        try:
            for order in self:
                # ==== FIX: nếu đã locked rồi thì coi như đã gửi duyệt (idempotent) ====
                if getattr(order, "locked", False):
                    order.message_post(
                        body=_("ℹ️ Đơn đã ở trạng thái chờ duyệt, bỏ qua thao tác gửi duyệt lại."),
                        subtype_xmlid="mail.mt_note",
                    )
                    continue
                
                # 0. Chọn workflow nếu chưa có
                if not order.workflow_id:
                    order._select_workflow_by_discount()
                if not order.workflow_id:
                    raise UserError(_("Không tìm thấy quy trình duyệt!"))
    
                steps = order.workflow_id.step_ids.sorted("sequence")
    
                # 1. Luôn chạy check công nợ trước
                msg, can_auto_pass = order._run_credit_check(post_to_chatter=True)
    
                current_seq = int(order.current_sequence or 0)
                context_vals = order.get_approval_context()
    
                # --- Helper: tính next_sequence theo conditions_json của 1 step ---
                def _compute_next_seq(step_obj):
                    conds = order._safe_parse_conditions(step_obj.conditions_json)
                    for cond in conds:
                        cond_if = cond.get("if", {}) or {}
                        match = True
                        for field, rule in cond_if.items():
                            val = context_vals.get(field)
                            if isinstance(rule, dict):
                                for op, rule_val in rule.items():
                                    try:
                                        if op == "$lte" and not (val <= rule_val):
                                            match = False
                                        if op == "$lt" and not (val < rule_val):
                                            match = False
                                        if op == "$gte" and not (val >= rule_val):
                                            match = False
                                        if op == "$gt" and not (val > rule_val):
                                            match = False
                                        if op == "$eq" and not (val == rule_val):
                                            match = False
                                    except Exception:
                                        match = False
                            else:
                                if val != rule:
                                    match = False
                            if not match:
                                break
                        if match:
                            return int(cond.get("next_sequence") or 0)
                    return 0
    
                # 2. Lấy step hiện tại (hoặc step đầu nếu current_sequence = 0)
                if current_seq:
                    step_obj = steps.filtered(lambda s: int(s.sequence) == current_seq)[:1]
                else:
                    step_obj = steps[:1]
                    if step_obj:
                        # ==== FIX: dùng write để chắc chắn persist ====
                        order.write({"current_sequence": int(step_obj.sequence)})
    
                if not step_obj:
                    raise UserError(_("Không tìm thấy bước duyệt hiện tại!"))
    
                next_seq = _compute_next_seq(step_obj[0])
    
                # 3. Nếu bước kế là Kế toán thì áp dụng auto-pass
                if next_seq:
                    next_step = steps.filtered(lambda s: int(s.sequence) == next_seq)[:1]
                    is_accountant = bool(
                        next_step
                        and getattr(next_step, "approver_type", "") == "role"
                        and getattr(next_step, "role_code", "") == "accountant"
                    )
    
                    if is_accountant:
                        if can_auto_pass:
                            # Công nợ OK -> tính bước SAU Kế toán và skip
                            after_next_seq = _compute_next_seq(next_step[0])
                            if after_next_seq:
                                order.message_post(
                                    body=_("🟢 Công nợ OK, tự động bỏ qua bước Kế toán, chuyển thẳng sang bước #%s")
                                    % after_next_seq,
                                    subtype_xmlid="mail.mt_note",
                                )
                                next_seq = after_next_seq
                            else:
                                # Nếu Kế toán là bước cuối -> hoàn tất quy trình (gọi approve)
                                order.message_post(
                                    body=_("🟢 Công nợ OK, tự động bỏ qua bước Kế toán và hoàn tất quy trình."),
                                    subtype_xmlid="mail.mt_note",
                                )
                                next_seq = 0
                        else:
                            # Công nợ không OK -> vẫn dừng ở bước Kế toán cho KT duyệt
                            order.message_post(
                                body=_("🟠 Cảnh báo công nợ: cần Kế toán duyệt thủ công.<br/>%s")
                                % (order.credit_notice or ""),
                                subtype_xmlid="mail.mt_note",
                            )
    
                # ==== FIX: helper vals khi submit từ website ====
                submit_vals = {"locked": True}
                if self.env.context.get("website_cart_submit") and "state" in order._fields and (order.state or "draft") == "draft":
                    # Đẩy state khỏi draft để website không bám lại đơn cũ làm cart
                    submit_vals["state"] = "sent"
    
                # 4. Áp dụng bước mới / hoàn tất quy trình
                if next_seq:
                    # Còn bước tiếp theo -> cập nhật current_sequence,
                    # để người phụ trách bước đó bấm Duyệt bước như bình thường.
                    submit_vals["current_sequence"] = next_seq
                    order.write(submit_vals)
                else:
                    # Không còn bước nào -> auto approve toàn bộ (bỏ kiểm tra quyền)
                    order.write(submit_vals)
                    res = order.with_context(skip_approval_permission=True).action_approve_step()
                    if isinstance(res, dict):
                        return res
    
            # Nếu chạy hết vòng for mà không có action đặc biệt -> báo gửi duyệt thành công + reload
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Gửi duyệt thành công"),
                    "message": _("Báo giá đã được đưa vào quy trình duyệt."),
                    "type": "success",
                    "sticky": False,
                    "next": {
                        "type": "ir.actions.client",
                        "tag": "reload",
                    },
                },
            }
    
        except UserError:
            raise
        except Exception as e:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Lỗi khi gửi duyệt"),
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }
    


    def update_applied_promotions(self):
        """Tổng hợp promotion của tất cả order_line, gán lên applied_promotion_ids."""
        for order in self:
            promo_ids = order.order_line.mapped('promotion_ids').ids
            order.applied_promotion_ids = [(6, 0, promo_ids)]
    
    @api.onchange('promotion_ids')
    def _onchange_update_applied_promotions(self):
        self.update_applied_promotions()

    @api.depends('order_line.price_unit', 'order_line.discount')
    def _compute_max_discount(self):
        for order in self:
            max_discount = 0.0
            for line in order.order_line:
                try:
                    base_price = line.get_oms_base_price() or 0.0
                except Exception:
                    base_price = 0.0
                sell_price = line.price_unit or 0.0
                if base_price > 0:
                    discount_percent = ((base_price - sell_price) / base_price) * 100
                    max_discount = max(max_discount, discount_percent)
            order.max_discount = max_discount

    @staticmethod
    def _safe_parse_conditions(cond_json):
        import json
        if not cond_json:
            return []
        if isinstance(cond_json, list):
            return cond_json
        if isinstance(cond_json, str):
            try:
                data = json.loads(cond_json)
                if isinstance(data, list):
                    return data
            except Exception as ex:
                _logger.error("[APPROVAL] ERROR json.loads: %s, DATA: %s", ex, cond_json)
        return []

    def _get_current_step(self):
        self.ensure_one()
        if not self.workflow_id:
            return None
        return self.workflow_id.step_ids.filtered(lambda s: s.sequence == self.current_sequence)[:1]

    def _match_conditions(self, step, context_vals):
        conditions = self._safe_parse_conditions(step.conditions_json)
        if not conditions:
            return True
        for cond in conditions:
            cond_if = cond.get("if", {})
            match = True
            for field, rule in cond_if.items():
                val = context_vals.get(field)
                _logger.info(f"[APPROVAL][MATCH] Field: {field} | Value: {val} | Rule: {rule}")
                if not isinstance(rule, dict):
                    continue
                for op, rule_val in rule.items():
                    _logger.info(f"[APPROVAL][CHECK] Op: {op} | Rule Value: {rule_val}")
                    if op == "$lte" and not (val <= rule_val): match = False
                    if op == "$lt"  and not (val <  rule_val): match = False
                    if op == "$gte" and not (val >= rule_val): match = False
                    if op == "$gt"  and not (val >  rule_val): match = False
                    if op == "$eq"  and not (val == rule_val): match = False
            _logger.info(f"[APPROVAL][MATCH] Condition: {cond_if} | Match: {match}")
            if match:
                return True
        return False

    def get_approval_context(self):
        self.ensure_one()

        # precision theo currency để tránh lệch làm tròn
        currency = self.currency_id or self.company_id.currency_id
        precision_rounding = getattr(currency, 'rounding', 0.01) or 0.01

        has_lower_than_base_price = any(
            l.price_unit < (l.get_oms_base_price() or 0.0)
            for l in self.order_line
            if not (getattr(l, 'is_gift', False) or getattr(l, 'is_bundle', False))
        )

        total_base = sum(
            (l.get_oms_base_price() or 0.0) * l.product_uom_qty
            for l in self.order_line
            if not (getattr(l, 'is_gift', False) or getattr(l, 'is_bundle', False))
        )
        total_sell = sum(
            (l.price_unit or 0.0) * l.product_uom_qty
            for l in self.order_line
            if not (getattr(l, 'is_gift', False) or getattr(l, 'is_bundle', False))
        )

        diff_amount = total_sell - total_base
        diff_percent = (diff_amount / total_base * 100) if total_base else 0.0

        max_discount = 0.0
        for line in self.order_line:
            base = line.get_oms_base_price() or 0.0
            sell = line.price_unit or 0.0
            if base > 0:
                max_discount = max(max_discount, ((base - sell) / base) * 100)
            _logger.info(
                '[APPROVAL][DEBUG] Line: %s | Base: %s | Sell: %s | Discount(%%): %s',
                line.product_id.display_name, base, sell,
                ('%.2f' % (((base - sell) / base) * 100) if base else 'N/A')
            )

        # ✅ Cờ tổng đơn = 0 theo rounding của currency
        amount_total_is_zero = float_is_zero(self.amount_total or 0.0, precision_rounding=precision_rounding)
        amount_total_rounded = currency.round(self.amount_total or 0.0)

        _logger.info(
            '[APPROVAL][CONTEXT] Total Base: %s | Total Sell: %s | Diff Amount: %s | Diff %%: %s | '
            'Has Lower Than Base: %s | Max Discount: %s | AmountTotalIsZero: %s | AmountTotalRounded: %s',
            total_base, total_sell, diff_amount, diff_percent,
            has_lower_than_base_price, max_discount, amount_total_is_zero, amount_total_rounded
        )

        return {
            "has_lower_than_base_price": has_lower_than_base_price,
            "diff_amount": diff_amount,
            "diff_percent": diff_percent,
            "max_discount": max_discount,
            # 👇 khóa mới cho rule JSON
            "amount_total_is_zero": amount_total_is_zero,
            # (tuỳ chọn) tiện debug
            "amount_total": amount_total_rounded,
        }

    def _select_workflow_by_discount(self):
        for order in self:
            context_vals = order.get_approval_context()
            matched_workflow = None
            matched_sequence = None
            for wf in self.env['approval.workflow'].search([]):
                steps = wf.step_ids.sorted('sequence')
                for step in steps:
                    conditions = order._safe_parse_conditions(step.conditions_json)
                    if not conditions:
                        conditions = [{}]
                    for cond in conditions:
                        cond_if = cond.get("if", {})
                        match = True
                        for field, rule in cond_if.items():
                            val = context_vals.get(field)
                            if not isinstance(rule, dict):
                                continue
                            for op, rule_val in rule.items():
                                if op == "$lte" and not (val <= rule_val): match = False
                                if op == "$lt"  and not (val <  rule_val): match = False
                                if op == "$gte" and not (val >= rule_val): match = False
                                if op == "$gt"  and not (val >  rule_val): match = False
                                if op == "$eq"  and not (val == rule_val): match = False
                        if match:
                            matched_workflow = wf
                            matched_sequence = step.sequence
                            break
                    if matched_workflow:
                        break
                if matched_workflow:
                    break
            order.workflow_id = matched_workflow
            order.current_sequence = matched_sequence or 0
    
    def _get_next_approval_step(self, context_vals):
        self.ensure_one()
        if not self.workflow_id:
            return (0, False)
        steps = self.workflow_id.step_ids.sorted('sequence')
        current_step = steps.filtered(lambda s: s.sequence == self.current_sequence)
        if not current_step:
            return (0, False)
        conditions = self._safe_parse_conditions(current_step[0].conditions_json)
        for cond in conditions:
            cond_if = cond.get("if", {})
            next_seq = int(cond.get("next_sequence", 0))
            match = True
            for field, rule in cond_if.items():
                val = context_vals.get(field)
                if isinstance(rule, dict):
                    for op, rule_val in rule.items():
                        if op == "$lte" and not (val <= rule_val): match = False
                        if op == "$lt"  and not (val <  rule_val): match = False
                        if op == "$gte" and not (val >= rule_val): match = False
                        if op == "$gt"  and not (val >  rule_val): match = False
                        if op == "$eq"  and not (val == rule_val): match = False
                else:
                    if val != rule:
                        match = False
            if match:
                next_step = steps.filtered(lambda s: s.sequence == next_seq)
                next_approver = next_step and next_step[0].approver_id or False
                return (next_seq, next_approver)
        return (0, False)

    def _json_stringify(self, data):
        try:
            if isinstance(data, (str, bytes)):
                return data.decode() if isinstance(data, bytes) else data
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            return repr(data)

    def _attach_json(self, order, filename, data):
        content = self._json_stringify(data)
        att = self.env['ir.attachment'].create({
            'name': filename,
            'res_model': order._name,
            'res_id': order.id,
            'type': 'binary',
            'mimetype': 'application/json',
            'datas': base64.b64encode(content.encode('utf-8')),
        })
        return att.id
    can_user_approve = fields.Boolean(
        string="Có quyền duyệt",
        compute="_compute_can_user_approve",
        store=False,
    )

    @api.depends('workflow_id', 'current_sequence')
    def _compute_can_user_approve(self):
        for order in self:
            allowed = False
            step = False
            try:
                if order.workflow_id and order.current_sequence:
                    step = order.workflow_id.step_ids.filtered(
                        lambda s: int(s.sequence) == int(order.current_sequence)
                    )[:1]
            except Exception:
                step = False
            if step and hasattr(step, 'can_user_approve'):
                allowed = bool(step.can_user_approve(self.env.user, order=order))
            order.can_user_approve = allowed

    def action_approve_step(self):
        def _compute_next_seq(order, step):
            """Tính next_sequence cho 1 step dựa trên conditions_json."""
            next_seq = 0
            context_vals = order.get_approval_context()
            conds = order._safe_parse_conditions(step.conditions_json)
            for cond in conds:
                cond_if = cond.get("if", {})
                match = True
                for field, rule in cond_if.items():
                    val = context_vals.get(field)
                    if isinstance(rule, dict):
                        for op, rule_val in rule.items():
                            try:
                                if op == "$lte" and not (val <= rule_val):
                                    match = False
                                if op == "$lt"  and not (val <  rule_val):
                                    match = False
                                if op == "$gte" and not (val >= rule_val):
                                    match = False
                                if op == "$gt"  and not (val >  rule_val):
                                    match = False
                                if op == "$eq"  and not (val == rule_val):
                                    match = False
                            except Exception:
                                match = False
                    else:
                        if val != rule:
                            match = False
                    if not match:
                        break
                if match:
                    next_seq = int(cond.get('next_sequence', 0))
                    break
            return next_seq

        for order in self:
            if not order.workflow_id:
                raise UserError(_("Không tìm thấy quy trình duyệt!"))

            step = order.workflow_id.step_ids.filtered(
                lambda s: int(s.sequence) == int(order.current_sequence)
            )[:1]
            if not step:
                raise UserError(_("Không tìm thấy bước duyệt hiện tại!"))
            skip_perm = bool(self.env.context.get("skip_approval_permission"))
            if (not skip_perm) and (not step.can_user_approve(self.env.user, order=order)):
                raise UserError(_("Bạn không có quyền duyệt ở bước hiện tại."))

            # Log duyệt bước hiện tại (sale leader / GM / v.v.)
            step_name = step.name
            user_name = self.env.user.name
            approve_time = fields.Datetime.now().strftime(DEFAULT_SERVER_DATETIME_FORMAT)
            order.message_post(
                body=f"✅ Bước duyệt: {step_name} - 👤 {user_name} - 🕒 {approve_time}",
                subtype_xmlid="mail.mt_note"
            )

            # 1) Tính bước kế từ step hiện tại
            next_seq = _compute_next_seq(order, step)

            # 2) Nếu bước kế là Kế toán thì xử lý công nợ
            if next_seq:
                next_step = order.workflow_id.step_ids.filtered(
                    lambda s: int(s.sequence) == int(next_seq)
                )[:1]

                is_accountant_next = bool(
                    next_step
                    and getattr(next_step, 'approver_type', '') == 'role'
                    and getattr(next_step, 'role_code', '') == 'accountant'
                )

                # 2.b) Nếu bước kế tiếp là Kế toán -> xử lý đặc biệt với công nợ
                if next_step and getattr(next_step, 'approver_type', False) == 'role' and getattr(next_step, 'role_code', '') == 'accountant':
                    # Ưu tiên dùng kết quả đã check khi Gửi duyệt
                    can_auto_pass = order.credit_auto_pass

                    # Trường hợp hiếm: chưa check lần nào (ví dụ gọi trực tiếp Duyệt bước)
                    if not order.show_credit_label:
                        # 1. Luôn chạy check công nợ trước
                        msg, can_auto_pass = order._run_credit_check(post_to_chatter=True)

                    if can_auto_pass:
                        # Công nợ OK -> tính bước sau Kế toán để SKIP
                        after_next_seq = _compute_next_seq(order, next_step)
                        if after_next_seq:
                            order.message_post(
                                body=_("🟢 Công nợ OK, tự động bỏ qua bước Kế toán, chuyển thẳng sang bước #%s") % after_next_seq,
                                subtype_xmlid="mail.mt_note",
                            )
                            next_seq = after_next_seq
                        else:
                            # Nếu Kế toán là bước cuối -> coi như hoàn tất quy trình
                            order.message_post(
                                body=_("🟢 Công nợ OK, tự động bỏ qua bước Kế toán và hoàn tất quy trình."),
                                subtype_xmlid="mail.mt_note",
                            )
                            next_seq = 0
                    else:
                        # Công nợ KHÔNG OK -> vẫn đi qua bước Kế toán, chỉ cảnh báo
                        order.message_post(
                            body=_("🟠 Cảnh báo công nợ: cần Kế toán duyệt thủ công.<br/>%s") % (order.credit_notice or ""),
                            subtype_xmlid="mail.mt_note",
                        )

            # 3) Nếu còn bước kế tiếp (có thể đã skip Kế toán) -> chuyển bước
            if next_seq:
                order.current_sequence = next_seq
                order.message_post(body=f"➡️ Chuyển sang bước duyệt #{next_seq}")
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Duyệt thành công"),
                        "message": _("Đã chuyển sang bước #%s") % next_seq,
                        "type": "success",
                        "sticky": False,
                        "next": {          # 👈 thêm phần này
                            "type": "ir.actions.client",
                            "tag": "reload",
                        },
                    },
                }

            # 4) Không còn bước nào -> gọi API CreateSO & duyệt xong
            ICP = self.env['ir.config_parameter'].sudo()
            api_user = 'trungtq'
            api_pass = 'Trung@2025'
            ts = fields.Datetime.now().strftime("%Y%m%d_%H%M%S")

            try:
                result = order.action_dat_create_so(api_user, api_pass, preview=False)
                att_id = self._attach_json(order, f"CreateSO_response_{ts}.json", result)
                order.write({'current_sequence': 0, 'state': 'approved', 'StatusID': '2'})
                order.message_post(body="🛰️ CreateSO thành công", attachment_ids=[att_id])
                order.message_post(body="✅ Đã duyệt xong toàn bộ quy trình (Approved)")
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Duyệt thành công"),
                        "message": _("Quy trình đã hoàn tất. Đơn hàng được Approved."),
                        "type": "success",
                        "sticky": False,
                        "next": {"type": "ir.actions.client", "tag": "reload"},
                    },
                }

            except Exception as e:
                att_id = self._attach_json(order, f"CreateSO_error_{ts}.json", str(e))
                order.message_post(body=f"❌ CreateSO thất bại: {e}", attachment_ids=[att_id])
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("CreateSO thất bại"),
                        "message": str(e),
                        "type": "danger",
                        "sticky": False,
                    },
                }

        return {"type": "ir.actions.client", "tag": "reload"}



    def action_reject(self):
        for order in self:
            # (Tuỳ chọn) chặn quyền từ chối như chặn quyền duyệt
            step = order.workflow_id and order.workflow_id.step_ids.filtered(
                lambda s: int(s.sequence) == int(order.current_sequence)
            )[:1] or False
            if step and not step.can_user_approve(self.env.user, order=order):
                raise UserError(_("Bạn không có quyền từ chối ở bước hiện tại."))

            step_name = step.name if step else ""
            user_name = self.env.user.name
            reject_time = fields.Datetime.now().strftime(DEFAULT_SERVER_DATETIME_FORMAT)
            order.message_post(
                body=(f"🚫 *ĐƠN HÀNG BỊ TỪ CHỐI*\n"
                      f"- Bước: {step_name}\n"
                      f"- Người từ chối: {user_name}\n"
                      f"- Thời gian: {reject_time}"),
                subtype_xmlid="mail.mt_note"
            )
            order.state = 'rejected'
            order.current_sequence = 0
        # Reload để thấy state=rejected
        return {'type': 'ir.actions.client', 'tag': 'reload'}
    
    def _compute_current_step_name(self):
        for order in self:
            name = ""
            current_step = order._get_current_step()
            if current_step:
                name = current_step.name
            order.current_step_name = name

    def action_open_promotion_wizard(self):
        self.ensure_one()
        product_ids = self.order_line.filtered(
            lambda l: l.product_id and not l.is_gift and not l.is_bundle
        ).mapped('product_id')
        product_tmpl_ids = set(product_ids.mapped('product_tmpl_id').ids)
        categ_ids = set(product_ids.mapped('categ_id').ids)
        today = fields.Date.today()

        # Lấy tất cả khuyến mãi còn hiệu lực, lọc theo phạm vi khách hàng
        partner_id = self.partner_id.id
        all_promos = self.env['oms.promotion'].search([
            ('valid_from', '<=', today),
            ('valid_to', '>=', today),
            '|',
            ('customer_scope', '=', 'all'),
            ('partner_ids', 'in', [partner_id] if partner_id else [0]),
        ])
        result_promos = self.env['oms.promotion']
        for promo in all_promos:
            has_main = bool(promo.apply_product_line_ids.filtered(
                lambda pl: pl.product_tmpl_id.id in product_tmpl_ids or pl.product_category_id.id in categ_ids
            ))

            # Bán kèm: đủ ít nhất 1 sp từ bất kỳ combo nào trong đơn
            all_bundle_tmpl_ids = set()
            for combo in promo.bundle_combo_ids:
                all_bundle_tmpl_ids.update(combo.product_tmpl_ids.ids)
            has_bundle = bool(all_bundle_tmpl_ids & product_tmpl_ids)

            if promo.apply_product_line_ids and promo.bundle_combo_ids:
                # Cần ít nhất 1 sp chính VÀ 1 sp bán kèm
                if has_main and has_bundle:
                    result_promos += promo
            elif promo.apply_product_line_ids:
                if has_main:
                    result_promos += promo
            elif promo.bundle_combo_ids:
                if has_bundle:
                    result_promos += promo

        promo_ids = result_promos.ids

        return {
            'name': 'Chọn khuyến mãi',
            'type': 'ir.actions.act_window',
            'res_model': 'promotion.multi.apply.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('sale_custom.view_oms_promotion_multi_apply_wizard_form').id,
            'target': 'new',
            'context': {
                'active_id': self.id,
                'active_model': self._name,
                'allowed_promotion_ids': promo_ids,
            },
        }
    def _get_header_filler(self):
        """
        Xác định Filler (kho/chi nhánh mặc định) cho header.
        Ưu tiên:
          1) Trường WhsCode (nếu bạn có M2O tới model kho, có thuộc tính .whs_code)
          2) Map theo chi nhánh/địa chỉ giao hàng
          3) Tham số hệ thống (có thể override)
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
    
        # 1) Nếu order có chọn kho cụ thể (M2O) thì ưu tiên dùng
        whs_m2o = getattr(self, 'WhsCode', False)
        if whs_m2o and getattr(whs_m2o, 'whs_code', False):
            return (whs_m2o.whs_code or '').strip()
    
        # 2) Suy luận theo chi nhánh/địa lý
        branch = (getattr(self, 'branch_code', '') or '').upper()
        city = ((self.partner_shipping_id.city or self.partner_id.city or '') or '').upper()
    
        # Cho phép override bằng tham số hệ thống
        filler_hcm = ICP.get_param('oms.filler_hcm', 'HCMVP201')
        filler_hni = ICP.get_param('oms.filler_hni', 'HNIVP201')
        filler_cth = ICP.get_param('oms.filler_cth', 'CTHVP201')
        filler_default = ICP.get_param('oms.filler_default', 'HCMVP201')
    
        if 'HCM' in branch or 'HO CHI MINH' in city or 'HỒ CHÍ MINH' in city:
            return filler_hcm
        if 'HNI' in branch or 'HA NOI' in city or 'HÀ NỘI' in city or branch == 'HN':
            return filler_hni
        if 'CTH' in branch or 'CAN THO' in city or 'CẦN THƠ' in city:
            return filler_cth
    
        # 3) Mặc định
        return filler_default

    def _yn(self, value, *, true='Y', false='N'):
        """
        Chuẩn hoá giá trị về 'Y'/'N' cho các field U_*
        Chấp nhận bool/int/str/None.
        """
        if value is None:
            return false
        if isinstance(value, bool):     
            return true if value else false
        if isinstance(value, (int, float)):
            return true if value != 0 else false
        s = str(value).strip().lower()
        return true if s in {'y', 'yes', 'true', 't', '1', 'on', 'x'} else false
    def remove_applied_promotion(self, promotion_id):
        """
        Remove a specific promotion_id from order:
        - Remove promotion_id khỏi promotion_ids của các dòng bán
        - Remove các dòng gift/bundle sinh ra từ promotion này
        - Làm việc an toàn với cả dòng new (chưa lưu), đã lưu
        """
        for order in self:
            # Bước 1: Xóa dòng quà tặng/bundle liên quan promotion này
            lines_to_remove = self.env['sale_custom.order.line']
            for line in list(order.order_line):  # Chuyển sang list để không bị lỗi khi remove
                # Dòng quà tặng/bundle phải có linked_line_id (dòng bán) và phải sinh từ đúng promotion
                if line.is_gift or line.is_bundle:
                    linked_sale_line = line.linked_line_id
                    if not linked_sale_line:
                        continue
                    # Chỉ remove nếu promotion_id đang có trong promotion_ids của linked_sale_line
                    if promotion_id in linked_sale_line.promotion_ids.ids:
                        if line.id and line.order_id:  # Đã lưu DB
                            lines_to_remove |= line
                        else:  # Dòng chưa lưu DB (transient)
                            order.order_line -= line
    
            if lines_to_remove:
                lines_to_remove.unlink()
    
            # Bước 2: Remove promotion_id khỏi promotion_ids trên tất cả dòng bán liên quan
            for line in order.order_line:
                if promotion_id in line.promotion_ids.ids:
                    line.promotion_ids = [(3, promotion_id)]
    
            # Bước 3: Áp dụng lại promotion logic (re-apply) cho từng dòng, để update lại discount/gift/bundle
            for line in order.order_line:
                if not (line.is_gift or line.is_bundle):
                    line.apply_promotions_to_line()
    
            # (Optional) Gọi hàm update lại các field summary của order nếu cần
            if hasattr(order, 'update_applied_promotions'):
                order.update_applied_promotions()

    # --- Auth helper to get token for DAT APIs ---
    def _get_token(self):
        """
        Lấy Bearer token từ Auth server.
        Bạn đang cố định username/password + auth_url ở đây theo ý muốn.
        Ném UserError khi không lấy được token để hiện ngay trên chatter.
        """
        from odoo.exceptions import UserError
        import requests

        username = "trungtq"
        password = "Trung@2025"
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"

        attempts = [
            ("json", {"username": username, "password": password}),
            ("json", {"userName": username, "password": password}),
            ("json", {"email": username, "password": password}),
            ("form", {"username": username, "password": password}),
        ]
        last_err = None

        for mode, payload in attempts:
            try:
                if mode == "json":
                    r = requests.post(auth_url, json=payload, timeout=15)
                else:
                    r = requests.post(auth_url, data=payload, timeout=15)
            except Exception as e:
                last_err = f"Lỗi kết nối: {e}"
                continue

            if not r.ok:
                # cắt gọn response để không dài chatter
                last_err = f"Auth HTTP {r.status_code}: {r.text[:500]}"
                continue

            # parse json & bắt nhiều khóa token thường gặp
            try:
                body = r.json()
            except Exception:
                last_err = f"Phản hồi không phải JSON: {r.text[:500]}"
                continue

            token = (
                body.get("token")
                or body.get("access_token")
                or body.get("accessToken")
                or (body.get("data") or {}).get("access_token")
                or (body.get("Data") or {}).get("Token")
            )
            if token:
                return token

            last_err = f"Không tìm thấy token trong phản hồi: {body}"

        raise UserError(_("Không lấy được token: %s") % last_err)

    def _post_data_with_token(self, api_url, token, payload):
        import requests
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            res = requests.post(api_url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            try:
                data = res.json()
                if isinstance(data, dict):
                    return data
                return {}
            except Exception:
                return {}
        except Exception:
            return {}


    def resequence_order_lines(self):
        """
        Đảm bảo thứ tự: dòng chính -> các dòng khuyến mãi liên kết -> các dòng khác.
        Chỉ write khi thứ tự thực sự thay đổi.
        """
        for order in self:
            main_lines = []
            child_map = {}
            for l in order.order_line:
                if not l.is_gift and not l.is_bundle:
                    main_lines.append(l)
                else:
                    child_map.setdefault(l.linked_line_id.id, []).append(l)

            seq = 10
            to_update = []
            for main in sorted(main_lines, key=lambda l: (l.sequence, l.id)):
                if main.sequence != seq:
                    main.sequence = seq
                    to_update.append(main)
                seq += 10
                for child in sorted(child_map.get(main.id, []), key=lambda l: l.id):
                    if child.sequence != seq:
                        child.sequence = seq
                        to_update.append(child)
                    seq += 10

            if to_update:
                for line in to_update:
                    line.write({'sequence': line.sequence})


     # -----------------------------
    # HELPERS
    # -----------------------------
    @staticmethod
    def _yn(v) -> str:
        """Trả 'Y' nếu truthy, ngược lại 'N'."""
        return "Y" if bool(v) else "N"

    @staticmethod
    def _normalize_whs(code: str) -> str:
        """Chuẩn hoá mã kho: một số master dùng ...101 nhưng API chỉ nhận ...201."""
        if not code:
            return ""
        code = code.strip().upper()
        fix = {"HCMVP101": "HCMVP201", "HNIVP101": "HNIVP201", "CTHVP101": "CTHVP201"}
        return fix.get(code, code)

    def _line_whs(self, line, header_filler: str) -> str:
        """
        Kho theo dòng: ưu tiên chọn ở dòng; nếu không có thì dùng header_filler.
        KHÔNG normalize (để API nhận mã ...101 theo đúng mẫu).
        """
        code = (getattr(line, "whs_code", None) or getattr(line, "WhsCode", None))
        if hasattr(code, "whs_code"):
            code = code.whs_code
        code = (code or "").strip().upper()
        return code or (header_filler or "")

    @staticmethod
    def _qty_str(qty):
        """Chuẩn hoá số lượng thành chuỗi: '1', '1.5'... (không dư .0)."""
        q = float(qty or 0.0)
        if abs(q - round(q)) < 1e-9:
            return str(int(round(q)))
        s = f"{q:.6f}".rstrip("0").rstrip(".")
        return s or "0"

    @staticmethod
    def _line_price(line):
        """
        Đơn giá CHƯA VAT, đã trừ chiết khấu — trả về CHUỖI 6 số thập phân.
        """
        qty = float(line.product_uom_qty or 0.0)
        if qty <= 0:
            return "0.000000"
        try:
            price = float(line.price_subtotal or 0.0) / qty
        except Exception:
            discount = float(getattr(line, "discount", 0.0) or 0.0)
            price = float(line.price_unit or 0.0) * (1.0 - discount / 100.0)
        return f"{price:.6f}"

    def _get_tax_code(self, line):
        """
        Lấy TaxCode (SVN1..SVN7) cho 1 dòng:
        1) Từ group gắn trực tiếp line/order
        2) Suy từ % thuế của line.tax_id -> tìm oms.tax.group.rate
        3) Fallback tham số hệ thống 'oms.taxgroup' (mặc định SVN3)
        """
        # 1) trực tiếp từ dòng
        for f in ("tax_group_id", "x_tax_group_id"):
            tg = getattr(line, f, None)
            code = getattr(tg, "code", None)
            if code:
                return str(code).strip()

        # 2) từ order
        for f in ("tax_group_id", "x_tax_group_id"):
            tg = getattr(self, f, None)
            code = getattr(tg, "code", None)
            if code:
                return str(code).strip()

        # 2b) suy từ % thuế
        try:
            rate = 0.0
            taxes = getattr(line, "tax_id", None) or self.env["account.tax"]
            for t in taxes:
                if getattr(t, "amount_type", "") == "percent" and getattr(t, "type_tax_use", "") in ("sale", "none"):
                    rate += float(getattr(t, "amount", 0.0) or 0.0)
            rate = round(rate, 6)
            try:
                rec = self.env["oms.tax.group"].search([("rate", "=", rate)], limit=1)
                if rec and rec.code:
                    return str(rec.code).strip()
            except Exception:
                # fallback map phổ biến
                rate_map = {10.0: "SVN1", 5.0: "SVN2", 8.0: "SVN4", 0.0: "SVN5"}
                if rate in rate_map:
                    return rate_map[rate]
        except Exception:
            pass

        # 3) tham số hệ thống
        ICP = self.env["ir.config_parameter"].sudo()
        return str(ICP.get_param("oms.taxgroup", "SVN3") or "SVN3").strip()

    def _store_from_user_branch(self) -> int:
        """
        Map U_Store theo chi nhánh của nhân viên bán hàng (SlpCode) gắn với đơn:
          - HCM -> store_hcm
          - CTH -> store_cth
          - HNI/HN -> store_hni
        Có thể override qua tham số hệ thống: oms.store_hcm/cth/hni, fallback oms.default_store.
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()

        def _as_int(v, d):
            try:
                return int(v)
            except Exception:
                return d

        store_hcm = _as_int(ICP.get_param("oms.store_hcm", "1"), 1)
        store_cth = _as_int(ICP.get_param("oms.store_cth", "2"), 2)
        store_hni = _as_int(ICP.get_param("oms.store_hni", "3"), 3)
        store_def = _as_int(ICP.get_param("oms.default_store", "1"), 1)

        # 🔑 Lấy branch từ SlpCode (người tạo đơn hàng)
        slp_user = getattr(self, "SlpCode", False)
        branch = (getattr(slp_user, "branch", "") or "").upper().strip()

        if branch.startswith("HCM"):
            return store_hcm
        if branch.startswith("CTH"):
            return store_cth
        if branch.startswith("HNI") or branch.startswith("HN"):
            return store_hni
        return store_def


    def _get_slp_code(self) -> int:
        """
        Trả về mã SlpCode (int) để gửi API.
        Ưu tiên: order.SlpCode -> order.user_id -> env.user.
        Đọc các thuộc tính có thể chứa mã: slp_code / sap_slpcode / x_sap_slpcode / sales_code / sap_code / code.
        Fallback: ir.config_parameter['oms.default_slpcode'] (mặc định 0).
        """
        def _pick_code(u):
            if not u:
                return None
            for attr in ('slp_code', 'sap_slpcode', 'x_sap_slpcode', 'sales_code', 'sap_code', 'code'):
                val = getattr(u, attr, None)
                if val not in (None, '', False):
                    try:
                        return int(str(val).strip())
                    except Exception:
                        pass
            return None

        # giống “cardcode = (A or B or C)”
        code = _pick_code(getattr(self, 'SlpCode', None)) \
            or _pick_code(getattr(self, 'user_id', None)) \
            or _pick_code(self.env.user)

        if code is not None:
            return code

        ICP = self.env['ir.config_parameter'].sudo()
        try:
            return int(ICP.get_param('oms.default_slpcode', '0') or 0)
        except Exception:
            return 0

    def _get_header_filler(self) -> str:
        """
        Lấy Filler từ order nếu có (field WhsCode), nếu không từ dòng đầu tiên,
        sau đó normalize về ...201.
        """
        code = ""
        # từ order
        w = getattr(self, "WhsCode", None)
        if hasattr(w, "whs_code"):
            code = w.whs_code
        elif isinstance(w, str):
            code = w

        if not code:
            # từ dòng đầu tiên có kho
            for ln in self.order_line:
                code = self._line_whs(ln, "")
                if code:
                    break

        return self._normalize_whs(code or "")

    def _get_warr_time_by_code(self, item_code: str) -> int:
        """Tra bảo hành (tháng) từ bảng oms.product.item theo ItemCode."""
        if not item_code:
            return 0
        rec = self.env['oms.product.item'].search([('item_code', '=', item_code)], limit=1)
        try:
            return int(rec.u_warr_time or 0)
        except Exception:
            return 0
    def _get_trnsp_code(self):
        """Trả về TrnspCode (int) để gửi sang payload"""
        self.ensure_one()
        if self.trnsp_id:
            return str(self.trnsp_id.trnsp_code)  # ép sang str vì API thường expect string
        return ""

    def _get_project_code(self) -> str:
        """Trả về mã dự án (prj_code) dùng cho payload."""
        self.ensure_one()
        p = self.Project
        if not p:
            return ""
        # Ưu tiên prj_code; fallback name
        code = getattr(p, "prj_code", None) or getattr(p, "name", None) or ""
        return str(code).strip()

        # --- Address helpers for payload (ShipTo/PayTo) ---
    def _get_ship_to_code(self) -> str:
        """
        MÃ ShipToCode gửi payload:
        - Nếu đã chọn self.ShipToCode: lấy field 'address' của bản ghi đó.
        - Nếu chưa chọn: tìm địa chỉ giao hàng đầu tiên (adres_type='S') theo partner_ref và lấy 'address'.
        """
        self.ensure_one()
        shipping_addr = self._find_oms_ship_to_from_partner_shipping()
        if shipping_addr:
            return (shipping_addr.address or '').strip()

        st = getattr(self, 'ShipToCode', False)
        if st:
            return (st.address or '').strip()

        partner_ref = (self.partner_ref or self._uc_partner_ref_from_partner(self.partner_id)).strip()
        if not partner_ref:
            return ''
        addr = self.env['oms.address'].search(
            [('card_code', '=', partner_ref), ('adres_type', '=', 'S')],
            order='sequence asc, id asc', limit=1
        )
        return (addr.address or '').strip()

    def _get_address2(self) -> str:
        """
        Theo yêu cầu: Address2 = name của ShipToCode.
        (Không dùng trường Address2 trên UI; đây là giá trị gửi payload)
        """
        self.ensure_one()
        shipping_addr = self._find_oms_ship_to_from_partner_shipping()
        if shipping_addr:
            return (getattr(shipping_addr, 'name', '') or '').strip()

        st = getattr(self, 'ShipToCode', False)
        if st:
            return (getattr(st, 'name', '') or '').strip()
        # fallback: nếu chưa chọn thì dùng cùng giá trị như _get_ship_to_code
        return self._get_ship_to_code()

    def _get_pay_to_code(self) -> str:
        """
        MÃ PayToCode gửi payload:
        - Nếu có M2O PayToCode thì lấy 'address' của bản ghi đó.
        - Nếu không: tìm địa chỉ thanh toán (adres_type='B') theo partner_ref và lấy 'address'.
        """
        self.ensure_one()
        pay_to = getattr(self, 'PayToCode', False)
        if pay_to:
            return (pay_to.address or '').strip()

        partner_ref = (self.partner_ref or self._uc_partner_ref_from_partner(self.partner_id)).strip()
        if not partner_ref:
            return ''
        addr = self.env['oms.address'].search(
            [('card_code', '=', partner_ref), ('adres_type', '=', 'B')],
            order=' id asc', limit=1
        )
        return (addr.address or '').strip()

    def _get_address(self) -> str:
        """
        Theo yêu cầu mới: Address = 'name' của PayToCode.
        """
        self.ensure_one()
        pay_to = getattr(self, 'PayToCode', False)
        if pay_to:
            return (pay_to.name or '').strip()

        partner_ref = (self.partner_ref or self._uc_partner_ref_from_partner(self.partner_id)).strip()
        if not partner_ref:
            return ''
        addr = self.env['oms.address'].search(
            [('card_code', '=', partner_ref), ('adres_type', '=', 'B')],
            order='id asc', limit=1
        )
        return (addr.name or '').strip()
    def _extract_cntct_code(self, contact) -> int | None:
        """Lấy mã CntctCode (int) từ oms.contact, trả None nếu không hợp lệ."""
        if not contact:
            return None
        for attr in ("cntct_code", "CntctCode", "cntctcode", "code", "sap_cntctcode", "sap_code"):
            val = getattr(contact, attr, None)
            if val not in (None, "", False):
                try:
                    return int(str(val).strip())
                except Exception:
                    pass
        return None


    def _get_cntct_code_for(self, field_name: str):
        """
        Lấy CntctCode số từ field M2O ('cntct_code' hoặc 'u_carcodecommission').
        Trả về None nếu không có → sẽ serialize thành null trong JSON payload.
        """
        rec = getattr(self, field_name, False)
        code = self._extract_cntct_code(rec)
        return int(code) if code is not None else None

    # -----------------------------
    # PAYLOAD
    # -----------------------------
    def _build_payload(self):
        self.ensure_one()

        # CardCode ưu tiên invoice -> main
        cardcode = (
            self._uc_partner_ref_from_partner(self.partner_invoice_id)
            or self._uc_partner_ref_from_partner(self.partner_id)
            or ""
        ).strip()
        if not cardcode:
            raise UserError(_("Thiếu CardCode (partner.ref)."))

        posting = (fields.Datetime.now()).date().isoformat()
        taxdate = posting
        duedate_src = (
            self.validity_date
            or self.commitment_date
            or self.date_order
            or fields.Datetime.now()
        )

        # Ép về date an toàn, dù là date hay datetime
        duedate = fields.Date.to_date(duedate_src).isoformat()

        filler_raw = (self._get_header_filler() or "").strip()
        filler = self._normalize_whs(filler_raw) or "HCMVP201"

        ICP = self.env["ir.config_parameter"].sudo()
        total = float(self.amount_total or 0)
        vt = "1340" if total == 0 else str(ICP.get_param("oms.voucher_type_id", "1310") or "1310").strip()
        prj = self._get_project_code()

        u_reasons = "01-998" if vt == "1340" else ""

        def _plain_text(value):
            value = (value or "")
            # Preserve line breaks and remove HTML tags/URLs from rich text
            value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
            value = re.sub(r'<[^>]+>', '', value)
            value = html.unescape(value)
            return value.strip()

        def _text(field_name):
            if field_name not in self._fields:
                return ""
            return _plain_text(getattr(self, field_name, False) or "")

        def _join_notes(*parts):
            return "\n".join(part for part in [(p or "").strip() for p in parts] if part)

        checkout_note = _text("Comments") or _text("dispatch_note")
        internal_note = _text("NoteInternal")
        accounting_note = _text("NoteForAct")
        outstation_parts = []
        if _text("oms_transport_need"):
            outstation_parts.append("Phuong an van chuyen: %s" % _text("oms_transport_need"))
        if _text("oms_transport_address"):
            outstation_parts.append("Dia chi nhan hang: %s" % _text("oms_transport_address"))
        if _text("oms_transport_note"):
            outstation_parts.append("Ghi chu van chuyen: %s" % _text("oms_transport_note"))
        outstation_note = _join_notes(*outstation_parts)
        team_note = _join_notes(checkout_note, outstation_note)
        logistic_note = outstation_note or _text("EL_Construction_Code")

        payload = {
            "CardCode": cardcode,
            "U_CardCode2": (self._uc_partner_ref_from_partner(self.CardCode2) or cardcode).strip(),
            "PostingDate": posting,
            "DocDueDate": duedate,
            "Filler": filler,
            "ToWhsCode": None,
            "TaxDate": taxdate,
            "Comments": checkout_note,
            "U_Store": self._store_from_user_branch(),
            "U_VoucherTypeID": vt,

            # Chỉ 1 trường: gửi thẳng ký tự Y/N/A/B/T/C
            "U_IsIssueInvoice": self.u_is_issue_invoice or "Y",

            # Map boolean -> Y/N
            "U_isInstall": self._yn(getattr(self, "IsInstall", False)),
            "U_IsCOCQ": self._yn(getattr(self, "IsCOCQ", False)),
            "U_IsSetup": self._yn(getattr(self, "IsSetup", False)),
            "SlpCode": self._get_slp_code(),
            "U_NoteForAcc": accounting_note,
            "U_NoteForAll": team_note,
            "U_NoteForWhs": internal_note,
            "U_NoteForLogistic": logistic_note,
            "U_BusinessUnit": "AUT",
            "U_InvStore": self._store_from_user_branch(),
            "TrnspCode": self._get_trnsp_code(),
            "Project": prj,
            "ShipToCode": self._get_ship_to_code(),
            "Address2": self._get_address2(),
            "PayToCode": self._get_pay_to_code(),
            "Address": self._get_address(),
            "U_Reasons":u_reasons,
            "CntctCode": self._get_cntct_code_for("cntct_code"),
            "U_CarCodeCommission": self._get_cntct_code_for("u_carcodecommission"), 
            "LicTradNum": getattr(self, "vat", None) or "", 
            "U_SONumberRef": self.name or "",
            "U_Compaign": "",
            "U_ExtCampaign": "", 
            "Lines": [],
        }
                # =============================
        # Promotions -> U_Compaign / U_NoteForWhs (ưu tiên suy "loại" từ dòng quà)
        # =============================
        def _collect_promotion_info():
            """
            Trả về (ids:list[int], labels:list[str]) cho các CTKM trong đơn.
            - Ưu tiên promotion_selected_ids (đã chốt), fallback promotion_ids.
            - "Loại quà" được SUY TỪ DÒNG QUÀ (KM) trước, rồi mới fallback từ promotion/product.
            - label = 'Tên KM (loại ... ; note ...)' nếu tìm thấy meta.
            """
            # 1) Tập promotion theo đơn
            prom_selected = self.order_line.mapped('promotion_selected_ids')
            prom_base     = self.order_line.mapped('promotion_ids')
            promos = (prom_selected or prom_base).exists()
            if not promos:
                return [], []   

            # 2) Duy trì thứ tự xuất hiện + meta map
            seen = set()
            ordered_promos = []
            for p in promos:
                if p.id not in seen:
                    seen.add(p.id); ordered_promos.append(p)

            def _s(x):
                try: return (str(x or '')).strip()
                except Exception: return ''

            meta_map = {
                p.id: {"name": _s(getattr(p, 'name', None) or getattr(p, 'display_name', None)),
                       "types": set(), "notes": set()}
                for p in ordered_promos
            }

            # helpers
            line_gift_fields = ['U_GiftType', 'gift_type', 'gift_level', 'gift_category',
                                'gift_kind', 'gift_type_code']
            prod_gift_fields = ['u_gift_type', 'gift_type', 'x_gift_type']
            line_note_fields = ['promotion_note', 'note', 'description']

            def _is_gift_line(ln):
                kind = (getattr(ln, 'U_isDiscount', None) or getattr(ln, 'line_kind', None) or '').upper()
                return bool(
                    getattr(ln, 'is_gift', False) or
                    kind == 'KM'
                )

            def _iter_line_promos(ln):
                sel = getattr(ln, 'promotion_selected_ids', None) or []
                base = getattr(ln, 'promotion_ids', None) or []
                res = []
                if sel: res += [p for p in sel if p]
                if base: res += [p for p in base if p]
                uniq, ss = [], set()
                for p in res:
                    if p.id not in ss:
                        uniq.append(p); ss.add(p.id)
                return uniq

            def _normalize_type(txt):
                t = _s(txt)
                if not t: return ''
                return (f"loại {t}" if t.isdigit() else t)

            # 3) Duyệt từng dòng: GOM meta THEO PROMO từ DÒNG QUÀ
            for ln in self.order_line:
                attached = _iter_line_promos(ln)
                if not attached:
                    continue

                # ghi chú trên line (dù quà hay không)
                for f in line_note_fields:
                    v = getattr(ln, f, None)
                    if v not in (None, '', False):
                        val = _s(v)
                        for p in attached:
                            meta_map[p.id]["notes"].add(val)

                if not _is_gift_line(ln):
                    continue  # chỉ xét "loại" trên dòng quà

                # lấy type từ LINE trước
                got_type = False
                for f in line_gift_fields:
                    v = getattr(ln, f, None)
                    if v not in (None, '', False):
                        val = _normalize_type(v)
                        if val:
                            for p in attached:
                                meta_map[p.id]["types"].add(val)
                            got_type = True

                # fallback: lấy type từ PRODUCT của dòng quà
                if not got_type and ln.product_id:
                    for f in prod_gift_fields:
                        v = getattr(ln.product_id, f, None)
                        if v not in (None, '', False):
                            val = _normalize_type(v)
                            if val:
                                for p in attached:
                                    meta_map[p.id]["types"].add(val)
                                break

            # 3b) Bổ sung meta từ chính promotion (fallback cuối)
            for p in ordered_promos:
                if not meta_map[p.id]["types"]:
                    pv = (getattr(p, 'gift_type', None) or getattr(p, 'reward_type', None)
                          or getattr(p, 'promo_type', None) or getattr(p, 'gift_category', None))
                    if pv:
                        meta_map[p.id]["types"].add(_normalize_type(pv))
                pnote = (getattr(p, 'note', None) or getattr(p, 'description', None)
                         or getattr(p, 'promotion_note', None))
                if pnote:
                    meta_map[p.id]["notes"].add(_s(pnote))

            # 4) Xuất ids + labels
            ids = [p.id for p in ordered_promos]
            labels = []
            for p in ordered_promos:
                name = meta_map[p.id]["name"] or f"KM #{p.id}"
                parts = []
                if meta_map[p.id]["types"]:
                    parts.append(", ".join(sorted(meta_map[p.id]["types"])))
                if meta_map[p.id]["notes"]:
                    parts.append("; ".join(sorted(meta_map[p.id]["notes"])))
                label = name
                if parts:
                    label += f" ({'; '.join([x for x in parts if x])})"
                labels.append(label)
            return ids, labels

        promo_ids, promo_labels = _collect_promotion_info()
        promo_text = ""
        if promo_ids:
            payload["U_Compaign"] = ",".join(str(i) for i in promo_ids)
            existing = (payload.get("U_NoteForWhs") or "").strip()
            promo_text = ", ".join(promo_labels)
            payload["U_NoteForWhs"] = (f"{existing}, {promo_text}" if existing else promo_text).strip(", ").strip()
        else:
            payload.setdefault("U_Compaign", "")
        
        payload["U_ExtCampaign"] = promo_text

        # helper nhỏ cho số tiền/giá
        def _to_number(x):
            try:
                return float(x or 0.0)
            except Exception:
                return 0.0
        warr_cache = {}
        def _kind_for_sap(l):
            # Ưu tiên có sẵn trên line, sau đó map từ line_kind/is_gift/is_bundle
            return (
                getattr(l, 'U_isDiscount', None)
                or getattr(l, 'line_kind', None)
                or ('KM' if getattr(l, 'is_gift', False) else
                    'BK' if getattr(l, 'is_bundle', False) else 'BT')
            )
        for l in self.order_line.filtered(lambda ln: not ln.display_type and ln.product_id and ln.product_uom_qty):
            # --- Ưu tiên default_code chụp trên dòng; fallback về product.default_code ---
            item_code = (
                (getattr(l, "default_code", None) or "")   # nếu bạn có field default_code trên line
                or (l.product_id.default_code or "")
            ).strip()
            if not item_code:
                # bỏ qua dòng không có mã hàng hợp lệ
                continue

            # Giá gốc & %discount
            orig_price = _to_number(l.price_unit)
            disc_pct   = _to_number(getattr(l, "discount", 0.0))
            disc_amt   = orig_price * disc_pct / 100.0
            price_after_disc = orig_price - disc_amt  # Giá đã giảm

            # --- Lấy bảo hành theo item_code từ bảng oms.product.item + cache ---
            if item_code in warr_cache:
                warr_time = warr_cache[item_code]
            else:
                warr_time = self._get_warr_time_by_code(item_code)
                warr_cache[item_code] = warr_time

            # Fallback nếu bảng item không có: lấy trên product hoặc ngay trên line
            if not warr_time:
                warr_time = int(getattr(l.product_id, "u_warr_time", 0) or getattr(l, "U_WarrTime", 0) or 0)

            line_dict = {
                "ItemCode": item_code,
                "Quantity": self._qty_str(l.product_uom_qty),
                "Price": round(price_after_disc, 6),             # Giá đã giảm
                "U_OrigiDiscPrcnt": round(disc_pct, 6),
                "U_OrigiPrice": round(orig_price, 6),
                "U_DiscAmt": round(disc_amt, 6),
                "U_isDiscount": getattr(l, "U_isDiscount", None) or "BT",
                "U_WarrTime": int(warr_time),                    # bảo hành theo item_code
                "WhsCode": (self._line_whs(l, filler_raw) or ""),
                "TaxCode": self._get_tax_code(l),
                "U_BusinessUnit": "AUT",
                "Project": prj,
                "U_isDiscount": _kind_for_sap(l),
            }

            # COGS chỉ gửi khi có dữ liệu
            for k in ("CogsOcrCod", "CogsOcrCo2", "CogsOcrCo3", "CogsOcrCo4", "CogsOcrCo5"):
                v = (getattr(l, k, None) or "")
                v = v.strip() if isinstance(v, str) else v
                if v:
                    line_dict[k] = v

            payload["Lines"].append(line_dict)

        if not payload["Lines"]:
            raise UserError(_("Đơn hàng không có dòng hợp lệ."))

        return payload


    # -----------------------------
    # CALL API + SAVE docnumber
    # -----------------------------
    # ====== One-shot & Lock & Idempotency ======
    so_create_attempted = fields.Boolean(default=False, tracking=True)
    so_create_attempted_at = fields.Datetime(tracking=True)
    so_create_result = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], tracking=True)

    so_create_lock_uuid = fields.Char(index=True)   # soft-lock chống re-entry (hết hạn sau ~30 phút)
    so_idem_key = fields.Char(index=True)           # idempotency key gửi qua API

    # -------------------------------------------------
    # Helper: sinh hoặc trả về Idempotency Key ổn định
    # -------------------------------------------------
    def _ensure_idem_key(self):
        self.ensure_one()
        if not self.so_idem_key:
            self.so_idem_key = f"odoo:{self._name}:{self.id}"
        return self.so_idem_key

    # -------------------------------------------------
    # Helper: đính kèm JSON làm attachment
    # -------------------------------------------------
    def _attach_json_text(self, name, text, mimetype="application/json"):
        self.ensure_one()
        if not isinstance(text, str):
            try:
                text = json.dumps(text, ensure_ascii=False, indent=2)
            except Exception:
                text = str(text)
        att = self.env["ir.attachment"].create({
            "name": name,
            "res_model": self._name,
            "res_id": self.id,
            "type": "binary",
            "datas": base64.b64encode(text.encode("utf-8")),
            "mimetype": mimetype,
        })
        return att.id

    # =====================================================================
    # ONLY-ONCE: Gọi CreateSO 1 lần – không tự retry, có lock & idempotency
    # =====================================================================
    def action_dat_create_so(self, username=None, password=None, preview=False):
        """
        Gọi API CreateSO cho bản ghi hiện tại + LOG đầy đủ:
          - Ghi 1 attachment payload JSON trước khi gọi
          - Ghi 1 attachment response sau khi gọi
          - Chống double: soft-lock + DB row lock + idempotency
        """
        self.ensure_one()
        ICP = self.env["ir.config_parameter"].sudo()
        api_base = (ICP.get_param("oms.api_base", "https://api-dat.datgroup.com.vn") or "").rstrip("/")
        url = f"{api_base}/OMS/CreateSO_DEV"

        # =========================
        # 0) CHỐNG DOUBLE (ONE-SHOT)
        # =========================
        # Chặn tất cả người dùng thường nếu đã từng bấm CreateSO.
        is_manager = (
            self.env.user.has_group('sales_team.group_sale_manager')
            or self.env.user.has_group('sale.group_sale_manager')
            or self.env.user.has_group('sales_team.group_sale_salesman_all_leads')
        )
        if self.so_create_attempted and not self.env.context.get('force_retry') and not is_manager:
            when = fields.Datetime.to_string(self.so_create_attempted_at) if self.so_create_attempted_at else "-"
            raise UserError(_("Đơn này đã thực hiện CreateSO trước đó vào %s. Không thể tạo lại.") % when)

        # Soft-lock (khoá mềm 30 phút): đặt khoá nếu trống hoặc khoá cũ đã hết hạn
        lock_uuid = str(uuid.uuid4())
        self.env.cr.execute(f"""
            UPDATE {self._table}
               SET so_create_lock_uuid = %s
             WHERE id = %s
               AND (
                     so_create_lock_uuid IS NULL
                  OR (so_create_attempted_at IS NOT NULL AND (NOW() - so_create_attempted_at) > INTERVAL '30 minutes')
               )
            RETURNING id
        """, (lock_uuid, self.id))
        if not self.env.cr.fetchone():
            # Có khoá đang active ⇒ đang được xử lý ở nơi khác
            raise UserError(_("Đơn đang được xử lý tạo (CreateSO in-progress). Vui lòng thử lại sau."))

        # Đặt cờ one-shot & thời điểm
        self.write({
            'so_create_attempted': True,
            'so_create_attempted_at': fields.Datetime.now(),
            'so_create_result': False,
        })

        # =========================
        # 0.1) HARD-LOCK DB (NOWAIT)
        # =========================
        try:
            # Khóa bản ghi; nếu có tiến trình khác đang xử lý sẽ ném lỗi ngay
            self.env.cr.execute(
                f"SELECT docnumber FROM {self._table} WHERE id=%s FOR UPDATE NOWAIT", (self.id,)
            )
        except Exception:
            # Mở khoá mềm để lần sau có thể thử lại
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("Đơn này đang được hệ thống khác xử lý. Vui lòng thử lại sau ít phút."))

        row = self.env.cr.fetchone()
        if row and row[0]:
            self.write({'so_create_lock_uuid': False})
            raise UserError(_("Đơn hàng này đã có số SAP (%s), không thể gửi lại.") % row[0])

        # ===== 1) Token =====
        try:
            token = self._get_token()
        except Exception as e:
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("Không lấy được token: %s") % e)

        # ===== 2) Payload + Idempotency =====
        try:
            payload = self._build_payload()
        except Exception as e:
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("Lỗi build payload: %s") % e)

        idem_key = self._ensure_idem_key()  # ổn định theo record
        payload['ClientRequestId'] = idem_key  # nếu backend đọc trong body

        # 2.1) Log payload
        try:
            payload_txt = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            payload_txt = str(payload)

        att_payload_id = self._attach_json_text(
            f"CreateSO-payload-{self.name or self.id}.json",
            payload_txt,
            mimetype="application/json"
        )
        self.message_post(
            body=("🛰️ <b>Gửi CreateSO</b><br/>"
                  f"<b>URL:</b> {url}<br/>"
                  "<b>Headers:</b> {'Authorization': 'Bearer ***', 'Content-Type': 'application/json', 'Idempotency-Key': '***'}<br/>"
                  "➡️ Đã đính kèm file <i>payload</i>."),
            attachment_ids=[att_payload_id],
            subtype_xmlid="mail.mt_note",
        )

        if preview:
            _logger.info("[DAT PREVIEW] %s", payload_txt)
            # mở khoá mềm để có thể gọi thực sau preview
            self.write({'so_create_lock_uuid': False})
            return payload

        # ===== 3) Call =====
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": idem_key,   # nếu backend hỗ trợ qua header
        }

        try:
            # Timeout hợp lý – không auto-retry
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
        except Exception as e:
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("Không gọi được CreateSO: %s") % e)

        ctype = (resp.headers.get("Content-Type") or "").lower()
        body = resp.json() if ctype.startswith("application/json") else resp.text

        # 3.1) Log response
        try:
            resp_txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        except Exception:
            resp_txt = str(body)

        self._attach_json_text(
            f"CreateSO-response-{self.name or self.id}.json" if ctype.startswith("application/json")
            else f"CreateSO-response-{self.name or self.id}.txt",
            resp_txt,
            mimetype="application/json" if ctype.startswith("application/json") else "text/plain",
        )

        # ===== 4) Xử lý kết quả =====
        if not resp.ok:
            _logger.warning("CreateSO FAILED %s: %s", resp.status_code, resp_txt[:1000])
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("CreateSO lỗi HTTP %s:\n%s") % (resp.status_code, resp_txt))

        if isinstance(body, dict) and str(body.get("status")).upper() == "FALSE":
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("CreateSO thất bại từ server: %s") % body.get("msg"))

        # ✅ Thành công: lấy docnumber
        def _pick_docnum(d):
            return (
                d.get("docnumber") or d.get("DocNumber")
                or (d.get("data") or {}).get("docnumber")
                or (d.get("Data") or {}).get("DocNumber")
            )

        docnum = None
        if isinstance(body, dict):
            docnum = _pick_docnum(body)
        elif isinstance(body, str):
            try:
                jb = json.loads(body)
                docnum = _pick_docnum(jb)
            except Exception:
                pass

        if docnum:
            # Re-check tránh race
            self.invalidate_recordset()
            if not getattr(self, "docnumber", False):
                self.write({"docnumber": docnum})
                self.message_post(
                    body=f"✅ CreateSO thành công: docnumber = {docnum}",
                    subtype_xmlid="mail.mt_note",
                )
            # clear lock + mark success
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'success'})
        else:
            # Không có docnumber ⇒ coi là fail
            self.write({'so_create_lock_uuid': False, 'so_create_result': 'failed'})
            raise UserError(_("CreateSO trả về không có 'docnumber'."))

        # --- WEBHOOK n8n (fire-and-forget, giữ nguyên như bạn, có bọc try/except) ---
        try:
            hook_url = (ICP.get_param("oms.webhook_n8n_create_so") or
                        "https://n8n.datgroup.com.vn/webhook/93f81e7c-19c3-46f5-ac37-f3a1f50195e7").strip().rstrip('/\\')

            so_dt = getattr(self, "confirmation_date", False)
            so_date = (fields.Date.to_string(so_dt.date()) if so_dt else
                       fields.Date.to_string(fields.Date.context_today(self)))

            customer_name = (self.partner_id and self.partner_id.display_name) or ""
            salesperson = ((getattr(self, "SlpCode", False) and self.SlpCode.name)
                           or (self.user_id and self.user_id.name) or "")
            store_code = self._store_from_user_branch()
            hook_payload = {
                "source": "odoo",
                "event": "CreateSO.success",
                "model": self._name,
                "id": self.id,
                "name": self.name,
                "docnumber": docnum,
                "so_date": so_date,
                "customer_name": customer_name,
                "salesperson": salesperson,
                "voucher_type_name": "Phiếu bán hàng",
                "note": (getattr(self, "dispatch_note", "") or "").strip(),
                "store": store_code,
            }

            method = (ICP.get_param("oms.webhook_n8n_method", "POST") or "POST").strip().upper()
            headers_base = {
                "User-Agent": f"odoo-18/{self.env.cr.dbname}",
                "Accept": "application/json",
            }

            req_text = json.dumps(hook_payload, ensure_ascii=False, indent=2)
            att_req_id = self._attach_json_text(
                f"n8n-webhook-request-{self.name or self.id}.json", req_text
            )
            self.message_post(
                body="📨 Payload webhook đã đính kèm.",
                attachment_ids=[att_req_id],
                subtype_xmlid="mail.mt_note",
            )

            if method == "GET":
                req = requests.Request('GET', hook_url, params=hook_payload, headers=headers_base).prepare()
                final_url = req.url
                r = requests.Session().send(req, timeout=5)
                sent_info = f"URL (GET): {final_url}"
            else:
                headers2 = {"Content-Type": "application/json; charset=utf-8", **headers_base}
                r = requests.post(hook_url, json=hook_payload, headers=headers2, timeout=5)
                sent_info = f"Body (POST JSON) đã gửi (xem attachment)."

            short_resp = (r.text or "")[:500]
            self.message_post(
                body=(f"📤 Webhook n8n → {hook_url} <b>[{method} {r.status_code}]</b><br/>"
                      f"{sent_info}<br/><pre>{short_resp}</pre>"),
                subtype_xmlid="mail.mt_note",
            )

        except Exception as ex:
            _logger.warning("Webhook n8n failed: %r", ex)
            self.message_post(
                body=f"⚠️ Webhook n8n lỗi: <code>{ex!r}</code>",
                subtype_xmlid="mail.mt_note",
            )

        _logger.info("[DAT SUCCESS] CreateSO -> %s", (resp_txt[:1000] if 'resp_txt' in locals() else 'OK'))
        return body
    show_credit_label = fields.Boolean(compute='_compute_show_credit_label', store=False)
    credit_notice = fields.Char(string="Công nợ (1 dòng)", readonly=True)
    credit_auto_pass = fields.Boolean(
        string="Công nợ OK (tự bỏ qua bước Kế toán)",
        readonly=True,
    )


    # --- Utils ---
    def _is_accountant_step(self):
        self.ensure_one()
        if not (self.workflow_id and self.current_sequence):
            return False
        step = self.workflow_id.step_ids.filtered(
            lambda s: int(s.sequence) == int(self.current_sequence)
        )[:1]
        return bool(
            step
            and getattr(step, 'approver_type', '') == 'role'
            and getattr(step, 'role_code', '') == 'accountant'
        )

    def _user_is_accountant(self):
        return self.env.user.has_group('sale_custom.group_accountant')

    @api.depends('workflow_id', 'current_sequence')
    def _compute_show_credit_label(self):
        has_group = self._user_is_accountant()
        for o in self:
            o.show_credit_label = bool(o._is_accountant_step() and has_group)

    # Bạn vẫn có thể giữ action thủ công nếu muốn gọi từ nơi khác
    def action_check_credit(self):
        self.ensure_one()
        if not (self._is_accountant_step() and self._user_is_accountant()):
            raise UserError(_("Chỉ kiểm tra ở bước Công nợ và bởi Kế toán."))
    
        msg, can_auto_pass = self._run_credit_check(post_to_chatter=True)
    
        if can_auto_pass:
            _logger.info("[CREDIT] API OK, tự động duyệt bước Công nợ cho đơn %s", self.name)
            return self.with_context(skip_credit_auto=True).action_approve_step()
    
        # Không auto-pass thì chỉ reload, để kế toán xem credit_notice
        return {'type': 'ir.actions.client', 'tag': 'reload'}



    def _to_jsonable(obj):
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, bytes):
            return base64.b64encode(obj).decode('ascii')
        if isinstance(obj, set):
            return [_to_jsonable(x) for x in obj]
        if isinstance(obj, (list, tuple)):
            return [_to_jsonable(x) for x in obj]
        if isinstance(obj, dict):
            return {str(k): _to_jsonable(v) for k, v in obj.items()}
        return str(obj)
    
    def _dumps_pretty(data):
        return json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2)
    
    def _run_credit_check(self, post_to_chatter=False):
        """
        Gọi API CheckLimitCreadit để kiểm tra công nợ.

        - Luôn cập nhật:
            + credit_notice: 1 dòng mô tả kết quả (hiện trên form)
            + credit_auto_pass: True nếu được phép auto bỏ qua bước Kế toán
        - Nếu post_to_chatter=True thì ghi thêm 1 log vào chatter.
        - Trả về: (msg, can_auto_pass)
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = (
            ICP.get_param('oms.credit_check_url')
            or globals().get('API_CREDIT_URL')
            or "https://api-dat.datgroup.com.vn/DATInsite/CheckLimitCreadit"
        )

        # ---- PAYLOAD (DocDate luôn là chuỗi YYYY-MM-DD) ----
        doc_date = fields.Date.context_today(self)
        payload = {
            "CardCode": (self.partner_id.ref or self.partner_id.display_name or "").strip(),
            "DocDate": fields.Date.to_string(doc_date),
            "Amount": float(self.amount_total or 0.0),
            "VoucherTypeID": "1310",
            "ContractCode": "-1",
        }

        msg = _("Không nhận được phản hồi.")
        resp = {}
        err_text = ""
        can_auto_pass = False

        try:
            token = self._get_token()
            resp = self._post_data_with_token(api_url, token, payload) or {}
            status_ok = str(resp.get("status")).upper() in ("TRUE", "1", "OK", "SUCCESS")
            if status_ok:
                result = resp.get("result")
                if isinstance(result, list):
                    it = (result or [{}])[0] or {}
                elif isinstance(result, dict):
                    it = result
                else:
                    it = {}

                err = it.get("error")
                err_msg = (it.get("error_msg") or "").strip()

                # Quy ước:
                #   - err is None       -> trong hạn mức, không cảnh báo  -> auto-pass
                #   - err >= 0          -> trong hạn mức                  -> auto-pass
                #   - err < 0           -> vượt hạn mức                  -> KHÔNG auto-pass
                if err is None:
                    msg = "🟢 Trong hạn mức (không cảnh báo)."
                    can_auto_pass = True
                else:
                    try:
                        err_int = int(err)
                    except Exception:
                        err_int = -999  # coi như lỗi, không auto-pass

                    if err_int < 0:
                        msg = f"🟠 {err_msg or 'Vượt hạn mức công nợ, liên hệ kế toán.'}"
                        can_auto_pass = False
                    else:
                        msg = f"🟢 {err_msg or 'Trong hạn mức công nợ.'}"
                        can_auto_pass = True
            else:
                msg = f"🔴 {resp.get('msg') or 'API trả về lỗi.'}"
                can_auto_pass = False

        except Exception as e:
            _logger.exception("Lỗi gọi CheckLimitCreadit")
            err_text = str(e)
            msg = f"🔴 Lỗi gọi API: {err_text}"
            can_auto_pass = False

        # 1) Ghi kết quả vào record (tránh loop write bằng context)
        vals = {
            "credit_notice": msg,
            "credit_auto_pass": can_auto_pass,
        }
        self.with_context(skip_credit_auto=True).write(vals)

        # 2) Ghi vào chatter nếu cần
        if post_to_chatter:
            try:
                self.message_post(
                    body=f"[Check công nợ] {msg}",
                    subtype_xmlid="mail.mt_note",
                )
            except Exception:
                _logger.exception("Không thể message_post kết quả check công nợ")

        # 3) Log ra file
        try:
            _logger.info("[CREDIT] Tóm tắt: %s", msg)
            if err_text:
                _logger.error("[CREDIT] Error:\n%s", err_text)
        except Exception:
            _logger.exception("Không thể ghi log CREDIT payload/response")

        # Trả về cả msg + flag để chỗ khác dùng
        return msg, can_auto_pass

    sap_completed_at = fields.Datetime(string="Ngày giờ hoàn thành (lần gần nhất)")

    sap_completed_at_hist = fields.Char(string="Lịch sử hoàn thành (CSV)")
    @api.model
    def cron_update_sap_status(self, limit=500):
        """
        Cron: Cập nhật trạng thái SAP cho các SO đã có docnumber
        - Bỏ qua nếu trạng thái hiện tại toàn 'Hoàn thành' hoặc 'Dừng xử lý'
        - Luôn GHI ĐÈ sap_status_name theo API
        - Khi trạng thái CHUYỂN SANG 'Hoàn thành' => ghi ngày giờ hoàn thành.
          Nếu có nhiều lần hoàn thành, nối lịch sử bằng dấu phẩy.
        """
        base_url = "https://api-dat.datgroup.com.vn/OMS/GetStatusSO"
        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        orders = self.search([("docnumber", "!=", False)], limit=limit)
        for o in orders:
            try:
                # 1) Bỏ qua nếu hiện tại chỉ toàn 'Hoàn thành' hoặc 'Dừng xử lý'
                current = (o.sap_status_name or "").strip()
                if current:
                    parts = [p.strip() for p in current.split(",") if p.strip()]
                    if parts and all(p in ("Hoàn thành", "Dừng xử lý") for p in parts):
                        continue

                # 2) Gọi API
                url = f"{base_url}?SO={o.docnumber}"
                resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                js = resp.json() or {}
                res = js.get("result") or {}

                new_status = (res.get("StatusName") or "").strip()
                if not new_status:
                    continue

                vals = {
                    "sap_u_sino":      res.get("U_SINo") or "",
                    "sap_opty_type":   res.get("OptyType") or "",
                    "sap_voucher_no":  res.get("VoucherNo") or "",
                    "sap_status_name": new_status,  # GHI ĐÈ
                    "sap_stt":         int(res.get("STT") or 0),
                }

                # 3) Nếu CHUYỂN SANG 'Hoàn thành' thì ghi ngày giờ
                was_completed = (current == "Hoàn thành")
                now_completed = (new_status == "Hoàn thành")
                if now_completed and not was_completed:
                    now_dt = fields.Datetime.now()  # lưu UTC chuẩn
                    # cập nhật lần gần nhất
                    vals["sap_completed_at"] = now_dt
                    # nối lịch sử CSV (YYYY-MM-DD HH:MM:SS)
                    stamp = fields.Datetime.to_string(now_dt)
                    hist_raw = (o.sap_completed_at_hist or "").strip()
                    vals["sap_completed_at_hist"] = (hist_raw + ", " + stamp).strip(", ")

                # 4) Chỉ ghi khi có thay đổi để giảm ghi DB/log
                changed = any((getattr(o, k) or "") != (v or "") for k, v in vals.items())
                if changed:
                    o.write(vals)
                    _logger.info(
                        "[OMS CRON] SO=%s cập nhật SAP: U_SINo=%s, OptyType=%s, STT=%s, Status=%s%s",
                        o.name,
                        vals["sap_u_sino"], vals["sap_opty_type"], vals["sap_stt"], vals["sap_status_name"],
                        f", CompletedAt={vals.get('sap_completed_at')}" if vals.get("sap_completed_at") else ""
                    )

            except Exception as e:
                _logger.error("[OMS CRON] Lỗi cập nhật SAP cho SO=%s: %s", o.name, e)

        return True

    def _run_cron_code(self, cron_xmlid: str):
        """Chạy dạn Python 'code' của ir.cron một cách an toàn cho user thường."""
        from odoo.tools.safe_eval import safe_eval
    
        # Lấy cron với sudo để không dính ACL trên field 'code'
        cron = self.env.ref(cron_xmlid, raise_if_not_found=False)
        if not cron:
            raise UserError(_("Không tìm thấy cron: %s") % cron_xmlid)
        cron = cron.sudo()
    
        # Chỉ hỗ trợ cron dạng 'code'
        if cron.state != "code":
            raise UserError(_("Cron %s không phải dạng 'code'.") % (cron.display_name or cron.id))
    
        code = (cron.code or "").strip()
        if not code:
            raise UserError(_("Cron %s không có code để chạy.") % (cron.display_name or cron.id))
    
        # Xác định model đích (nếu có)
        model_name = cron.model_id and cron.model_id.model or None
        if not model_name or model_name not in self.env:
            raise UserError(_("Model %s không tồn tại trong env.") % (model_name or "∅"))
    
        # Dùng user đã được cấu hình trên cron
        run_uid = cron.user_id and cron.user_id.id or self.env.user.id
        cron_env = self.env(user=run_uid)
    
        # Tôn trọng công ty của cron (nếu có)
        if getattr(cron, "company_id", False) and cron.company_id:
            ctx = dict(cron_env.context or {})
            ctx["allowed_company_ids"] = [cron.company_id.id]
            cron_env = cron_env(context=ctx)
    
        model_rs = cron_env[model_name]
    
        # local env cho safe_eval
        localdict = {
            "env": cron_env,
            "model": model_rs,
            # Có thể bổ sung: "UserError": UserError, "self": model_rs  (nếu code của bạn dùng)
        }
    
        # Chạy trong savepoint để an toàn rollback cục bộ
        try:
            with self.env.cr.savepoint():
                safe_eval(code, localdict, nocopy=True)
        except Exception as e:
            # Bọc lại để hiện thông điệp rõ ràng cho UI
            raise UserError(_("Lỗi khi chạy cron %s:\n%s") % ((cron.display_name or cron.id), str(e)))
    
    
    def action_sync_customers(self):
        """Đồng bộ: Khách hàng + Liên hệ + Địa chỉ"""
        for xmlid in (
            "sale_custom.ir_cron_sync_customer",
            "sale_custom.ir_cron_sync_contact",
            "sale_custom.ir_cron_sync_address",
        ):
            self._run_cron_code(xmlid)
    
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Đồng bộ khách hàng"),
                "message": _("Đã chạy xong: Khách hàng, Liên hệ, Địa chỉ."),
                "sticky": False,
            },
        }
    
    
    def action_sync_products(self):
        """Đồng bộ: Sản phẩm"""
        self._run_cron_code("sale_custom.ir_cron_sync_product_item")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Đồng bộ sản phẩm"),
                "message": _("Đã chạy xong cron đồng bộ sản phẩm."),
                "sticky": False,
            },
        }

    sepay_qr_url = fields.Char(compute='_compute_sepay_qr_url', store=False)

    # cấu hình dự phòng (có thể set ở Cài đặt hệ thống)
    def _cfg(self, key, default=''):
        return (self.env['ir.config_parameter'].sudo().get_param(key) or default).strip()


    def _vietin_acc_by_branch(self):
        """
        Trả STK Vietcombank theo chi nhánh:

          - HNI: 1051318386 – CÔNG TY CP TẬP ĐOÀN DAT – CHI NHÁNH HÀ NỘI
                  (có thể cấu hình bằng key: oms.vcb.hni)
          - Khác: 1036936868 – CÔNG TY CP TẬP ĐOÀN DAT – CN KỲ ĐỒNG – HCM
                  (có thể cấu hình bằng key: oms.vcb.ky_dong)
        """
        branch = ((self.user_id.branch or '') if self.user_id else '').upper()

        acc_hcm = self._cfg('oms.vcb.ky_dong', '1036936868')
        acc_hni = self._cfg('oms.vcb.hni', '1051318386')

        if branch == 'HNI':
            return (acc_hni or acc_hcm).strip()
        return (acc_hcm or '').strip()


    def _qr_desc(self):
        """
        Mô tả QR:
          - AUT: AUT + partner_ref + TT + order_id
          - ELE: MMYY + partner_ref + OMS + order_id + THANHTOAN
          - AUT có partner_ref, id=98765: AUTC12345TT98765
          - ELE có partner_ref, id=98765: 1125C12345OMS98765THANHTOAN
          - Nếu record chưa có id, dùng '0'
        """
        mmyy = fields.Date.context_today(self).strftime('%m%y')
        bu = re.sub(
            r'[^0-9A-Za-z]',
            '',
            str(
                getattr(self, 'BusinessArea', None)
                or getattr(self, 'business_area', None)
                or getattr(getattr(self, 'user_id', None), 'business_area', None)
                or 'AUT'
            )
        ).upper()
        if bu not in ('AUT', 'ELE'):
            bu = 'AUT'

        # partner_ref: giữ chữ/số, rỗng thì dùng 00000
        pref_raw = (self.partner_ref or '').strip()
        pref = re.sub(r'[^0-9A-Za-z]', '', pref_raw) or "00000"

        # order id: dùng self.id, chỉ lấy chữ số; nếu chưa có id thì '0'
        oid_raw = str(self.id or '0')
        oid = re.sub(r'\D', '', oid_raw) or '0'

        if bu == 'ELE':
            return f"{mmyy}{pref}OMS{oid}THANHTOAN"
        return f"AUT{pref}TT{oid}"


    def _qr_amount(self):
        # Ẩn số tiền nếu 'san_xuat'; ngược lại dùng amount_total (làm tròn)
        if self.report_type == 'san_xuat':
            return None
        try:
            amt = int(round(float(self.amount_total or 0)))
            return amt if amt > 0 else None
        except Exception:
            return None

    @api.depends('amount_total', 'report_type', 'user_id.branch', 'name', 'partner_ref')
    def _compute_sepay_qr_url(self):
        for o in self:
            acc = o._vietin_acc_by_branch() or ''
            if not acc:
                o.sepay_qr_url = False
                continue

            params = {
                'acc': acc,
                'bank': 'Vietcombank',
            }
            amount = o._qr_amount()
            if amount:
                params['amount'] = str(amount)

            desc = o._qr_desc().strip()
            if desc:
                params['des'] = desc

            o.sepay_qr_url = "https://qr.sepay.vn/img?" + urlencode(params, safe='')

    dispatch_state = fields.Selection([
        ('none', 'Chưa gửi'),
        ('requested', 'Đã yêu cầu (đã gọi webhook)'),
        ('ack', 'Điều vận đã tiếp nhận'),
        ('rejected', 'Điều vận từ chối'),
        ('done', 'Hoàn tất chuẩn bị'),
    ], default='none', tracking=True)

    dispatch_note = fields.Text(string="Ghi chú điều vận", tracking=True)
    dispatch_notified_at = fields.Datetime(string="Thời điểm gửi điều vận", tracking=True)
    dispatch_notified_by = fields.Many2one('res.users', string="Người gửi điều vận", tracking=True)
    dispatch_webhook_key = fields.Char(string="Dispatch Idempotency Key", index=True)

    # Helper: attach text/json vào chatter
    def _attach_text(self, name, text, mimetype="application/json"):
        self.ensure_one()
        if not isinstance(text, str):
            try:
                text = json.dumps(text, ensure_ascii=False, indent=2)
            except Exception:
                text = str(text)
        att = self.env["ir.attachment"].create({
            "name": name, "res_model": self._name, "res_id": self.id,
            "type": "binary", "datas": base64.b64encode(text.encode("utf-8")), "mimetype": mimetype,
        })
        return att.id

    # =============================
    # REPLACE: _build_dispatch_payload
    # =============================
    dispatch_send_count = fields.Integer(
        string="Số lần gửi kho",
        default=0,
        tracking=True,
        readonly=True,
    )
    def _build_dispatch_payload(self):
        self.ensure_one()
        header_wh_code = self._get_header_filler()
        wh_m2o = getattr(self, 'WhsCode', False)
        header_wh_name = ""
        if wh_m2o:
            header_wh_name = getattr(wh_m2o, 'display_name', '') or \
                             getattr(wh_m2o, 'store_name', '') or \
                             getattr(wh_m2o, 'name', '') or ""
    
        lines = []
        for l in self.order_line:
            if getattr(l, 'display_type', False):
                continue
            line_wh_code = self._line_whs(l, header_wh_code)
            lines.append({
                "product_id": l.product_id.id,
                "product_code": l.product_id.default_code or "",
                "product_name": l.product_id.display_name or "",
                "qty": float(l.product_uom_qty or 0.0),
                "uom": (l.product_uom and l.product_uom.name) or "",
                "warehouse_code": line_wh_code,
                "is_gift": bool(getattr(l, 'is_gift', False)),
                "is_bundle": bool(getattr(l, 'is_bundle', False)),
                "unit_price": self._line_price(l),
                "subtotal": float(getattr(l, "price_subtotal", 0.0) or 0.0),
            })
    
        partner = self.partner_id
        shipto = self.partner_shipping_id
    
        salesperson = ""
        if hasattr(self, "SlpCode") and getattr(self, "SlpCode"):
            salesperson = getattr(self.SlpCode, "name", "") or ""
        if not salesperson and getattr(self, "user_id", False):
            salesperson = getattr(self, "user_id", "name") and self.user_id.name or ""
    
        try:
            odt = self.date_order.date() if self.date_order else fields.Date.context_today(self)
        except Exception:
            odt = fields.Date.context_today(self)
        order_date = fields.Date.to_string(odt)
        try:
            store_code = self._store_from_user_branch()
        except Exception:
            store_code = None
        return {
            # ➕ ID đơn hàng
            "order_id": self.id,
    
            "name": self.name or "",
            "partner": partner.display_name or "",
            "partner_info": {
                "phone": partner.phone or "",
                "mobile": partner.mobile or "",
                "email": partner.email or "",
                "vat": partner.vat or "",
                "address": (shipto and shipto.contact_address) or "",
            },
            "salesperson": salesperson,
            "warehouse_code": header_wh_code,
            "warehouse_name": header_wh_name,
            "store_code": store_code, 
    
            # Ghi chú điều phối hiện có
            "note": (getattr(self, "dispatch_note", "") or "").strip(),
    
            # ➕ Ghi chú chứng từ (dùng Comments)
            "doc_note": (getattr(self, "Comments", "") or "").strip(),
    
            # ➕ Ghi chú nội bộ (dùng NoteInternal)
            "internal_note": (getattr(self, "NoteInternal", "") or "").strip(),
    
            "order_date": order_date,
            "lines": lines,
            "client_request_id": self.dispatch_webhook_key or f"dispatch:{self._name}:{self.id}",
        }
    
    def action_notify_dispatch(self):
        """Gọi webhook Điều vận: cho phép gửi nhiều lần; mỗi lần +1 và reload form."""
        ICP = self.env['ir.config_parameter'].sudo()
        url = (ICP.get_param('oms.dispatch_webhook_url') or '').strip()
        if not url:
            raise UserError(_("Chưa cấu hình URL webhook điều vận (oms.dispatch_webhook_url)."))

        method = (ICP.get_param("oms.dispatch_webhook_method", "POST") or "POST").strip().upper()
        token = (ICP.get_param("oms.dispatch_webhook_token") or "").strip()
        timeout_sec = int(ICP.get_param("oms.dispatch_webhook_timeout", "20") or 20)

        for order in self:
            # Không chặn gửi lại theo dispatch_state nữa

            # Base key để nhận diện đơn (ổn định qua các lần gửi)
            if not order.dispatch_webhook_key:
                order.dispatch_webhook_key = f"dispatch:{order._name}:{order.id}"

            # Số lần sắp gửi (ghi DB sau khi thành công)
            next_count = (order.dispatch_send_count or 0) + 1

            payload = order._build_dispatch_payload()
            payload["dispatch_send_count"] = next_count  # gửi kèm

            # Idempotency-Key mới MỖI LẦN để server không coi là trùng
            idem_key = f"{order.dispatch_webhook_key}:{next_count}:{uuid.uuid4().hex[:6]}"

            req_att = order._attach_text(
                f"DispatchWebhook-request-{order.name or order.id}.json",
                payload
            )
            order.message_post(
                body=f"🚚 Gửi yêu cầu điều vận qua webhook (lần {next_count}, đã đính kèm payload).",
                attachment_ids=[req_att],
                subtype_xmlid="mail.mt_note"
            )

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": f"odoo-18/{self.env.cr.dbname}",
                "Idempotency-Key": idem_key,
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            try:
                if method == "GET":
                    req = requests.Request('GET', url, params=payload, headers=headers).prepare()
                    resp = requests.Session().send(req, timeout=timeout_sec)
                else:
                    resp = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
            except Exception as ex:
                raise UserError(_("Không gọi được webhook điều vận: %s") % ex)

            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.text if not ctype.startswith("application/json") else (resp.json() if resp.text else {})
            try:
                resp_txt = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
            except Exception:
                resp_txt = str(body)

            res_att = order._attach_text(
                f"DispatchWebhook-response-{order.name or order.id}.json" if ctype.startswith("application/json")
                else f"DispatchWebhook-response-{order.name or order.id}.txt",
                resp_txt,
                mimetype="application/json" if ctype.startswith("application/json") else "text/plain",
            )

            if not resp.ok:
                order.message_post(
                    body=f"❌ Webhook điều vận lỗi HTTP {resp.status_code}. (Xem attachment)",
                    attachment_ids=[res_att],
                    subtype_xmlid="mail.mt_note",
                )
                raise UserError(_("Webhook điều vận trả lỗi HTTP %s") % resp.status_code)

            # Thành công: cập nhật & tăng đếm (không khóa trạng thái để còn gửi tiếp)
            order.write({
                'dispatch_state': 'requested',
                'dispatch_notified_at': fields.Datetime.now(),
                'dispatch_notified_by': order.env.user.id,
                'dispatch_send_count': next_count,
            })
            order.message_post(
                body=f"✅ Gọi webhook điều vận thành công (HTTP {resp.status_code}). Lần gửi: {next_count}.",
                attachment_ids=[res_att],
                subtype_xmlid="mail.mt_note",
            )

        # Reload form ngay
        return {"type": "ir.actions.client", "tag": "reload"}

    # Có dòng hoa hồng?
    has_commission_line = fields.Boolean(
        compute='_compute_has_commission_line', store=True)

    @api.depends('order_line.product_id', 'order_line.product_id.default_code',
                 'order_line.display_type', 'order_line.product_uom_qty')
    def _compute_has_commission_line(self):
        for o in self:
            o.has_commission_line = any(
                not l.display_type
                and (l.product_uom_qty or 0) > 0
                and ((l.product_id.default_code or '').strip().upper() in COMMISSION_SKUS)
                for l in o.order_line
            )

    @api.constrains('order_line', 'u_carcodecommission')
    def _check_receiver_for_commission(self):
        for o in self:
            if o.has_commission_line and not o.u_carcodecommission:
                raise ValidationError(_("Vui lòng chọn 'Người nhận HH' vì có dòng hoa hồng."))

    # nếu bạn đã có action_confirm rồi, chỉ cần chèn đoạn check này
    def action_confirm(self):
        # Extra validation for commission lines
        for o in self:
            if o.has_commission_line and not o.u_carcodecommission:
                raise UserError(_("Bạn phải chọn 'Người nhận HH' trước khi xác nhận."))

        # === Confirm logic (copy theo action_confirm chuẩn, không dùng super) ===
        self.order_line._validate_analytic_distribution()

        for order in self:
            if order.state not in ('draft', 'sent'):
                continue
            order._remove_delivery_line()
            #order._remove_shipping_fee_lines()
            order._remove_system_lines()

        self._check_order_line_delivery_status()
        self._check_order_line_delivery_used()
        self._check_order_line_shipping_fee_used()
        self._check_order_line_system_used()

        self._validate_taxes_on_sales_order()

        for order in self:
            if order.state in ('draft', 'sent'):
                order.validate_taxes_on_sales_order()
                order.validate_stock_status_on_sales_order()

        self._check_product_sale_ability()
        self._check_inventory()
        self._check_order_status()

        if self._get_forbidden_state_confirm() & set(self.mapped('state')):
            raise UserError(_('It is not possible to confirm an order in the following states: %s')
                            % (', '.join(self._get_forbidden_state_confirm())))

        for order in self:
            if order.partner_id not in order.message_partner_ids:
                order.message_subscribe([order.partner_id.id])

        self.write({'date_order': fields.Datetime.now()})
        self._action_confirm()

        # Website payments confirm orders server-side; tránh gửi lại mail confirm nếu từ website payment
        if not self.env.context.get('from_website_payment'):
            self._send_order_confirmation_mail()

        return True

    
    can_logistics_bypass_lock = fields.Boolean(
        compute='_compute_can_logistics_bypass_lock',
        store=False
    )

    def _is_logistics_user(self):
        return self.env.user.has_group('sale_custom.group_logistics')

    def _is_approved_or_confirmed(self):
        """Trả True nếu đơn đã được approve theo workflow hoặc đã xác nhận (sale/done)."""
        approved = False
        # nếu có field approval_state thì xét
        if 'approval_state' in self._fields:
            approved = self.approval_state in ('approved', 'done')
        # hoặc xét state chuẩn
        return approved or (self.state in ('sale', 'done'))

    @api.depends('state', 'approval_state', 'locked')
    def _compute_can_logistics_bypass_lock(self):
        is_log = self._is_logistics_user()
        for so in self:
            # Kho chỉ được bypass khi CHƯA approved/confirmed
            so.can_logistics_bypass_lock = bool(is_log and not so._is_approved_or_confirmed())

    def action_apply_eta_all(self):
        """Lấy ETA của dòng đầu tiên (có giá trị) và copy xuống tất cả line."""
        for order in self:
            lines = order.order_line.filtered(lambda l: not l.display_type)
            if not lines:
                continue
            # ưu tiên dòng đầu tiên có ETA; nếu chưa có thì lấy now()
            first_line = next((l for l in lines if l.eta_done), None)
            eta = first_line.eta_done if first_line and first_line.eta_done else fields.Datetime.now()
            lines.write({'eta_done': eta})
        return True

    def action_prepare_all(self):
        """Bật 'Sẵn sàng?' cho tất cả line (không phải dòng tiêu đề/ghi chú)."""
        for order in self:
            lines = order.order_line.filtered(lambda l: not l.display_type)
            if lines:
                lines.write({'is_prepared': True})
        return True

    # (tuỳ chọn) nút bỏ tích toàn bộ
    def action_unprepare_all(self):
        for order in self:
            lines = order.order_line.filtered(lambda l: not l.display_type)
            if lines:
                lines.write({'is_prepared': False})
        return True

    def action_open_crm_by_ref(self):
        self.ensure_one()
        ref = (self.partner_ref or '').strip()
        if not ref:
            raise UserError(_("Không có Mã khách hàng (partner_ref)."))
        return {
            'type': 'ir.actions.act_url',
            'url': f'https://portal.datgroup.com.vn/Account/Detail?ID={ref}',
            'target': 'new',  # mở tab mới
        }

    pay_accumulated = fields.Float(string="Tiền đã nhận (tích lũy)", default=0.0, tracking=True, copy=False)
    pay_last_amount = fields.Float(string="Lần nhận gần nhất", tracking=True, copy=False)
    pay_last_remark = fields.Char(string="Ghi chú thanh toán", tracking=True, copy=False)
    pay_updated_at = fields.Datetime(string="Cập nhật thanh toán lúc", tracking=True, copy=False)

    def apply_incoming_payment(self, amount: float, remark: str = ""):
        """Cập nhật các trường thanh toán và ghi log."""
        self.ensure_one()
        if amount is None:
            raise UserError(_("Thiếu số tiền."))
        try:
            amt = float(amount)
        except Exception:
            raise UserError(_("Số tiền không hợp lệ."))
        if amt <= 0:
            raise UserError(_("Số tiền phải > 0."))

        vals = {
            'pay_accumulated': (self.pay_accumulated or 0.0) + amt,
            'pay_last_amount': amt,
            'pay_last_remark': (remark or "").strip(),
            'pay_updated_at': fields.Datetime.now(),
        }
        self.write(vals)

        # Ghi log vào chatter
        msg = _("<b>Nhận thanh toán</b>: +%s<br/><b>Ghi chú</b>: %s") % (amt, (remark or "").strip())
        self.message_post(body=msg)

        return vals
    
    payment_status = fields.Selection(
        [('none', 'Chưa thanh toán'), ('partial', 'Thanh toán một phần'), ('done', 'Hoàn tất')],
        string="Thanh toán",
        compute='_compute_payment_status',
        store=False
    )

    @api.depends('pay_accumulated', 'amount_total', 'amount_paid')
    def _compute_payment_status(self):
        for r in self:
            paid = max(float(r.pay_accumulated or 0.0), float(r.amount_paid or 0.0))
            total = float(r.amount_total or 0.0)
            if total and r.currency_id.compare_amounts(paid, total) >= 0:
                r.payment_status = 'done'
            elif paid > 0.0:
                r.payment_status = 'partial'
            else:
                r.payment_status = 'none'


    cancel_request_state = fields.Selection(
        selection=[
            ('none', 'Không'),
            ('requested', 'Đã yêu cầu'),
            ('approved', 'Đã duyệt'),
            ('rejected', 'Từ chối'),
        ],
        string="Yêu cầu hủy",
        default='none',
        copy=False,
        tracking=True,
        readonly=True,
    )
    cancel_request_reason = fields.Text(string="Lý do hủy", copy=False)
    cancel_requested_by = fields.Many2one('res.users', string="Người yêu cầu", copy=False, readonly=True)
    cancel_requested_on = fields.Datetime(string="Ngày yêu cầu", copy=False, readonly=True)
    cancel_processed_by = fields.Many2one('res.users', string="Người xử lý", copy=False, readonly=True)
    cancel_processed_on = fields.Datetime(string="Ngày xử lý", copy=False, readonly=True)

    can_portal_request_cancel = fields.Boolean(
        compute="_compute_can_portal_request_cancel",
        string="Portal: có thể yêu cầu hủy",
    )

    @api.depends('state', 'locked', 'cancel_request_state')
    def _compute_can_portal_request_cancel(self):
        for o in self:
            o.can_portal_request_cancel = (
                not o.locked
                and (o.state not in ('cancel',))
                and (o.cancel_request_state in ('none', 'rejected'))
            )

    # ============================================================
    # Portal: Request cancel (KHÔNG hủy đơn)
    # ============================================================
    def action_portal_request_cancel(self, reason=None, requested_by=None):
        self.ensure_one()

        if self.locked:
            raise UserError(_("You cannot request cancel a locked order. Please unlock it first."))
        if self.state == 'cancel':
            return True
        if self.cancel_request_state == 'requested':
            return True

        user = self.env['res.users'].browse(int(requested_by)).exists() if requested_by else self.env.user
        now = fields.Datetime.now()

        # ghi nhận request (sudo để portal user không bị chặn quyền write)
        self.sudo().write({
            'cancel_request_state': 'requested',
            'cancel_request_reason': (reason or '').strip() or False,
            'cancel_requested_by': user.id if user else False,
            'cancel_requested_on': now,
            'cancel_processed_by': False,
            'cancel_processed_on': False,
        })

        author_id = user.partner_id.id if user and user.partner_id else False
        body = Markup(
            "<p><b>Yêu cầu hủy đơn</b></p>"
            "<ul>"
            f"<li>Người yêu cầu: {Markup.escape(user.name) if user else ''}</li>"
            f"<li>Lý do: {Markup.escape((reason or '').strip()) if (reason or '').strip() else '—'}</li>"
            "</ul>"
        )
        self.sudo().message_post(body=body, author_id=author_id, subtype_xmlid="mail.mt_note")

        # tạo activity cho Salesperson phụ trách (hoặc người tạo)
        target_user = self.user_id or self.create_uid
        if target_user:
            self.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=target_user.id,
                summary=_("Yêu cầu hủy đơn từ Portal"),
                note=_("Đơn %s có yêu cầu hủy. Vào đơn để duyệt/từ chối.") % (self.name,),
            )
        return True

    # ============================================================
    # Nội bộ: Approve / Reject (để bạn dùng ở backend sau)
    # ============================================================
    def action_approve_cancel_request(self):
        self.ensure_one()
        if self.cancel_request_state != 'requested':
            return True

        if self.locked:
            raise UserError(_("You cannot cancel a locked order. Please unlock it first."))

        self.sudo().write({
            'cancel_request_state': 'approved',
            'cancel_processed_by': self.env.user.id,
            'cancel_processed_on': fields.Datetime.now(),
        })
        # hủy thật (bỏ wizard để chạy “thẳng”)
        return self.with_context(disable_cancel_warning=True)._action_cancel()

    def action_reject_cancel_request(self, reason=None):
        self.ensure_one()
        if self.cancel_request_state != 'requested':
            return True
        self.sudo().write({
            'cancel_request_state': 'rejected',
            'cancel_processed_by': self.env.user.id,
            'cancel_processed_on': fields.Datetime.now(),
        })
        msg = _("Yêu cầu hủy bị từ chối.") + (f" ({reason})" if reason else "")
        self.sudo().message_post(body=msg, subtype_xmlid="mail.mt_note")
        return True

    website_voucher_id = fields.Many2one(
        "oms.promotion",
        string="Website Voucher",
        copy=False,
    )


    def website_clear_voucher(self):
        """Gỡ voucher + dọn gift/bundle sinh ra từ voucher."""
        self.ensure_one()
        if self.website_voucher_id:
            # dùng hàm bạn đã có để remove sạch + re-apply
            self.remove_applied_promotion(self.website_voucher_id.id)
        self.website_voucher_id = False

    def website_apply_voucher(self, promo):
        """Áp voucher vào order, KHÔNG làm mất 'tự động' (auto/tier)."""
        self.ensure_one()

        Promo = self.env['oms.promotion']
        has_apply_mode = 'apply_mode' in Promo._fields

        sale_lines = self.order_line.filtered(lambda l: l.product_id and not l.is_gift and not l.is_bundle)

        def _reprice_oms():
            # Ưu tiên engine OMS của bạn (đừng dùng _recompute_prices nếu bạn đang override giá theo OMS)
            for ln in sale_lines:
                if hasattr(ln, "_onchange_set_oms_price_and_promotion"):
                    ln._onchange_set_oms_price_and_promotion()

        def _sync_gift_combo():
            if hasattr(self, "action_sync_oms_gift_combo"):
                self.action_sync_oms_gift_combo()

        # 0) Không có voucher => clear + reprice + auto
        if not promo:
            self.website_clear_voucher()
            _reprice_oms()
            _sync_gift_combo()
            if hasattr(self, "_auto_apply_promotions"):
                self._auto_apply_promotions(for_website=True)
            if sale_lines:
                sale_lines.apply_promotions_to_line()
            _sync_gift_combo()
            if hasattr(self, "update_applied_promotions"):
                self.update_applied_promotions()
            if hasattr(self, "resequence_order_lines"):
                self.resequence_order_lines()
            return

        # 1) Validate voucher
        eligible = self.get_valid_vouchers_for_website()
        if promo.id not in eligible.ids:
            raise ValidationError(_("Voucher không hợp lệ cho giỏ hàng hiện tại."))

        old_voucher = getattr(self, "website_voucher_id", False)

        # 2) Gỡ voucher cũ khỏi line.promotion_ids
        if old_voucher and old_voucher.id != promo.id and sale_lines:
            for line in sale_lines:
                if old_voucher.id in line.promotion_ids.ids:
                    line.promotion_ids = [(3, old_voucher.id)]

        # 3) Nếu voucher không cho kết hợp:
        #    - KHÔNG clear sạch, mà chỉ clear MANUAL, giữ AUTO (để không mất "tự động")
        if not promo.can_be_combined and sale_lines:
            for line in sale_lines:
                if has_apply_mode:
                    keep_auto = line.promotion_ids.filtered(lambda p: p.apply_mode == 'auto')
                    line.promotion_ids = [Command.set(keep_auto.ids)]
                else:
                    # không có apply_mode thì không phân biệt được -> fallback clear hết
                    line.promotion_ids = [(5, 0, 0)]
            sale_lines.apply_promotions_to_line()

        # 4) Set voucher vào order
        self.website_voucher_id = promo

        # 5) Reprice lại theo OMS (tự động/tier)
        _reprice_oms()
        _sync_gift_combo()

        # 6) LUÔN chạy auto promotions để phục hồi "tự động"
        if hasattr(self, "_auto_apply_promotions"):
            self._auto_apply_promotions(for_website=True)

        # 7) Gắn voucher vào line đủ điều kiện
        apply_scope = getattr(promo, 'apply_scope', 'main_only') or 'main_only'
        for line in sale_lines:
            applies = False
            if not promo.apply_product_line_ids:
                applies = True
            else:
                tmpl_id = line.product_id.product_tmpl_id.id
                categ_id = line.product_id.categ_id.id
                for pl in promo.apply_product_line_ids:
                    if pl.product_tmpl_id and pl.product_tmpl_id.id == tmpl_id:
                        applies = True; break
                    if pl.product_category_id and pl.product_category_id.id == categ_id:
                        applies = True; break
                # Khi apply_scope = main_and_bundle: sp bán kèm cũng được gắn promo
                if not applies and apply_scope == 'main_and_bundle':
                    for combo in promo.bundle_combo_ids:
                        if line.product_id.product_tmpl_id.id in combo.product_tmpl_ids.ids:
                            applies = True; break

            if promo.gift_combo_ids:
                applies = True

            if applies and promo.id not in line.promotion_ids.ids:
                line.promotion_ids = [(4, promo.id)]

        # 8) Apply 1 lần (batch)
        if sale_lines:
            sale_lines.apply_promotions_to_line()
        _sync_gift_combo()

        if hasattr(self, "update_applied_promotions"):
            self.update_applied_promotions()
        if hasattr(self, "resequence_order_lines"):
            self.resequence_order_lines()


    def _promo_applies_to_line(self, promo, line):
        """Promo có áp dụng cho line này không.
        FIX: 2 promo auto-purchase (mua lần đầu/hai) sẽ áp cho mọi SP (trừ nhóm exclude 'Tấm pin'),
        không phụ thuộc apply_product_line_ids/qty range.
        """
        self.ensure_one()
        if not promo or not line or not line.product_id:
            return False

        # không áp cho gift/bundle
        if getattr(line, "is_gift", False) or getattr(line, "is_bundle", False):
            return False

        # ===== AUTO PURCHASE BYPASS =====
        try:
            first_promo, second_promo, cfg = self._get_auto_purchase_promos()
            auto_ids = set([p.id for p in (first_promo, second_promo) if p])
            if promo.id in auto_ids:
                # loại trừ nhóm "Tấm pin"
                if self._is_excluded_auto_purchase_product(line.product_id, cfg):
                    return False
                return True
        except Exception:
            pass

        # ===== DEFAULT RULE =====
        if not promo.apply_product_line_ids:
            return True

        qty = float(line.product_uom_qty or 0.0)
        tmpl_id = line.product_id.product_tmpl_id.id
        categ_id = line.product_id.categ_id.id

        for rule in promo.apply_product_line_ids:
            match_prod = bool(rule.product_tmpl_id and rule.product_tmpl_id.id == tmpl_id)
            match_categ = bool(rule.product_category_id and rule.product_category_id.id == categ_id)
            if (match_prod or match_categ) and (rule.qty_from or 0) <= qty <= (rule.qty_to or 999999999):
                return True

        # Khi apply_scope = main_and_bundle: sp bán kèm cũng được giảm giá
        if getattr(promo, 'apply_scope', 'main_only') == 'main_and_bundle':
            for combo in promo.bundle_combo_ids:
                if tmpl_id in combo.product_tmpl_ids.ids:
                    return True

        return False


    def _filter_combination(self, promos):
        """
        Lọc theo rule kết hợp (can_be_combined / restrict_combination / allowed_promotion_ids).
        Implement đơn giản & an toàn:
        - Nếu có promo không cho kết hợp (can_be_combined=False) => chỉ lấy 1 promo ưu tiên theo sequence.
        - Nếu promo restrict_combination=True => chỉ giữ các promo nằm trong allowed list của nó.
        """
        self.ensure_one()
        promos = promos.sorted(key=lambda p: (p.sequence or 0, p.valid_from or fields.Date.from_string("1970-01-01"), p.id))

        if not promos:
            return promos

        # Nếu có promo exclusive => chỉ lấy 1 cái ưu tiên
        exclusive = promos.filtered(lambda p: not p.can_be_combined)
        if exclusive:
            top = exclusive[0]
            return top

        # Nếu tất cả đều combine được: xử lý restrict list theo từng promo
        # (Nếu có restrict mà allowed list rỗng => coi như không hạn chế)
        result = promos
        restrictors = promos.filtered(lambda p: p.restrict_combination and p.allowed_promotion_ids)
        if restrictors:
            # giao của tất cả allowed lists + chính nó
            allowed_ids = set(result.ids)
            for p in restrictors:
                allowed_ids &= set(p.allowed_promotion_ids.ids + [p.id])
            result = result.filtered(lambda p: p.id in allowed_ids)

        return result

    # ---------------------------------------------------------------------
    # Main selector
    # ---------------------------------------------------------------------
    def get_valid_promotions(self, *, apply_mode=None, include_coupon=False, for_website=False):
        self.ensure_one()
        today = fields.Date.context_today(self)

        Promo = self.env['oms.promotion'].sudo()

        dom = [
            ('active', '=', True),
            '|', ('valid_from', '=', False), ('valid_from', '<=', today),
            '|', ('valid_to', '=', False), ('valid_to', '>=', today),
        ]

        if for_website:
            dom += ['|', ('channel', '=', False), ('channel', '=', 'online')]

        if not include_coupon and 'use_coupon' in Promo._fields:
            dom += [('use_coupon', '=', False)]

        if apply_mode and ('apply_mode' in Promo._fields):
            dom += [('apply_mode', '=', apply_mode)]

        promos = Promo.search(dom, order='sequence asc, valid_from desc, id desc')

        sale_lines = self.order_line.filtered(lambda l: l.product_id and not l.is_gift and not l.is_bundle)
        if not sale_lines:
            return Promo.browse([])

        tmpl_ids = set(sale_lines.mapped('product_id.product_tmpl_id').ids)
        categ_ids = set(sale_lines.mapped('product_id.categ_id').ids)

        eligible = Promo.browse([])
        for p in promos:
            if getattr(p, 'min_total_amount', 0.0) and (self.amount_total or 0.0) < (p.min_total_amount or 0.0):
                continue

            # Lọc theo phạm vi khách hàng (dùng customer_scope)
            customer_scope = getattr(p, 'customer_scope', 'all') or 'all'
            if customer_scope == 'specific':
                if getattr(p, 'partner_ids', False) and (self.partner_id.id not in p.partner_ids.ids):
                    continue
                if getattr(p, 'partner_category_ids', False) and not (set(self.partner_id.category_id.ids) & set(p.partner_category_ids.ids)):
                    continue

            apl = getattr(p, 'apply_product_line_ids', False)
            ok = False  # ít nhất 1 sp chính có trong đơn
            if apl:
                for pl in apl:
                    for line in sale_lines:
                        tmpl_id = line.product_id.product_tmpl_id.id
                        categ_id = line.product_id.categ_id.id
                        qty = float(line.product_uom_qty or 0.0)
                        qty_from = float(getattr(pl, 'qty_from', 1) or 1)
                        qty_to = float(getattr(pl, 'qty_to', 999999) or 999999)
                        if not (qty_from <= qty <= qty_to):
                            continue
                        if pl.product_tmpl_id and pl.product_tmpl_id.id == tmpl_id:
                            ok = True
                            break
                        if pl.product_category_id and pl.product_category_id.id == categ_id:
                            ok = True
                            break
                    if ok:
                        break
                if not ok and not getattr(p, 'bundle_combo_ids', False) and not getattr(p, 'gift_combo_ids', False):
                    continue

            if getattr(p, 'bundle_combo_ids', False):
                # Chỉ cần ít nhất 1 sp từ bất kỳ combo nào có trong đơn
                all_bundle_tmpl_ids = set()
                for combo in p.bundle_combo_ids:
                    all_bundle_tmpl_ids.update(combo.product_tmpl_ids.ids)
                has_bundle = bool(all_bundle_tmpl_ids & tmpl_ids)

                if apl:
                    # Có cả sp chính lẫn bán kèm: cần 1 sp chính AND 1 sp bán kèm
                    if not ok or not has_bundle:
                        continue
                else:
                    # Chỉ có bán kèm: cần ít nhất 1
                    if not has_bundle:
                        continue

            eligible |= p

        return eligible

    def _promo_can_stack(self, p1, p2):
        if not p1 or not p2 or p1.id == p2.id:
            return True
        if getattr(p1, 'can_be_combined', False) is False or getattr(p2, 'can_be_combined', False) is False:
            return False
        if getattr(p1, 'restrict_combination', False) and getattr(p1, 'allowed_promotion_ids', False):
            if p2.id not in p1.allowed_promotion_ids.ids:
                return False
        if getattr(p2, 'restrict_combination', False) and getattr(p2, 'allowed_promotion_ids', False):
            if p1.id not in p2.allowed_promotion_ids.ids:
                return False
        return True

    website_auto_purchase_promo_id = fields.Many2one(
        "oms.promotion", string="Website Auto Purchase Promo", copy=False, index=True
    )
    website_auto_purchase_stage = fields.Selection(
        [("first", "First"), ("second", "Second")],
        string="Website Auto Purchase Stage",
        copy=False,
        index=True,
    )
    # % mua lần đầu/lần hai (để idempotent, không cộng dồn)
    website_auto_purchase_pct = fields.Float(
        string="Website Auto Purchase Pct", copy=False, default=0.0
    )

    # ==========================================================
    # Config
    # ==========================================================
    def _auto_purchase_cfg(self):
        ICP = self.env["ir.config_parameter"].sudo()

        def _int(key, default):
            try:
                return int(ICP.get_param(key, default))
            except Exception:
                return default

        def _float(key, default):
            try:
                return float(ICP.get_param(key, default))
            except Exception:
                return default

        return {
            "first_id": _int("sale_custom.auto_purchase_first_promo_id", 0),
            "second_id": _int("sale_custom.auto_purchase_second_promo_id", 0),
            "second_days": _int("sale_custom.auto_purchase_second_days", 15),
            "reset_months": _int("sale_custom.auto_purchase_reset_months", 12),
            "exclude_group_name": (ICP.get_param("sale_custom.auto_purchase_exclude_group_name", "Tấm pin") or "").strip(),

            # % mặc định theo policy
            "first_pct": _float("sale_custom.auto_purchase_first_pct", 2.0),
            "second_pct": _float("sale_custom.auto_purchase_second_pct", 1.0),
        }
    def _get_auto_purchase_promos(self):
        cfg = self._auto_purchase_cfg()
        Promo = self.env["oms.promotion"].sudo()
        first_promo = Promo.browse(cfg["first_id"]).exists() if cfg["first_id"] else Promo
        second_promo = Promo.browse(cfg["second_id"]).exists() if cfg["second_id"] else Promo
        return first_promo, second_promo, cfg

    # ==========================================================
    # Exclude products (Tấm pin)
    # ==========================================================
    def _is_excluded_auto_purchase_product(self, product, cfg):
        if not product:
            return False
        ex = (cfg.get("exclude_group_name") or "").lower().strip()
        if not ex:
            return False

        vals = []
        for f in ("ItmsGrpNam", "itms_grp_nam"):
            if hasattr(product, f):
                vals.append(getattr(product, f))
        tmpl = getattr(product, "product_tmpl_id", False)
        if tmpl:
            for f in ("ItmsGrpNam", "itms_grp_nam"):
                if hasattr(tmpl, f):
                    vals.append(getattr(tmpl, f))

        return any(v and str(v).strip().lower() == ex for v in vals)

    # ==========================================================
    # Lines to apply purchase pct
    # NOTE: KHÔNG lọc linked_line_id để tránh “ăn dòng đầu”
    # ==========================================================
    def _uc_sale_lines_for_purchase(self):
        """Áp KM mua lần đầu/hai cho tất cả dòng SP thật (chỉ loại gift)."""
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: l.product_id and not l.display_type)
        if "is_gift" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_gift)
        # KHÔNG lọc is_bundle, KHÔNG lọc linked_line_id
        return lines

    # ==========================================================
    # Stage calc (count BOTH sale_custom.order + sale.order; draft có line cũng tính)
    # ==========================================================
    def _compute_auto_purchase_stage(self, partner, cfg):
        """Xác định stage 'first' / 'second' / False.
        - Không tính cart draft bình thường (chưa submit, chưa có payment transaction).
        - Vẫn tính draft nếu đã có transaction pending/authorized/done hoặc đã chốt/gửi duyệt.
        - Gộp sale_custom.order + sale.order, dedupe theo sale_order_id.
        """
        self.ensure_one()
        partner = (partner or self.env["res.partner"]).sudo().commercial_partner_id
        if not partner:
            return False
    
        today = fields.Date.context_today(self)
        second_days = max(int(cfg.get("second_days") or 15), 0)
        reset_months = max(int(cfg.get("reset_months") or 12), 0)
    
        cur_cd = self.create_date or fields.Datetime.now()
    
        # nếu self là sale_custom.order và có sale_order_id -> exclude sale.order tương ứng
        current_so_id = False
        if "sale_order_id" in self._fields and self.sale_order_id:
            current_so_id = self.sale_order_id.id
        elif self._name == "sale.order":
            current_so_id = self.id
    
        def _has_ok_tx(o):
            if "transaction_ids" not in o._fields:
                return False
            return bool(o.transaction_ids.filtered(lambda t: (t.state or "") in ("pending", "authorized", "done")))
    
        def _is_locked_like(o):
            if getattr(o, "locked", False):
                return True
            if getattr(o, "uc_website_finalized", False):
                return True
            ap = getattr(o, "approval_state", False)
            if ap and ap != "draft":
                return True
            return False
    
        def _qualify(o):
            st = (getattr(o, "state", "") or "").strip()
            if st == "cancel":
                return False
    
            # sale/done chắc chắn là đã phát sinh
            if st in ("sale", "done"):
                return True
    
            # draft chỉ tính nếu có transaction OK hoặc đã chốt/gửi duyệt
            if _has_ok_tx(o):
                return True
            if _is_locked_like(o):
                return True
    
            # fallback: nếu không draft (vd custom state) vẫn coi là phát sinh
            if st and st != "draft":
                return True
    
            return False
    
        def _odate(o):
            dt = getattr(o, "date_order", False) or getattr(o, "create_date", False)
            return fields.Date.to_date(dt) if dt else today
    
        def _fetch(model):
            M = self.env[model].sudo()
            dom = [
                ("partner_id", "child_of", partner.id),
                ("state", "!=", "cancel"),
                ("create_date", "<", cur_cd),
            ]
            # exclude current record theo model
            if self._name == model:
                dom.append(("id", "!=", self.id))
            # exclude sale.order mapping của self nếu có
            if model == "sale.order" and current_so_id:
                dom.append(("id", "!=", current_so_id))
            # same website
            if "website_id" in M._fields and getattr(self, "website_id", False):
                dom.append(("website_id", "=", self.website_id.id))
            return M.search(dom)
    
        prev_custom = _fetch("sale_custom.order")
        prev_sale = _fetch("sale.order")
    
        # Dedupe: nếu sale_custom.order có sale_order_id thì key theo sale.order id
        seen = set()
        prev = []
        for o in list(prev_custom) + list(prev_sale):
            # map về sale.order nếu có
            if o._name == "sale_custom.order" and "sale_order_id" in o._fields and o.sale_order_id:
                key = ("sale.order", o.sale_order_id.id)
            else:
                key = (o._name, o.id)
    
            if key in seen:
                continue
            seen.add(key)
    
            if _qualify(o):
                prev.append(o)
    
        # sort theo ngày phát sinh
        prev.sort(key=lambda o: (_odate(o), o.id))
    
        # không có lịch sử => first
        if not prev:
            return "first"
    
        # reset nếu gap > reset_months so với đơn gần nhất
        last_dt = _odate(prev[-1])
        if reset_months and today > (last_dt + relativedelta(months=reset_months)):
            return "first"
    
        # tìm cycle_start: nếu có khoảng gap > reset_months thì cycle reset
        cycle_start = prev[0]
        prev_dt = _odate(prev[0])
        for o in prev[1:]:
            dt = _odate(o)
            if reset_months and dt > (prev_dt + relativedelta(months=reset_months)):
                cycle_start = o
            prev_dt = dt
    
        cycle_start_dt = _odate(cycle_start)
        cycle_prev = [o for o in prev if _odate(o) >= cycle_start_dt]
    
        # cycle trống => first
        if not cycle_prev:
            return "first"
    
        # cycle có đúng 1 đơn trước đó => current có thể là second nếu trong second_days
        if len(cycle_prev) == 1:
            first_dt = _odate(cycle_prev[0])
            if second_days and today <= (first_dt + timedelta(days=second_days)):
                return "second"
            return False
    
        # >=2 đơn trong cycle => không áp
        return False

    # ==========================================================
    # Apply pct idempotent (không cộng dồn)
    # ==========================================================
    def _uc_purchase_lines(self):
        """Tất cả dòng SP thật để áp KM mua lần đầu/hai.
        FIX: KHÔNG loại is_bundle, KHÔNG loại linked_line_id (tránh chỉ ăn dòng đầu).
        Chỉ loại display_type và gift.
        """
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: l.product_id and not l.display_type)
        if "is_gift" in lines._fields:
            lines = lines.filtered(lambda l: not l.is_gift)
        # không filter is_bundle / linked_line_id
        return lines

    def _uc_log_lines(self, title=""):
        """Log trạng thái order_line để soi KM."""
        self.ensure_one()
        rows = []
        for l in self.order_line:
            if not l.product_id:
                continue
            rows.append({
                "line_id": l.id,
                "prod": l.product_id.display_name,
                "qty": float(getattr(l, "product_uom_qty", 0.0) or 0.0),
                "discount": float(getattr(l, "discount", 0.0) or 0.0),
                "is_gift": bool(getattr(l, "is_gift", False)),
                "is_bundle": bool(getattr(l, "is_bundle", False)),
                "linked_line_id": getattr(getattr(l, "linked_line_id", False), "id", False),
                "promo_ids": (l.promotion_ids.ids if "promotion_ids" in l._fields else []),
            })
        _logger.info(
            "[UC_LINES] %s order=%s stage=%s promo_id=%s pct=%s lines=%s",
            title,
            self.id,
            getattr(self, "website_auto_purchase_stage", False),
            getattr(getattr(self, "website_auto_purchase_promo_id", False), "id", False),
            getattr(self, "website_auto_purchase_pct", 0.0),
            rows
        )

    def _apply_purchase_pct_idempotent(self, pct_new, cfg):
        self.ensure_one()
        if "discount" not in self.order_line._fields:
            return

        pct_prev = float(self.website_auto_purchase_pct or 0.0)
        pct_new = float(pct_new or 0.0)

        def _clamp(x):
            try:
                x = float(x)
            except Exception:
                x = 0.0
            return 0.0 if x < 0.0 else (100.0 if x > 100.0 else x)

        pct_prev = _clamp(pct_prev)
        pct_new = _clamp(pct_new)

        _logger.info("[UC_PROMO] _apply_purchase_pct_idempotent order=%s pct_prev=%s pct_new=%s", self.id, pct_prev, pct_new)

        # set sớm
        self.sudo().write({"website_auto_purchase_pct": pct_new})

        for l in self._uc_purchase_lines():
            before = float(l.discount or 0.0)
            total = _clamp(before)

            promo_other = total
            if 0.0 < pct_prev < 100.0 and total >= (pct_prev - 1e-6):
                denom = 1.0 - pct_prev / 100.0
                if denom > 0:
                    promo_other = 100.0 * (1.0 - (1.0 - total / 100.0) / denom)

            promo_other = _clamp(promo_other)

            if self._is_excluded_auto_purchase_product(l.product_id, cfg) or pct_new <= 0.0:
                l.with_context(skip_pricelist_discount_compute=True).sudo().write({"discount": promo_other})
                _logger.info("[UC_PROMO] line=%s prod=%s excluded_or_zero before=%s after=%s",
                             l.id, l.product_id.display_name, before, promo_other)
                continue

            eff = 100.0 * (1.0 - (1.0 - promo_other / 100.0) * (1.0 - pct_new / 100.0))
            eff = _clamp(eff)
            l.with_context(skip_pricelist_discount_compute=True).sudo().write({"discount": eff})
            _logger.info("[UC_PROMO] line=%s prod=%s before=%s promo_other=%s after=%s",
                         l.id, l.product_id.display_name, before, promo_other, eff)

        try:
            if hasattr(self.order_line, "_compute_amount"):
                self.order_line._compute_amount()
        except Exception:
            pass

        self._uc_log_lines("AFTER _apply_purchase_pct_idempotent")


    def website_apply_auto_purchase_promo(self):
        for order in self:
            if order.env.context.get("skip_auto_purchase_promo"):
                continue
            if "website_id" in order._fields and not order.website_id:
                continue
            if getattr(order, "state", "draft") != "draft":
                continue

            first_promo, second_promo, cfg = order._get_auto_purchase_promos()
            stage = order._compute_auto_purchase_stage(order.partner_id, cfg)
            target_promo = first_promo if stage == "first" else (second_promo if stage == "second" else False)

            _logger.info("[UC_PROMO] website_apply_auto_purchase_promo order=%s stage=%s target_promo=%s",
                         order.id, stage, getattr(target_promo, "id", False))

            order._uc_log_lines("BEFORE website_apply_auto_purchase_promo")

            # line thật
            lines = order.order_line.filtered(lambda l: l.product_id and not getattr(l, "display_type", False))
            if "is_gift" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_gift)
            if "is_bundle" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_bundle)

            if not target_promo:
                order.sudo().write({"website_auto_purchase_promo_id": False, "website_auto_purchase_stage": False})
                order._apply_purchase_pct_idempotent(0.0, cfg)  # clear %
                order._uc_log_lines("AFTER website_apply_auto_purchase_promo (no target)")
                continue

            # ====== % policy ======
            pct = float(cfg.get("first_pct") or 2.0) if stage == "first" else float(cfg.get("second_pct") or 1.0)
            order._apply_purchase_pct_idempotent(pct, cfg)

            # ====== promotion_ids: ensure promo có trên all lines (để engine khác vẫn thấy) ======
            eligible = lines.filtered(lambda l: not order._is_excluded_auto_purchase_product(l.product_id, cfg))
            if "promotion_ids" in eligible._fields:
                missing = eligible.filtered(lambda l: target_promo.id not in l.promotion_ids.ids)
                _logger.info("[UC_PROMO] eligible=%s missing=%s target=%s", len(eligible), len(missing), target_promo.id)
                if missing:
                    missing.sudo().write({"promotion_ids": [(4, target_promo.id)]})
                    _logger.info("[UC_PROMO] added promo_id=%s to lines=%s", target_promo.id, missing.ids)

                try:
                    eligible.apply_promotions_to_line()
                except Exception:
                    _logger.exception("[UC_PROMO] apply_promotions_to_line failed after ensure promo")

            order.sudo().write({
                "website_auto_purchase_promo_id": target_promo.id,
                "website_auto_purchase_stage": stage,
            })

            order._uc_log_lines("AFTER website_apply_auto_purchase_promo")
        return True
    # ==========================================================
    # cart_update core for sale_custom.order (đảm bảo mỗi lần add/+/- đều tính lại)
    # ==========================================================
    def _cart_update(
        self,
        product_id,
        line_id=None,
        add_qty=None,
        set_qty=None,
        product_custom_attribute_values=None,
        no_variant_attribute_value_ids=None,
        **kwargs
    ):
        """
        Website cart update for sale_custom.order:
        - Update/Create/Unlink line
        - ALWAYS recompute: website pricelist -> price_unit -> apply promos -> auto purchase -> amounts
        """
        self.ensure_one()
        self = self.with_company(self.company_id)
        self._uc_force_web_pricelist_and_recompute()

        _logger.info("[UC_CART] _cart_update order=%s product_id=%s line_id=%s add_qty=%s set_qty=%s",
                     self.id, product_id, line_id, add_qty, set_qty)

        # --- dump lines BEFORE
        try:
            self._uc_log_lines("BEFORE _cart_update")
        except Exception:
            pass

        def _safe_float(v, default=0.0):
            try:
                if v is None:
                    return default
                return float(v)
            except Exception:
                return default

        def _get_request():
            try:
                from odoo.http import request as req
                return req
            except Exception:
                return None

        def _force_website_pricelist(order):
            req = _get_request()
            if not req or not getattr(req, "website", None):
                return

            # ✅ lấy pricelist CURRENT (theo session/geo/partner logic của website_sale)
            try:
                pl = req.website._get_current_pricelist()
            except Exception:
                pl = getattr(req.website, "pricelist_id", False)

            if not pl:
                return

            # log để bạn thấy “đang dùng pricelist nào”
            _logger.info(
                "[UC_PL] current_pricelist=%s(%s) order=%s old_pricelist=%s(%s)",
                pl.display_name, pl.id,
                order.id,
                getattr(getattr(order, "pricelist_id", False), "display_name", None),
                getattr(getattr(order, "pricelist_id", False), "id", None),
            )

            if getattr(req, "session", None) is not None:
                req.session["website_sale_current_pl"] = pl.id

            if getattr(order, "pricelist_id", False) and order.pricelist_id.id != pl.id:
                order.sudo().write({"pricelist_id": pl.id})

        def _sale_lines(order):
            lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
            if "is_gift" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_gift)
            # NOTE: bạn đang lọc linked_line_id ở đây -> OK cho promo engine hiện tại
            if "linked_line_id" in lines._fields:
                lines = lines.filtered(lambda l: not l.linked_line_id)
            return lines

        def _pricing_lines(order):
            lines = order.order_line.filtered(lambda l: l.product_id and not l.display_type)
            if "is_gift" in lines._fields:
                lines = lines.filtered(lambda l: not l.is_gift)
            return lines

        def _recompute_prices(order):
            lines = _pricing_lines(order)
            try:
                if hasattr(lines, "_compute_price_unit"):
                    lines._compute_price_unit()
                elif hasattr(order, "_recompute_prices"):
                    order.sudo()._recompute_prices()
            except Exception:
                _logger.exception("[UC_CART] recompute prices failed")

        def _apply_promos_engine(order):
            # 1) auto apply promotions (nếu có)
            try:
                if hasattr(order, "_auto_apply_promotions"):
                    try:
                        order.sudo()._auto_apply_promotions(for_website=True)
                    except TypeError:
                        order.sudo()._auto_apply_promotions()
            except Exception:
                _logger.exception("[UC_CART] _auto_apply_promotions failed")

            # 2) apply promotions to lines
            sale_lines = _sale_lines(order)
            try:
                if sale_lines and hasattr(sale_lines, "apply_promotions_to_line"):
                    sale_lines.apply_promotions_to_line()
                else:
                    for l in sale_lines:
                        if hasattr(l, "apply_promotions_to_line"):
                            l.apply_promotions_to_line()
            except Exception:
                _logger.exception("[UC_CART] apply_promotions_to_line failed")

            # 3) helper
            try:
                if hasattr(order, "update_applied_promotions"):
                    order.update_applied_promotions()
                if hasattr(order, "resequence_order_lines"):
                    order.resequence_order_lines()
            except Exception:
                _logger.exception("[UC_CART] update_applied_promotions/resequence_order_lines failed")

        def _compute_amounts(order):
            try:
                if hasattr(order, "_compute_amounts"):
                    order._compute_amounts()
                elif hasattr(order, "_amount_all"):
                    order._amount_all()
            except Exception:
                pass

        def _after_update_pipeline(order):
            if order.env.context.get("uc_skip_cart_pipeline"):
                return
            order = order.with_context(uc_skip_cart_pipeline=True)

            _force_website_pricelist(order)
            _recompute_prices(order)

            _apply_promos_engine(order)

            _force_website_pricelist(order)
            _recompute_prices(order)

            # Auto purchase chạy sau cùng
            try:
                order.website_apply_auto_purchase_promo()
            except Exception:
                _logger.exception("[UC_PROMO] website_apply_auto_purchase_promo failed")

            _compute_amounts(order)

        # ---- block nếu order không editable
        if getattr(self, "uc_website_finalized", False):
            raise UserError(_("Đơn hàng đã được chốt và gửi duyệt. Vui lòng tạo đơn mới."))
        if getattr(self, "locked", False):
            raise UserError(_("Đơn hàng đã được gửi duyệt/đang chờ xử lý nên không thể cập nhật giỏ hàng."))
        if "state" in self._fields and (self.state or "draft") != "draft":
            raise UserError(_("Đơn hàng không còn ở trạng thái giỏ (draft) nên không thể cập nhật."))

        approval_state = getattr(self, "approval_state", False)
        if approval_state and approval_state != "draft":
            raise UserError(_("Đơn hàng đã vào quy trình duyệt nên không thể cập nhật giỏ hàng."))

        # ---- product + line
        try:
            product_id_int = int(product_id)
        except Exception:
            product_id_int = 0

        product = self.env["product.product"].browse(product_id_int).exists()
        if not product:
            raise UserError(_("Product not found."))

        Line = self.env["sale_custom.order.line"]

        line = False
        if line_id:
            try:
                line_id_int = int(line_id)
            except Exception:
                line_id_int = 0
            if line_id_int:
                line = Line.browse(line_id_int).exists()
                if line and line.order_id.id != self.id:
                    line = False

        if not line:
            dom = [("order_id", "=", self.id), ("product_id", "=", product.id), ("display_type", "=", False)]
            if "linked_line_id" in Line._fields:
                dom.append(("linked_line_id", "=", False))
            if "is_gift" in Line._fields:
                dom.append(("is_gift", "=", False))
            line = Line.search(dom, limit=1)

        _logger.info("[UC_CART] resolved line=%s product=%s", getattr(line, "id", False), product.display_name)

        add_qty_f = _safe_float(add_qty, 0.0)
        if set_qty is None:
            cur = _safe_float(getattr(line, "product_uom_qty", 0.0) if line else 0.0, 0.0)
            new_qty = cur + add_qty_f
        else:
            new_qty = _safe_float(set_qty, 0.0)

        _logger.info("[UC_CART] qty calc: cur=%s add=%s set=%s => new_qty=%s",
                     (getattr(line, "product_uom_qty", 0.0) if line else 0.0),
                     add_qty_f, set_qty, new_qty)

        # ==========================================================
        # ✅ FIX DỨT ĐIỂM: vals_attr LUÔN ĐƯỢC KHAI BÁO
        # ==========================================================
        vals_attr = {}

        # Parse attributes
        try:
            if no_variant_attribute_value_ids is not None and "product_no_variant_attribute_value_ids" in Line._fields:
                nv_ids = []
                for x in (no_variant_attribute_value_ids or []):
                    if isinstance(x, int):
                        nv_ids.append(x)
                    elif str(x).isdigit():
                        nv_ids.append(int(x))
                vals_attr["product_no_variant_attribute_value_ids"] = [Command.set(nv_ids)]

            if product_custom_attribute_values is not None and "product_custom_attribute_value_ids" in Line._fields:
                cmds = [Command.clear()]
                if isinstance(product_custom_attribute_values, list):
                    for v in product_custom_attribute_values:
                        if not isinstance(v, dict):
                            continue
                        ptav_id = v.get("custom_product_template_attribute_value_id") or v.get("id") or v.get("value")
                        if isinstance(ptav_id, dict):
                            ptav_id = ptav_id.get("id")
                        ptav_id = int(ptav_id) if str(ptav_id).isdigit() else 0
                        custom_value = v.get("custom_value") or v.get("value") or ""
                        if ptav_id:
                            cmds.append(Command.create({
                                "custom_product_template_attribute_value_id": ptav_id,
                                "custom_value": custom_value,
                            }))
                vals_attr["product_custom_attribute_value_ids"] = cmds
        except Exception:
            _logger.exception("[UC_CART] parse vals_attr failed (ignore)")

        _logger.info("[UC_CART] vals_attr keys=%s", list(vals_attr.keys()))

        # remove / update / create
        if new_qty <= 0:
            lid = line.id if line else False
            if line:
                _logger.info("[UC_CART] unlink line=%s product=%s", line.id, line.product_id.display_name)
                line.unlink()
            _after_update_pipeline(self)
            try:
                self._uc_log_lines("AFTER _cart_update (unlink)")
            except Exception:
                pass
            return {"line_id": lid, "quantity": 0, "warning": ""}

        if line:
            _logger.info("[UC_CART] update line=%s qty=%s", line.id, new_qty)
            # ✅ vals_attr luôn tồn tại => không còn NameError
            line.write({"product_uom_qty": new_qty, **vals_attr})
            _after_update_pipeline(self)
            try:
                self._uc_log_lines("AFTER _cart_update (update)")
            except Exception:
                pass
            return {"line_id": line.id, "quantity": new_qty, "warning": ""}

        vals = {"order_id": self.id, "product_id": product.id, "product_uom_qty": new_qty}
        if "product_uom" in Line._fields:
            vals["product_uom"] = product.uom_id.id
        vals.update(vals_attr)

        line = Line.create(vals)
        _logger.info("[UC_CART] create line=%s product=%s qty=%s", line.id, line.product_id.display_name, new_qty)

        _after_update_pipeline(self)
        try:
            self._uc_log_lines("AFTER _cart_update (create)")
        except Exception:
            pass
        return {"line_id": line.id, "quantity": new_qty, "warning": ""}


    # ==========================================================
    # SAFE super call (tránh recursion / tránh AttributeError)
    # ==========================================================
    def _auto_apply_promotions(self, *args, **kwargs):
        """SAFE: không dùng super(SaleCustomOrder, self) để tránh NameError + recursion."""
        # gọi method ở parent theo MRO nếu có, nhưng không gọi lại chính mình
        try:
            for cls in type(self).mro()[1:]:
                m = cls.__dict__.get("_auto_apply_promotions")
                if m:
                    try:
                        return m(self, *args, **kwargs)
                    except TypeError:
                        return m(self)
        except Exception:
            pass
        return True
    # ----------------------------
    # Filter dropdown voucher: ẩn 2 KM auto khỏi voucher list
    # ----------------------------
    def get_valid_vouchers_for_website(self):
       """Trả danh sách KM/voucher hợp lệ để hiển thị / áp dụng trên website cart."""
       self.ensure_one()
       Promo = self.env["oms.promotion"].sudo()
    
       # ✅ Dùng selector chuẩn (đã lọc theo: date, partner, lines, channel online, ...)
       # - apply_mode='manual' để chỉ show cái user chọn tay (nếu field apply_mode tồn tại)
       # - include_coupon=True để show cả coupon/voucher + KM thường
       res = self.get_valid_promotions(apply_mode="manual", include_coupon=True, for_website=True)
    
       # (tuỳ DB) nếu có website_published thì giữ đúng behavior publish trên web
       if "website_published" in Promo._fields:
           res = res.filtered(lambda p: p.website_published)
    
       # ✅ loại 2 KM auto-purchase khỏi list dropdown
       try:
           first_promo, second_promo, _cfg = self._get_auto_purchase_promos()
           if first_promo:
               res -= first_promo
           if second_promo:
               res -= second_promo
       except Exception:
           pass
        
       return res

    
    
    @api.depends('order_line.product_uom_qty', 'order_line.display_type', 'order_line.is_gift', 'order_line.is_bundle')
    def _compute_cart_quantity(self):
        for order in self:
            qty = 0.0
            for line in order.order_line:
                if line.display_type:
                    continue
                if getattr(line, 'is_gift', False) or getattr(line, 'is_bundle', False):
                    continue
                if not line.product_id:
                    continue
                qty += float(line.product_uom_qty or 0.0)
            order.cart_quantity = int(qty)

    def _get_amount_total_excluding_delivery(self):
        # sale_custom.order.line của bạn _is_delivery() luôn False => cứ dùng amount_total
        self.ensure_one()
        return self.amount_total or 0.0

    def _is_cart_ready(self):
        self.ensure_one()
        return True

    def _cart_accessories(self):
        # Không gợi ý phụ kiện thì trả recordset rỗng cho safe
        return self.env['product.template'].browse([])

    uc_website_finalized = fields.Boolean(
        string="UC Website Finalized",
        default=False,
        copy=False,
        readonly=True,
        help="Đơn website đã được chốt (gửi duyệt / chờ duyệt). Không cho phép cart update nữa."
    )
    uc_website_finalized_at = fields.Datetime(string="UC Finalized At", copy=False, readonly=True)
    uc_website_finalize_reason = fields.Selection([
        ("paid", "Paid"),
        ("deposit", "Deposit"),
        ("debt_paynow", "Debt PayNow"),
        ("other", "Other"),
    ], string="UC Finalize Reason", copy=False, readonly=True)

    # ----------------------------
    # CART SESSION HELPERS
    # ----------------------------
    @api.model
    def uc_clear_website_cart_session(self):
        """Clear cart session để khách tạo đơn mới ngay.
        Safe: chỉ chạy khi có HTTP request.
        """
        if not request or not getattr(request, "session", None):
            return

        # ưu tiên reset theo core website_sale nếu có
        try:
            if getattr(request, "website", None) and hasattr(request.website, "sale_reset"):
                request.website.sale_reset()
        except Exception:
            pass

        # hard reset các key phổ biến
        for k in ("sale_order_id", "website_sale_order_id", "website_sale_cart_quantity"):
            request.session.pop(k, None)
        request.session["website_sale_cart_quantity"] = 0

    # ----------------------------
    # FINALIZE (SEND APPROVAL) HELPERS
    # ----------------------------
    def uc_website_finalize_send_approval(self, reason="other", payment_tx=None):
        """Chốt đơn website: lock + gọi 'gửi duyệt'.
        Idempotent: nếu đã finalized thì bỏ qua.
        """
        self.ensure_one()

        if self.uc_website_finalized:
            return True

        # 1) Lock trước để chặn mọi update tiếp theo (kể cả race condition)
        vals = {
            "uc_website_finalized": True,
            "uc_website_finalized_at": fields.Datetime.now(),
            "uc_website_finalize_reason": reason or "other",
        }
        self.sudo().write(vals)

        # 2) Gửi duyệt: ưu tiên method custom của bạn nếu có
        # Bạn đổi tên method theo code bạn đang có (ví dụ action_send_for_approval / action_submit_for_approval)
        send_m = (
            getattr(self.sudo(), "action_send_for_approval", None)
            or getattr(self.sudo(), "action_submit_for_approval", None)
            or getattr(self.sudo(), "action_send_to_approval", None)
        )

        if callable(send_m):
            # truyền context để log/debug nếu cần
            send_m = self.sudo().with_context(
                uc_from_website=True,
                uc_finalize_reason=reason,
                uc_payment_tx_id=payment_tx.id if payment_tx else False,
            )
            # gọi lại đúng method trên recordset có context
            (
                getattr(send_m, "action_send_for_approval", None)
                or getattr(send_m, "action_submit_for_approval", None)
                or getattr(send_m, "action_send_to_approval", None)
            )()
            return True

        # 3) Fallback (trường hợp chưa có workflow gửi duyệt)
        # Nếu bạn KHÔNG muốn confirm thì có thể bỏ fallback này.
        if hasattr(self.sudo(), "action_confirm"):
            self.sudo().action_confirm()
            return True
        else:
            raise UserError(_("Không tìm thấy hàm gửi duyệt trên Sale Order."))

    def uc_website_finalize_and_clear_cart(self, reason="other", payment_tx=None):
        """Gọi 1 phát: gửi duyệt + clear cart session."""
        self.ensure_one()
        self.uc_website_finalize_send_approval(reason=reason, payment_tx=payment_tx)
        self.uc_clear_website_cart_session()

   # ------------------------------------------------------------
    # Helpers: branch/store/warehouse mapping (đi theo SlpCode.branch)
    # ------------------------------------------------------------
    def _branch_from_user(self, user):
        return (getattr(user, "branch", "") or "").upper().strip()

    def _branch_code_from_branch(self, branch: str) -> str:
        b = (branch or "").upper().strip()
        if b.startswith("HCM"):
            return "HCM"
        if b.startswith("HNI") or b.startswith("HN"):
            return "HNI"
        if b.startswith("CTH"):
            return "CTH"
        return "CTH"

    def _warehouse_from_branch(self, branch: str):
        """Return record oms.warehouse (or empty) by branch."""
        code_map = {
            "HCM": "HCMVP201",
            "CTH": "CTHVP101",
            "HNI": "HNIVP101",
        }
        bcode = self._branch_code_from_branch(branch)
        whs_code = code_map.get(bcode)
        if not whs_code:
            return self.env["oms.warehouse"]
        return self.env["oms.warehouse"].sudo().search([("whs_code", "=", whs_code)], limit=1)

    def _store_fields_from_branch(self, branch: str) -> dict:
        """
        Store/InvStore là selection '1','2','3' theo file bạn đang dùng.
        BPLId chỉ có '1'(HCM) và '2'(HN) nên CTH map về '1' (giữ default hiện tại).
        """
        bcode = self._branch_code_from_branch(branch)
        if bcode == "HCM":
            return {"Store": "1", "InvStore": "1", "BPLId": "1"}
        if bcode == "HNI":
            return {"Store": "3", "InvStore": "3", "BPLId": "2"}
        # CTH
        return {"Store": "2", "InvStore": "2", "BPLId": "1"}

    def _get_oms_customer_from_partner(self, partner):
        """Map res.partner -> oms.customer theo CardCode (partner.ref)."""
        Customer = self.env["oms.customer"].sudo()
        partner = (partner or self.env["res.partner"]).sudo().commercial_partner_id
        if not partner:
            return Customer.browse()
    
        card_code = (partner.ref or "").strip()
        if not card_code and "card_code" in partner._fields:
            card_code = (partner.card_code or "").strip()
    
        if not card_code:
            return Customer.browse()
    
        return Customer.search([("card_code", "=", card_code)], limit=1)

    def _autofill_from_partner(self, partner, slp_user=None) -> dict:
        partner = (partner or self.env["res.partner"]).sudo().commercial_partner_id
        slp_user = (slp_user or self._slp_user_from_partner(partner)).sudo()
    
        out = {}
    
        # --- AUTO CONTACT (cntct_code): nếu chưa set thì pick theo CardCode ---
        card = (partner.ref or "").strip() if partner else ""
        if card:
            c = self._uc_pick_contact(card)
            if c:
                out["cntct_code"] = c.id
    
        # 1) SlpCode
        if slp_user:
            out["SlpCode"] = slp_user.id
            out["user_id"] = slp_user.id

        # 2) GroupNum theo oms.customer.group_num (logic bạn đang làm ở onchange cũ):contentReference[oaicite:19]{index=19}
        cust = self._get_oms_customer_from_partner(partner)  # bạn đã có func này:contentReference[oaicite:20]{index=20}
        if cust and getattr(cust, "group_num", False):
            group_term = self.env["oms.payment.terms"].sudo().search(
                [("group_num", "=", cust.group_num)],
                limit=1
            )
            if group_term:
                out["GroupNum"] = group_term.id

        # 3) Branch -> Store/InvStore/BPLId/WhsCode
        branch = self._branch_from_user(slp_user) or (self.env.user.branch or "").upper().strip()

        out.update(self._store_fields_from_branch(branch))

        whs = self._warehouse_from_branch(branch)
        if whs:
            out["WhsCode"] = whs.id

        return out

    # ------------------------------------------------------------
    # CREATE: đánh số SQ + auto-fill theo partner/slp (server-side)
    # ------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if self._is_logistics_user():
            raise AccessError(_("Kho/Điều vận không được tạo Sales Order."))

        today = fields.Date.context_today(self)
        yyMM = today.strftime("%y%m")

        new_vals_list = []
        for vals in vals_list:
            v = self._map_elevator_inputs(vals)  # giữ pipeline map elevator:contentReference[oaicite:21]{index=21}

            # --- auto-fill theo partner/slp (KHÔNG override nếu caller truyền sẵn) ---
            partner = None
            slp_u = None
            if v.get("partner_id"):
                partner = self.env["res.partner"].sudo().browse(v["partner_id"])
                if v.get("SlpCode"):
                    slp_u = self.env["res.users"].sudo().browse(v["SlpCode"])
                else:
                    slp_u = self._slp_user_from_partner(partner)
                    if slp_u:
                        v["SlpCode"] = slp_u.id

                auto = self._autofill_from_partner(partner, slp_user=slp_u)
                for k, val in auto.items():
                    v.setdefault(k, val)

                # --- AUTO CONTACT: nếu caller không truyền cntct_code thì tự pick theo CardCode ---
                if not v.get("cntct_code"):
                    card = self._uc_partner_ref_from_partner(partner)
                    c = self._uc_pick_contact(card)
                    if c:
                        v["cntct_code"] = c.id

            # --- đánh số SQ theo branch của slp_u (fallback env.user) ---
            branch = self._branch_from_user(slp_u) or (getattr(self.env.user, "branch", "") or "").upper().strip()
            branch_code = self._branch_code_from_branch(branch)

            if not v.get("name") or v.get("name") == _("New"):
                prefix = f"SQ-{branch_code}-{yyMM}/"
                company_id = v.get("company_id") or self.env.company.id
                seq_raw = self.env["ir.sequence"].with_company(company_id).next_by_code("sale_custom.order") or "1"

                import re
                m = re.search(r"(\d+)$", str(seq_raw))
                seq_digits = m.group(1) if m else str(seq_raw)
                v["name"] = f"{prefix}{str(seq_digits).zfill(4)}"

            new_vals_list.append(v)

        return super().create(new_vals_list)

    # ------------------------------------------------------------
    # WRITE: giữ lock pricelist + subscribe + auto-fill khi đổi partner/slp
    # ------------------------------------------------------------
    def write(self, vals):
        vals = self._map_elevator_inputs(vals)

        if "pricelist_id" in vals and any(so.state == "sale" for so in self):
            raise UserError(_("You cannot change the pricelist of a confirmed order !"))

        need_autofill = any(k in vals for k in ("partner_id", "SlpCode"))
        if need_autofill and len(self) > 1:
            for order in self:
                v = dict(vals)

                partner = order.partner_id
                if v.get("partner_id"):
                    partner = order.env["res.partner"].sudo().browse(v["partner_id"])

                    # --- AUTO CONTACT: đổi partner mà caller không set cntct_code -> tự pick ---
                    if "cntct_code" not in v:
                        card = order._uc_partner_ref_from_partner(partner)
                        c = order._uc_pick_contact(card)
                        v["cntct_code"] = c.id if c else False

                slp_u = None
                if v.get("SlpCode"):
                    slp_u = order.env["res.users"].sudo().browse(v["SlpCode"])
                else:
                    slp_u = order._slp_user_from_partner(partner)

                auto = order._autofill_from_partner(partner, slp_user=slp_u)
                for k, val in auto.items():
                    v.setdefault(k, val)

                super(SaleOrder, order).write(v)
            res = True
        else:
            if need_autofill:
                partner = self.partner_id
                if vals.get("partner_id"):
                    partner = self.env["res.partner"].sudo().browse(vals["partner_id"])

                    # --- AUTO CONTACT: đổi partner mà caller không set cntct_code -> tự pick ---
                    if "cntct_code" not in vals:
                        card = self._uc_partner_ref_from_partner(partner)
                        c = self._uc_pick_contact(card)
                        # copy để không mutate dict bên ngoài
                        vals = dict(vals)
                        vals["cntct_code"] = c.id if c else False

                slp_u = self.env["res.users"].sudo().browse(vals["SlpCode"]) if vals.get("SlpCode") else self._slp_user_from_partner(partner)
                auto = self._autofill_from_partner(partner, slp_user=slp_u)
                for k, v in auto.items():
                    vals.setdefault(k, v)

            res = super().write(vals)

        # Auto-subscribe partner khi đổi partner_id (giữ như code bạn đang có):contentReference[oaicite:22]{index=22}
        if vals.get("partner_id"):
            self.filtered(lambda so: so.state in ("sent", "sale")).message_subscribe(partner_ids=[vals["partner_id"]])

        return res    

    @api.model
    def _slp_user_from_partner(self, partner):
        """Trả res.users theo thứ tự ưu tiên:
           oms.customer.slp_code (theo partner.ref) -> partner.user_id -> env.user
           NOTE: dùng được trong create() (self có thể là recordset rỗng)
        """
        User = self.env["res.users"].sudo()

        # partner có thể là None / recordset rỗng
        partner = (partner or self.env["res.partner"]).sudo()
        if partner:
            partner = partner.commercial_partner_id

        # 1) ưu tiên theo partner.ref -> oms.customer.slp_code -> res.users.slp_code
        if partner and partner.ref:
            cust = self.env["oms.customer"].sudo().search(
                [("card_code", "=", partner.ref)], limit=1
            )
            if cust and getattr(cust, "slp_code", False):
                u = User.search(
                    [("slp_code", "=", cust.slp_code), ("active", "=", True)],
                    limit=1,
                )
                if u:
                    return u

        # 2) fallback theo partner.user_id
        if partner and getattr(partner, "user_id", False):
            return partner.user_id.sudo()

        # 3) fallback cuối
        return self.env.user.sudo()

    
