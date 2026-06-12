from odoo import models, fields, api

class OmsTransport(models.Model):
    _name = "oms.transport"
    _description = "Phương thức vận chuyển"
    _order = "trnsp_code"
    _rec_name = "name"

    trnsp_code = fields.Integer(string="Mã vận chuyển", required=True, index=True)
    trnsp_name = fields.Char(string="Tên vận chuyển", required=True)

    # Hiển thị kết hợp code + name
    name = fields.Char(string="Tên hiển thị", compute="_compute_name", store=True)

    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("uniq_trnsp_code", "unique(trnsp_code)", "Mã vận chuyển phải là duy nhất!")
    ]

    @api.depends("trnsp_code", "trnsp_name")
    def _compute_name(self):
        for rec in self:
            code = rec.trnsp_code or ""
            label = rec.trnsp_name or ""
            rec.name = f"[{code}] {label}".strip()
