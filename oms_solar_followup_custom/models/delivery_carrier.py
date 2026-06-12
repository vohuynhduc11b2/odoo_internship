from odoo import fields, models


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    oms_is_outstation = fields.Boolean(
        string="OMS giao hàng ngoại tỉnh",
        help="Đánh dấu phương thức giao hàng này là giao hàng ngoại tỉnh để OMS yêu cầu nhập thông tin vận chuyển bổ sung.",
    )
    oms_customer_pay_shipping_text = fields.Char(
        string="Nội dung phí vận chuyển OMS",
        default="Phí VC khách hàng tự chi trả",
        help="Nội dung hiển thị thay cho nhãn giá vận chuyển trên website.",
    )
