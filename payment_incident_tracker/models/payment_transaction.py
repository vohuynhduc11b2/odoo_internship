from datetime import timedelta

from odoo import _, api, fields, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    incident_log_ids = fields.One2many(
        'oms.payment.incident.log',
        'transaction_id',
        string='Sự cố/Cảnh báo',
    )
    incident_count = fields.Integer(compute='_compute_incident_stats', string='Số sự cố')
    open_incident_count = fields.Integer(compute='_compute_incident_stats', string='Sự cố đang mở')

    def _compute_incident_stats(self):
        grouped = self.env['oms.payment.incident.log'].read_group(
            [('transaction_id', 'in', self.ids)],
            ['transaction_id', 'state'],
            ['transaction_id', 'state'],
            lazy=False,
        ) if self.ids else []
        data = {tx.id: {'all': 0, 'open': 0} for tx in self}
        for row in grouped:
            tx_id = row['transaction_id'][0]
            count = row['__count']
            data.setdefault(tx_id, {'all': 0, 'open': 0})
            data[tx_id]['all'] += count
            if row['state'] in ('open', 'in_progress'):
                data[tx_id]['open'] += count
        for tx in self:
            tx.incident_count = data.get(tx.id, {}).get('all', 0)
            tx.open_incident_count = data.get(tx.id, {}).get('open', 0)

    def _get_primary_sale_order(self):
        self.ensure_one()
        if 'sale_order_ids' in self._fields and self.sale_order_ids:
            return self.sale_order_ids[:1]
        return False

    def action_view_payment_incidents(self):
        self.ensure_one()
        action = self.env.ref('payment_incident_tracker.action_oms_payment_incident_log').read()[0]
        action['domain'] = [('transaction_id', '=', self.id)]
        action['context'] = {
            'default_transaction_id': self.id,
            'default_sale_order_id': self._get_primary_sale_order().id if self._get_primary_sale_order() else False,
            'default_scope': 'payment',
            'search_default_open': 1,
        }
        return action

    def action_log_payment_incident(self):
        self.ensure_one()
        sale_order = self._get_primary_sale_order()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Ghi nhận sự cố thanh toán'),
            'res_model': 'oms.payment.incident.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_transaction_id': self.id,
                'default_sale_order_id': sale_order.id if sale_order else False,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_scope': 'payment',
                'default_title': _('Sự cố thanh toán - %s') % (self.reference or self.id),
            },
        }

    @api.model
    def _get_payment_incident_config(self):
        icp = self.env['ir.config_parameter'].sudo()
        auto_warning = str(icp.get_param('payment_incident_tracker.auto_warning', 'True')).lower() in ('1', 'true', 'yes')
        pending_minutes = int(icp.get_param('payment_incident_tracker.pending_minutes', '30') or 30)
        return {
            'auto_warning': auto_warning,
            'pending_minutes': pending_minutes,
        }

    def _build_incident_vals(
        self,
        *,
        title,
        incident_type,
        severity,
        root_cause,
        prevention_action,
        correction_action=None,
        category_code=None,
        repeat_reference=None,
        auto_generated=True,
        is_repeat_issue=False,
        state='open',
    ):
        self.ensure_one()
        sale_order = self._get_primary_sale_order()
        category = False
        if category_code:
            category = self.env['oms.payment.incident.category'].search([('code', '=', category_code)], limit=1)
        return {
            'title': title,
            'scope': 'payment',
            'incident_type': incident_type,
            'severity': severity,
            'state': state,
            'category_id': category.id if category else False,
            'transaction_id': self.id,
            'sale_order_id': sale_order.id if sale_order else False,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'provider_id': self.provider_id.id if self.provider_id else False,
            'currency_id': self.currency_id.id if self.currency_id else False,
            'amount': self.amount,
            'root_cause': root_cause,
            'prevention_action': prevention_action,
            'correction_action': correction_action or False,
            'auto_generated': auto_generated,
            'repeat_reference': repeat_reference,
            'is_repeat_issue': is_repeat_issue,
            'company_id': self.company_id.id if 'company_id' in self._fields and self.company_id else self.env.company.id,
        }

    def _create_incident_if_missing(self, vals):
        self.ensure_one()
        repeat_reference = vals.get('repeat_reference')
        Incident = self.env['oms.payment.incident.log']
        if repeat_reference:
            existed = Incident.search([
                ('transaction_id', '=', self.id),
                ('repeat_reference', '=', repeat_reference),
            ], limit=1)
            if existed:
                return existed
        return Incident.create(vals)

    def _auto_log_failure_if_needed(self):
        for tx in self:
            if tx.state not in ('error', 'cancel'):
                continue
            repeat_reference = f'auto_failure:{tx.state}:{tx.reference or tx.id}'
            title = _('Giao dịch %s - %s') % (
                dict(tx._fields['state'].selection).get(tx.state, tx.state),
                tx.reference or tx.id,
            )
            root_cause = tx.state_message or (
                _('Giao dịch bị lỗi khi xử lý phản hồi thanh toán.')
                if tx.state == 'error'
                else _('Giao dịch bị hủy trong quá trình thanh toán.')
            )
            prevention = _(
                'Kiểm tra cấu hình cổng thanh toán, callback/webhook, token phiên, số tiền thanh toán và quy trình xác nhận thanh toán trước khi cho phép khách tiếp tục.'
            )
            correction = _(
                'Đối soát log giao dịch, xác minh lại trạng thái thực tế ở cổng thanh toán và hướng dẫn khách thanh toán lại nếu cần.'
            )
            vals = tx._build_incident_vals(
                title=title,
                incident_type='error',
                severity='high' if tx.state == 'error' else 'medium',
                root_cause=root_cause,
                prevention_action=prevention,
                correction_action=correction,
                category_code='PAYMENT_ERROR' if tx.state == 'error' else 'CUSTOMER_ABORT',
                repeat_reference=repeat_reference,
                auto_generated=True,
            )
            tx._create_incident_if_missing(vals)

    def _auto_resolve_pending_warnings(self):
        Incident = self.env['oms.payment.incident.log']
        for tx in self:
            warnings = Incident.search([
                ('transaction_id', '=', tx.id),
                ('repeat_reference', 'like', 'pending_timeout:%'),
                ('state', 'in', ('open', 'in_progress')),
            ])
            if warnings:
                warnings.write({
                    'state': 'resolved',
                    'resolution_note': _('Giao dịch đã chuyển sang trạng thái %s.') % dict(tx._fields['state'].selection).get(tx.state, tx.state),
                })

    def write(self, vals):
        old_state = {tx.id: tx.state for tx in self} if 'state' in vals else {}
        res = super().write(vals)
        if 'state' in vals:
            changed = self.filtered(lambda tx: old_state.get(tx.id) != tx.state)
            failed = changed.filtered(lambda tx: tx.state in ('error', 'cancel'))
            progressed = changed.filtered(lambda tx: tx.state in ('authorized', 'done'))
            if failed:
                failed._auto_log_failure_if_needed()
            if progressed:
                progressed._auto_resolve_pending_warnings()
        return res

    @api.model
    def cron_scan_pending_payment_transactions(self):
        config = self._get_payment_incident_config()
        if not config['auto_warning']:
            return True

        deadline = fields.Datetime.now() - timedelta(minutes=config['pending_minutes'])
        txs = self.search([
            ('state', 'in', ('draft', 'pending')),
            ('create_date', '<=', deadline),
        ])
        for tx in txs:
            repeat_reference = f'pending_timeout:{config["pending_minutes"]}:{tx.reference or tx.id}'
            vals = tx._build_incident_vals(
                title=_('Giao dịch treo quá %s phút - %s') % (config['pending_minutes'], tx.reference or tx.id),
                incident_type='warning',
                severity='medium',
                root_cause=_('Giao dịch ở trạng thái draft/pending quá lâu, có nguy cơ khách rời bước thanh toán hoặc webhook chưa cập nhật.'),
                prevention_action=_('Thiết lập giám sát callback/webhook, kiểm tra session checkout và chủ động nhắc khách hoàn tất thanh toán khi giao dịch treo quá ngưỡng.'),
                correction_action=_('Kiểm tra trạng thái thực tế ở cổng thanh toán và đối soát với đơn hàng trước khi hướng dẫn khách thanh toán lại.'),
                category_code='PAYMENT_PENDING_TIMEOUT',
                repeat_reference=repeat_reference,
                auto_generated=True,
            )
            tx._create_incident_if_missing(vals)
        return True
