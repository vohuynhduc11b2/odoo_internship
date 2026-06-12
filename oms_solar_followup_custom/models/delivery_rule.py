from odoo import api, fields, models


class OmsSolarDeliveryRule(models.Model):
    _name = "oms.solar.delivery.rule"
    _description = "OMS Solar Delivery Leadtime Rule"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    state_id = fields.Many2one("res.country.state", string="Tỉnh/Thành")
    city_name = fields.Char(string="Thành phố/Quận/Huyện")
    transport_name = fields.Char(string="Điều kiện phương thức VC")
    outstation_only = fields.Boolean(string="Chỉ áp dụng giao ngoại tỉnh")
    days_to_add = fields.Integer(string="Số ngày cộng", required=True, default=1)
    note = fields.Char(string="Ghi chú")

    def _match_order(self, order):
        self.ensure_one()
        shipping = order.partner_shipping_id
        state_name = (shipping.state_id.name or "").strip().lower()
        city = (shipping.city or "").strip().lower()
        transport_name = (getattr(order.trnsp_id, "name", "") or "").strip().lower()

        if self.outstation_only and not getattr(order, "x_is_outstation", False):
            return False
        if self.state_id and self.state_id != shipping.state_id:
            return False
        if self.city_name and self.city_name.strip().lower() not in city and self.city_name.strip().lower() not in state_name:
            return False
        if self.transport_name and self.transport_name.strip().lower() not in transport_name:
            return False
        return True

    @api.model
    def find_best_rule(self, order):
        rules = self.search([("active", "=", True)], order="sequence asc, id asc")
        for rule in rules:
            if rule._match_order(order):
                return rule
        return self.browse()
