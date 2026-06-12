from odoo import fields, models, api
import logging
_logger = logging.getLogger(__name__)

class OmsWarehouseOrderPreparation(models.Model):
    _name = 'oms.warehouse.order.preparation'
    _description = 'Đơn hàng chờ kho chuẩn bị đi giao'
    _order = 'order_id'

    order_id = fields.Many2one('sale_custom.order', string='Đơn hàng', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', string='Khách hàng', store=True)
    date_order = fields.Datetime(related='order_id.date_order', string='Ngày duyệt', store=True)
    state = fields.Selection(related='order_id.state', string='Trạng thái đơn', store=True)
    late_delivery_status = fields.Selection([
        ('normal', 'Bình thường'),
        ('warning', 'Sắp trễ'),
        ('danger', 'Đã trễ'),
    ], string='Trạng thái màu', compute='_compute_late_delivery_status', store=True)
    minutes_waiting = fields.Integer(string='Số phút chờ', compute='_compute_minutes_waiting', store=True)
    whs_code = fields.Many2one('oms.warehouse', related='order_id.WhsCode', string="Kho xuất", store=True)
    delivery_method = fields.Selection(related='order_id.TrnspCode', string="Phương thức vận chuyển", store=True)
    ship_to = fields.Many2one('oms.address', related='order_id.ShipToCode', string="Địa chỉ giao hàng", store=True)
    is_prepared = fields.Boolean(string="Đã chuẩn bị xong", default=False)

    # Danh sách sản phẩm của đơn (readonly, chỉ xem)
    order_line_ids = fields.One2many(
        related='order_id.order_line',
        string='Dòng sản phẩm',
        readonly=True,
        store=False,
    )

    @api.depends('order_id.date_order', 'order_id.state', 'is_prepared')
    def _compute_late_delivery_status(self):
        for rec in self:
            order = rec.order_id
            if rec.is_prepared:
                rec.late_delivery_status = 'normal'
                continue
            if not order or order.state != 'approved':
                rec.late_delivery_status = 'normal'
                continue
            now = fields.Datetime.now()
            delta = now - order.date_order if order.date_order else 0
            mins = delta.total_seconds() / 60 if order.date_order else 0
            if mins >= 120:
                rec.late_delivery_status = 'danger'
            elif mins >= 90:
                rec.late_delivery_status = 'warning'
            else:
                rec.late_delivery_status = 'normal'

    @api.depends('order_id.date_order')
    def _compute_minutes_waiting(self):
        for rec in self:
            if rec.order_id and rec.order_id.date_order:
                delta = fields.Datetime.now() - rec.order_id.date_order
                rec.minutes_waiting = int(delta.total_seconds() // 60)
            else:
                rec.minutes_waiting = 0

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    def sync_preparation_orders(self):
        Order = self.env['sale_custom.order']
        Preparation = self.env['oms.warehouse.order.preparation']

        orders = Order.search([('state', '=', 'approved')])
        _logger.info("[SYNC TEST] Found %s approved orders.", len(orders))

        existing = Preparation.search([])
        existing_order_ids = existing.mapped('order_id.id')
        _logger.info("[SYNC TEST] Existing records in preparation: %s", len(existing_order_ids))

        for order in orders:
            if order.id not in existing_order_ids:
                _logger.info("[SYNC TEST] Creating preparation for order_id: %s", order.id)
                Preparation.create({'order_id': order.id})
            else:
                _logger.info("[SYNC TEST] Already exists: order_id %s", order.id)

        for pre in existing:
            if pre.order_id.state != 'approved':
                _logger.info("[SYNC TEST] Removing preparation for order_id %s (state=%s)", pre.order_id.id, pre.order_id.state)
                pre.unlink()
    