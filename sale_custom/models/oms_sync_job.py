import requests
from datetime import datetime, timedelta
from urllib.parse import urlencode
from odoo import models, api, fields, _
from odoo.exceptions import UserError
import time
import json
import logging
_logger = logging.getLogger(__name__)

def convert_datetime_str(val):
    if not val:
        return False
    val = val.replace('T', ' ')
    if '.' in val:
        val = val.split('.')[0]
    return val

CUSTOMER_FIELD_MAPPING = {
    'card_code': 'CardCode',
    'card_name': 'CardName',
    'group_code': 'GroupCode',
    'group_name': 'GroupName',
    'lic_trad_num': 'LicTradNum',
    'credit_line': 'CreditLine',
    'debt_line': 'DebtLine',
    'slp_code': 'SlpCode',
    'currency': 'Currency',
    'territory': 'Territory',
    'descript': 'descript',
    'industry_c': 'IndustryC',
    'ind_name': 'IndName',
    'ten_xuat_hoa_don': 'TenXuatHoaDon',
    'u_business_unit': 'U_BusinessUnit',
    'u_debt_level': 'U_DebtLevel',
    'u_tele_note': 'U_TeleNote',
    'u_cs_note': 'U_CsNote',
    'valid_for': 'validFor',
    'ship_to_def': 'ShipToDef',
    'cntct_prsn': 'CntctPrsn',         # mới
    'pymnt_group': 'PymntGroup',       # mới
    'extra_days': 'ExtraDays',         # mới
    'group_num': 'GroupNum',           # mới
    'u_first_buy_date': 'U_FirstBuyDate',
    'u_last_buy_date': 'U_LastBuyDate',
}

ADDRESS_FIELD_MAPPING = {
    'address': 'Address',         # Mã địa chỉ
    'card_code': 'CardCode',
    'street': 'Street',
    'address3': 'Address3',
    'county': 'County',
    'city': 'City',
    'country': 'Country',
    'adres_type': 'AdresType',    # S, B, O
    'create_date_api': 'CreateDate',
}
CONTACT_FIELD_MAPPING = {
    'cntct_code': 'CntctCode',
    'card_code': 'CardCode',
    'name': 'Name',
    'first_name': 'FirstName',
    'position': 'Position',
    'title': 'Title',
    'cellolar': 'Cellolar',
    'tel1': 'Tel1',
    'tel2': 'Tel2',
    'create_date_api': 'CreateDate',
    'update_date_api': 'UpdateDate',
    'active': 'Active',   # mapping Y/N
}

PAYMENT_TERMS_FIELD_MAPPING = {
    'group_num': 'GroupNum',
    'pymnt_group': 'PymntGroup',
    'extra_days': 'ExtraDays',
    'bsline_date': 'BslineDate',
}

PRJINFO_FIELD_MAPPING = {
    'prj_code': 'PrjCode',
    'prj_name': 'PrjName',
    'u_card_code': 'U_CardCode',
    'card_name': 'CardName',
    'u_category': 'U_Category',
    'u_subcate': 'U_SubCate',
    'u_appendix_amount': 'U_AppendixAmount',
    'u_mkt_amount': 'U_MktAmount',
    'nv_tao': 'NVTao',
    'nv_cap_nhat': 'NVCapNhat',
    'ngay_tao': 'NgayTao',
    'ngay_cap_nhat': 'NgayCapNhat',
}
WAREHOUSE_FIELD_MAPPING = {
    'whs_code': 'WhsCode',
    'whs_name': 'WhsName',
    'store_id': 'StoreID',
    'store_name': 'StoreName',
    'u_whs_type': 'U_WhsType',
}

MARKETING_CAMPAIGN_FIELD_MAPPING = {
    'code': 'Code',
    'name': 'Name',
    'doc_entry': 'DocEntry',
    'canceled': 'Canceled',
    'object': 'Object',
    'log_inst': 'LogInst',
    'user_sign': 'UserSign',
    'transfered': 'Transfered',
    'create_date_api': 'CreateDate',
    'update_date_api': 'UpdateDate',
    'data_source': 'DataSource',
    'u_active': 'U_Active',
    'u_has_price_list': 'U_HasPriceList',
    'u_from_date': 'U_FromDate',
    'u_to_date': 'U_ToDate',
    'u_is_clear_stock': 'U_IsClearStock',
}

SALES_BLANKET_AGREEMENT_FIELD_MAPPING = {
    'bp_code': 'BpCode',
    'card_name': 'CardName',
    'abs_id': 'AbsID',
    'method': 'Method',
    'start_date': 'StartDate',
    'end_date': 'EndDate',
    'sign_date': 'SignDate',
    'num_at_card': 'NumAtCard',
    'bp_curr': 'BPCurr',
    'descript': 'Descript',
    'voucher_type': 'VoucherType',
    'status': 'Status',
    'u_slp_code': 'U_SlpCode',
    'slp_name': 'SlpName',
    'u_bplid': 'U_BPLid',
    'u_detail_entry': 'U_DetailEntry',
    'u_total_value': 'U_TotalValue',
    'u_check_limit_amount': 'U_CheckLimitAmount',
    'u_check_by': 'U_CheckBy',
    'u_debt_line': 'U_DebtLine',
    'store': 'Store',
    'group_num': 'GroupNum',
    'pymnt_group': 'PymntGroup',
}

SALES_USER_FIELD_MAPPING = {
    'slp_code': 'SlpCode',
    'name': 'SlpName',
    'branch': 'Branch',
    'business_area': 'BusinessArea',
}
OAUGRUPS_FIELD_MAPPING = {
    'user_name': 'UserName',
    'window_user': 'WindowUser',
    'authorization_group': 'AuthorizationGroup',
    'group_role': 'GroupRole',
    'slp_code': 'SlpCode',
    'authorization_type': 'AuthorizationType',
}
INVENTORY_FIELD_MAPPING = {
    'item_code':    'ItemCode',
    'whs_code':     'WhsCode',
    'whs_name':     'WhsName',
    'on_hand':      'OnHand',
    'is_commited':  'IsCommited',
    'on_order':     'OnOrder',
    'u_available':  'U_Available',
}

TRANSPORT_FIELD_MAPPING = {
    'trnsp_code': 'TrnspCode',
    'trnsp_name': 'TrnspName',
}

