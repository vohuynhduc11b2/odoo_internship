# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import logging
from psycopg2 import IntegrityError

_logger = logging.getLogger(__name__)


def convert_datetime_str(val):
    """
    Convert chuỗi datetime từ API kiểu:
    '2026-01-31T08:34:16' hoặc '2026-01-31T08:34:16.000'
    -> '2026-01-31 08:34:16'
    (Odoo fields.Datetime nhận string format này ok)
    """
    if not val:
        return False
    val = str(val).replace('T', ' ')
    if '.' in val:
        val = val.split('.')[0]
    return val


# =========================================================
# OMS CUSTOMER
# =========================================================
class OmsCustomer(models.Model):
    _name = 'oms.customer'
    _description = 'OMS Customer (imported from API)'

    _sql_constraints = [
        ('oms_customer_card_code_uniq', 'unique(card_code)', 'CardCode đã tồn tại.'),
    ]

    card_code = fields.Char(string="Mã KH (CardCode)", required=True, index=True)
    card_name = fields.Char(string="Tên KH (CardName)")
    group_code = fields.Char(string="Mã nhóm KH (GroupCode)")
    group_name = fields.Char(string="Nhóm KH (GroupName)")
    lic_trad_num = fields.Char(string="Mã số DN (LicTradNum)")
    credit_line = fields.Float(string="Hạn mức nợ (CreditLine)")
    debt_line = fields.Float(string="Dư nợ (DebtLine)")
    slp_code = fields.Char(string="SlpCode")
    currency = fields.Char(string="Currency")
    territory = fields.Char(string="Territory")
    descript = fields.Char(string="Địa chỉ/miêu tả (descript)")
    create_date_api = fields.Datetime(string="Ngày tạo API (CreateDate)")
    update_date_api = fields.Datetime(string="Ngày sửa API (UpdateDate)")
    industry_c = fields.Char(string="IndustryC")
    ind_name = fields.Char(string="IndName")
    ten_xuat_hoa_don = fields.Char(string="Tên xuất hóa đơn (TenXuatHoaDon)")
    u_business_unit = fields.Char(string="Business Unit (U_BusinessUnit)")
    u_debt_level = fields.Char(string="Nợ xấu (U_DebtLevel)")
    u_tele_note = fields.Char(string="Ghi chú Tele (U_TeleNote)")
    u_cs_note = fields.Char(string="Ghi chú CS (U_CsNote)")
    valid_for = fields.Selection([('Y', 'Yes'), ('N', 'No')], string="Còn hoạt động? (validFor)")
    ship_to_def = fields.Char(string="Mã địa chỉ giao hàng (ShipToDef)")

    # Quan hệ
    contact_ids = fields.One2many('oms.contact', 'customer_id', string="Liên hệ")
    address_ids = fields.One2many('oms.address', 'customer_id', string="Địa chỉ")

    active = fields.Boolean(string="Hoạt động", default=True)
    cntct_prsn = fields.Char("Contact Person")
    pymnt_group = fields.Char("Payment Group")
    extra_days = fields.Char("Extra Days")
    group_num = fields.Char("Group Num")

    # Map qua res.partner (để website checkout dùng)
    res_partner_id = fields.Many2one('res.partner', string="Đã map sang res.partner")

    # ✅ LƯU DB THEO API (U_FirstBuyDate / U_LastBuyDate)
    u_first_buy_date = fields.Datetime(string="Ngày mua đầu tiên", readonly=True, index=True, copy=False)
    u_last_buy_date = fields.Datetime(string="Ngày mua cuối cùng", readonly=True, index=True, copy=False)

    # Hiển thị
    name = fields.Char(string="Tên hiển thị", compute="_compute_name", store=True, index=True)

    @api.depends('card_code', 'card_name')
    def _compute_name(self):
        for rec in self:
            parts = []
            if rec.card_code:
                parts.append(rec.card_code)
            if rec.card_name:
                parts.append(rec.card_name)
            rec.name = " - ".join(parts) if parts else False

    def name_get(self):
        result = []
        for rec in self:
            display = rec.name or rec.card_code or rec.card_name or str(rec.id)
            result.append((rec.id, display))
        return result

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        args = args or []
        if name:
            domain = ['|', ('card_code', operator, name), ('card_name', operator, name)]
        else:
            domain = []
        recs = self.search(domain + args, limit=limit)
        return recs.name_get()

    # -------------------------
    # Helpers
    # -------------------------
    @staticmethod
    def _normalize_api_datetime_vals(vals: dict):
        for k in ("create_date_api", "update_date_api", "u_first_buy_date", "u_last_buy_date"):
            if k in vals and vals.get(k):
                vals[k] = convert_datetime_str(vals[k])

    def _relink_children(self):
        """Nếu contact/address tạo trước customer thì customer tạo xong sẽ link lại."""
        Contact = self.env['oms.contact'].sudo()
        Address = self.env['oms.address'].sudo()
        for cust in self:
            if not cust.card_code:
                continue
            Contact.search([
                ('card_code', '=', cust.card_code),
                ('customer_id', '=', False),
            ]).write({'customer_id': cust.id})

            Address.search([
                ('card_code', '=', cust.card_code),
                ('customer_id', '=', False),
            ]).write({'customer_id': cust.id})

    # -------------------------
    # Create/Write: normalize + relink + sync partner
    # -------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_api_datetime_vals(vals)
        recs = super().create(vals_list)
        recs._relink_children()

        if not self.env.context.get("oms_skip_sync_partner"):
            recs.action_sync_to_res_partner()

        return recs

    def write(self, vals):
        self._normalize_api_datetime_vals(vals)
        res = super().write(vals)

        self._relink_children()

        if not self.env.context.get("oms_skip_sync_partner"):
            self.action_sync_to_res_partner()

        return res

    # =========================================================
    # SYNC OMS -> res.partner (để /shop/checkout dùng)
    # =========================================================
    def action_sync_to_res_partner(self):
        """
        - Tạo/Update company partner: x_oms_card_code = CardCode
        - Tạo/Update contact child: type='contact', x_oms_cntct_code = CntctCode
        - Tạo/Update address child:
            + Shipping -> type='delivery', x_oms_address_code = Address
            + Billing  -> type='invoice',  x_oms_address_code = Address
        """
        Partner = self.env['res.partner'].sudo()
        Country = self.env['res.country'].sudo()

        def _country_id(code):
            if not code:
                return False
            return Country.search([('code', '=', str(code).upper())], limit=1).id

        def _map_type(adres_type):
            if adres_type == 'S':
                return 'delivery'
            if adres_type == 'B':
                return 'invoice'
            return 'other'

        for cust in self:
            company = cust.res_partner_id
            if not company:
                company = Partner.search([
                    ('x_oms_card_code', '=', cust.card_code),
                    ('parent_id', '=', False),
                ], limit=1)

            billing = cust.address_ids.filtered(lambda a: a.active and a.adres_type == 'B')[:1] \
                      or cust.address_ids.filtered(lambda a: a.active)[:1]

            ct = cust.contact_ids.filtered(lambda c: c.active)[:1]

            company_vals = {
                'name': cust.card_name or cust.card_code,
                'company_type': 'company',
                'vat': cust.lic_trad_num or False,
                'x_oms_card_code': cust.card_code,
                'x_oms_ship_to_def': cust.ship_to_def or False,
                'street': billing.street if billing else False,
                'street2': ", ".join([p for p in [billing.address3, billing.county] if p]) if billing else False,
                'city': billing.city if billing else False,
                'country_id': _country_id(billing.country) if billing else False,
            }
            if ct and ct.email:
                company_vals['email'] = ct.email
            if ct and (ct.cellolar or ct.tel1 or ct.tel2):
                company_vals['phone'] = ct.cellolar or ct.tel1 or ct.tel2

            if company:
                company.write(company_vals)
            else:
                company = Partner.create(company_vals)

            cust.with_context(oms_skip_sync_partner=True).write({'res_partner_id': company.id})

            # contacts
            for c in cust.contact_ids.filtered(lambda x: x.active):
                child = Partner.search([
                    ('parent_id', '=', company.id),
                    ('type', '=', 'contact'),
                    ('x_oms_cntct_code', '=', c.cntct_code),
                ], limit=1)

                c_vals = {
                    'parent_id': company.id,
                    'type': 'contact',
                    'name': c.name or c.first_name or _("Contact %s") % c.cntct_code,
                    'x_oms_cntct_code': c.cntct_code,
                    'x_oms_card_code': cust.card_code,
                }
                if c.email:
                    c_vals['email'] = c.email
                if c.cellolar:
                    c_vals['mobile'] = c.cellolar
                if c.tel1 or c.tel2:
                    c_vals['phone'] = c.tel1 or c.tel2
                if child:
                    child.write(c_vals)
                else:
                    Partner.create(c_vals)

            # addresses
            for a in cust.address_ids.filtered(lambda x: x.active):
                p_type = _map_type(a.adres_type)
                child = Partner.search([
                    ('parent_id', '=', company.id),
                    ('type', '=', p_type),
                    ('x_oms_address_code', '=', a.address),
                ], limit=1)

                a_vals = {
                    'parent_id': company.id,
                    'type': p_type,
                    # bạn có thể đổi name để tránh lặp tên công ty trên checkout
                    'name': a.address or a.name or ("%s - %s" % (cust.card_name or cust.card_code, a.address)),
                    'street': a.street or False,
                    'street2': ", ".join([p for p in [a.address3, a.county] if p]) or False,
                    'city': a.city or False,
                    'country_id': _country_id(a.country) or False,
                    'x_oms_address_code': a.address,
                    'x_oms_card_code': cust.card_code,
                }
                if child:
                    child.write(a_vals)
                else:
                    Partner.create(a_vals)

        return True


