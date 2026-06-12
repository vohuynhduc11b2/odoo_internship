from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SaleCustomOrder(models.Model):
    _inherit = "sale_custom.order"

    x_is_outstation = fields.Boolean(string="Giao hàng ngoại tỉnh", tracking=True)
    x_outstation_transport_need = fields.Selection([
        ("lalamove", "Book Lalamove"),
        ("grab", "Book Grab"),
        ("chanh", "Gửi chành"),
        ("other", "Khác"),
    ], string="Nhu cầu vận chuyển", tracking=True)
    x_outstation_delivery_address = fields.Char(string="Địa chỉ nhận ngoại tỉnh", tracking=True)
    x_outstation_note = fields.Text(string="Ghi chú giao hàng ngoại tỉnh", tracking=True)
    x_delivery_rule_id = fields.Many2one("oms.solar.delivery.rule", string="Rule ngày giao dự kiến", readonly=True, copy=False)
    x_invoice_partner_id = fields.Many2one("res.partner", string="Đối tác xuất hóa đơn", compute="_compute_x_invoice_partner_id", store=False)

    @api.depends("partner_invoice_id")
    def _compute_x_invoice_partner_id(self):
        for order in self:
            order.x_invoice_partner_id = order.partner_invoice_id.commercial_partner_id

    @api.onchange("partner_invoice_id")
    def _onchange_partner_invoice_id_sync_cardcode2(self):
        for order in self:
            order._oms_sync_invoice_partner_fields()
            order._oms_apply_expected_delivery_rule()

    @api.onchange("partner_shipping_id", "trnsp_id", "x_is_outstation")
    def _onchange_oms_delivery_rule(self):
        for order in self:
            order._oms_apply_expected_delivery_rule()

    @api.constrains("x_is_outstation", "x_outstation_transport_need", "x_outstation_delivery_address")
    def _check_outstation_required_fields(self):
        for order in self:
            if order.x_is_outstation and (not order.x_outstation_transport_need or not order.x_outstation_delivery_address):
                raise ValidationError("Đơn giao hàng ngoại tỉnh bắt buộc có nhu cầu vận chuyển và địa chỉ nhận hàng.")

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._oms_post_sync()
        return orders

    def write(self, vals):
        res = super().write(vals)
        watched = {
            "partner_invoice_id",
            "partner_shipping_id",
            "trnsp_id",
            "x_is_outstation",
            "x_outstation_transport_need",
            "x_outstation_delivery_address",
            "locked",
        }
        if watched.intersection(vals.keys()):
            self._oms_post_sync()
        return res

    def _oms_post_sync(self):
        for order in self:
            order._oms_sync_invoice_partner_fields()
            order._oms_apply_expected_delivery_rule()
            order._oms_sync_lock_to_preparation()

    def _oms_sync_invoice_partner_fields(self):
        for order in self:
            invoice_partner = order.partner_invoice_id.commercial_partner_id if order.partner_invoice_id else False
            current = order.CardCode2.commercial_partner_id if order.CardCode2 else False
            if invoice_partner and invoice_partner != order.partner_id.commercial_partner_id and current != invoice_partner:
                order.CardCode2 = invoice_partner
            elif invoice_partner and invoice_partner == order.partner_id.commercial_partner_id and order.CardCode2:
                order.CardCode2 = False

    def _oms_get_billing_card_code(self):
        self.ensure_one()
        invoice_partner = self.partner_invoice_id.commercial_partner_id if self.partner_invoice_id else False
        if invoice_partner and invoice_partner.ref:
            return (invoice_partner.ref or "").strip()
        if self.CardCode2 and self.CardCode2.commercial_partner_id.ref:
            return (self.CardCode2.commercial_partner_id.ref or "").strip()
        if self.partner_ref:
            return (self.partner_ref or "").strip()
        return (self.partner_id.commercial_partner_id.ref or "").strip()

    def _get_pay_to_code(self):
        self.ensure_one()
        pay_to = getattr(self, "PayToCode", False)
        if pay_to:
            return (pay_to.address or "").strip()
        partner_ref = self._oms_get_billing_card_code()
        if not partner_ref:
            return ""
        addr = self.env["oms.address"].search(
            [("card_code", "=", partner_ref), ("adres_type", "=", "B")],
            order="id asc",
            limit=1,
        )
        return (addr.address or "").strip()

    def _get_address(self):
        self.ensure_one()
        pay_to = getattr(self, "PayToCode", False)
        if pay_to:
            return (pay_to.name or "").strip()
        partner_ref = self._oms_get_billing_card_code()
        if not partner_ref:
            return ""
        addr = self.env["oms.address"].search(
            [("card_code", "=", partner_ref), ("adres_type", "=", "B")],
            order="id asc",
            limit=1,
        )
        return (addr.name or "").strip()

    def _oms_apply_expected_delivery_rule(self):
        Rule = self.env["oms.solar.delivery.rule"].sudo()
        for order in self:
            rule = Rule.find_best_rule(order)
            order.x_delivery_rule_id = rule or False
            if rule:
                base_date = fields.Date.context_today(order)
                order.expected_delivery_date = base_date + relativedelta(days=rule.days_to_add)

    def _oms_sync_lock_to_preparation(self):
        Preparation = self.env["oms.warehouse.order.preparation"].sudo()
        if "x_locked_from_sale_order" not in Preparation._fields:
            return
        preps = Preparation.search([("order_id", "in", self.ids)])
        for prep in preps:
            prep.x_locked_from_sale_order = bool(prep.order_id.locked)

    def action_open_payment_incidents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Sự cố thanh toán",
            "res_model": "oms.payment.incident",
            "view_mode": "list,form",
            "domain": [("order_id", "=", self.id)],
        }


class OmsWarehouseOrderPreparation(models.Model):
    _inherit = "oms.warehouse.order.preparation"

    x_locked_from_sale_order = fields.Boolean(string="Giữ lock từ SO", readonly=True)
