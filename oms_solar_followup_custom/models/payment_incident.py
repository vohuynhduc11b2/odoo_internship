from odoo import api, fields, models


class OmsPaymentIncident(models.Model):
    _name = "oms.payment.incident"
    _description = "OMS Payment Incident"
    _order = "create_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    type = fields.Selection([
        ("warning", "Cảnh báo"),
        ("incident", "Sự cố"),
    ], required=True, default="warning", tracking=True)
    state = fields.Selection([
        ("open", "Mới"),
        ("doing", "Đang xử lý"),
        ("done", "Đã xử lý"),
        ("cancel", "Bỏ qua"),
    ], default="open", tracking=True)
    severity = fields.Selection([
        ("low", "Thấp"),
        ("medium", "Trung bình"),
        ("high", "Cao"),
        ("critical", "Nghiêm trọng"),
    ], default="medium", tracking=True)
    payment_transaction_id = fields.Many2one("payment.transaction", string="Giao dịch thanh toán", index=True)
    order_id = fields.Many2one("sale_custom.order", string="Đơn OMS", index=True)
    partner_id = fields.Many2one("res.partner", string="Khách hàng", index=True)
    provider_id = fields.Many2one("payment.provider", string="Cổng thanh toán")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    payment_state = fields.Char(string="Trạng thái thanh toán")
    root_cause = fields.Text(string="Nguyên nhân")
    prevention = fields.Text(string="Cách phòng tránh")
    resolution = fields.Text(string="Hướng xử lý")
    resolution_result = fields.Text(string="Kết quả xử lý")
    detected_on = fields.Datetime(default=fields.Datetime.now, string="Thời điểm ghi nhận")
    q1_2026 = fields.Boolean(string="Q1/2026", compute="_compute_q1_2026", store=True)
    unique_key = fields.Char(index=True, copy=False)

    _sql_constraints = [
        ("oms_payment_incident_unique_key", "unique(unique_key)", "Sự cố thanh toán đã được ghi nhận trước đó."),
    ]

    @api.depends("detected_on")
    def _compute_q1_2026(self):
        for rec in self:
            value = rec.detected_on
            rec.q1_2026 = bool(value and value.year == 2026 and value.month in (1, 2, 3))

    @api.model
    def _build_unique_key(self, tx, suffix):
        tx_id = tx.id if tx else 0
        return f"tx:{tx_id}:{suffix}"

    @api.model
    def create_from_transaction(self, tx, *, suffix, name, type="warning", severity="medium", root_cause="", prevention="", resolution=""):
        unique_key = self._build_unique_key(tx, suffix)
        existing = self.search([("unique_key", "=", unique_key)], limit=1)
        if existing:
            return existing
        order = tx.sale_order_ids[:1] if hasattr(tx, "sale_order_ids") else self.env["sale_custom.order"]
        partner = tx.partner_id or (order.partner_id if order else False)
        return self.create({
            "name": name,
            "type": type,
            "severity": severity,
            "payment_transaction_id": tx.id,
            "order_id": order.id if order else False,
            "partner_id": partner.id if partner else False,
            "provider_id": tx.provider_id.id if tx.provider_id else False,
            "payment_state": tx.state,
            "root_cause": root_cause,
            "prevention": prevention,
            "resolution": resolution,
            "unique_key": unique_key,
        })