# =========================================================
# OMS CONTACT
# =========================================================
class OmsContact(models.Model):
    _name = 'oms.contact'
    _description = 'OMS Contact'

    _sql_constraints = [
        ('oms_contact_uniq', 'unique(cntct_code, card_code)', 'Liên hệ đã tồn tại (CntctCode + CardCode).'),
    ]

    cntct_code = fields.Integer(string="Mã liên hệ (CntctCode)", required=True, index=True)
    card_code = fields.Char(string="Mã KH (CardCode)", required=True, index=True)

    customer_id = fields.Many2one(
        'oms.customer',
        string="Khách hàng liên kết",
        ondelete='cascade',
        index=True,
    )

    name = fields.Char(string="Tên liên hệ (Name)")
    first_name = fields.Char(string="Tên gọi (FirstName)")
    position = fields.Char(string="Chức vụ (Position)")
    title = fields.Char(string="Danh xưng (Title)")
    cellolar = fields.Char(string="SĐT di động (Cellolar)")
    tel1 = fields.Char(string="SĐT cố định 1 (Tel1)")
    tel2 = fields.Char(string="SĐT cố định 2 (Tel2)")
    create_date_api = fields.Datetime(string="Ngày tạo API (CreateDate)")
    update_date_api = fields.Datetime(string="Ngày sửa API (UpdateDate)")
    email = fields.Char(string="Email")
    active = fields.Boolean(string="Hoạt động", default=True)

    @staticmethod
    def _normalize_api_datetime_vals(vals: dict):
        for k in ("create_date_api", "update_date_api"):
            if k in vals and vals.get(k):
                vals[k] = convert_datetime_str(vals[k])

    def _auto_link_customer(self):
        Customer = self.env['oms.customer'].sudo()
        for rec in self:
            if not rec.customer_id and rec.card_code:
                cust = Customer.search([('card_code', '=', rec.card_code)], limit=1)
                if cust:
                    rec.customer_id = cust.id

    # ✅ UPSERT create
    @api.model_create_multi
    def create(self, vals_list):
        Customer = self.env['oms.customer'].sudo()

        recs = self.browse()
        to_create = []

        for vals in vals_list:
            self._normalize_api_datetime_vals(vals)

            if not vals.get('customer_id') and vals.get('card_code'):
                cust = Customer.search([('card_code', '=', vals['card_code'])], limit=1)
                if cust:
                    vals['customer_id'] = cust.id

            cntct = vals.get('cntct_code')
            card = vals.get('card_code')
            if cntct and card:
                existing = self.search([('cntct_code', '=', cntct), ('card_code', '=', card)], limit=1)
                if existing:
                    existing.with_context(oms_skip_sync_partner=True).write(vals)
                    recs |= existing
                    continue

            to_create.append(vals)

        if to_create:
            try:
                recs |= super().create(to_create)
            except IntegrityError:
                self.env.cr.rollback()
                for vals in to_create:
                    cntct = vals.get('cntct_code')
                    card = vals.get('card_code')
                    existing = self.search([('cntct_code', '=', cntct), ('card_code', '=', card)], limit=1) if cntct and card else False
                    if existing:
                        existing.with_context(oms_skip_sync_partner=True).write(vals)
                        recs |= existing
                        continue
                    try:
                        recs |= super(OmsContact, self).create([vals])
                    except IntegrityError:
                        self.env.cr.rollback()
                        existing = self.search([('cntct_code', '=', cntct), ('card_code', '=', card)], limit=1)
                        if existing:
                            existing.with_context(oms_skip_sync_partner=True).write(vals)
                            recs |= existing
                        else:
                            raise

        linked_customers = recs.mapped('customer_id').filtered(lambda x: x)
        if linked_customers and not self.env.context.get("oms_skip_sync_partner"):
            linked_customers.action_sync_to_res_partner()

        return recs

    def write(self, vals):
        self._normalize_api_datetime_vals(vals)
        res = super().write(vals)

        if 'card_code' in vals or not all(self.mapped('customer_id')):
            self._auto_link_customer()

        linked_customers = self.mapped('customer_id').filtered(lambda x: x)
        if linked_customers and not self.env.context.get("oms_skip_sync_partner"):
            linked_customers.action_sync_to_res_partner()

        return res

    def name_get(self):
        result = []
        for rec in self:
            parts = []
            if rec.card_code:
                parts.append(str(rec.card_code))
            if rec.name:
                parts.append(rec.name)
            if rec.cellolar:
                parts.append(rec.cellolar)
            display = " - ".join(parts) if parts else str(rec.id)
            result.append((rec.id, display))
        return result


