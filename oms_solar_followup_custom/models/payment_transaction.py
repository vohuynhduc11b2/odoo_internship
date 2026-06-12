from odoo import api, fields, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    oms_payment_incident_count = fields.Integer(string='Số sự cố OMS', compute='_compute_oms_payment_incident_count')

    def _compute_oms_payment_incident_count(self):
        Incident = self.env['oms.payment.incident']
        for tx in self:
            tx.oms_payment_incident_count = Incident.search_count([('payment_transaction_id', '=', tx.id)])

    def _oms_get_sale_custom_orders(self):
        self.ensure_one()
        candidates = ['sale_order_ids', 'sale_custom_order_ids', 'order_ids']
        for fname in candidates:
            if fname in self._fields:
                records = self[fname]
                if records and records._name == 'sale_custom.order':
                    return records
        return self.env['sale_custom.order']

    def _oms_prepare_incident_vals(self, short_description, incident_type='warning', severity='medium'):
        self.ensure_one()
        orders = self._oms_get_sale_custom_orders()
        order = orders[:1]
        partner = False
        for fname in ['partner_id', 'partner_invoice_id']:
            if fname in self._fields and self[fname]:
                partner = self[fname]
                break
        return {
            'payment_transaction_id': self.id,
            'sale_order_id': order.id if order else False,
            'partner_id': partner.id if partner else False,
            'provider_code': getattr(self, 'provider_code', False) or getattr(getattr(self, 'provider_id', False), 'code', False),
            'payment_reference': self.reference,
            'incident_type': incident_type,
            'severity': severity,
            'short_description': short_description,
        }

    def _oms_create_incident_if_missing(self, short_description, incident_type='warning', severity='medium', root_cause=False, prevention=False):
        Incident = self.env['oms.payment.incident']
        for tx in self:
            domain = [
                ('payment_transaction_id', '=', tx.id),
                ('short_description', '=', short_description),
                ('state', 'in', ['open', 'in_progress']),
            ]
            if Incident.search_count(domain):
                continue
            vals = tx._oms_prepare_incident_vals(short_description, incident_type=incident_type, severity=severity)
            if root_cause:
                vals['root_cause'] = root_cause
            if prevention:
                vals['prevention'] = prevention
            Incident.create(vals)

    def write(self, vals):
        tracked_state = vals.get('state') if 'state' in vals else False
        res = super().write(vals)
        if tracked_state:
            pending_cause = 'Giao dịch thanh toán bị treo/pending kéo dài hoặc chưa hoàn tất callback.'
            pending_prevention = 'Chuẩn hoá theo dõi callback, timeout và cơ chế đối soát định kỳ cho từng provider.'
            error_cause = 'Provider hoặc dữ liệu giao dịch trả về lỗi, sai tham chiếu hoặc không đồng bộ trạng thái.'
            error_prevention = 'Kiểm tra mapping reference, logging callback và rule retry/đối soát sau thanh toán.'
            cancel_cause = 'Khách hàng huỷ giữa chừng hoặc quy trình thanh toán bị dừng.'
            cancel_prevention = 'Tăng hướng dẫn luồng thanh toán và theo dõi bước dở dang để sale hỗ trợ kịp thời.'
            for tx in self:
                if tx.state == 'pending':
                    tx._oms_create_incident_if_missing(
                        'Giao dịch đang pending',
                        incident_type='warning',
                        severity='medium',
                        root_cause=pending_cause,
                        prevention=pending_prevention,
                    )
                elif tx.state == 'error':
                    tx._oms_create_incident_if_missing(
                        'Giao dịch lỗi',
                        incident_type='error',
                        severity='high',
                        root_cause=error_cause,
                        prevention=error_prevention,
                    )
                elif tx.state == 'cancel':
                    tx._oms_create_incident_if_missing(
                        'Giao dịch bị huỷ',
                        incident_type='cancel',
                        severity='medium',
                        root_cause=cancel_cause,
                        prevention=cancel_prevention,
                    )
        return res

    @api.model
    def cron_scan_pending_payment_transactions(self):
        param = self.env['ir.config_parameter'].sudo()
        enabled = param.get_param('oms_solar_followup_custom.auto_warning', 'True') == 'True'
        if not enabled:
            return
        threshold = int(param.get_param('oms_solar_followup_custom.pending_minutes', '30') or 30)
        limit_dt = fields.Datetime.subtract(fields.Datetime.now(), minutes=threshold)
        txs = self.search([
            ('state', '=', 'pending'),
            ('create_date', '<=', limit_dt),
        ])
        txs._oms_create_incident_if_missing(
            f'Giao dịch pending quá {threshold} phút',
            incident_type='warning',
            severity='high',
            root_cause='Giao dịch pending kéo dài vượt ngưỡng theo dõi.',
            prevention='Cần cơ chế cảnh báo sớm, kiểm tra webhook/status polling và đối soát định kỳ.',
        )
