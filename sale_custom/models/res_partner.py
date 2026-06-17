# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.addons.base.models.res_partner import WARNING_MESSAGE, WARNING_HELP
from odoo.osv import expression


class ResPartner(models.Model):
    _inherit = "res.partner"

    sale_order_count = fields.Integer(
        string="Sale Order Count",
        groups="sales_team.group_sale_salesman",
        compute="_compute_sale_order_count",
    )
    sale_order_ids = fields.One2many("sale_custom.order", "partner_id", "Sales Order")
    sale_warn = fields.Selection(WARNING_MESSAGE, "Sales Warnings", default="no-message", help=WARNING_HELP)
    sale_warn_msg = fields.Text("Message for Sales Order")

    loyalty_card_count = fields.Integer(string="Loyalty Cards", compute="_compute_loyalty_card_count")

    # ==========================
    # Bảng giá áp dụng cho khách hàng
    # ==========================
    customer_pricelist_ids = fields.One2many(
        'oms.customer.pricelist',
        'partner_id',
        string='Bảng giá áp dụng',
        help='Danh sách bảng giá được gán cho khách hàng này.',
    )
    
    customer_pricelist_count = fields.Integer(
        string='Số bảng giá',
        compute='_compute_customer_pricelist_count',
        store=False,
        help='Số bảng giá đang áp dụng cho khách hàng này.',
    )

    # ==========================
    # CTKM 2% / 1% (AUTO POLICY)
    # ==========================
    uc_policy_2p1p_active = fields.Boolean(string="Áp dụng policy 2%/1%", default=False)
    uc_policy_2p1p_start_date = fields.Date(string="Ngày bắt đầu policy")

    x_oms_card_code = fields.Char(string="OMS CardCode", index=True, copy=False)
    x_oms_cntct_code = fields.Integer(string="OMS CntctCode", index=True, copy=False)
    x_oms_address_code = fields.Char(string="OMS Address Code", index=True, copy=False)
    x_oms_ship_to_def = fields.Char(string="OMS ShipToDef", index=True, copy=False)

    oms_is_strategic_customer = fields.Boolean(
        string="Khách hàng chiến lược",
        help="Bật để website dùng bảng giá công nợ khi xem hàng và đổi sang bảng giá trả trước khi khách chọn thanh toán trước.",
    )
    oms_debt_pricelist_id = fields.Many2one(
        "product.pricelist",
        string="Bảng giá công nợ",
        help="Bảng giá dùng để hiển thị/mặc định cho khách chiến lược khi chưa chọn thanh toán trước.",
    )
    oms_prepaid_pricelist_id = fields.Many2one(
        "product.pricelist",
        string="Bảng giá trả trước",
        help="Bảng giá dùng để tính lại đơn khi khách chiến lược chọn thanh toán toàn bộ, cọc hoặc UNC.",
    )

    def _oms_get_strategic_pricing_partner(self):
        self.ensure_one()
        candidates = self
        if self.commercial_partner_id and self.commercial_partner_id not in candidates:
            candidates |= self.commercial_partner_id
        group_root = getattr(self, "oms_effective_group_root_id", False)
        if group_root and group_root not in candidates:
            candidates |= group_root
        group_parent = getattr(self, "oms_group_parent_id", False)
        if group_parent and group_parent not in candidates:
            candidates |= group_parent

        return candidates.filtered(
            lambda p: p.oms_is_strategic_customer
            or p.oms_debt_pricelist_id
            or p.oms_prepaid_pricelist_id
        )[:1]

    def _compute_loyalty_card_count(self):
        for r in self:
            r.loyalty_card_count = 0

    def _compute_customer_pricelist_count(self):
        """Đếm số bảng giá đang áp dụng cho khách hàng."""
        for partner in self:
            count = self.env['oms.customer.pricelist'].search_count([
                ('partner_id', '=', partner.id),
                ('state', '=', 'active'),
                ('active', '=', True),
            ])
            partner.customer_pricelist_count = count

    def action_view_pricelists(self):
        """Xem danh sách bảng giá áp dụng cho khách hàng."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('sale_custom.action_oms_customer_pricelist')
        action['domain'] = [('partner_id', '=', self.id)]
        action['context'] = {'default_partner_id': self.id}
        return action

    @api.model
    def _get_sale_order_domain_count(self):
        return []

    def _compute_sale_order_count(self):
        self.sale_order_count = 0
        if not self.env.user._has_group("sales_team.group_sale_salesman"):
            return

        all_partners = self.with_context(active_test=False).search_fetch(
            [("id", "child_of", self.ids)],
            ["parent_id"],
        )
        sale_order_groups = self.env["sale_custom.order"]._read_group(
            domain=expression.AND([self._get_sale_order_domain_count(), [("partner_id", "in", all_partners.ids)]]),
            groupby=["partner_id"],
            aggregates=["__count"],
        )
        self_ids = set(self._ids)

        for partner, count in sale_order_groups:
            while partner:
                if partner.id in self_ids:
                    partner.sale_order_count += count
                partner = partner.parent_id

    def _has_order(self, partner_domain):
        self.ensure_one()
        sale_order = self.env["sale_custom.order"].sudo().search(
            expression.AND([
                partner_domain,
                [("state", "in", ("sent", "sale"))],
            ]),
            limit=1,
        )
        return bool(sale_order)

    def _can_edit_name(self):
        return super()._can_edit_name() and not self._has_order(
            [
                ("partner_invoice_id", "=", self.id),
                ("partner_id", "=", self.id),
            ]
        )

    def can_edit_vat(self):
        return super().can_edit_vat() and not self._has_order(
            [("partner_id", "child_of", self.commercial_partner_id.id)]
        )

    def action_view_sale_order(self):
        action = self.env["ir.actions.act_window"]._for_xml_id("sale_custom.act_res_partner_2_sale_order")
        all_child = self.with_context(active_test=False).search([("id", "child_of", self.ids)])
        action["domain"] = [("partner_id", "in", all_child.ids)]
        return action

    def _compute_credit_to_invoice(self):
        super()._compute_credit_to_invoice()
        company = self.env.company
        if not company.account_use_credit_limit:
            return

        sale_orders = self.env["sale_custom.order"].search([
            ("company_id", "=", company.id),
            ("partner_invoice_id", "any", [("commercial_partner_id", "in", self.commercial_partner_id.ids)]),
            ("order_line", "any", [("untaxed_amount_to_invoice", ">", 0)]),
            ("state", "=", "sale"),
        ])
        for (partner, currency), orders in sale_orders.grouped(lambda so: (so.partner_invoice_id, so.currency_id)).items():
            amount_to_invoice_sum = sum(orders.mapped("amount_to_invoice"))
            credit_company_currency = currency._convert(
                amount_to_invoice_sum,
                company.currency_id,
                company,
                fields.Date.context_today(self),
            )
            partner.commercial_partner_id.credit_to_invoice += credit_company_currency

    def unlink(self):
        self.env["sale_custom.order"].sudo().search([
            ("state", "in", ["draft", "cancel"]),
            "|", "|",
            ("partner_id", "in", self.ids),
            ("partner_invoice_id", "in", self.ids),
            ("partner_shipping_id", "in", self.ids),
        ]).unlink()
        return super().unlink()

    def name_get(self):
        # FIX: không return super() trong vòng for
        if not self.env.context.get("show_ref"):
            return super().name_get()

        res = []
        for partner in self:
            name = partner.name or ""
            ref = partner.ref or ""
            res.append((partner.id, f"[{ref}] {name}" if ref else name))
        return res
