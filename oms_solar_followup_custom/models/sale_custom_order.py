from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SaleCustomOrder(models.Model):
    _inherit = 'sale_custom.order'

    oms_buyer_partner_id = fields.Many2one('res.partner', string='Khách hàng mua thực tế')
    oms_invoice_customer_id = fields.Many2one('res.partner', string='Khách hàng xuất hoá đơn')
    oms_available_invoice_partner_ids = fields.Many2many(
        'res.partner',
        string='KH xuất hoá đơn hợp lệ',
        compute='_compute_oms_available_invoice_partner_ids',
    )
    oms_available_buyer_partner_ids = fields.Many2many(
        'res.partner',
        string='Đối tác mua hợp lệ',
        compute='_compute_oms_available_invoice_partner_ids',
    )
    oms_delivery_method = fields.Selection([
        ('standard', 'Giao hàng thông thường'),
        ('outstation', 'Giao hàng ngoại tỉnh'),
    ], string='Phương thức giao hàng OMS', default='standard', required=True)
    oms_outstation_option_id = fields.Many2one('oms.solar.outstation.option', string='Phương án giao ngoại tỉnh')
    oms_transport_need = fields.Char(string='Nhu cầu vận chuyển')
    oms_transport_address = fields.Text(string='Địa chỉ nhận hàng ngoại tỉnh')
    oms_transport_note = fields.Text(string='Ghi chú vận chuyển')
    oms_destination_state_id = fields.Many2one('res.country.state', string='Tỉnh/Thành giao hàng')
    oms_expected_delivery_date = fields.Date(string='Ngày giao hàng dự kiến')
    oms_delivery_leadtime_rule_id = fields.Many2one('oms.delivery.leadtime.rule', string='Rule ngày giao hàng', readonly=True)
    oms_lock_flag = fields.Boolean(string='Lock OMS')
    oms_lock_note = fields.Char(string='Lý do lock OMS')
    oms_low_stock_warning_text = fields.Text(string='Cảnh báo tồn kho thấp', compute='_compute_oms_low_stock_warning_text')
    oms_duplicate_info_status = fields.Selection([
        ('none', 'Chưa phát hiện'),
        ('review', 'Cần rà soát'),
        ('resolved', 'Đã xử lý'),
    ], string='Trạng thái double thông tin', default='none')
    oms_duplicate_info_note = fields.Text(string='Ghi chú rà soát double thông tin')
    oms_approval_rule_note = fields.Text(string='Ghi chú rule duyệt')
    oms_deposit_hold_required = fields.Boolean(string='Cần xác nhận giữ hàng đơn cọc')
    oms_deposit_hold_note = fields.Text(string='Ghi chú giữ hàng đơn cọc')
    oms_payment_incident_count = fields.Integer(string='Số sự cố thanh toán', compute='_compute_oms_payment_incident_count')
    oms_has_contact_price_item = fields.Boolean(string='Có dòng cần gửi sales báo giá', compute='_compute_oms_has_contact_price_item')

    def _get_partner_field(self):
        for fname in ['partner_id', 'partner_invoice_id']:
            if fname in self._fields:
                return fname
        return False

    def _get_shipping_state(self):
        self.ensure_one()
        if self.oms_destination_state_id:
            return self.oms_destination_state_id
        return self._get_base_shipping_state()

    def _get_base_shipping_state(self):
        self.ensure_one()
        for fname in ['partner_shipping_id', 'partner_invoice_id', 'partner_id']:
            if fname in self._fields and self[fname]:
                state = getattr(self[fname], 'state_id', False)
                if state:
                    return state
        return False

    def _oms_get_group_root(self, partner=None):
        self.ensure_one()
        partner = partner or self.oms_buyer_partner_id or getattr(self, 'partner_id', False) or getattr(self, 'partner_invoice_id', False)
        partner = partner.commercial_partner_id if partner else False
        return (partner.oms_group_parent_id or partner).commercial_partner_id if partner else self.env['res.partner']

    def _oms_get_group_companies(self, partner=None, include_selected=True):
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        root = self._oms_get_group_root(partner)
        if not root:
            return Partner.browse()
        companies = root | Partner.search([
            ('oms_group_parent_id', '=', root.id),
            ('is_company', '=', True),
        ])
        if include_selected:
            selected = partner or self.oms_buyer_partner_id or getattr(self, 'partner_id', False) or getattr(self, 'partner_invoice_id', False)
            selected = selected.commercial_partner_id if selected else Partner.browse()
            if selected:
                companies |= selected
        return companies.sorted(lambda p: (0 if p.id == root.id else 1, p.display_name or ''))

    def _oms_get_invoice_candidates(self, buyer=None):
        self.ensure_one()
        Partner = self.env['res.partner'].sudo().with_context(show_address=1)
        buyer = buyer or self.oms_buyer_partner_id or getattr(self, 'partner_id', False) or getattr(self, 'partner_invoice_id', False)
        buyer = buyer.commercial_partner_id if buyer else Partner.browse()
        if not buyer:
            return Partner.browse()

        allowed_companies = self._oms_get_group_companies(buyer)
        partners = Partner.browse()
        for company in allowed_companies:
            company = company.commercial_partner_id
            partners |= Partner.search([
                ('id', 'child_of', company.ids),
                '|',
                ('type', 'in', ['invoice', 'other', 'contact']),
                ('id', '=', company.id),
            ], order='type, id')

        if allowed_companies:
            partners |= allowed_companies
        if buyer:
            partners |= buyer

        selected_invoice = self.oms_invoice_customer_id.exists() if self.oms_invoice_customer_id else Partner.browse()
        if selected_invoice:
            partners |= selected_invoice

        partners = partners.filtered(lambda p: p.oms_allow_invoice_selection)
        if not partners:
            partners = allowed_companies
            if buyer:
                partners |= buyer
        selected_id = self.oms_invoice_customer_id.id if self.oms_invoice_customer_id else (buyer.id if buyer else False)
        return partners.sorted(lambda p: (0 if p.id == selected_id else 1, p.display_name or ''))

    def _oms_get_delivery_rule_values(self):
        self.ensure_one()
        Rule = self.env['oms.delivery.leadtime.rule']
        rule = False
        expected_date = False
        state = self.oms_destination_state_id or self._get_base_shipping_state()
        if state:
            rule = Rule.search([
                ('state_id', '=', state.id),
                ('company_id', 'in', [False, self.env.company.id]),
                ('active', '=', True),
            ], order='company_id desc, sequence, id', limit=1)
        if rule:
            expected_date = fields.Date.add(fields.Date.context_today(self), days=rule.delivery_days)
        return {
            'oms_delivery_leadtime_rule_id': rule.id if rule else False,
            'oms_expected_delivery_date': expected_date,
        }

    def _oms_build_sync_vals(self):
        self.ensure_one()
        vals = {}
        partner_field = self._get_partner_field()

        buyer = self.oms_buyer_partner_id
        if not buyer and partner_field and self[partner_field]:
            buyer = self[partner_field].commercial_partner_id
            vals['oms_buyer_partner_id'] = buyer.id

        invoice_partner = self.oms_invoice_customer_id
        if not invoice_partner:
            if 'partner_invoice_id' in self._fields and self.partner_invoice_id:
                invoice_partner = self.partner_invoice_id
            elif buyer:
                invoice_partner = buyer
            if invoice_partner:
                vals['oms_invoice_customer_id'] = invoice_partner.id

        if invoice_partner:
            for fname in ['partner_invoice_id', 'invoice_partner_id']:
                if fname in self._fields and self[fname].id != invoice_partner.id:
                    vals[fname] = invoice_partner.id
            cardcode2_field = self._fields.get('CardCode2')
            if cardcode2_field:
                target = invoice_partner.commercial_partner_id.id if cardcode2_field.type == 'many2one' else (
                    invoice_partner.commercial_partner_id.ref or invoice_partner.commercial_partner_id.display_name
                )
                current = self.CardCode2.id if cardcode2_field.type == 'many2one' and self.CardCode2 else self.CardCode2
                if current != target:
                    vals['CardCode2'] = target

        state = self._get_base_shipping_state()
        if state and self.oms_destination_state_id.id != state.id:
            vals['oms_destination_state_id'] = state.id

        if self.oms_outstation_option_id and not (self.oms_transport_need or '').strip():
            vals['oms_transport_need'] = self.oms_outstation_option_id.name

        future_state = self.env['res.country.state'].browse(vals.get('oms_destination_state_id')) if vals.get('oms_destination_state_id') else (self.oms_destination_state_id or state)
        rule_vals = self._oms_get_delivery_rule_values_for_state(future_state)
        for key, value in rule_vals.items():
            current = self[key].id if self._fields[key].type == 'many2one' and self[key] else self[key]
            target = value.id if getattr(value, '_name', False) else value
            if current != target:
                vals[key] = target

        salesperson_user = self._oms_get_salesperson_user()
        if salesperson_user:
            if 'user_id' in self._fields and getattr(self, 'user_id', False).id != salesperson_user.id:
                vals['user_id'] = salesperson_user.id
            if 'SlpCode' in self._fields and getattr(self, 'SlpCode', False).id != salesperson_user.id:
                vals['SlpCode'] = salesperson_user.id
        return vals

    def _oms_get_salesperson_user(self):
        self.ensure_one()
        User = self.env['res.users'].sudo()

        for candidate in (
            getattr(self, 'SlpCode', False),
            getattr(self, 'user_id', False),
        ):
            if candidate and getattr(candidate, '_name', '') == 'res.users':
                return candidate.sudo()

        partner = (
            self.oms_buyer_partner_id
            or getattr(self, 'partner_id', False)
            or self.oms_invoice_customer_id
            or getattr(self, 'partner_invoice_id', False)
        )
        partner = partner.commercial_partner_id if partner else False
        if not partner:
            return User.browse()

        resolver = getattr(self, '_slp_user_from_partner', False)
        salesperson_user = resolver(partner) if resolver else User.browse()
        if salesperson_user and getattr(salesperson_user, '_name', '') == 'res.users':
            return salesperson_user.sudo()
        return User.browse()

    def _oms_get_delivery_rule_values_for_state(self, state):
        self.ensure_one()
        Rule = self.env['oms.delivery.leadtime.rule']
        rule = False
        expected_date = False
        if state:
            rule = Rule.search([
                ('state_id', '=', state.id),
                ('company_id', 'in', [False, self.env.company.id]),
                ('active', '=', True),
            ], order='company_id desc, sequence, id', limit=1)
        if rule:
            expected_date = fields.Date.add(fields.Date.context_today(self), days=rule.delivery_days)
        return {
            'oms_delivery_leadtime_rule_id': rule.id if rule else False,
            'oms_expected_delivery_date': expected_date,
        }

    def _oms_apply_sync_updates(self):
        if self.env.context.get('oms_skip_sync'):
            return
        for order in self:
            vals = order._oms_build_sync_vals()
            if vals:
                super(SaleCustomOrder, order.with_context(oms_skip_sync=True)).write(vals)

    def _sync_base_invoice_partner(self):
        self._oms_apply_sync_updates()

    def _sync_defaults_from_base_partner(self):
        self._oms_apply_sync_updates()

    def _apply_delivery_rule(self):
        if self.env.context.get('oms_skip_sync'):
            return
        for order in self:
            vals = order._oms_get_delivery_rule_values_for_state(order.oms_destination_state_id or order._get_base_shipping_state())
            clean_vals = {}
            for key, value in vals.items():
                current = order[key].id if order._fields[key].type == 'many2one' and order[key] else order[key]
                if current != value:
                    clean_vals[key] = value
            if clean_vals:
                super(SaleCustomOrder, order.with_context(oms_skip_sync=True)).write(clean_vals)

    def _oms_apply_expected_delivery_rule(self):
        self._apply_delivery_rule()

    def _oms_get_selected_delivery_record(self):
        self.ensure_one()
        for fname in ('carrier_id', 'delivery_carrier_id', 'trnsp_id'):
            if fname in self._fields and self[fname]:
                return self[fname]
        return False

    def _oms_is_outstation_selected(self):
        self.ensure_one()
        if self.oms_delivery_method == 'outstation':
            return True
        delivery = self._oms_get_selected_delivery_record()
        if not delivery:
            return False
        if getattr(delivery, 'oms_is_outstation', False):
            return True
        name = ''
        for attr in ('name', 'display_name'):
            name = getattr(delivery, attr, False) or name
        return 'ngoại tỉnh' in (name or '').lower()

    def _oms_validate_checkout_requirements(self):
        for order in self:
            if order._oms_is_outstation_selected():
                if not order.oms_outstation_option_id:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc chọn phương án vận chuyển.')
                if not order.oms_transport_address:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc nhập địa chỉ nhận hàng.')
                if not order.oms_transport_note:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc nhập ghi chú vận chuyển.')
            order._ensure_invoice_partner_is_valid()

    @api.depends('oms_buyer_partner_id', 'oms_invoice_customer_id', 'partner_id', 'partner_invoice_id')
    def _compute_oms_available_invoice_partner_ids(self):
        Partner = self.env['res.partner'].sudo()
        for order in self:
            partner = order.oms_buyer_partner_id or getattr(order, 'partner_id', False) or getattr(order, 'partner_invoice_id', False)
            partner = partner.commercial_partner_id if partner else False
            if not partner:
                order.oms_available_buyer_partner_ids = False
                order.oms_available_invoice_partner_ids = False
                continue

            root = order._oms_get_group_root(partner)
            buyer_candidates = (root | Partner.search([
                ('oms_group_parent_id', '=', root.id),
                ('is_company', '=', True),
            ])).filtered(lambda p: p.oms_allow_buyer_selection)
            if not buyer_candidates:
                buyer_candidates = root
            buyer_candidates = buyer_candidates.sorted(lambda p: (0 if p.id == partner.id else 1, p.display_name or ''))
            order.oms_available_buyer_partner_ids = buyer_candidates

            selected_buyer = order.oms_buyer_partner_id.commercial_partner_id if order.oms_buyer_partner_id else partner
            order.oms_available_invoice_partner_ids = order._oms_get_invoice_candidates(selected_buyer)

    @api.depends('order_line.product_id', 'order_line.product_uom_qty')
    def _compute_oms_low_stock_warning_text(self):
        for order in self:
            warnings = []
            lines = getattr(order, 'order_line', self.env['sale_custom.order.line'])
            for line in lines:
                product = getattr(line, 'product_id', False)
                if not product or getattr(line, 'display_type', False):
                    continue
                template = getattr(product, 'product_tmpl_id', False) or product
                enabled, threshold, message = template._oms_get_low_stock_policy()
                if not enabled:
                    continue
                qty_available = float(getattr(template, 'oms_available_qty', 0.0) or 0.0)
                if qty_available < threshold:
                    warnings.append(f'- {product.display_name}: tồn khả dụng {qty_available:g} < ngưỡng {threshold:g}. {message}')
            order.oms_low_stock_warning_text = "\n".join(warnings) if warnings else False

    @api.depends('order_line.price_unit', 'order_line.product_uom_qty')
    def _compute_oms_has_contact_price_item(self):
        for order in self:
            has_contact = False
            lines = order.order_line.filtered(
                lambda l: not getattr(l, 'display_type', False)
                and l.product_id
                and not getattr(l, 'is_delivery', False)
                and not getattr(l, 'is_gift', False)
                and not getattr(l, 'is_bundle', False)
            )
            for line in lines:
                price = float(line.price_unit or 0.0)
                if price <= 1.0 and 'technical_price_unit' in line._fields:
                    price = max(price, float(line.technical_price_unit or 0.0))
                if price <= 1.0:
                    has_contact = True
                    break
            order.oms_has_contact_price_item = has_contact

    def _compute_oms_payment_incident_count(self):
        Incident = self.env['oms.payment.incident']
        for order in self:
            order.oms_payment_incident_count = Incident.search_count([('sale_order_id', '=', order.id)])

    @api.constrains('oms_delivery_method', 'oms_outstation_option_id', 'oms_transport_address', 'oms_transport_note')
    def _check_outstation_requirements(self):
        for order in self:
            if order._oms_is_outstation_selected() or order.oms_delivery_method == 'outstation':
                if not order.oms_outstation_option_id:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc chọn phương án vận chuyển.')
                if not order.oms_transport_address:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc nhập địa chỉ nhận hàng.')
                if not order.oms_transport_note:
                    raise ValidationError('Khi chọn giao hàng ngoại tỉnh, bắt buộc nhập ghi chú vận chuyển.')

    def _ensure_invoice_partner_is_valid(self):
        for order in self:
            if order.oms_invoice_customer_id and order.oms_invoice_customer_id not in order.oms_available_invoice_partner_ids:
                raise ValidationError('Khách hàng xuất hoá đơn phải thuộc cùng nhóm khách hàng đã cấu hình.')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._oms_apply_sync_updates()
        records._propagate_oms_lock_to_downstream()
        return records

    def write(self, vals):
        if self.env.context.get('oms_skip_sync'):
            return super().write(vals)
        res = super().write(vals)
        watched = {
            'oms_buyer_partner_id', 'oms_invoice_customer_id', 'oms_outstation_option_id', 'oms_destination_state_id',
            'oms_delivery_method', 'partner_id', 'partner_shipping_id', 'partner_invoice_id', 'invoice_partner_id',
            'oms_transport_need', 'CardCode2',
        }
        if watched.intersection(vals.keys()):
            self._oms_apply_sync_updates()
        if {'oms_lock_flag', 'oms_lock_note', 'oms_expected_delivery_date'}.intersection(vals.keys()):
            self._propagate_oms_lock_to_downstream()
        return res

    def action_confirm(self):
        self._ensure_invoice_partner_is_valid()
        self._sync_defaults_from_base_partner()
        self._sync_base_invoice_partner()
        return super().action_confirm()

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        if self.oms_invoice_customer_id:
            vals['partner_id'] = self.oms_invoice_customer_id.id
        return vals

    def _prepare_picking(self):
        vals = super()._prepare_picking()
        vals.update({
            'oms_lock_flag': self.oms_lock_flag,
            'oms_lock_note': self.oms_lock_note,
            'oms_expected_delivery_date': self.oms_expected_delivery_date,
        })
        return vals

    def _get_pay_to_code(self):
        self.ensure_one()
        pay_to = getattr(self, 'PayToCode', False)
        if pay_to:
            return (getattr(pay_to, 'address', False) or '').strip()

        invoice_partner = self.oms_invoice_customer_id or getattr(self, 'partner_invoice_id', False) or getattr(self, 'partner_id', False)
        partner_ref = ((invoice_partner and invoice_partner.commercial_partner_id.ref) or self.partner_id.commercial_partner_id.ref or '').strip()
        if not partner_ref or 'oms.address' not in self.env:
            return ''
        addr = self.env['oms.address'].search([
            ('card_code', '=', partner_ref), ('adres_type', '=', 'B')
        ], order='id asc', limit=1)
        return (getattr(addr, 'address', False) or '').strip()

    def _get_address(self):
        self.ensure_one()
        pay_to = getattr(self, 'PayToCode', False)
        if pay_to:
            return (getattr(pay_to, 'name', False) or '').strip()

        invoice_partner = self.oms_invoice_customer_id or getattr(self, 'partner_invoice_id', False) or getattr(self, 'partner_id', False)
        partner_ref = ((invoice_partner and invoice_partner.commercial_partner_id.ref) or self.partner_id.commercial_partner_id.ref or '').strip()
        if not partner_ref or 'oms.address' not in self.env:
            return ''
        addr = self.env['oms.address'].search([
            ('card_code', '=', partner_ref), ('adres_type', '=', 'B')
        ], order='id asc', limit=1)
        return (getattr(addr, 'name', False) or '').strip()

    def _propagate_oms_lock_to_downstream(self):
        Picking = self.env['stock.picking'].sudo()
        Preparation = self.env['oms.warehouse.order.preparation'].sudo() if 'oms.warehouse.order.preparation' in self.env else False
        for order in self:
            pickings = Picking.search([('origin', '=', order.name)]) if 'origin' in Picking._fields else Picking.browse()
            if pickings:
                pickings.write({
                    'oms_lock_flag': order.oms_lock_flag,
                    'oms_lock_note': order.oms_lock_note,
                    'oms_expected_delivery_date': order.oms_expected_delivery_date,
                })
            if Preparation:
                domain = []
                for fname in ['sale_order_id', 'sale_custom_order_id', 'order_id', 'source_order_id']:
                    if fname in Preparation._fields:
                        field = Preparation._fields[fname]
                        if getattr(field, 'comodel_name', False) == 'sale_custom.order':
                            domain = [(fname, '=', order.id)]
                            break
                if not domain and 'origin' in Preparation._fields:
                    domain = [('origin', '=', order.name)]
                if domain:
                    recs = Preparation.search(domain)
                    write_vals = {k: v for k, v in {
                        'oms_lock_flag': order.oms_lock_flag,
                        'oms_lock_note': order.oms_lock_note,
                        'oms_expected_delivery_date': order.oms_expected_delivery_date,
                    }.items() if k in Preparation._fields}
                    if write_vals and recs:
                        recs.write(write_vals)

    def action_request_sales_quote(self):
        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for order in self:
            salesperson_user = order._oms_get_salesperson_user()
            write_vals = {}
            if salesperson_user and 'user_id' in order._fields and getattr(order, 'user_id', False).id != salesperson_user.id:
                write_vals['user_id'] = salesperson_user.id
            if salesperson_user and 'SlpCode' in order._fields and getattr(order, 'SlpCode', False).id != salesperson_user.id:
                write_vals['SlpCode'] = salesperson_user.id
            if write_vals:
                super(SaleCustomOrder, order.with_context(oms_skip_sync=True)).write(write_vals)
            summary = _('Khách hàng cần sales báo giá')
            note = _('Đơn %(order)s có sản phẩm giá 1đ / liên hệ. Vui lòng liên hệ khách hàng để báo giá và hỗ trợ chốt đơn.') % {
                'order': order.name or order.display_name,
            }
            order.message_post(body=note)
            if activity_type and salesperson_user and getattr(salesperson_user, '_name', '') == 'res.users':
                order.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=salesperson_user.id,
                    summary=summary,
                    note=note,
                )

    def action_view_oms_payment_incidents(self):
        self.ensure_one()
        action = self.env.ref('oms_solar_followup_custom.action_oms_payment_incident').sudo().read()[0]
        action['domain'] = [('sale_order_id', '=', self.id)]
        action['context'] = {'default_sale_order_id': self.id}
        return action