PRICELIST_FRAME_FIELD_MAPPING = {
    "api_id": "Id",
    "category_id": "CategoryId",
    "category_name": "CategoryName",
    "price_list_name": "PriceListName",
    "pm": "PM",
    "sup": "SUP",
    "sale": "Sale",
    "cc_tech": "CC_Tech",
}


class OmsSyncJob(models.Model):  
    _name = 'oms.sync.job'
    _description = 'OMS Sync Job'

    def _get_token(self, auth_url, username, password):
        try:
            res = requests.post(auth_url, json={"username": username, "password": password}, timeout=15)
            res.raise_for_status()
            data = res.json()
            token = data.get("token")
            if not token:
                raise UserError(_("Không lấy được token: %s") % data)
            return token
        except Exception as e:
            raise UserError(_('Lỗi xác thực: %s' % str(e)))

    def _get_data_with_token(self, api_url, token):
        try:
            headers = {"Authorization": f"Bearer {token}"}
            res = requests.get(api_url, headers=headers, timeout=30)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            raise UserError(_('Lỗi lấy dữ liệu (GET): %s' % str(e)))

    def _sync_external_list(self, username, password, api_url, model_name, unique_field, field_mapping):
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        token = self._get_token(auth_url, username, password)
        api_response = self._get_data_with_token(api_url, token)
        data_list = api_response.get('result', [])

        if not isinstance(data_list, list):
            raise UserError('API trả về dữ liệu không đúng format (result phải là list): %s' % api_response)

        Model = self.env[model_name]
        for rec in data_list:
            if not isinstance(rec, dict):
                continue
            vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in field_mapping.items()}
            vals['active'] = True
            domain = [(unique_field, '=', vals[unique_field])]
            record = Model.search(domain, limit=1)
            if record:
                record.write(vals)
            else:
                Model.create(vals)
        return True

    @api.model
    def sync_taxgroup(self, username, password):
        return self._sync_external_list(
            username,
            password,
            "https://api-dat.datgroup.com.vn/DATInsite/VATGroups",
            "oms.taxgroup",
            "code",
            {
                "code": "Code",
                "name": "Name",
                "rate": "Rate",
            }
        )

    @api.model
    def sync_product_group(self, username, password):
        res = self._sync_external_list(
            username,
            password,
            "https://api-dat.datgroup.com.vn/DATInsite/ItemGroups",
            "oms.product.group",
            "itms_grp_cod",
            {
                "itms_grp_cod": "ItmsGrpCod",
                "itms_grp_nam": "ItmsGrpNam",
                "u_cost_act1": "U_CostAct1",
                "u_product_line": "U_ProductLine",
                "u_product_family": "U_ProductFamily",
                "u_brand": "U_Brand",
            }
        )
        self.sync_oms_groups_to_odoo_category(self.env['oms.product.group'].search([]))
        return res

    @api.model
    def sync_product_item(self, username, password):
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        base_url = "https://api-dat.datgroup.com.vn/DATInsite/Items"
        token = self._get_token(auth_url, username, password)

        now = datetime.now()
        before = now - timedelta(minutes=10080)
        date_start = before.strftime('%Y-%m-%d %H:%M:%S.000')
        date_end = now.strftime('%Y-%m-%d %H:%M:%S.000')

        params = {
            "ModifiedDateStart": date_start,
            "ModifiedDateEnd": date_end
        }
        full_url = f"{base_url}?{urlencode(params)}"
        api_response = self._get_data_with_token(full_url, token)
        items = api_response.get('result', [])

        field_mapping = {
            "item_code": "ItemCode",
            "item_name": "ItemName",
            "frgn_name": "FrgnName",
            "valid_for": "validFor",
            "u_is_hidden": "U_IsHidden",
            "u_warr_time_vendor": "U_WarrTimeVendor",
            "u_warr_time": "U_WarrTime",
            "u_warr_time_dist": "U_WarrTimeDist",
            "u_min_stock": "U_MinStock",
            "u_max_stock": "U_MaxStock",
            "country_org": "CountryOrg",
            "lead_time": "LeadTime",
            "toleran_day": "ToleranDay",
            "u_item_power": "U_ItemPower",
            "u_business_unit": "U_BusinessUnit",
            "qry_group32": "QryGroup32",
            "qry_group33": "QryGroup33",
            "qry_group34": "QryGroup34",
            "qry_group35": "QryGroup35",
            "qry_group36": "QryGroup36",
            "qry_group37": "QryGroup37",
            "firm_code": "FirmCode",
            "man_ser_num": "ManSerNum",
            "man_btch_num": "ManBtchNum",
            "qry_group24": "QryGroup24",
            "prcrmnt_mtd": "PrcrmntMtd",
            "invntry_uom": "InvntryUom",
            "buy_unit_msr": "BuyUnitMsr",
            "sal_unit_msr": "SalUnitMsr",
            "qry_group29": "QryGroup29",
            "qry_group30": "QryGroup30",
            "itms_grp_cod": "ItmsGrpCod",
            "u_info_text": "U_InfoText",
            "u_vis_order": "U_VisOrder",
            "vat_group_pu": "VatGroupPu",
            "b_height1": "BHeight1",
            "b_width1": "BWidth1",
            "b_length1": "BLength1",
            "b_weight1": "BWeight1",
            "vat_gourp_sa": "VatGourpSa",
            "s_height1": "SHeight1",
            "s_width1": "SWidth1",
            "s_length1": "SLength1",
            "s_weight1": "SWeight1",
            "create_date": "CreateDate",
            "update_date": "UpdateDate",
        }

        ProductItem = self.env['oms.product.item']
        ProductGroup = self.env['oms.product.group']
        for rec in items:
            if not isinstance(rec, dict):
                continue
            vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in field_mapping.items()}
            vals['active'] = True
            vals['create_date'] = convert_datetime_str(vals.get('create_date'))
            vals['update_date'] = convert_datetime_str(vals.get('update_date'))
            grp = ProductGroup.search([('itms_grp_cod', '=', vals.get('itms_grp_cod'))], limit=1)
            vals['product_group_id'] = grp.id if grp else False

            domain = [("item_code", "=", vals["item_code"])]
            record = ProductItem.search(domain, limit=1)
            if record:
                record.write(vals)
            else:
                ProductItem.create(vals)
        self.sync_items_to_product_template(ProductItem.search([]))
        return True

    def sync_items_to_product_template(self, oms_items, batch_size=50):
        ProductTemplate = self.env['product.template']
        ProductUom = self.env['uom.uom']
        ProductCategory = self.env['product.category']
        AccountTax = self.env['account.tax']

        # Mapping code thuế -> % thuế
        TAX_CODE_TO_RATE = {
            "SVN1": 10.0,
            "SVN2": 5.0,
            "SVN4": 8.0,
            "SVN3": 0.0,
            "SVN5": 0.0,
            "SVN6": 0.0,
            "SVN7": 0.0,
        }

        # Tạo/lấy category mặc định (All)
        default_categ = ProductCategory.search([('name', '=', 'All')], limit=1)
        if not default_categ:
            default_categ = ProductCategory.create({'name': 'All'})

        # Hàm chia batch
        def batch_iterator(data_list, batch_size):
            for i in range(0, len(data_list), batch_size):
                yield data_list[i:i+batch_size]

        total = len(oms_items)
        processed = 0

        for batch in batch_iterator(oms_items, batch_size):
            for item in batch:
                # 1. Xác định UoM
                uom = self._find_or_create_uom(ProductUom, item.sal_unit_msr or item.invntry_uom or "Cái")
                if not uom:
                    _logger.warning(f"[OMS SYNC] Không xác định được UoM cho sản phẩm {item.item_code}")
                    continue

                # 2. Xác định Category
                categ = False
                if item.product_group_id:
                    categ = ProductCategory.search([('name', '=', item.product_group_id.display_name)], limit=1)
                    if not categ:
                        categ = ProductCategory.create({'name': item.product_group_id.display_name})
                if not categ:
                    categ = default_categ

                # 3. Map các trường bắt buộc
                name_val = item.item_name or item.item_code or "NoName"
                if not item.item_code or not name_val or not categ:
                    _logger.warning(f"[OMS SYNC] Dữ liệu thiếu hoặc không hợp lệ: code={item.item_code}, name={name_val}, categ={categ}")
                    continue

                # 4. Map thuế
                tax_code = (item.vat_gourp_sa or "").strip()
                tax = False
                if tax_code in TAX_CODE_TO_RATE:
                    rate = TAX_CODE_TO_RATE[tax_code]
                    tax = AccountTax.search([
                        ('amount', '=', rate),
                        ('amount_type', '=', 'percent'),
                        ('type_tax_use', '=', 'sale'),
                        ('active', '=', True)
                    ], limit=1)

                # 5. Sync hoặc update
                product = ProductTemplate.search([('default_code', '=', item.item_code)], limit=1)

                info = {}
                try:
                    if item.u_info_text:
                        info = json.loads(item.u_info_text).get("info", {})
                except Exception:
                    info = {}
                
                item_note = info.get("ItemNote") or ""
                
                vals = {
                    'default_code': item.item_code,
                    'name': name_val,
                    'description_sale': item_note,   # chỉ lấy ItemNote hoặc trống
                    'uom_id': uom.id,
                    'uom_po_id': uom.id,
                    'categ_id': categ.id,
                }
                if tax:
                    vals['taxes_id'] = [(6, 0, [tax.id])]
                if product:
                    product.write(vals)
                else:
                    ProductTemplate.create(vals)
                processed += 1

            # Commit mỗi batch và nghỉ để tránh timeout/lock DB
            self.env.cr.commit()
            _logger.info(f"[OMS SYNC] Đã xử lý {processed}/{total} sản phẩm (batch {batch_size})")
            time.sleep(1)

        _logger.info(f"[OMS SYNC] Hoàn thành đồng bộ {processed}/{total} sản phẩm.")
        return True


    def sync_oms_groups_to_odoo_category(self, oms_groups):
        ProductCategory = self.env['product.category']
        for group in oms_groups:
            category = ProductCategory.search([('name', '=', group.itms_grp_nam)], limit=1)
            if not category:
                category = ProductCategory.create({'name': group.itms_grp_nam})
            group.odoo_category_id = category.id
        return True

    def _find_or_create_uom(self, ProductUom, uom_name):
        uom = ProductUom.search([
            '|',
            ('name', '=', uom_name),
            ('name', 'ilike', uom_name)
        ], limit=1)
        if uom:
            return uom

        all_uoms = ProductUom.search([])
        for candidate in all_uoms:
            try:
                name_dict = json.loads(candidate.name) if isinstance(candidate.name, str) and candidate.name.startswith('{') else {}
            except Exception:
                name_dict = {}
            if (name_dict.get('vi_VN') == uom_name or name_dict.get('en_US') == uom_name):
                return candidate

        for fallback in ['Bộ', 'Set', 'Piece', 'Cái', 'Units', 'Unit']:
            uom = ProductUom.search([
                '|',
                ('name', '=', fallback),
                ('name', 'ilike', fallback)
            ], limit=1)
            if uom:
                return uom
            uom = ProductUom.search([
                ('name', 'ilike', json.dumps({'vi_VN': fallback}))
            ], limit=1)
            if uom:
                return uom

        en_name = "Set" if uom_name in ['Bộ', 'Set'] else "Piece"
        ProductUomCateg = self.env['uom.category']
        uom_categ = ProductUomCateg.search(['|', ('name', '=', 'Unit'), ('name', '=', 'Units')], limit=1)
        if not uom_categ:
            uom_categ = ProductUomCateg.create({'name': 'Units'})
        uom = ProductUom.create({
            'name': json.dumps({"en_US": en_name, "vi_VN": uom_name}),
            'category_id': uom_categ.id,
            'factor_inv': 1.0,
            'uom_type': 'reference',
            'active': True,
        })
        return uom

    def _map_customer_vals(self, rec):
        vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in CUSTOMER_FIELD_MAPPING.items()}
    
        vals['active'] = (rec.get('validFor') or 'Y') == 'Y'
        vals['create_date_api'] = convert_datetime_str(rec.get('CreateDate'))
        vals['update_date_api'] = convert_datetime_str(rec.get('UpdateDate'))
    
        # convert 2 field mới (lấy từ mapping)
        vals['u_first_buy_date'] = convert_datetime_str(vals.get('u_first_buy_date'))
        vals['u_last_buy_date']  = convert_datetime_str(vals.get('u_last_buy_date'))
    
        for f in ['group_code', 'slp_code', 'territory', 'extra_days', 'group_num']:
            if vals.get(f) is not None:
                vals[f] = str(vals[f])
    
        return vals


    def sync_customer_api(self, username, password, date_start=None, date_end=None):
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/CustomerAUT"
        token = self._get_token(auth_url, username, password)

        if not date_end:
            date_end = datetime.now()
        else:
            date_end = datetime.strptime(date_end, "%Y-%m-%d %H:%M:%S.000")
        if not date_start:
            date_start = date_end - timedelta(days=7000)
        else:
            date_start = datetime.strptime(date_start, "%Y-%m-%d %H:%M:%S.000")

        params = {
            "ModifiedDateStart": date_start.strftime('%Y-%m-%d %H:%M:%S.000'),
            "ModifiedDateEnd": date_end.strftime('%Y-%m-%d %H:%M:%S.000')
        }
        full_url = f"{api_url}?{urlencode(params)}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(full_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Customer: %s" % e))
        customers = data.get('result', [])
        for rec in customers:
            vals = self._map_customer_vals(rec)
            domain = [('card_code', '=', vals['card_code'])]
            customer = self.env['oms.customer'].search(domain, limit=1)
            if customer:
                customer.write(vals)
            else:
                self.env['oms.customer'].create(vals)
        self.map_to_res_partner()
        return True


    def map_to_res_partner(self):
        partner_obj = self.env['res.partner']
        PARTNER_MAPPING = {
            'ref': 'card_code',
            'name': 'card_name',
            'vat': 'lic_trad_num',
            'comment': 'descript',
            'active': 'active',
        }
        count_create = 0
        count_update = 0
        for customer in self.env['oms.customer'].search([]):
            if not customer.card_code or not customer.card_name:
                continue
            vals = {p: getattr(customer, c, False) for p, c in PARTNER_MAPPING.items()}
            partner = partner_obj.search([('ref', '=', customer.card_code)], limit=1)
            if partner:
                partner.write(vals)
                count_update += 1
            else:
                partner = partner_obj.create(vals)
                count_create += 1
            customer.res_partner_id = partner.id
        return True

    def sync_address_api(self, username, password):
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/AddressAccountAUT"
        
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Address: %s") % e)
        
        addresses = data.get("result", []) or []
        Address = self.env["oms.address"]
        
        created = 0
        updated = 0
        skipped = 0
        
        for rec in addresses:
            vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in ADDRESS_FIELD_MAPPING.items()}
        
            # thời gian tạo từ API
            vals["create_date_api"] = convert_datetime_str(rec.get("CreateDate"))
        
            # loại địa chỉ
            adres_type = (vals.get("adres_type") or rec.get("AdresType") or "").strip()
            if adres_type not in ("S", "B"):
                skipped += 1
                continue
                
            # active
            valid_for = (rec.get("validFor") or rec.get("ValidFor") or "").upper()
            vals["active"] = (adres_type in ("S", "B")) and (valid_for in ("", "Y"))
        
            # log thử giá trị khóa
            _logger.info(
                "SYNC ADDR rec: card_code=%s, address=%s, type=%s",
                vals.get("card_code"),
                vals.get("address"),
                adres_type,
            )
        
            domain = [
                ("address", "=", vals.get("address")),
                ("card_code", "=", vals.get("card_code")),
                ("adres_type", "=", adres_type),
            ]
        
            recs = Address.search(domain)
            if recs:
                recs.write(vals)
                updated += len(recs)
            else:
                Address.create(vals)
                created += 1
        
        _logger.info(
            "sync_address_api DONE: created=%s, updated=%s, skipped=%s (total API=%s)",
            created, updated, skipped, len(addresses),
        )
        return True
        
    
    def sync_contact_api(self, username, password):
        """
        Đồng bộ liên hệ khách hàng từ API DATInsite/ContactPerson về oms.contact.
        - Luôn cập nhật bản ghi đã tồn tại (kể cả khi Active = 'N').
        - Có thể tạo mới (nếu anh muốn chỉ tạo Y thì giữ điều kiện bên dưới).
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/ContactPersonAUT"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
    
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Contact: %s" % e))
    
        contacts = data.get("result", [])
        # Cho chạy qua cả record đã archive nếu model có field active
        ContactModel = self.env["oms.contact"].with_context(active_test=False)
    
        for rec in contacts:
            # Map field từ API -> Odoo theo CONTACT_FIELD_MAPPING
            vals = {
                odoo_field: rec.get(api_field)
                for odoo_field, api_field in CONTACT_FIELD_MAPPING.items()
            }
    
            # Ngày tạo / cập nhật từ API
            vals["create_date_api"] = convert_datetime_str(rec.get("CreateDate"))
            vals["update_date_api"] = convert_datetime_str(rec.get("UpdateDate"))
    
            # --- Map Active (Y/N) -> boolean active ---
            active_raw = (rec.get("Active") or "Y").strip().upper()  # "Y" / "N"
            is_active = active_raw == "Y"
            if "active" in ContactModel._fields:
                vals["active"] = is_active   # checkbox Hoạt động của Odoo
    
            domain = [
                ("cntct_code", "=", vals.get("cntct_code")),
                ("card_code", "=", vals.get("card_code")),
            ]
            contact = ContactModel.search(domain, limit=1)
    
            if contact:
                # Luôn cập nhật, kể cả khi Active = "N"
                contact.write(vals)
            else:
                # Nếu muốn luôn tạo, kể cả N, thì bỏ điều kiện if này
                if is_active:
                    ContactModel.create(vals)
    
        return True
            
    def _map_payment_terms_vals(self, rec):
        """Mapping dữ liệu 1 record PaymentTerms từ API về Odoo."""
        vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in PAYMENT_TERMS_FIELD_MAPPING.items()}
        pymnt_group = vals.get('pymnt_group') or rec.get('PymntGroup')
        if not pymnt_group or str(pymnt_group).strip() == '':
            pymnt_group = f"Payment Term {rec.get('GroupNum', 'Unknown')}"
        vals['name'] = pymnt_group
        return vals
    
    @api.model
    def sync_payment_terms_api(self, username, password):
        """
        Đồng bộ điều khoản thanh toán từ API DATInsite/PaymentTerms về model oms.payment.terms
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/PaymentTerms"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Payment Terms: %s" % e))
    
        payment_terms = data.get('result', [])
        PaymentTermsModel = self.env['oms.payment.terms']
        count_create = 0
        count_update = 0
        for rec in payment_terms:
            vals = self._map_payment_terms_vals(rec)
            # Siêu an toàn!
            if not vals.get('name') or str(vals.get('name')).strip() == '' or vals.get('name') is None:
                vals['name'] = f"Payment Term {vals.get('group_num', 'Unknown')}"
                _logger.warning(f"[OMS SYNC] Patch name for record {vals}")
            print(f"SYNC Payment Term vals: {vals}")
            domain = [('group_num', '=', vals.get('group_num'))]
            record = PaymentTermsModel.search(domain, limit=1)
            if record:
                record.write(vals)
                count_update += 1
            else:
                PaymentTermsModel.create(vals)
                count_create += 1
        _logger.info(f"[OMS SYNC] PaymentTerms: Đã tạo {count_create}, cập nhật {count_update} điều khoản thanh toán.")
        return True


    def _map_prjinfo_vals(self, rec):
        vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in PRJINFO_FIELD_MAPPING.items()}
        # name luôn lấy từ prj_name
        prj_name = vals.get('prj_name') or rec.get('PrjName')
        vals['name'] = prj_name or f"Project {vals.get('prj_code', '')}"
        # Xử lý giá trị None cho float
        vals['u_appendix_amount'] = vals.get('u_appendix_amount') or 0.0
        vals['u_mkt_amount'] = vals.get('u_mkt_amount') or 0.0
        # Chuyển đổi ngày nếu cần (nếu model dùng DateTime)
        vals['ngay_tao'] = convert_datetime_str(vals.get('ngay_tao'))
        vals['ngay_cap_nhat'] = convert_datetime_str(vals.get('ngay_cap_nhat'))
        return vals

    @api.model
    def sync_prjinfo_api(self, username, password):
        """
        Đồng bộ Dự án OMS từ API DATInsite/PrjInfo về model oms.prjinfo
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/PrjInfo"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu PrjInfo: %s" % e))

        prjinfo_list = data.get('result', [])
        PrjInfoModel = self.env['oms.prjinfo']
        count_create = 0
        count_update = 0
        for rec in prjinfo_list:
            vals = self._map_prjinfo_vals(rec)
            # Đảm bảo prj_code không rỗng
            if not vals.get('prj_code'):
                continue
            domain = [('prj_code', '=', vals.get('prj_code'))]
            record = PrjInfoModel.search(domain, limit=1)
            if record:
                record.write(vals)
                count_update += 1
            else:
                PrjInfoModel.create(vals)
                count_create += 1
        _logger.info(f"[OMS SYNC] PrjInfo: Đã tạo {count_create}, cập nhật {count_update} dự án OMS.")
        return True

    def sync_warehouse_api(self, username, password):
        """
        Đồng bộ kho từ API DATInsite/WareHouseInfo về model oms.warehouse
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/WareHouseInfo"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Warehouse: %s" % e))

        warehouses = data.get('result', [])
        Model = self.env['oms.warehouse']
        for rec in warehouses:
            vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in WAREHOUSE_FIELD_MAPPING.items()}
            vals['active'] = True
            domain = [('whs_code', '=', vals['whs_code'])]
            record = Model.search(domain, limit=1)
            if record:
                record.write(vals)
            else:
                Model.create(vals)
        return True
    def _map_marketing_campaign_vals(self, rec):
        vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in MARKETING_CAMPAIGN_FIELD_MAPPING.items()}
        # Convert date fields if needed
        vals['create_date_api'] = convert_datetime_str(vals.get('create_date_api'))
        vals['update_date_api'] = convert_datetime_str(vals.get('update_date_api'))
        vals['u_from_date'] = convert_datetime_str(vals.get('u_from_date'))
        vals['u_to_date'] = convert_datetime_str(vals.get('u_to_date'))
        return vals

    @api.model
    def sync_marketing_campaign_api(self, username, password):
        """
        Đồng bộ Chương trình Marketing từ API DATInsite/MarketingCampaign về model oms.marketing.campaign
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/MarketingCampaign"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Marketing Campaign: %s" % e))

        campaigns = data.get('result', [])
        CampaignModel = self.env['oms.marketing.campaign']
        count_create, count_update = 0, 0
        for rec in campaigns:
            vals = self._map_marketing_campaign_vals(rec)
            domain = [('code', '=', vals.get('code'))]
            campaign = CampaignModel.search(domain, limit=1)
            if campaign:
                campaign.write(vals)
                count_update += 1
            else:
                CampaignModel.create(vals)
                count_create += 1
        _logger.info(f"[OMS SYNC] MarketingCampaign: Đã tạo {count_create}, cập nhật {count_update} chiến dịch.")
        return True
    
    def _map_sales_blanket_agreement_vals(self, rec):
        vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in SALES_BLANKET_AGREEMENT_FIELD_MAPPING.items()}
        # Convert date fields
        for date_field in ['start_date', 'end_date', 'sign_date']:
            vals[date_field] = convert_datetime_str(vals.get(date_field))
        return vals
    
    @api.model
    def sync_sales_blanket_agreement_api(self, username, password):
        """
        Đồng bộ Hợp đồng bán hàng nguyên tắc từ API DATInsite/SalesBlanketAgreement về model oms.sales.blanket.agreement
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/SalesBlanketAgreement"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu SalesBlanketAgreement: %s" % e))
        results = data.get('result', [])
        Model = self.env['oms.sales.blanket.agreement']
        count_create = 0
        count_update = 0
        for rec in results:
            vals = self._map_sales_blanket_agreement_vals(rec)
            domain = [('abs_id', '=', vals.get('abs_id'))]
            agreement = Model.search(domain, limit=1)
            if agreement:
                agreement.write(vals)
                count_update += 1
            else:
                Model.create(vals)
                count_create += 1
        _logger.info(f"[OMS SYNC] SalesBlanketAgreement: Đã tạo {count_create}, cập nhật {count_update} hợp đồng bán hàng nguyên tắc.")
        return True

    def _map_sales_user_vals(self, rec):
        vals = {}
        for odoo_field, api_field in SALES_USER_FIELD_MAPPING.items():
            vals[odoo_field] = rec.get(api_field)
        vals['slp_code'] = str(vals.get('slp_code') or '')
        base = f"sales{vals['slp_code'] or vals.get('name', '')}".replace(' ', '').lower()
        email = f"{base}@datgroup.com.vn"
        vals['email'] = email
        vals['login'] = email          # <<< quan trọng
        return vals


    @api.model
    def sync_sales_user_api(self, username, password, batch_size=10, sleep_time=1):
        """
        Đồng bộ dữ liệu nhân viên sales về res.users.
        Chạy theo batch để tránh lock DB khi data lớn.
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/SalesMasterData"
        try:
            token = self._get_token(auth_url, username, password)
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            _logger.error("Không lấy được dữ liệu SalesMasterData: %s", e)
            raise UserError(_("Không lấy được dữ liệu SalesMasterData: %s") % e)

        users_data = data.get('result', [])
        if not users_data:
            raise UserError(_("Không có dữ liệu user trả về từ API"))

        UserModel = self.env['res.users']
        _logger.info("Số lượng user lấy về: %d", len(users_data))

        created_count, updated_count, skipped_count, error_count = 0, 0, 0, 0
        for idx, rec in enumerate(users_data):
            vals = self._map_sales_user_vals(rec)
            slp_code = vals['slp_code']
            if not slp_code or not vals['login']:
                _logger.warning("Bỏ qua user thiếu slp_code hoặc login: %s", rec)
                skipped_count += 1
                continue

            user = UserModel.search([('slp_code', '=', slp_code)], limit=1)
            try:
                if user:
                    _logger.info("Update user %s - %s", slp_code, vals['name'])
                    user.write(vals)
                    updated_count += 1
                else:
                    # Check trùng login
                    if UserModel.search([('login', '=', vals['login'])], limit=1):
                        _logger.warning("Login đã tồn tại, bỏ qua: %s", vals['login'])
                        skipped_count += 1
                        continue
                    _logger.info("Tạo mới user %s - %s", slp_code, vals['name'])
                    UserModel.create(vals)
                    created_count += 1
            except Exception as e:
                _logger.error("Lỗi tạo/cập nhật user: %s - %s", vals['login'], e)
                error_count += 1

            # Nghỉ 1 chút sau mỗi batch để tránh lock db
            if batch_size and idx > 0 and idx % batch_size == 0:
                time.sleep(sleep_time)

        summary = f"Tạo mới: {created_count}, Cập nhật: {updated_count}, Bỏ qua: {skipped_count}, Lỗi: {error_count}"
        _logger.info("Kết quả đồng bộ user sales: %s", summary)
        return summary

    def _map_oaugrups_user_vals(self, rec):
        base = (rec.get('UserName') or '').replace(' ', '').lower()
        if not base:
            base = f"user{rec.get('ID', '')}"
        email = f"{base}@datgroup.com.vn"
        vals = {
            'login': email,            # <<< quan trọng
            'email': email,
            'name': rec.get('UserName') or base,
            'slp_code': str(rec.get('SlpCode') or ''),
        }
        return vals

    @api.model
    def sync_oaugrups_users(self, username, password, batch_size=10, sleep_time=1):
        api_url = "https://api-dat.datgroup.com.vn/DATInsite/OAUGRUPS"
        try:
            token = self._get_token("https://auth.datgroup.com.vn/api/auth/login", username, password)
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            _logger.error("Không lấy được dữ liệu OAUGRUPS: %s", e)
            raise UserError(_("Không lấy được dữ liệu OAUGRUPS: %s") % e)

        users_data = data.get('result', [])
        if not users_data:
            raise UserError(_("Không có dữ liệu user trả về từ API OAUGRUPS"))

        UserModel = self.env['res.users']
        TeamModel = self.env['crm.team']
        _logger.info("Số lượng OAUGRUPS user lấy về: %d", len(users_data))

        for idx, rec in enumerate(users_data):
            vals = self._map_oaugrups_user_vals(rec)
            login = vals.get('login')
            if not login:
                _logger.warning("Bỏ qua user thiếu login: %s", rec)
                continue

            # Map AuthorizationGroup sang team sale
            team_name = rec.get('AuthorizationGroup') or 'Default Team'
            team = TeamModel.search([('name', '=', team_name)], limit=1)
            if not team:
                team = TeamModel.create({'name': team_name})
                _logger.info("Tạo mới sales team: %s", team_name)

            # Tìm hoặc tạo user Odoo
            user = UserModel.search([('login', '=', login)], limit=1)
            try:
                if user:
                    _logger.info("Update OAUGRUPS user %s - %s", login, vals['name'])
                    user.write(vals)
                else:
                    _logger.info("Tạo mới OAUGRUPS user %s - %s", login, vals['name'])
                    user = UserModel.create(vals)

                # *** SỬA Ở ĐÂY ***
                # Thêm user vào team (dùng member_ids)
                if user and user not in team.member_ids:
                    team.member_ids = [(4, user.id)]

                # Set team leader nếu có field user_id (và nếu GroupRole = Leader)
                if rec.get('GroupRole', '').lower() == 'leader' and hasattr(team, 'user_id'):
                    team.user_id = user.id
            except Exception as e:
                _logger.error("Lỗi tạo/cập nhật user OAUGRUPS: %s - %s", login, e)

            if batch_size and idx > 0 and idx % batch_size == 0:
                time.sleep(sleep_time)

        _logger.info("Đồng bộ user OAUGRUPS hoàn thành.")

    @api.model
    def sync_inventory_api(self, username, password, api_url=None, batch_size=300, sleep_time=0.5):
        """
        Đồng bộ tồn kho theo kho từ API DATInsite/ItemsWarehouse về model oms.inventory.
        Dữ liệu API (result) là list:
        {
            "ItemCode": "12029-00312",
            "WhsCode": "HCMVP201",
            "WhsName": "[HCMVP201] Kho hàng bán",
            "OnHand": 2.0,
            "IsCommited": 0.0,
            "OnOrder": 0.0,
            "U_Available": 2.0
        }
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if not api_url:
            api_url = ICP.get_param('oms.inventory.api_url', default='https://api-dat.datgroup.com.vn/DATInsite/ItemsWarehouse')

        # 1) Token
        token = self._get_token("https://auth.datgroup.com.vn/api/auth/login", username, password)
        headers = {"Authorization": f"Bearer {token}"}

        # 2) Gọi API
        try:
            resp = requests.get(api_url, headers=headers, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu tồn kho: %s") % e)

        data_list = payload.get('result', [])
        if not isinstance(data_list, list):
            raise UserError(_("API trả về dữ liệu không đúng định dạng (result phải là list)"))

        Inventory = self.env['oms.inventory']
        Product   = self.env['product.product']
        Warehouse = self.env['oms.warehouse']

        def _batch_iter(lst, size):
            for i in range(0, len(lst), size):
                yield lst[i:i+size]

        created = updated = skipped = 0
        for batch in _batch_iter(data_list, batch_size):
            for rec in batch:
                if not isinstance(rec, dict):
                    skipped += 1
                    continue
                
                vals = {o: rec.get(a) for o, a in INVENTORY_FIELD_MAPPING.items()}

                item_code = (vals.get('item_code') or '').strip()
                whs_code  = (vals.get('whs_code') or '').strip()
                if not item_code or not whs_code:
                    skipped += 1
                    continue
                
                # Liên kết product & warehouse
                prod = Product.search([('default_code', '=', item_code)], limit=1)
                if prod:
                    vals['product_id'] = prod.id

                whs = Warehouse.search([('whs_code', '=', whs_code)], limit=1)
                if whs:
                    vals['whs_id'] = whs.id

                # Upsert theo (item_code, whs_code)
                old = Inventory.search([('item_code', '=', item_code), ('whs_code', '=', whs_code)], limit=1)
                if old:
                    old.write(vals)
                    updated += 1
                else:
                    Inventory.create(vals)
                    created += 1

            self.env.cr.commit()
            time.sleep(sleep_time)

        _logger.info(f"[OMS SYNC] Inventory: created={created}, updated={updated}, skipped={skipped}")
        return f"Inventory synced. created={created}, updated={updated}, skipped={skipped}"

    @api.model
    def sync_transport_api(self, username, password):
        """
        Đồng bộ phương thức vận chuyển từ API DATInsite/Transports
        về model oms.transport
        """
        auth_url = "https://auth.datgroup.com.vn/api/auth/login"
        api_url = "https://api-dat.datgroup.com.vn/OMS/GetOSHP"
        token = self._get_token(auth_url, username, password)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise UserError(_("Không lấy được dữ liệu Transport: %s" % e))

        transports = data.get('result', [])
        Model = self.env['oms.transport']
        count_create, count_update = 0, 0

        for rec in transports:
            vals = {odoo_field: rec.get(api_field) for odoo_field, api_field in TRANSPORT_FIELD_MAPPING.items()}
            vals['active'] = True
            domain = [('trnsp_code', '=', vals['trnsp_code'])]
            record = Model.search(domain, limit=1)
            if record:
                record.write(vals)
                count_update += 1
            else:
                Model.create(vals)
                count_create += 1

        _logger.info(f"[OMS SYNC] Transport: Đã tạo {count_create}, cập nhật {count_update} phương thức vận chuyển.")
        return True

    @api.model
    def sync_pricelist_frame_api(self, username=None, password=None):
        """
        Đồng bộ OMS/PriceListFrame về model oms.pricelist.frame
        - Cho phép cron truyền username/password: model.sync_pricelist_frame_api('u','p')
        - Nếu System Parameters có cấu hình thì ưu tiên dùng (an toàn hơn)
        - Upsert theo api_id (Id)
        - Không overwrite min_qty/max_qty
        """
        ICP = self.env["ir.config_parameter"].sudo()
    
        # Ưu tiên cấu hình hệ thống; nếu không có thì dùng param từ cron
        username = ICP.get_param("oms.api.username") or username
        password = ICP.get_param("oms.api.password") or password
    
        api_url = ICP.get_param(
            "oms.pricelist_frame.api_url",
            default="https://api-dat.datgroup.com.vn/OMS/PriceListFrame"
        )
    
        if not username or not password:
            raise UserError(_("Thiếu username/password (cron) hoặc oms.api.username/oms.api.password (System Parameters)."))
    
        token = self._get_token("https://auth.datgroup.com.vn/api/auth/login", username, password)
        payload = self._get_data_with_token(api_url, token)
        data_list = payload.get("result", []) or []
        if not isinstance(data_list, list):
            raise UserError(_("API trả về sai định dạng: result phải là list."))
    
        Model = self.env["oms.pricelist.frame"].with_context(active_test=False)
    
        def _to_bool_x(v):
            return str(v or "").strip().lower() == "x"
    
        created = updated = skipped = 0
        for rec in data_list:
            if not isinstance(rec, dict):
                skipped += 1
                continue
            
            vals = {odoo_f: rec.get(api_f) for odoo_f, api_f in PRICELIST_FRAME_FIELD_MAPPING.items()}
    
            vals["pm"] = _to_bool_x(rec.get("PM"))
            vals["sup"] = _to_bool_x(rec.get("SUP"))
            vals["sale"] = _to_bool_x(rec.get("Sale"))
            vals["cc_tech"] = _to_bool_x(rec.get("CC_Tech"))
    
            api_id = vals.get("api_id")
            if not api_id:
                skipped += 1
                continue
            
            old = Model.search([("api_id", "=", api_id)], limit=1)
            if old:
                protected = {"min_qty", "max_qty"}
                write_vals = {k: v for k, v in vals.items() if k not in protected}
                old.write(write_vals)
                if not old.active:
                    old.active = True
                updated += 1
            else:
                Model.create({**vals, "active": True})
                created += 1
    
        _logger.info(
            "[OMS SYNC] PriceListFrame: created=%s updated=%s skipped=%s total_api=%s",
            created, updated, skipped, len(data_list)
        )
        return f"PriceListFrame synced. created={created}, updated={updated}, skipped={skipped}"

    @api.model
    def sync_item_price_aut_api(self, username=None, password=None):
        """
        Sync OMS/ItemPriceAUT rows into OMS price list lines.
        """
        ICP = self.env["ir.config_parameter"].sudo()

        username = ICP.get_param("oms.api.username") or username
        password = ICP.get_param("oms.api.password") or password
        api_url = ICP.get_param(
            "oms.item_price_aut.api_url",
            default="https://api-dat.datgroup.com.vn/OMS/ItemPriceAUT",
        )
        category_id = int(ICP.get_param("oms.item_price_aut.category_id", default="0") or 0)
        category_name = ICP.get_param("oms.item_price_aut.category_name", default="AUT") or "AUT"

        if not username or not password:
            raise UserError(_("Thieu username/password hoac System Parameters oms.api.username/oms.api.password."))

        token = self._get_token("https://auth.datgroup.com.vn/api/auth/login", username, password)
        payload = self._get_data_with_token(api_url, token)

        status = str(payload.get("status") or "").strip().upper()
        if status and status != "TRUE":
            raise UserError(_("API OMS/ItemPriceAUT tra ve that bai: %s") % (payload.get("msg") or payload))

        data_list = payload.get("result", []) or []
        if not isinstance(data_list, list):
            raise UserError(_("API OMS/ItemPriceAUT tra ve sai dinh dang: result phai la list."))

        Product = self.env["product.product"].sudo()
        Frame = self.env["oms.pricelist.frame"].sudo().with_context(active_test=False)
        Pricelist = self.env["oms.price.list"].sudo()
        Line = self.env["oms.price.list.line"].sudo()

        price_list_ids = sorted({
            int(rec.get("PriceList") or 0)
            for rec in data_list
            if isinstance(rec, dict) and rec.get("PriceList")
        })
        if len(price_list_ids) > 99:
            raise UserError(_("OMS/ItemPriceAUT co hon 99 PriceList, khong du price_type BG01-BG99."))

        price_type_by_api_id = {
            api_id: f"BG{idx:02d}"
            for idx, api_id in enumerate(price_list_ids, start=1)
        }
        frame_by_api_id = {}
        for api_id in price_list_ids:
            sample = next(
                (
                    rec for rec in data_list
                    if isinstance(rec, dict) and int(rec.get("PriceList") or 0) == api_id
                ),
                {},
            )
            list_name = (sample.get("ListName") or str(api_id)).strip()
            frame = Frame.search([("api_id", "=", api_id)], limit=1)
            if frame:
                vals = {"price_list_name": list_name or frame.price_list_name, "active": True}
                if not frame.category_id:
                    vals["category_id"] = category_id
                    vals["category_name"] = category_name
                frame.write(vals)
            else:
                frame = Frame.create({
                    "api_id": api_id,
                    "category_id": category_id,
                    "category_name": category_name,
                    "price_list_name": list_name,
                    "min_qty": 1.0,
                    "max_qty": 999999.0,
                    "active": True,
                })
            frame_by_api_id[api_id] = frame

        old_active = Pricelist.search([
            ("category_id", "=", category_id),
            ("category_name", "=", category_name),
            ("note", "=", "API ItemPriceAUT import"),
            ("active", "=", True),
        ])
        if old_active:
            old_active.write({"active": False})

        last = Pricelist.search([
            ("category_id", "=", category_id),
            ("category_name", "=", category_name),
            ("note", "=", "API ItemPriceAUT import"),
        ], order="version desc", limit=1)
        new_version = (last.version or 0) + 1 if last else 1
        today = fields.Date.today()

        pricelist = Pricelist.create({
            "name": f"{category_name} ItemPriceAUT - v{new_version}",
            "category_id": category_id,
            "category_name": category_name,
            "version": new_version,
            "from_date": today,
            "to_date": "2099-12-31",
            "active": True,
            "note": "API ItemPriceAUT import",
        })

        create_vals_by_key = {}
        skipped = 0
        skipped_missing_product = 0
        skipped_invalid_price = 0
        for rec in data_list:
            if not isinstance(rec, dict):
                skipped += 1
                continue

            item_code = (rec.get("ItemCode") or "").strip()
            api_id = int(rec.get("PriceList") or 0)
            try:
                price = float(rec.get("Price") or 0.0)
            except Exception:
                price = 0.0

            if not item_code or not api_id:
                skipped += 1
                continue
            if price <= 0:
                skipped_invalid_price += 1
                continue

            product = Product.search([("default_code", "=", item_code)], limit=1)
            if not product:
                skipped_missing_product += 1
                _logger.warning("[OMS SYNC] ItemPriceAUT skip missing product ItemCode=%s", item_code)
                continue

            frame = frame_by_api_id.get(api_id)
            line_vals = {
                "pricelist_id": pricelist.id,
                "item_id": product.id,
                "min_qty": int(frame.min_qty or 1) if frame else 1,
                "max_qty": int(frame.max_qty or 999999) if frame else 999999,
                "from_date": today,
                "to_date": "2099-12-31",
                "price_type": price_type_by_api_id.get(api_id, "BG01"),
                "price_frame_id": frame.id if frame else False,
                "price_frame_name": frame.price_list_name if frame else (rec.get("ListName") or ""),
                "price": price,
                "is_invoice": False,
            }
            line_key = (
                line_vals["pricelist_id"],
                line_vals["item_id"],
                line_vals["min_qty"],
                line_vals["max_qty"],
                line_vals["price_type"],
                line_vals["from_date"],
                line_vals["to_date"],
            )
            create_vals_by_key[line_key] = line_vals

        create_vals = list(create_vals_by_key.values())
        if create_vals:
            Line.create(create_vals)

        _logger.info(
            "[OMS SYNC] ItemPriceAUT: pricelist_id=%s lines=%s skipped=%s missing_product=%s invalid_price=%s total_api=%s",
            pricelist.id, len(create_vals), skipped, skipped_missing_product, skipped_invalid_price, len(data_list),
        )
        return (
            "ItemPriceAUT synced. "
            f"pricelist_id={pricelist.id}, lines={len(create_vals)}, skipped={skipped}, "
            f"missing_product={skipped_missing_product}, invalid_price={skipped_invalid_price}"
        )
    