# =========================================================
# OMS ADDRESS
# =========================================================
class OmsAddress(models.Model):
    _name = 'oms.address'
    _description = 'OMS Address'

    _sql_constraints = [
        ('oms_address_uniq', 'unique(address, card_code, adres_type)', 'Địa chỉ đã tồn tại (Address + CardCode + AdresType).'),
    ]

    name = fields.Char(string="Tên địa chỉ", compute="_compute_name", store=True)

    address = fields.Char(string="Mã địa chỉ (Address)", required=True, index=True)
    card_code = fields.Char(string="Mã KH (CardCode)", required=True, index=True)
    street = fields.Char(string="Đường/phố (Street)")
    address3 = fields.Char(string="Phường/xã (Address3)")
    county = fields.Char(string="Quận/huyện (County)")
    city = fields.Char(string="Thành phố (City)")
    country = fields.Char(string="Quốc gia (Country)")

    adres_type = fields.Selection([
        ('S', 'Giao hàng (Shipping)'),
        ('B', 'Xuất hóa đơn (Billing)'),
        ('O', 'Khác (Other)'),
    ], string="Loại địa chỉ (AdresType)")

    create_date_api = fields.Datetime(string="Ngày tạo API (CreateDate)")
    active = fields.Boolean(default=True)

    customer_id = fields.Many2one(
        'oms.customer',
        string="Khách hàng liên kết",
        ondelete='cascade',
        index=True,
    )

    @staticmethod
    def _normalize_api_datetime_vals(vals: dict):
        if "create_date_api" in vals and vals.get("create_date_api"):
            vals["create_date_api"] = convert_datetime_str(vals["create_date_api"])

    def _auto_link_customer(self):
        Customer = self.env['oms.customer'].sudo()
        for rec in self:
            if not rec.customer_id and rec.card_code:
                cust = Customer.search([('card_code', '=', rec.card_code)], limit=1)
                if cust:
                    rec.customer_id = cust.id

    # ✅ UPSERT create
    from psycopg2 import IntegrityError

    @api.model_create_multi
    def create(self, vals_list):
        Customer = self.env['oms.customer'].sudo()
        AddressAll = self.with_context(active_test=False)  # <- quan trọng
    
        recs = self.browse()
        to_create = []
    
        for vals in vals_list:
            self._normalize_api_datetime_vals(vals)
    
            # normalize để khỏi dính space
            if vals.get('address'):
                vals['address'] = str(vals['address']).strip()
            if vals.get('card_code'):
                vals['card_code'] = str(vals['card_code']).strip()
            if vals.get('adres_type'):
                vals['adres_type'] = str(vals['adres_type']).strip()
    
            # auto link customer_id nếu có card_code
            if not vals.get('customer_id') and vals.get('card_code'):
                cust = Customer.search([('card_code', '=', vals['card_code'])], limit=1)
                if cust:
                    vals['customer_id'] = cust.id
    
            # UPSERT theo unique key (address, card_code, adres_type) - tìm cả inactive
            addr = vals.get('address')
            card = vals.get('card_code')
            typ = vals.get('adres_type')
    
            if addr and card and typ:
                existing = AddressAll.search([
                    ('address', '=', addr),
                    ('card_code', '=', card),
                    ('adres_type', '=', typ),
                ], limit=1)
                if existing:
                    # nếu record đang archived thì bật lại
                    if not existing.active and 'active' not in vals:
                        vals['active'] = True
    
                    existing.with_context(oms_skip_sync_partner=True).write(vals)
                    recs |= existing
                    continue
                
            to_create.append(vals)
    
        if to_create:
            try:
                recs |= super().create(to_create)
            except IntegrityError:
                self.env.cr.rollback()
                for vals in to_create:
                    addr = vals.get('address')
                    card = vals.get('card_code')
                    typ = vals.get('adres_type')
    
                    existing = AddressAll.search([
                        ('address', '=', addr),
                        ('card_code', '=', card),
                        ('adres_type', '=', typ),
                    ], limit=1) if addr and card and typ else False
    
                    if existing:
                        if not existing.active and 'active' not in vals:
                            vals['active'] = True
                        existing.with_context(oms_skip_sync_partner=True).write(vals)
                        recs |= existing
                        continue
                    
                    try:
                        recs |= super(OmsAddress, self).create([vals])
                    except IntegrityError:
                        self.env.cr.rollback()
                        existing = AddressAll.search([
                            ('address', '=', addr),
                            ('card_code', '=', card),
                            ('adres_type', '=', typ),
                        ], limit=1)
                        if existing:
                            if not existing.active and 'active' not in vals:
                                vals['active'] = True
                            existing.with_context(oms_skip_sync_partner=True).write(vals)
                            recs |= existing
                        else:
                            raise
                        
        linked_customers = recs.mapped('customer_id').filtered(lambda x: x)
        if linked_customers and not self.env.context.get("oms_skip_sync_partner"):
            linked_customers.action_sync_to_res_partner()
    
        return recs
    

    def write(self, vals):
        self._normalize_api_datetime_vals(vals)
        res = super().write(vals)

        if 'card_code' in vals or not all(self.mapped('customer_id')):
            self._auto_link_customer()

        linked_customers = self.mapped('customer_id').filtered(lambda x: x)
        if linked_customers and not self.env.context.get("oms_skip_sync_partner"):
            linked_customers.action_sync_to_res_partner()

        return res

    @api.depends('card_code', 'address', 'address3', 'street', 'county', 'city', 'country')
    def _compute_name(self):
        for rec in self:
            country_name = "Việt Nam" if rec.country and rec.country.upper() == "VN" else (rec.country or "")
            parts = [rec.street or "", rec.address3 or "", rec.county or "", rec.city or "", country_name]
            rec.name = ", ".join([p for p in parts if p]) or str(rec.id)
