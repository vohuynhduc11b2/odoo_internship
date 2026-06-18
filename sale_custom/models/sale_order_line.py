# Part of Odoo. See LICENSE file for full copyright and licensing details.

from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.osv import expression
from odoo.tools import float_compare, float_is_zero, format_date, groupby
from odoo.tools.translate import _
from datetime import date
import logging
import math
import requests
import logging
_logger = logging.getLogger(__name__)

API_AUTH_URL = "https://auth.datgroup.com.vn/api/auth/login"
API_STOCK_URL = "https://api-dat.datgroup.com.vn/DATInsite/CheckStockPriceByWhs"
API_COMMISSION_URL = "https://api-dat.datgroup.com.vn/DATInsite/CalcProvisionalCommission"
API_USERNAME = "trungtq"
API_PASSWORD = "Trung@2025"


class SaleOrderLine(models.Model):
    _name = 'sale_custom.order.line'
    _inherit = 'analytic.mixin'
    _description = "Sales Order Line"
    _rec_names_search = ['name', 'order_id.name']
    _order = 'order_id, sequence, id'
    _check_company_auto = True

    _sql_constraints = [
        ('accountable_required_fields',
            "CHECK(display_type IS NOT NULL OR is_downpayment OR (product_id IS NOT NULL AND product_uom IS NOT NULL))",
            "Missing required fields on accountable sale order line."),
        ('non_accountable_null_fields',
            "CHECK(display_type IS NULL OR (product_id IS NULL AND price_unit = 0 AND product_uom_qty = 0 AND product_uom IS NULL AND customer_lead = 0))",
            "Forbidden values on non-accountable sale order line"),
    ]

    # Fields are ordered according by tech & business logics
    # and computed fields are defined after their dependencies.
    # This reduces execution stacks depth when precomputing fields
    # on record creation (and is also a good ordering logic imho)

    order_id = fields.Many2one(
        comodel_name='sale_custom.order',
        string="Order Reference",
        required=True, ondelete='cascade', index=True, copy=False)
    sequence = fields.Integer(string="Sequence", default=10)

    # Order-related fields
    company_id = fields.Many2one(
        related='order_id.company_id',
        store=True, index=True, precompute=True)
    currency_id = fields.Many2one(
        related='order_id.currency_id',
        depends=['order_id.currency_id'],
        store=True, precompute=True)
    order_partner_id = fields.Many2one(
        related='order_id.partner_id',
        string="Customer",
        store=True, index=True, precompute=True)
    salesman_id = fields.Many2one(
        related='order_id.user_id',
        string="Salesperson",
        store=True, precompute=True)
    state = fields.Selection(
        related='order_id.state',
        string="Order Status",
        copy=False, store=True, precompute=True)
    tax_country_id = fields.Many2one(related='order_id.tax_country_id')

    # Fields specifying custom line logic
    display_type = fields.Selection(
        selection=[
            ('line_section', "Section"),
            ('line_note', "Note"),
        ],
        default=False)
    is_configurable_product = fields.Boolean(
        string="Is the product configurable?",
        related='product_template_id.has_configurable_attributes',
        depends=['product_id'])
    is_downpayment = fields.Boolean(
        string="Is a down payment",
        help="Down payments are made when creating invoices from a sales order."
            " They are not copied when duplicating a sales order.")
    is_expense = fields.Boolean(
        string="Is expense",
        help="Is true if the sales order line comes from an expense or a vendor bills")

    # Generic configuration fields
    product_id = fields.Many2one(
        comodel_name='product.product',
        string="Product",
        change_default=True, ondelete='restrict', index='btree_not_null',
        domain="[('sale_ok', '=', True)]")
    product_template_id = fields.Many2one(
        string="Product Template",
        comodel_name='product.template',
        compute='_compute_product_template_id',
        readonly=False,
        search='_search_product_template_id',
        # previously related='product_id.product_tmpl_id'
        # not anymore since the field must be considered editable for product configurator logic
        # without modifying the related product_id when updated.
        domain=[('sale_ok', '=', True)])
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id', depends=['product_id'])

    product_template_attribute_value_ids = fields.Many2many(
        related='product_id.product_template_attribute_value_ids',
        depends=['product_id'])
    product_custom_attribute_value_ids = fields.One2many(
        comodel_name='product.attribute.custom.value', inverse_name='sale_order_line_id',
        string="Custom Values",
        compute='_compute_custom_attribute_values',
        store=True, readonly=False, precompute=True, copy=True)
    # M2M holding the values of product.attribute with create_variant field set to 'no_variant'
    # It allows keeping track of the extra_price associated to those attribute values and add them to the SO line description
    product_no_variant_attribute_value_ids = fields.Many2many(
        comodel_name='product.template.attribute.value',
        string="Extra Values",
        compute='_compute_no_variant_attribute_values',
        store=True, readonly=False, precompute=True, ondelete='restrict')
    is_product_archived = fields.Boolean(compute="_compute_is_product_archived")    
    provisional_commission = fields.Monetary("Hoa hồng tạm tính",compute='_compute_provisional_commission', store=True, readonly=True, precompute=True, copy=True)
    stock_value = fields.Float("Giá vốn kho", readonly=True)
 

    name = fields.Text(
        string="Description",
        compute='_compute_name',
        store=True, readonly=False, precompute=True)

    product_uom_qty = fields.Float(
        string="Quantity",
        compute='_compute_product_uom_qty',
        digits='Product Unit of Measure', default=1.0,
        store=True, readonly=False, required=True, precompute=True)
    product_uom = fields.Many2one(
        comodel_name='uom.uom',
        string="Unit of Measure",
        compute='_compute_product_uom',
        store=True, readonly=False, precompute=True, ondelete='restrict',
        domain="[('category_id', '=', product_uom_category_id)]")
    linked_line_id = fields.Many2one(
        string="Linked Order Line",
        comodel_name='sale_custom.order.line',
        ondelete='cascade',
        domain="[('order_id', '=', order_id)]",
        copy=False,
        index=True,
    )
    linked_line_ids = fields.One2many(
        string="Linked Order Lines", comodel_name='sale_custom.order.line', inverse_name='linked_line_id',
    )
    # Uniquely identifies this sale order line before the record is saved in the DB, i.e. before the
    # record has an `id`.
    virtual_id = fields.Char()
    # Links this sale order line to another sale order line, via its `virtual_id`.
    linked_virtual_id = fields.Char()
    # Local storage of this sale order line's selected combo items, iff this is a combo product
    # line.
    selected_combo_items = fields.Char(store=False)
    combo_item_id = fields.Many2one(comodel_name='product.combo.item')

    # Pricing fields
    tax_id = fields.Many2many(
        comodel_name='account.tax',
        string="Taxes",
        readonly=True,
        compute='_compute_tax_id',
        store=True, precompute=True,
        context={'active_test': False},
        check_company=True)

    # Tech field caching pricelist rule used for price & discount computation
    pricelist_item_id = fields.Many2one(
        comodel_name='product.pricelist.item',
        compute='_compute_pricelist_item_id')

    price_unit = fields.Float(
        string="Unit Price",
        compute='_compute_price_unit',
        digits='Product Price',
        store=True, readonly=False, required=True, precompute=True)
    technical_price_unit = fields.Float()
    
    discount = fields.Float(
        string="Discount (%)",
        compute='_compute_discount',
        digits='Discount',
        copy=True, store=True)

    price_subtotal = fields.Monetary(
        string="Subtotal",
        compute='_compute_amount',
        store=True, precompute=True)
    price_tax = fields.Float(
        string="Total Tax",
        compute='_compute_amount',
        store=True, precompute=True)
    price_total = fields.Monetary(
        string="Total",
        compute='_compute_amount',
        store=True, precompute=True)
    price_reduce_taxexcl = fields.Monetary(
        string="Price Reduce Tax excl",
        compute='_compute_price_reduce_taxexcl',
        store=True, precompute=True)
    price_reduce_taxinc = fields.Monetary(
        string="Price Reduce Tax incl",
        compute='_compute_price_reduce_taxinc',
        store=True, precompute=True)

    oms_base_price = fields.Monetary(
        "Giá ban đầu",
        compute='_compute_oms_base_price',
        store=True,
        readonly=False
    )

    @api.depends('product_id', 'product_uom_qty', 'order_id.partner_id', 'order_id.date_order')
    def _compute_oms_base_price(self):
        for line in self:
            if line.is_gift or line.is_bundle:
                line.oms_base_price = 0.0
            elif line.product_id and line.order_id and line.order_id.partner_id:
                line.oms_base_price = line.get_oms_base_price_cached()
            else:
                line.oms_base_price = 0.0


    # Logistics/Delivery fields
    product_packaging_id = fields.Many2one(
        comodel_name='product.packaging',
        string="Packaging",
        compute='_compute_product_packaging_id',
        store=True, readonly=False, precompute=True,
        domain="[('sales', '=', True), ('product_id','=',product_id)]",
        check_company=True)
    product_packaging_qty = fields.Float(
        string="Packaging Quantity",
        compute='_compute_product_packaging_qty',
        store=True, readonly=False, precompute=True)

    customer_lead = fields.Float(
        string="Lead Time",
        compute='_compute_customer_lead',
        store=True, readonly=False, required=True, precompute=True,
        help="Number of days between the order confirmation and the shipping of the products to the customer")

    qty_delivered_method = fields.Selection(
        selection=[
            ('manual', "Manual"),
            ('analytic', "Analytic From Expenses"),
        ],
        string="Method to update delivered qty",
        compute='_compute_qty_delivered_method',
        store=True, precompute=True,
        help="According to product configuration, the delivered quantity can be automatically computed by mechanism:\n"
             "  - Manual: the quantity is set manually on the line\n"
             "  - Analytic From expenses: the quantity is the quantity sum from posted expenses\n"
             "  - Timesheet: the quantity is the sum of hours recorded on tasks linked to this sale line\n"
             "  - Stock Moves: the quantity comes from confirmed pickings\n")
    qty_delivered = fields.Float(
        string="Delivery Quantity",
        compute='_compute_qty_delivered',
        default=0.0,
        digits='Product Unit of Measure',
        store=True, readonly=False, copy=False)

    # Analytic & Invoicing fields
    qty_invoiced = fields.Float(
        string="Invoiced Quantity",
        compute='_compute_qty_invoiced',
        digits='Product Unit of Measure',
        store=True)
    qty_invoiced_posted = fields.Float(
        string="Invoiced Quantity (posted)",
        compute='_compute_qty_invoiced_posted',
        digits='Product Unit of Measure')
    qty_to_invoice = fields.Float(
        string="Quantity To Invoice",
        compute='_compute_qty_to_invoice',
        digits='Product Unit of Measure',
        store=True)

    analytic_line_ids = fields.One2many(
        comodel_name='account.analytic.line', inverse_name='so_line',
        string="Analytic lines")

    invoice_lines = fields.Many2many(
        comodel_name='account.move.line',
        relation='sale_custom_order_line_invoice_rel', column1='order_line_id', column2='invoice_line_id',
        string="Invoice Lines",
        copy=False)
    invoice_status = fields.Selection(
        selection=[
            ('upselling', "Upselling Opportunity"),
            ('invoiced', "Fully Invoiced"),
            ('to invoice', "To Invoice"),
            ('no', "Nothing to Invoice"),
        ],
        string="Invoice Status",
        compute='_compute_invoice_status',
        store=True)

    untaxed_amount_invoiced = fields.Monetary(
        string="Untaxed Invoiced Amount",
        compute='_compute_untaxed_amount_invoiced',
        store=True)
    amount_invoiced = fields.Monetary(
        string="Invoiced Amount",
        compute='_compute_amount_invoiced',
        compute_sudo=True,  # ensure same access as `untaxed_amount_invoiced`
    )
    untaxed_amount_to_invoice = fields.Monetary(
        string="Untaxed Amount To Invoice",
        compute='_compute_untaxed_amount_to_invoice',
        store=True)
    amount_to_invoice = fields.Monetary(
        string="Un-invoiced Balance",
        compute='_compute_amount_to_invoice',
        compute_sudo=True,  # ensure same access as `untaxed_amount_to_invoice`
    )

    # Technical computed fields for UX purposes (hide/make fields readonly, ...)
    product_type = fields.Selection(related='product_id.type', depends=['product_id'])
    service_tracking = fields.Selection(related='product_id.service_tracking', depends=['product_id'])
    product_updatable = fields.Boolean(
        string="Can Edit Product",
        compute='_compute_product_updatable')
    product_uom_readonly = fields.Boolean(
        compute='_compute_product_uom_readonly')
    tax_calculation_rounding_method = fields.Selection(
        related='company_id.tax_calculation_rounding_method',
        string='Tax calculation rounding method', readonly=True)
    company_price_include = fields.Selection(related="company_id.account_price_include")


    promotion_ids = fields.Many2many(
        'oms.promotion', 'order_line_promotion_rel', 'order_line_id', 'promotion_id',
        string="Khuyến mãi áp dụng"
    )
    promotion_selected_ids = fields.Many2many(
        'oms.promotion', 'order_line_selected_promotion_rel', 'order_line_id', 'promotion_id',
        string="Khuyến mãi đã chọn", help="Lưu lại khuyến mãi thực sự đã áp cho dòng này"
    )

    has_promotion = fields.Boolean(
        string="Có KM",
        compute='_compute_has_promotion',
        store=False
    )
    last_oms_base_key = fields.Char(copy=False)
    def _is_manual_price(self):
        self.ensure_one()
        # Manual price changes from Sales should be preserved even on website orders.
        return float_compare(
            self.price_unit,
            self.technical_price_unit,
            precision_rounding=self.currency_id.rounding
        ) != 0

    def _make_oms_base_key(self):
        """Ghép key theo các yếu tố ảnh hưởng đến base price."""
        self.ensure_one()
        pid = self.product_id.id or 0
        qty = self.product_uom_qty or 0
        partner = self.order_id.partner_id.id or 0
        od = (self.order_id.date_order and self.order_id.date_order.date()) or fields.Date.today()
        return f"{pid}:{qty}:{partner}:{od}"

    @api.depends('promotion_ids', 'promotion_selected_ids')
    def _compute_has_promotion(self):
        """
        Đánh dấu dòng này có khuyến mãi hay không (dựa vào 2 field many2many)
        """
        for rec in self:
            has_promo = bool(rec.promotion_ids or rec.promotion_selected_ids)
            rec.has_promotion = has_promo

            # Lấy tên khuyến mãi cho đẹp log
            promo_list = [p.display_name for p in rec.promotion_ids]
            selected_list = [p.display_name for p in rec.promotion_selected_ids]

            _logger.info(
                "[OMS][HAS_PROMO] line_id=%s | promotion_ids=%s | promotion_selected_ids=%s | has_promotion=%s",
                rec.id,
                promo_list,
                selected_list,
                has_promo
            )
    fixed_discount = fields.Float(string='Giảm cố định', default=0.0)
    is_gift = fields.Boolean("Dòng sản phẩm tặng", default=False)
    is_bundle = fields.Boolean("Dòng sản phẩm mua kèm", default=False)
    price_base_delta_percent = fields.Float("Chênh lệch giá (%)")
    price_unit_color = fields.Selection(
        [('green', 'Tăng'), ('red', 'Giảm')],
        compute='_compute_price_unit_color',
        store=False
    )
    base_price_and_delta = fields.Html(
        compute='_compute_base_price_and_delta', string='Bảng giá (+%)'
    )

    # Gắn combo quà tặng lên dòng line và dòng order line quà tặng
    gift_combo_id = fields.Many2one('oms.gift.combo', string="Combo Quà tặng", index=True)
    gift_combo_sequence = fields.Integer(string="Thứ tự chọn combo", default=0)

    # --- fields ---
    whs_code = fields.Char(related='order_id.WhsCode.whs_code', store=True)

    stock_onhand    = fields.Float(string='On Hand',    compute='_compute_stock_by_whs', digits=(16, 1))
    stock_committed = fields.Float(string='Committed',  compute='_compute_stock_by_whs', digits=(16, 1))
    stock_onorder   = fields.Float(string='On Order',   compute='_compute_stock_by_whs', digits=(16, 1))
    stock_available = fields.Float(string='Available',  compute='_compute_stock_by_whs', digits=(16, 1))

    stock_compact = fields.Char(
        string="Tồn kho",
        compute="_compute_stock_compact",
        readonly=True,
        store=False,
    )
    stock_compact_more = fields.Char(
        string="Tồn chi nhánh khác",
        compute="_compute_stock_compact",
        readonly=True,
        store=False,
    )

    # Map chi nhánh -> prefix whs_code để quét các kho đuôi 01
    _BRANCH_PREFIX = {'HCM': 'HCM', 'CTH': 'CTH', 'HNI': 'HNI'}
    _BRANCH_LABEL  = {'HCM': 'HCM', 'CTH': 'CTH', 'HNI': 'HNI'}

    is_prepared = fields.Boolean(
        string="Đã chuẩn bị hàng",
        default=False,
        tracking=True,
        store=True,
    )
    eta_done = fields.Datetime(
        string="ETA hoàn thành",
        tracking=True,
        store=True,
    )
    is_prepared_1 = fields.Boolean(
        string="Sẵn sàng?",
        compute='_compute_mirrors',
        store=False,
        readonly=True,
    )
    eta_done_1 = fields.Datetime(
        string="ETA hoàn thành",
        compute='_compute_mirrors',
        store=False,
        readonly=True,
    )

    @api.depends('is_prepared', 'eta_done')
    def _compute_mirrors(self):
        for rec in self:
            rec.is_prepared_1 = bool(rec.is_prepared)
            rec.eta_done_1 = rec.eta_done

    # Một số hệ thống có phê duyệt (approval_state), một số không → làm hàm check
    def _order_is_pending_approval_domain(self):
        """Domain để lọc các đơn CHƯA duyệt/xác nhận (còn ở draft/sent/đang chờ).
           Không tính sale/done/cancel.
        """
        # approval_state is not stored, so it cannot be used in SQL domains/read_group.
        return [('order_id.state', 'not in', ['sale', 'done', 'cancel'])]

    def _pending_prepared_qty(self, product_id, whs_code):
        """Tổng qty đang prepared=1 ở CÁC đơn chưa duyệt, theo product + kho."""
        Line = self.env['sale_custom.order.line'].sudo()
        domain = [
            ('is_prepared', '=', True),
            ('display_type', '=', False),
            ('product_id', '=', product_id.id),
            ('whs_code', '=', whs_code or False),
        ] + self._order_is_pending_approval_domain()
    
        # Nhanh và gọn bằng read_group
        data = Line.read_group(domain, ['product_uom_qty:sum'], [])
    
        if not data:
            return 0.0
    
        rec = data[0]
        # Bình thường alias sẽ là 'product_uom_qty_sum', nhưng để chắc ăn thì fallback thêm:
        qty = rec.get('product_uom_qty_sum')
        if qty is None:
            qty = rec.get('product_uom_qty')  # fallback nếu Odoo trả về key này
    
        return float(qty or 0.0)

    @api.depends(
        'whs_code', 'product_id', 'product_uom_qty',
        'order_id.state', 'order_id.approval_state',
        'is_prepared'
    )
    def _compute_stock_by_whs(self):
        for line in self:
            # --- 1) Lấy tồn kho gốc theo kho (như bạn đang làm) ---
            # Ví dụ:
            Inv = line.env['oms.inventory']
            inv = Inv.search([
                ('item_code', '=', getattr(line, 'item_code', line.product_id.default_code)),
                ('whs_code', '=', line.whs_code or False),
            ], limit=1)

            on_hand   = float(inv.on_hand or 0.0)
            committed = float(inv.is_commited or 0.0)
            on_order  = float(inv.on_order or 0.0)
            base_available = float(inv.u_available) if inv.u_available is not None else (on_hand - committed + on_order)

            # --- 2) Trừ lượng prepared đang CHỜ DUYỆT ở các đơn khác (và cả chính đơn này) ---
            pending_prepared = line._pending_prepared_qty(line.product_id, line.whs_code)

            # Nếu bạn muốn loại trừ CHÍNH ĐƠN HIỆN TẠI khỏi số trừ tạm,
            # thì bớt đi qty của line hiện tại khi is_prepared:
            # if line.is_prepared:
            #     pending_prepared = max(0.0, pending_prepared - float(line.product_uom_qty or 0.0))

            effective_available = base_available - pending_prepared

            # --- 3) Gán lại 4 field hiển thị ---
            line.stock_onhand    = round(on_hand, 1)
            line.stock_committed = round(committed, 1)
            line.stock_onorder   = round(on_order, 1)
            line.stock_available = round(effective_available, 1)
    # ===== Helpers =====
    def _fmt(self, v):
        f = float(v or 0.0)
        return f"{int(f)}" if f.is_integer() else f"{f:.1f}"

    def _user_branch(self):
        # nếu user.branch không có, mặc định HCM
        return (getattr(self.env.user, 'branch', '') or '').upper() or 'HCM'

    def _sum_branch_tail01(self, item_code, branch_code):
        """
        Cộng dồn tồn/khả dụng cho *mọi* kho của chi nhánh có đuôi '01'.
        Ví dụ: HCM%01 khớp HCMVP201, HCMWH001, ...
        """
        prefix = self._BRANCH_PREFIX.get(branch_code, branch_code)
        Inv = self.env['oms.inventory']
        recs = Inv.search([
            ('item_code', '=', item_code),
            ('whs_code', 'ilike', f'{prefix}%01'),
        ])

        tot_onhand = tot_committed = tot_onorder = tot_available = 0.0
        for r in recs:
            on_hand     = r.on_hand or 0.0
            is_commited = r.is_commited or 0.0
            on_order    = r.on_order or 0.0
            available   = r.u_available if r.u_available is not None else (on_hand - is_commited + on_order)

            tot_onhand    += on_hand
            tot_committed += is_commited
            tot_onorder   += on_order
            tot_available += available

        return {
            'onhand': tot_onhand,
            'committed': tot_committed,
            'onorder': tot_onorder,
            'available': tot_available,
        }

    @api.depends('product_id', 'whs_code', 'stock_onhand', 'stock_committed', 'stock_onorder', 'stock_available')
    def _compute_stock_compact(self):
        """
        Hiển thị tồn/khả dụng theo *từng chi nhánh* = tổng các kho đuôi '01' của chi nhánh đó.
        - stock_compact: chi nhánh chính (theo user.branch), KHÔNG kèm nhãn chi nhánh.
        - stock_compact_more: 2 chi nhánh còn lại, CÓ kèm nhãn viết tắt (HCM/HNI/CTH) ở đầu.
        """
        BRANCHES = ['HCM', 'HNI', 'CTH']  # Thứ tự mong muốn khi show
    
        for line in self:
            line.stock_compact = ''
            line.stock_compact_more = ''
    
            # Không có product -> bỏ qua
            if not line.product_id:
                continue
            
            item_code = line.product_id.default_code or ''
            if not item_code:
                continue
            
            # Chi nhánh ưu tiên: từ user.branch (fallback HCM)
            main_branch = (line._user_branch() or 'HCM').upper()
            if main_branch not in BRANCHES:
                main_branch = 'HCM'
    
            other_branches = [b for b in BRANCHES if b != main_branch]
    
            # --- Phần chi nhánh chính (không kèm nhãn) ---
            s_main = line._sum_branch_tail01(item_code, main_branch) or {}
            onhand_main = line._fmt(s_main.get('onhand', 0))
            avail_main = line._fmt(s_main.get('available', 0))
            line.stock_compact = f"Tồn {onhand_main} | Khả dụng {avail_main}"
    
            # --- Phần các chi nhánh khác (kèm nhãn viết tắt) ---
            more_parts = []
            for b in other_branches:
                s = line._sum_branch_tail01(item_code, b) or {}
                onhand = line._fmt(s.get('onhand', 0))
                available = line._fmt(s.get('available', 0))
                more_parts.append(f"{b}: Tồn {onhand} | Khả dụng {available}")
    
            line.stock_compact_more = " ; ".join(more_parts) if more_parts else ''



    @api.depends('price_unit', 'oms_base_price')
    def _compute_price_unit_color(self):
        for rec in self:
            if rec.price_unit and rec.oms_base_price:
                if rec.price_unit > rec.oms_base_price:
                    rec.price_unit_color = 'green'
                elif rec.price_unit < rec.oms_base_price:
                    rec.price_unit_color = 'red'
                else:
                    rec.price_unit_color = False
            else:
                rec.price_unit_color = False   

    @api.depends('price_unit', 'oms_base_price')
    def _compute_base_price_and_delta(self):
        def vn_currency(val):
            return '{:,.0f}'.format(val).replace(',', '.')
        for rec in self:       
            base = rec.oms_base_price or 0
            price = rec.price_unit or 0
            val = 0.0
            if base > 0:
                val = 100 * (price - base) / base
            rec.price_base_delta_percent = val
            display = vn_currency(base)
            # Vẫn cho hiển thị phần trăm trên giá nếu muốn, còn không thì bỏ luôn đoạn dưới
            if val > 0.01:
                display += ' <span style="color:green;font-size:0.75em">(+%.1f%%)</span>' % val
            elif val < -0.01:
                display += ' <span style="color:red;font-size:0.75em">(%.1f%%)</span>' % val
            rec.base_price_and_delta = display
    #=== COMPUTE METHODS ===#

    @api.depends('order_partner_id', 'order_id', 'product_id')
    def _compute_display_name(self):
        name_per_id = self._additional_name_per_id()
        for so_line in self.sudo():
            product = so_line.product_id
            parts = (so_line.name or "").split('\n', 2)
            # if there's a description, use the first line (skipping the product name)
            description = (parts[1:2] and parts[1]) or product.name if product else parts[0]
            name = f"{so_line.order_id.name} - {description}"
            additional_name = name_per_id.get(so_line.id)
            if additional_name:
                name = f'{name} {additional_name}'
            so_line.display_name = name

    @api.depends('product_id')
    def _compute_product_template_id(self): 
        for line in self:
            line.product_template_id = line.product_id.product_tmpl_id

    def _search_product_template_id(self, operator, value):
        return [('product_id.product_tmpl_id', operator, value)]

    @api.depends('product_id')
    def _compute_is_product_archived(self):
        for line in self:
            line.is_product_archived = line.product_id and not line.product_id.active

    @api.depends('product_id')
    def _compute_custom_attribute_values(self):
        for line in self:
            if not line.product_id: 
                line.product_custom_attribute_value_ids = False
                continue
            if not line.product_custom_attribute_value_ids:
                continue
            valid_values = line.product_id.product_tmpl_id.valid_product_template_attribute_line_ids.product_template_value_ids
            # remove the is_custom values that don't belong to this template
            for pacv in line.product_custom_attribute_value_ids:
                if pacv.custom_product_template_attribute_value_id not in valid_values:
                    line.product_custom_attribute_value_ids -= pacv

    @api.depends('product_id')
    def _compute_no_variant_attribute_values(self):
        for line in self:
            if not line.product_id:
                line.product_no_variant_attribute_value_ids = False
                continue
            if not line.product_no_variant_attribute_value_ids:
                continue
            valid_values = line.product_id.product_tmpl_id.valid_product_template_attribute_line_ids.product_template_value_ids
            # remove the no_variant attributes that don't belong to this template
            for ptav in line.product_no_variant_attribute_value_ids:
                if ptav._origin not in valid_values:
                    line.product_no_variant_attribute_value_ids -= ptav

    @api.depends('product_id', 'linked_line_id', 'linked_line_ids')
    def _compute_name(self):
        for line in self:
            if not line.product_id and not line.is_downpayment:
                continue

            lang = line.order_id._get_lang()
            if lang != self.env.lang:
                line = line.with_context(lang=lang)

            if line.product_id:
                line.name = line._get_sale_order_line_multiline_description_sale()  
                continue

            if line.is_downpayment:
                line.name = line._get_downpayment_description()

    def _get_sale_order_line_multiline_description_sale(self):
        """
        Trả về mô tả cho dòng bán hàng KHÔNG gồm mã hàng và tên SP.
        Chỉ dùng description_sale + phần mô tả thuộc tính (nếu có).
        Bỏ luôn các dòng 'Option for ...' và 'Option: ...'.
        """
        self.ensure_one()

        # Lấy ngôn ngữ của đơn hàng để mô tả đúng locale
        lang = self.order_id._get_lang() if self.order_id else self.env.lang
        product = self.product_id.with_context(lang=lang)

        # Chỉ lấy phần mô tả bán hàng, không lấy display_name (tên/mã)
        sale_desc = (product.description_sale
                     or product.product_tmpl_id.with_context(lang=lang).description_sale
                     or " ")

        description = (sale_desc or " ").strip()

        # Cộng thêm mô tả thuộc tính (nếu có), KHÔNG thêm 'Option for/Option'
        variants_txt = self._get_sale_order_line_multiline_description_variants()
        if variants_txt:
            description = (description + "\n" if description else "") + variants_txt.lstrip("\n")

        return description

    def _get_sale_order_line_multiline_description_variants(self):
        """When using no_variant attributes or is_custom values, the product
        itself is not sufficient to create the description: we need to add
        information about those special attributes and values.

        :return: the description related to special variant attributes/values
        :rtype: string
        """
        no_variant_ptavs = self.product_no_variant_attribute_value_ids._origin.filtered(
            # Only describe the attributes where a choice was made by the customer
            lambda ptav: ptav.display_type == 'multi' or ptav.attribute_line_id.value_count > 1
        )
        if not self.product_custom_attribute_value_ids and not no_variant_ptavs:
            return ""

        name = ""

        custom_ptavs = self.product_custom_attribute_value_ids.custom_product_template_attribute_value_id
        multi_ptavs = no_variant_ptavs.filtered(lambda ptav: ptav.display_type == 'multi').sorted()

        # display the no_variant attributes, except those that are also
        # displayed by a custom (avoid duplicate description)
        for ptav in (no_variant_ptavs - multi_ptavs - custom_ptavs):
            name += "\n" + ptav.display_name

        # display the selected values per attribute on a single for a multi checkbox
        for pta, ptavs in groupby(multi_ptavs, lambda ptav: ptav.attribute_id):
            name += "\n" + _(
                "%(attribute)s: %(values)s",
                attribute=pta.name,
                values=", ".join(ptav.name for ptav in ptavs)
            )

        # Sort the values according to _order settings, because it doesn't work for virtual records in onchange
        sorted_custom_ptav = self.product_custom_attribute_value_ids.custom_product_template_attribute_value_id.sorted()
        for patv in sorted_custom_ptav:
            pacv = self.product_custom_attribute_value_ids.filtered(lambda pcav: pcav.custom_product_template_attribute_value_id == patv)
            name += "\n" + pacv.display_name

        return name

    def _get_downpayment_description(self):
        self.ensure_one()
        if self.display_type:
            return _("Down Payments")

        dp_state = self._get_downpayment_state()
        name = _("Down Payment")
        if dp_state == 'draft':
            name = _(
                "Down Payment: %(date)s (Draft)",
                date=format_date(self.env, self.create_date.date()),
            )
        elif dp_state == 'cancel':
            name = _("Down Payment (Cancelled)")
        else:
            invoice = self._get_invoice_lines().filtered(
                lambda aml: aml.quantity >= 0
            ).move_id.filtered(lambda move: move.move_type == 'out_invoice')
            if len(invoice) == 1 and invoice.payment_reference and invoice.invoice_date:
                name = _(
                    "Down Payment (ref: %(reference)s on %(date)s)",
                    reference=invoice.payment_reference,
                    date=format_date(self.env, invoice.invoice_date),
                )

        return name

    @api.depends('display_type', 'product_id', 'product_packaging_qty')
    def _compute_product_uom_qty(self):
        for line in self:
            if line.display_type:
                line.product_uom_qty = 0.0
                continue

            if not line.product_packaging_id:
                continue
            packaging_uom = line.product_packaging_id.product_uom_id
            qty_per_packaging = line.product_packaging_id.qty
            product_uom_qty = packaging_uom._compute_quantity(
                line.product_packaging_qty * qty_per_packaging, line.product_uom)
            if float_compare(product_uom_qty, line.product_uom_qty, precision_rounding=line.product_uom.rounding) != 0:
                line.product_uom_qty = product_uom_qty

    @api.depends('product_id')
    def _compute_product_uom(self):
        for line in self:
            if not line.product_uom or (line.product_id.uom_id.id != line.product_uom.id):
                line.product_uom = line.product_id.uom_id

    @api.depends('product_id')
    def _compute_provisional_commission(self):
        for line in self:
            if getattr(line, 'is_gift', False) or getattr(line, 'is_bundle', False):
                line.provisional_commission = 0.0
                line.stock_value = 0.0
                continue
            commission = 0.0
            stock_value = 0.0
            qty = line.product_uom_qty or 1.0
            # Nếu thiếu product hoặc số lượng hoặc không có order -> bỏ qua
            if not line.product_id or not line.product_id.default_code or not line.order_id:
                line.provisional_commission = 0.0
                line.stock_value = 0.0
                continue

            try:
                token = line._get_token()
                # Gọi lấy stock_value trước
                stock_payload = {
                    "ItemCode": line.product_id.default_code,
                    "WhsCode": "HCMVP201",  # Hoặc lấy từ order_id nếu cần
                }
                stock_response = line._post_data_with_token(API_STOCK_URL, token, stock_payload)
                if isinstance(stock_response, dict):
                    result = stock_response.get("result") or {}
                    if isinstance(result, dict):
                        stock_value = result.get("AvgPrice", 0.0)

                # Gọi API hoa hồng
                commission_payload = {
                    "BU": "AUT",
                    "VoucherTypeID": "1310",
                    "ItemCode": line.product_id.default_code,
                    "MktCampaign": "",
                    "Upselling": "",
                    "DocDate": fields.Date.today().strftime("%Y-%m-%d"),
                    "RealSale": line.price_reduce_taxexcl or 0.0,
                    "StockValue": stock_value,
                    "DocType": "B",
                    "ProjectCode": ""
                }
                commission_response = line._post_data_with_token(API_COMMISSION_URL, token, commission_payload)
                if isinstance(commission_response, dict) and commission_response.get("status") == "TRUE":
                    result = commission_response.get("result") or []
                    if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
                        per_unit_commission = result[0].get("ComissionAmountByCM", 0.0)
                        commission = per_unit_commission * qty  # <-- nhân số lượng ở đây!

                # Gán vào field
                line.provisional_commission = commission
                line.stock_value = stock_value
            except Exception as e:
                # Nếu có lỗi thì cho = 0
                line.provisional_commission = 0.0
                line.stock_value = 0.0



    @api.depends('product_id', 'company_id')
    def _compute_tax_id(self):
        lines_by_company = defaultdict(lambda: self.env['sale_custom.order.line'])
        cached_taxes = {}
        for line in self:
            if line.product_type == 'combo':
                line.tax_id = False
                continue
            lines_by_company[line.company_id] += line
        for company, lines in lines_by_company.items():
            for line in lines.with_company(company):
                taxes = None
                if line.product_id:
                    taxes = line.product_id.taxes_id._filter_taxes_by_company(company)
                if not line.product_id or not taxes:
                    # Nothing to map
                    line.tax_id = False
                    continue
                fiscal_position = line.order_id.fiscal_position_id
                cache_key = (fiscal_position.id, company.id, tuple(taxes.ids))
                cache_key += line._get_custom_compute_tax_cache_key()
                if cache_key in cached_taxes:
                    result = cached_taxes[cache_key]
                else:
                    result = fiscal_position.map_tax(taxes)
                    cached_taxes[cache_key] = result
                # If company_id is set, always filter taxes by the company
                line.tax_id = result

    def _get_custom_compute_tax_cache_key(self):
        """Hook method to be able to set/get cached taxes while computing them"""
        return tuple()

    @api.depends('product_id', 'product_uom', 'product_uom_qty')
    def _compute_pricelist_item_id(self):
        for line in self:
            if not line.product_id or line.display_type or not line.order_id.pricelist_id:
                line.pricelist_item_id = False
            else:
                line.pricelist_item_id = line.order_id.pricelist_id._get_product_rule(
                    line.product_id,
                    quantity=line.product_uom_qty or 1.0,
                    uom=line.product_uom,
                    date=line._get_order_date(),
                )

    @api.depends('product_id', 'product_uom', 'product_uom_qty')
    def _compute_price_unit(self):
        for line in self:
            # Don't compute the price for deleted lines.
            if not line.order_id:
                continue
            # Website quote flow: Sales can replace contact-price/OMS placeholder
            # prices on draft website orders. Later checkout/approval flows may call
            # price recomputation with force_price_recomputation=True; that must not
            # overwrite a price Sales has already quoted.
            if (
                line.env.context.get('force_price_recomputation')
                and getattr(line.order_id, 'website_id', False)
                and not line.display_type
                and line.product_id
                and not getattr(line, 'is_delivery', False)
                and not getattr(line, 'is_gift', False)
                and not getattr(line, 'is_bundle', False)
                and float(line.price_unit or 0.0) > 1.0
                and float(line.technical_price_unit or 0.0) > 1.0
                and line.technical_price_unit != line.price_unit
            ):
                continue
            # check if the price has been manually set or there is already invoiced amount.
            # if so, the price shouldn't change as it might have been manually edited.
            if (
                (line.technical_price_unit != line.price_unit and not line.env.context.get('force_price_recomputation'))
                or line.qty_invoiced > 0
                or (line.product_id.expense_policy == 'cost' and line.is_expense)
            ):
                continue
            line = line.with_context(sale_write_from_compute=True)
            if not line.product_uom or not line.product_id:
                line.price_unit = 0.0
                line.technical_price_unit = 0.0
            else:
                line = line.with_company(line.company_id)
                price = line._get_display_price()
                line.price_unit = line.product_id._get_tax_included_unit_price_from_price(
                    price,
                    product_taxes=line.product_id.taxes_id.filtered(
                        lambda tax: tax.company_id == line.env.company
                    ),
                    fiscal_position=line.order_id.fiscal_position_id,
                )
                line.technical_price_unit = line.price_unit

    def _get_order_date(self):
        self.ensure_one()
        return self.order_id.date_order

    def _get_display_price(self):
        """Compute the displayed unit price for a given line.

        Overridden in custom flows:
        * where the price is not specified by the pricelist
        * where the discount is not specified by the pricelist

        Note: self.ensure_one()
        """
        self.ensure_one()

        if self.product_type == 'combo':
            return 0  # The display price of a combo line should always be 0.
        if self.combo_item_id:
            return self._get_combo_item_display_price()
        return self._get_display_price_ignore_combo()

    def _get_display_price_ignore_combo(self):
        """ This helper method allows to compute the display price of a AUT, while ignoring combo logic. """
        self.ensure_one()
    
        # ======================================================
        # UC FIX: Website/Online order -> lấy giá theo tier OMS
        # ======================================================
        is_online = (
            bool(self.env.context.get("website_id"))
            or bool(getattr(self.order_id, "website_id", False))
            or (getattr(self.order_id, "channel", "") == "online")
        )
        if is_online:
            # Gift/Bundle luôn 0
            if getattr(self, "is_gift", False) or getattr(self, "is_bundle", False) or self.display_type:
                return 0.0
    
            try:
                if hasattr(self.order_id, "_uc_compute_tier_price_unit"):
                    return float(self.order_id._uc_compute_tier_price_unit(
                        self.product_id,
                        self.product_uom_qty or 1.0,
                    ) or 0.0)
                if hasattr(self.product_id, "uc_get_tier_price_unit"):
                    price = self.product_id.uc_get_tier_price_unit(
                        order=self.order_id,
                        qty=self.product_uom_qty or 1.0,
                        pricelist=self.order_id.pricelist_id,
                        partner=self.order_id.partner_id,
                    )
                    if price is not None:
                        return float(price or 0.0)
            except Exception:
                # Website tier errors fall back to the regular pricelist path.
                _logger.exception("[UC_TIER] _get_display_price_ignore_combo website tier failed line=%s", self.id)
    
        # ====== Original pricelist logic (giữ nguyên) ======
        pricelist_price = self._get_pricelist_price()
    
        if not self.pricelist_item_id._show_discount():
            return pricelist_price
    
        base_price = self._get_pricelist_price_before_discount()
        return max(base_price, pricelist_price)

    def _get_pricelist_price(self):
        """Compute the price given by the pricelist for the given line information.

        :return: the product sales price in the order currency (without taxes)
        :rtype: float
        """
        self.ensure_one()
        self.product_id.ensure_one()

        price = self.pricelist_item_id._compute_price(
            product=self.product_id.with_context(**self._get_product_price_context()),
            quantity=self.product_uom_qty or 1.0,
            uom=self.product_uom,
            date=self._get_order_date(),
            currency=self.currency_id,
        )

        return price

    def _get_product_price_context(self):
        """Gives the context for product price computation.

        :return: additional context to consider extra prices from attributes in the base product price.
        :rtype: dict
        """
        self.ensure_one()
        return self.product_id._get_product_price_context(
            self.product_no_variant_attribute_value_ids,
        )

    def _get_pricelist_price_context(self):
        """DO NOT USE in new code, this contextual logic should be dropped or heavily refactored soon"""
        self.ensure_one()
        return {
            'pricelist': self.order_id.pricelist_id.id,
            'uom': self.product_uom.id,
            'quantity': self.product_uom_qty,
            'date': self._get_order_date(),
        }

    def _get_pricelist_price_before_discount(self):
        """Compute the price used as base for the pricelist price computation.

        :return: the product sales price in the order currency (without taxes)
        :rtype: float
        """
        self.ensure_one()
        self.product_id.ensure_one()

        return self.pricelist_item_id._compute_price_before_discount(
            product=self.product_id.with_context(**self._get_product_price_context()),
            quantity=self.product_uom_qty or 1.0,
            uom=self.product_uom,
            date=self._get_order_date(),
            currency=self.currency_id,
        )

    def _get_combo_item_display_price(self):
        """ Compute the display price of this AUT's combo item.

        A combo item's price is a fraction of its combo product's price (i.e. the product of type
        `combo` which is referenced in this AUT's linked line). It is independent of the combo
        item's product (i.e. the product referenced in this AUT). The combo's `base_price` will be
        used to prorate the price of this combo with respect to the other combos in the combo
        product.

        Note: this method will throw if this AUT has no combo item or no linked combo product.
        """
        self.ensure_one()

        # Compute the combo product's price.
        combo_line = self._get_linked_line()
        combo_product_price = combo_line._get_display_price_ignore_combo()
        # Compute the combos' base prices.
        combo_base_prices = {
            combo_id: combo_id.currency_id._convert(
                from_amount=combo_id.base_price,
                to_currency=self.currency_id,
                company=self.company_id,
                date=self.order_id.date_order,
            ) for combo_id in combo_line.product_template_id.combo_ids
        }
        total_combo_base_price = sum(combo_base_prices.values())
        # Compute the prorated combo prices.
        combo_prices = {
            combo_id: self.currency_id.round(
                # Don't divide by total_combo_base_price if it's 0. This will make the prorating
                # wrong, but the delta will be fixed by combo_price_delta below.
                base_price * combo_product_price / (total_combo_base_price or 1)
            )
            for (combo_id, base_price) in combo_base_prices.items()
        }
        # Compute the delta between the combo product's price and the sum of its combo prices.
        # Ideally, this should be 0, but division in python isn't perfect, so we may need to adjust
        # the combo prices to make the delta 0.
        combo_price_delta = combo_product_price - sum(combo_prices.values())
        if combo_price_delta:
            combo_prices[combo_line.product_template_id.combo_ids[-1]] += combo_price_delta
        # Add the extra price of this combo item, as well as the extra prices of any `no_variant`
        # attributes to the combo price.
        return (
            combo_prices[self.combo_item_id.combo_id]
            + self.combo_item_id.extra_price
            + self.product_id._get_no_variant_attributes_price_extra(
                self.product_no_variant_attribute_value_ids
            )
        )

    @api.depends('product_id', 'product_uom', 'product_uom_qty')
    def _compute_discount(self):
        discount_enabled = self.env['product.pricelist.item']._is_discount_feature_enabled()
        for line in self:
            if not line.product_id or line.display_type:
                line.discount = 0.0
                continue

            # ==============================
            # UC FIX: KHÔNG ghi đè discount nếu line đang có KM (promotion/fixed)
            # hoặc order đang áp auto purchase.
            # ==============================
            if (
                line.env.context.get("skip_pricelist_discount_compute")
                or bool(getattr(line, "promotion_ids", False) and line.promotion_ids)
                or float(getattr(line, "fixed_discount", 0.0) or 0.0) > 0.0
                or float(getattr(line.order_id, "website_auto_purchase_pct", 0.0) or 0.0) > 0.0
                or bool(getattr(line.order_id, "website_auto_purchase_promo_id", False))
            ):
                # giữ nguyên giá trị hiện có (đã do engine KM set)
                line.discount = float(line.discount or 0.0)
                continue

            # ===== Original logic theo pricelist (giữ như cũ) =====
            if not (line.order_id.pricelist_id and discount_enabled):
                line.discount = 0.0
                continue

            line.discount = 0.0

            if not line.pricelist_item_id._show_discount():
                continue

            line = line.with_company(line.company_id)
            pricelist_price = line._get_pricelist_price()
            base_price = line._get_pricelist_price_before_discount()

            if base_price != 0:
                discount = (base_price - pricelist_price) / base_price * 100
                if (discount > 0 and base_price > 0) or (discount < 0 and base_price < 0):
                    line.discount = discount

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        """ Convert the current record to a dictionary in order to use the generic taxes computation method
        defined on account.tax.

        :return: A python dictionary.
        """
        self.ensure_one()
        return self.env['account.tax']._prepare_base_line_for_taxes_computation(
            self,
            **{
                'tax_ids': self.tax_id,
                'quantity': self.product_uom_qty,
                'partner_id': self.order_id.partner_id,
                'currency_id': self.order_id.currency_id or self.order_id.company_id.currency_id,
                'rate': self.order_id.currency_rate,
                **kwargs,
            },
        )
    uc_auto_purchase_pct = fields.Float(
        string="UC Auto Purchase Pct",
        copy=False,
        default=0.0,
        help="Phần trăm KM mua lần đầu/hai (website auto purchase), tách riêng khỏi discount promotions."
    )
    @api.depends('product_uom_qty', 'discount', 'uc_auto_purchase_pct', 'price_unit', 'tax_id')
    def _compute_amount(self):
        for line in self:
            # ghép discount theo kiểu nhân hiệu lực để không cộng dồn sai
            d_other = float(line.discount or 0.0)
            d_auto  = float(getattr(line, "uc_auto_purchase_pct", 0.0) or 0.0)
            eff = 100.0 * (1.0 - (1.0 - d_other/100.0) * (1.0 - d_auto/100.0))
            eff = 0.0 if eff < 0 else (100.0 if eff > 100.0 else eff)

            base_line = line._prepare_base_line_for_taxes_computation(discount=eff)
            self.env['account.tax']._add_tax_details_in_base_line(base_line, line.company_id)
            line.price_subtotal = base_line['tax_details']['raw_total_excluded_currency']
            line.price_total = base_line['tax_details']['raw_total_included_currency']
            line.price_tax = line.price_total - line.price_subtotal

    @api.depends('price_subtotal', 'product_uom_qty')
    def _compute_price_reduce_taxexcl(self):
        for line in self:
            line.price_reduce_taxexcl = line.price_subtotal / line.product_uom_qty if line.product_uom_qty else 0.0

    @api.depends('price_total', 'product_uom_qty')
    def _compute_price_reduce_taxinc(self):
        for line in self:
            line.price_reduce_taxinc = line.price_total / line.product_uom_qty if line.product_uom_qty else 0.0

    @api.depends('product_id', 'product_uom_qty', 'product_uom')
    def _compute_product_packaging_id(self):
        for line in self:
            # remove packaging if not match the product
            if line.product_packaging_id.product_id != line.product_id:
                line.product_packaging_id = False
            # suggest biggest suitable packaging matching the SO's company
            if line.product_id and line.product_uom_qty and line.product_uom:
                suggested_packaging = line.product_id.packaging_ids\
                        .filtered(lambda p: p.sales and (p.product_id.company_id <= p.company_id <= line.company_id))\
                        ._find_suitable_product_packaging(line.product_uom_qty, line.product_uom)
                line.product_packaging_id = suggested_packaging or line.product_packaging_id

    @api.depends('product_packaging_id', 'product_uom', 'product_uom_qty')
    def _compute_product_packaging_qty(self):
        self.product_packaging_qty = 0
        for line in self:
            if not line.product_packaging_id:
                continue
            line.product_packaging_qty = line.product_packaging_id._compute_qty(line.product_uom_qty, line.product_uom)

    # This computed default is necessary to have a clean computation inheritance
    # (cf sale_stock) instead of simply removing the default and specifying
    # the compute attribute & method in sale_stock.
    def _compute_customer_lead(self):
        self.customer_lead = 0.0

    @api.depends('is_expense')
    def _compute_qty_delivered_method(self):
        """ Sale module compute delivered qty for product [('type', 'in', ['consu']), ('service_type', '=', 'manual')]
                - consu + expense_policy : analytic (sum of analytic unit_amount)
                - consu + no expense_policy : manual (set manually on AUT)
                - service (+ service_type='manual', the only available option) : manual

            This is true when only sale is installed: sale_stock redifine the behavior for 'consu' type,
            and sale_timesheet implements the behavior of 'service' + service_type=timesheet.
        """
        for line in self:
            if line.is_expense:
                line.qty_delivered_method = 'analytic'
            else:  # service and consu
                line.qty_delivered_method = 'manual'

    @api.depends(
        'qty_delivered_method',
        'analytic_line_ids.so_line',
        'analytic_line_ids.unit_amount',
        'analytic_line_ids.product_uom_id')
    def _compute_qty_delivered(self):
        """ This method compute the delivered quantity of the SO lines: it covers the case provide by sale module, aka
            expense/vendor bills (sum of unit_amount of AAL), and manual case.
            This method should be overridden to provide other way to automatically compute delivered qty. Overrides should
            take their concerned so lines, compute and set the `qty_delivered` field, and call super with the remaining
            records.
        """
        # compute for analytic lines
        lines_by_analytic = self.filtered(lambda sol: sol.qty_delivered_method == 'analytic')
        mapping = lines_by_analytic._get_delivered_quantity_by_analytic([('amount', '<=', 0.0)])
        for so_line in lines_by_analytic:
            so_line.qty_delivered = mapping.get(so_line.id or so_line._origin.id, 0.0)

    def _get_downpayment_state(self):
        self.ensure_one()

        if self.display_type:
            return ''

        invoice_lines = self._get_invoice_lines()
        if all(line.parent_state == 'draft' for line in invoice_lines):
            return 'draft'
        if all(line.parent_state == 'cancel' for line in invoice_lines):
            return 'cancel'

        return ''

    def _get_delivered_quantity_by_analytic(self, additional_domain):
        """ Compute and write the delivered quantity of current SO lines, based on their related
            analytic lines.
            :param additional_domain: domain to restrict AAL to include in computation (required since timesheet is an AAL with a project ...)
        """
        result = defaultdict(float)

        # avoid recomputation if no SO lines concerned
        if not self:
            return result

        # group analytic lines by product uom and so line
        domain = expression.AND([[('so_line', 'in', self.ids)], additional_domain])
        data = self.env['account.analytic.line']._read_group(
            domain,
            ['product_uom_id', 'so_line'],
            ['unit_amount:sum', 'move_line_id:count_distinct', '__count'],
        )

        # convert uom and sum all unit_amount of analytic lines to get the delivered qty of SO lines
        for uom, so_line, unit_amount_sum, move_line_id_count_distinct, count in data:
            if not uom:
                continue
            # avoid counting unit_amount twice when dealing with multiple analytic lines on the same move line
            if move_line_id_count_distinct == 1 and count > 1:
                qty = unit_amount_sum / count
            else:
                qty = unit_amount_sum
            if so_line.product_uom.category_id == uom.category_id:
                qty = uom._compute_quantity(qty, so_line.product_uom, rounding_method='HALF-UP')
            result[so_line.id] += qty

        return result

    @api.depends('invoice_lines.move_id.state', 'invoice_lines.quantity')
    def _compute_qty_invoiced(self):
        """
        Compute the quantity invoiced. If case of a refund, the quantity invoiced is decreased. Note
        that this is the case only if the refund is generated from the SO and that is intentional: if
        a refund made would automatically decrease the invoiced quantity, then there is a risk of reinvoicing
        it automatically, which may not be wanted at all. That's why the refund has to be created from the SO
        """
        for line in self:
            qty_invoiced = 0.0
            for invoice_line in line._get_invoice_lines():
                if invoice_line.move_id.state != 'cancel' or invoice_line.move_id.payment_state == 'invoicing_legacy':
                    if invoice_line.move_id.move_type == 'out_invoice':
                        qty_invoiced += invoice_line.product_uom_id._compute_quantity(invoice_line.quantity, line.product_uom)
                    elif invoice_line.move_id.move_type == 'out_refund':
                        qty_invoiced -= invoice_line.product_uom_id._compute_quantity(invoice_line.quantity, line.product_uom)
            line.qty_invoiced = qty_invoiced

    @api.depends('invoice_lines.move_id.state', 'invoice_lines.quantity')
    def _compute_qty_invoiced_posted(self):
        """
        This method is almost identical to '_compute_qty_invoiced()'. The only difference lies in the fact that
        for accounting purposes, we only want the quantities of the posted invoices.
        We need a dedicated computation because the triggers are different and could lead to incorrect values for
        'qty_invoiced' when computed together.
        """
        for line in self:
            qty_invoiced_posted = 0.0
            for invoice_line in line._get_invoice_lines():
                if invoice_line.move_id.state == 'posted' or invoice_line.move_id.payment_state == 'invoicing_legacy':
                    qty_unsigned = invoice_line.product_uom_id._compute_quantity(invoice_line.quantity, line.product_uom)
                    qty_signed = qty_unsigned * -invoice_line.move_id.direction_sign
                    qty_invoiced_posted += qty_signed
            line.qty_invoiced_posted = qty_invoiced_posted

    def _get_invoice_lines(self):
        self.ensure_one()
        if self._context.get('accrual_entry_date'):
            return self.invoice_lines.filtered(
                lambda l: l.move_id.invoice_date and l.move_id.invoice_date <= self._context['accrual_entry_date']
            )
        else:
            return self.invoice_lines

    # no trigger product_id.invoice_policy to avoid retroactively changing SO
    @api.depends('qty_invoiced', 'qty_delivered', 'product_uom_qty', 'state')
    def _compute_qty_to_invoice(self):
        """
        Compute the quantity to invoice. If the invoice policy is order, the quantity to invoice is
        calculated from the ordered quantity. Otherwise, the quantity delivered is used.
        For combo product lines, compute the value if a linked combo item line gets recomputed,
        and set `qty_to_invoice` only if at least one of its combo item lines is invoiceable.
        """
        combo_lines = set()
        for line in self:
            if line.state == 'sale' and not line.display_type:
                if line.product_id.type == 'combo':
                    combo_lines.add(line)
                elif line.product_id.invoice_policy == 'order':
                    line.qty_to_invoice = line.product_uom_qty - line.qty_invoiced
                else:
                    line.qty_to_invoice = line.qty_delivered - line.qty_invoiced
                if line.combo_item_id and line.linked_line_id:
                    combo_lines.add(line.linked_line_id)
            else:
                line.qty_to_invoice = 0
        for combo_line in combo_lines:
            if any(
                line.combo_item_id and line.qty_to_invoice
                for line in combo_line.linked_line_ids
            ):
                combo_line.qty_to_invoice = combo_line.product_uom_qty - combo_line.qty_invoiced
            else:
                combo_line.qty_to_invoice = 0

    @api.depends('state', 'product_uom_qty', 'qty_delivered', 'qty_to_invoice', 'qty_invoiced')
    def _compute_invoice_status(self):
        """
        Compute the invoice status of a SO line. Possible statuses:
        - no: if the SO is not in status 'sale', we consider that there is nothing to
          invoice. This is also the default value if the conditions of no other status is met.
        - to invoice: we refer to the quantity to invoice of the line. Refer to method
          `_compute_qty_to_invoice()` for more information on how this quantity is calculated.
        - upselling: this is possible only for a product invoiced on ordered quantities for which
          we delivered more than expected. The could arise if, for example, a project took more
          time than expected but we decided not to invoice the extra cost to the client. This
          occurs only in state 'sale', the upselling opportunity is removed from the list.
        - invoiced: the quantity invoiced is larger or equal to the quantity ordered.
        """
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for line in self:
            if line.state != 'sale':
                line.invoice_status = 'no'
            elif line.is_downpayment and line.untaxed_amount_to_invoice == 0:
                line.invoice_status = 'invoiced'
            elif not float_is_zero(line.qty_to_invoice, precision_digits=precision):
                line.invoice_status = 'to invoice'
            elif line.state == 'sale' and line.product_id.invoice_policy == 'order' and\
                    line.product_uom_qty >= 0.0 and\
                    float_compare(line.qty_delivered, line.product_uom_qty, precision_digits=precision) == 1:
                line.invoice_status = 'upselling'
            elif float_compare(line.qty_invoiced, line.product_uom_qty, precision_digits=precision) >= 0:
                line.invoice_status = 'invoiced'
            else:
                line.invoice_status = 'no'

    def _can_be_invoiced_alone(self):
        """ Whether a given line is meaningful to invoice alone.

        It is generally meaningless/confusing or even wrong to invoice some specific SOlines
        (delivery, discounts, rewards, ...) without others, unless they are the only left to invoice
        in the SO.
        """
        self.ensure_one()
        return self.product_id.id != self.company_id.sale_discount_product_id.id

    @api.depends('invoice_lines', 'invoice_lines.price_total', 'invoice_lines.move_id.state', 'invoice_lines.move_id.move_type')
    def _compute_untaxed_amount_invoiced(self):
        """ Compute the untaxed amount already invoiced from the sale order line, taking the refund attached
            the so line into account. This amount is computed as
                SUM(inv_line.price_subtotal) - SUM(ref_line.price_subtotal)
            where
                `inv_line` is a customer invoice line linked to the SO line
                `ref_line` is a customer credit note (refund) line linked to the SO line
        """
        for line in self:
            amount_invoiced = 0.0
            for invoice_line in line._get_invoice_lines():
                if invoice_line.move_id.state == 'posted' or invoice_line.move_id.payment_state == 'invoicing_legacy':
                    invoice_date = invoice_line.move_id.invoice_date or fields.Date.today()
                    if invoice_line.move_id.move_type == 'out_invoice':
                        amount_invoiced += invoice_line.currency_id._convert(invoice_line.price_subtotal, line.currency_id, line.company_id, invoice_date)
                    elif invoice_line.move_id.move_type == 'out_refund':
                        amount_invoiced -= invoice_line.currency_id._convert(invoice_line.price_subtotal, line.currency_id, line.company_id, invoice_date)
            line.untaxed_amount_invoiced = amount_invoiced

    @api.depends('invoice_lines', 'invoice_lines.price_total', 'invoice_lines.move_id.state')
    def _compute_amount_invoiced(self):
        for line in self:
            amount_invoiced = 0.0
            for invoice_line in line._get_invoice_lines():
                invoice = invoice_line.move_id
                if invoice.state == 'posted' or invoice_line.move_id.payment_state == 'invoicing_legacy':
                    invoice_date = invoice.invoice_date or fields.Date.context_today(self)
                    amount_invoiced_unsigned = invoice_line.currency_id._convert(invoice_line.price_total, line.currency_id, line.company_id, invoice_date)
                    amount_invoiced += amount_invoiced_unsigned * -invoice.direction_sign
            line.amount_invoiced = amount_invoiced

    @api.depends('state', 'product_id', 'untaxed_amount_invoiced', 'qty_delivered', 'product_uom_qty', 'price_unit')
    def _compute_untaxed_amount_to_invoice(self):
        """ Total of remaining amount to invoice on the sale order line (taxes excl.) as
                total_sol - amount already invoiced
            where Total_sol depends on the invoice policy of the product.

            Note: Draft invoice are ignored on purpose, the 'to invoice' amount should
            come only from the SO lines.
        """
        for line in self:
            amount_to_invoice = 0.0
            if line.state == 'sale':
                # Note: do not use price_subtotal field as it returns zero when the ordered quantity is
                # zero. It causes problem for expense line (e.i.: ordered qty = 0, deli qty = 4,
                # price_unit = 20 ; subtotal is zero), but when you can invoice the line, you see an
                # amount and not zero. Since we compute untaxed amount, we can use directly the price
                # reduce (to include discount) without using `compute_all()` method on taxes.
                price_subtotal = 0.0
                uom_qty_to_consider = line.qty_delivered if line.product_id.invoice_policy == 'delivery' else line.product_uom_qty
                price_reduce = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                price_subtotal = price_reduce * uom_qty_to_consider
                if len(line.tax_id.filtered(lambda tax: tax.price_include)) > 0:
                    # As included taxes are not excluded from the computed subtotal, `compute_all()` method
                    # has to be called to retrieve the subtotal without them.
                    # `price_reduce_taxexcl` cannot be used as it is computed from `price_subtotal` field. (see upper Note)
                    price_subtotal = line.tax_id.compute_all(
                        price_reduce,
                        currency=line.currency_id,
                        quantity=uom_qty_to_consider,
                        product=line.product_id,
                        partner=line.order_id.partner_shipping_id)['total_excluded']
                inv_lines = line._get_invoice_lines()
                if any(inv_lines.mapped(lambda l: l.discount != line.discount)):
                    # In case of re-invoicing with different discount we try to calculate manually the
                    # remaining amount to invoice
                    amount = 0
                    for l in inv_lines:
                        if len(l.tax_ids.filtered(lambda tax: tax.price_include)) > 0:
                            amount += l.tax_ids.compute_all(l.currency_id._convert(l.price_unit, line.currency_id, line.company_id, l.date or fields.Date.today(), round=False) * l.quantity)['total_excluded']
                        else:
                            amount += l.currency_id._convert(l.price_unit, line.currency_id, line.company_id, l.date or fields.Date.today(), round=False) * l.quantity

                    amount_to_invoice = max(price_subtotal - amount, 0)
                else:
                    amount_to_invoice = price_subtotal - line.untaxed_amount_invoiced

            line.untaxed_amount_to_invoice = amount_to_invoice

    @api.depends('discount', 'price_total', 'product_uom_qty', 'qty_delivered', 'qty_invoiced_posted')
    def _compute_amount_to_invoice(self):
        for line in self:
            if line.product_uom_qty:
                uom_qty_to_consider = line.qty_delivered if line.product_id.invoice_policy == 'delivery' else line.product_uom_qty
                qty_to_invoice = uom_qty_to_consider - line.qty_invoiced_posted
                unit_price_total = line.price_total / line.product_uom_qty
                line.amount_to_invoice = unit_price_total * qty_to_invoice
            else:
                line.amount_to_invoice = 0.0

    @api.depends('order_id.partner_id', 'product_id')
    def _compute_analytic_distribution(self):
        for line in self:
            if not line.display_type:
                distribution = line.env['account.analytic.distribution.model']._get_distribution({
                    "product_id": line.product_id.id,
                    "product_categ_id": line.product_id.categ_id.id,
                    "partner_id": line.order_id.partner_id.id,
                    "partner_category_id": line.order_id.partner_id.category_id.ids,
                    "company_id": line.company_id.id,
                })
                line.analytic_distribution = distribution or line.analytic_distribution

    @api.depends('product_id', 'state', 'qty_invoiced', 'qty_delivered')
    def _compute_product_updatable(self):
        self.product_updatable = True
        for line in self:
            if (
                line.is_downpayment
                or line.state == 'cancel'
                or line.state == 'sale' and (
                    line.order_id.locked
                    or line.qty_invoiced > 0
                    or line.qty_delivered > 0
                )
            ):
                line.product_updatable = False

    @api.depends('state')
    def _compute_product_uom_readonly(self):
        for line in self:
            # line.ids checks whether it's a new record not yet saved
            line.product_uom_readonly = line.ids and line.state in ['sale', 'cancel']

    #=== CONSTRAINT METHODS ===#

    @api.constrains('combo_item_id')
    def _check_combo_item_id(self):
        """ `combo_item_id` should never be set manually. This constraint mainly serves to avoid
        programming errors.
        """
        for line in self:
            linked_line = line._get_linked_line()
            allowed_combo_items = linked_line.product_template_id.combo_ids.combo_item_ids
            if line.combo_item_id and line.combo_item_id not in allowed_combo_items:
                raise ValidationError(_(
                    "A sale order line's combo item must be among its linked line's available"
                    " combo items."
                ))
            if line.combo_item_id and line.combo_item_id.product_id != line.product_id:
                raise ValidationError(_(
                    "A sale order line's product must match its combo item's product."
                ))

    #=== ONCHANGE METHODS ===#

    @api.onchange('product_id')
    def _onchange_product_id_warning(self):
        if not self.product_id:
            return

        product = self.product_id
        if product.sale_line_warn != 'no-message':
            if product.sale_line_warn == 'block':
                self.product_id = False

            return {
                'warning': {
                    'title': _("Warning for %s", product.name),
                    'message': product.sale_line_warn_msg,
                }
            }

    @api.onchange('product_packaging_id')
    def _onchange_product_packaging_id(self):
        if self.product_packaging_id and self.product_uom_qty:
            newqty = self.product_packaging_id._check_qty(self.product_uom_qty, self.product_uom, "UP")
            if float_compare(newqty, self.product_uom_qty, precision_rounding=self.product_uom.rounding) != 0:
                return {
                    'warning': {
                        'title': _('Warning'),
                        'message': _(
                            "This product is packaged by %(pack_size).2f %(pack_name)s. You should sell %(quantity).2f %(unit)s.",
                            pack_size=self.product_packaging_id.qty,
                            pack_name=self.product_id.uom_id.name,
                            quantity=newqty,
                            unit=self.product_uom.name
                        ),
                    },
                }

    def _is_logistics_user(self):
        """User có thuộc nhóm Kho/Điều vận không?"""
        return self.env.user.has_group('sale_custom.group_logistics')
    #=== CRUD METHODS ===#
    @api.model_create_multi
    def create(self, vals_list):
        # Logistics tuyệt đối không được tạo dòng
        if self._is_logistics_user():
            raise UserError(_("Bạn không có quyền tạo dòng sản phẩm (Kho/Điều vận chỉ được bật 'Đã chuẩn bị hàng')."))

        # ---- GIỮ NGUYÊN LOGIC CŨ CỦA BẠN (đã có trong file) ----
        for vals in vals_list:
            if vals.get('display_type') or self.default_get(['display_type']).get('display_type'):
                vals['product_uom_qty'] = 0.0
            if 'technical_price_unit' in vals and 'price_unit' not in vals:
                vals.pop('technical_price_unit')

            kind = vals.get('line_kind')
            if vals.get('is_gift'):
                kind = 'KM'
            if vals.get('is_bundle'):
                kind = 'BK'
            if kind in ('BK', 'KM'):
                vals['line_kind'] = kind
                vals['price_unit'] = 0.0
                vals['is_bundle'] = (kind == 'BK')
                vals['is_gift'] = (kind == 'KM')

        lines = super(SaleOrderLine, self).create(vals_list)

        for line in lines:
            linked_line = line._get_linked_line()
            if linked_line:
                line.linked_line_id = linked_line
        if self.env.context.get('sale_no_log_for_new_lines'):
            return lines
        for line in lines:
            if line.product_id and line.state == 'sale':
                msg = _("Extra line with %s", line.product_id.display_name)
                line.order_id.message_post(body=msg)
        return lines
    def _add_precomputed_values(self, vals_list):
        super()._add_precomputed_values(vals_list)
        for vals in vals_list:
            if 'price_unit' in vals and 'technical_price_unit' not in vals:
                vals['technical_price_unit'] = vals['price_unit']

    

    def write(self, values):
        # GIỮ NGUYÊN logic gốc, bỏ toàn bộ check is_prepared/eta_done theo role
        if 'display_type' in values and self.filtered(lambda line: line.display_type != values.get('display_type')):
            raise UserError(_("You cannot change the type of a sale order line. Instead you should delete the current line and create a new line of the proper type."))

        if 'product_id' in values and any(
            sol.product_id.id != values['product_id']
            and not sol.product_updatable
            for sol in self
        ):
            raise UserError(_("You cannot modify the product of this order line."))

        if 'product_uom_qty' in values:
            precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
            self.filtered(
                lambda r: r.state == 'sale' and float_compare(r.product_uom_qty, values['product_uom_qty'], precision_digits=precision) != 0
            )._update_line_quantity(values)

        if (
            'technical_price_unit' in values
            and 'price_unit' not in values
            and not self.env.context.get('sale_write_from_compute')
        ):
            values.pop('technical_price_unit')

        protected_fields = self._get_protected_fields()

        # =========================================================
        # HOTFIX: Website checkout cần recompute Taxes/Price/Discount...
        # Nhưng order đang locked sớm => bị chặn và nổ.
        # Chỉ bypass trong ngữ cảnh website và chỉ cho whitelist field checkout.
        # =========================================================
        allowed_checkout_fields = {
            "name",
            "product_uom_qty",
            "price_unit",
            "discount",
            "tax_id",
            "product_packaging_id",

            # cần cho voucher/km của bạn
            "fixed_discount",
            "technical_price_unit",
            "oms_base_price",
            "gift_combo_id",
            "gift_combo_sequence",
        }

        
        bypass_locked_checkout = (
            self.env.context.get("website_id")
            and set(values.keys()).issubset(allowed_checkout_fields)
        )
        
        if (not bypass_locked_checkout) and any(self.order_id.mapped('locked')) and any(f in values.keys() for f in protected_fields):
            protected_fields_modified = list(set(protected_fields) & set(values.keys()))
            if 'name' in protected_fields_modified and all(self.mapped('is_downpayment')):
                protected_fields_modified.remove('name')
            fields = self.env['ir.model.fields'].sudo().search([
                ('name', 'in', protected_fields_modified), ('model', '=', self._name)
            ])
            if fields:
                raise UserError(
                    _('It is forbidden to modify the following fields in a locked order:\n%s',
                      '\n'.join(fields.mapped('field_description')))
                )
        
        
        if 'line_kind' in values and values['line_kind'] in ('BK', 'KM'):
            kind = values['line_kind']
            values.update({
                'price_unit': 0.0,
                'is_bundle': kind == 'BK',
                'is_gift': kind == 'KM',
            })
        if 'price_unit' in values and any(l.line_kind in ('BK','KM') for l in self):
            values = dict(values)
            values['price_unit'] = 0.0

        result = super(SaleOrderLine, self).write(values)

        if 'product_uom_qty' in values and 'product_packaging_qty' in values and 'product_packaging_id' not in values:
            self.env.remove_to_compute(self._fields['product_packaging_id'], self)

        return result

    @api.model
    def _get_protected_fields(self):
        """
        Fields KHÔNG được sửa khi SO bị khóa.
        Cho phép ngoại lệ: is_prepared, eta_done vẫn sửa được.
        """
        base = []
        try:
            base = super()._get_protected_fields()
        except Exception:
            # fallback theo mặc định Odoo 18
            base = [
                'product_id', 'name', 'price_unit', 'product_uom', 'product_uom_qty',
                'tax_id', 'analytic_distribution',
            ]

        # Bổ sung vài field thường cần khóa khi locked (tùy bạn giữ hoặc bỏ):
        extra_protected = [
            'discount',              # khóa chiết khấu
            'display_type',          # tránh đổi line thành section/note
            'route_id',              # tuyến giao
        ]
        protected = set(base) | set(extra_protected)

        # Cho phép 2 field này sửa kể cả khi locked
        protected -= {'is_prepared', 'eta_done'}

        return list(protected)


    def _update_line_quantity(self, values):
        orders = self.mapped('order_id')
        for order in orders:
            order_lines = self.filtered(lambda x: x.order_id == order)
            msg = Markup("<b>%s</b><ul>") % _("The ordered quantity has been updated.")
            for line in order_lines:
                if 'product_id' in values and values['product_id'] != line.product_id.id:
                    # tracking is meaningless if the product is changed as well.
                    continue
                msg += Markup("<li> %s: <br/>") % line.product_id.display_name
                msg += _(
                    "Ordered Quantity: %(old_qty)s -> %(new_qty)s",
                    old_qty=line.product_uom_qty,
                    new_qty=values["product_uom_qty"]
                ) + Markup("<br/>")
                if line.product_id.type == 'consu':
                    msg += _("Delivered Quantity: %s", line.qty_delivered) + Markup("<br/>")
                msg += _("Invoiced Quantity: %s", line.qty_invoiced) + Markup("<br/>")
            msg += Markup("</ul>")
            order.message_post(body=msg)

    def _check_line_unlink(self):
        """ Check whether given lines can be deleted or not.

        * Lines cannot be deleted if the order is confirmed.
        * Down payment lines who have not yet been invoiced bypass that exception.
        * Sections and Notes can always be deleted.

        :returns: Sales Order Lines that cannot be deleted
        :rtype: `sale_custom.order.line` recordset
        """
        return self.filtered(
            lambda line:
                line.state == 'sale'
                and (line.invoice_lines or not line.is_downpayment)
                and not line.display_type
        )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        if self._check_line_unlink():
            raise UserError(_("Once a sales order is confirmed, you can't remove one of its lines (we need to track if something gets invoiced or delivered).\n\
                Set the quantity to 0 instead."))

    #=== ACTION METHODS ===#

    def action_add_from_catalog(self):
        order = self.env['sale_custom.order'].browse(self.env.context.get('order_id'))
        return order.with_context(child_field='order_line').action_add_from_catalog()

    #=== BUSINESS METHODS ===#

    def _expected_date(self):
        self.ensure_one()
        if self.state == 'sale' and self.order_id.date_order:
            order_date = self.order_id.date_order
        else:
            order_date = fields.Datetime.now()
        return order_date + timedelta(days=self.customer_lead or 0.0)

    def compute_uom_qty(self, new_qty, stock_move, rounding=True):
        return self.product_uom._compute_quantity(new_qty, stock_move.product_uom, rounding)

    def _get_invoice_line_sequence(self, new=0, old=0):
        """
        Method intended to be overridden in third-party module if we want to prevent the resequencing
        of invoice lines.

        :param int new:   the new line sequence
        :param int old:   the old line sequence

        :return:          the sequence of the SO line, by default the new one.
        """
        return new or old

    def _prepare_invoice_line(self, **optional_values):
        """Prepare the values to create the new invoice line for a sales order line.

        :param optional_values: any parameter that should be added to the returned invoice line
        :rtype: dict
        """
        self.ensure_one()

        if self.product_id.type == 'combo':
            # If the quantity to invoice is a whole number, format it as an integer (with no decimal point)
            qty_to_invoice = int(self.qty_to_invoice) if self.qty_to_invoice == int(self.qty_to_invoice) else self.qty_to_invoice
            return {
                'display_type': 'line_section',
                'sequence': self.sequence,
                'name': f'{self.product_id.name} x {qty_to_invoice}',
                'product_uom_id': self.product_uom.id,
                'quantity': self.qty_to_invoice,
                'sale_line_ids': [Command.link(self.id)],
                **optional_values,
            }
        res = {
            'display_type': self.display_type or 'product',
            'sequence': self.sequence,
            'name': self.env['account.move.line']._get_journal_items_full_name(self.name, self.product_id.display_name),
            'product_id': self.product_id.id,
            'product_uom_id': self.product_uom.id,
            'quantity': self.qty_to_invoice,
            'discount': self.discount,
            'price_unit': self.price_unit,
            'tax_ids': [Command.set(self.tax_id.ids)],
            'sale_line_ids': [Command.link(self.id)],
            'is_downpayment': self.is_downpayment,
        }
        self._set_analytic_distribution(res, **optional_values)
        downpayment_lines = self.invoice_lines.filtered('is_downpayment')
        if self.is_downpayment and downpayment_lines:
            res['account_id'] = downpayment_lines.account_id[:1].id
        if optional_values:
            res.update(optional_values)
        if self.display_type:
            res['account_id'] = False
        return res

    def _set_analytic_distribution(self, inv_line_vals, **optional_values):
        if self.analytic_distribution and not self.display_type:
            inv_line_vals['analytic_distribution'] = self.analytic_distribution

    def _prepare_procurement_values(self, group_id=False):
        """ Prepare specific key for moves or other components that will be created from a stock rule
        coming from a sale order line. This method could be override in order to add other custom key that could
        be used in move/po creation.
        """
        return {}

    def _validate_analytic_distribution(self):
        for line in self.filtered(lambda l: not l.display_type and l.state in ['draft', 'sent']):
            line._validate_distribution(**{
                'product': line.product_id.id,
                'business_domain': 'sale_order',
                'company_id': line.company_id.id,
            })

    def _get_downpayment_line_price_unit(self, invoices):
        return sum(
            l.price_unit if l.move_id.move_type == 'out_invoice' else -l.price_unit
            for l in self.invoice_lines
            if l.move_id.state == 'posted' and l.move_id not in invoices  # don't recompute with the final invoice
        )

    #=== CORE METHODS OVERRIDES ===#

    def _get_partner_display(self):
        self.ensure_one()
        commercial_partner = self.sudo().order_partner_id.commercial_partner_id
        return f'({commercial_partner.ref or commercial_partner.name})'

    def _additional_name_per_id(self):
        return {
            so_line.id: so_line._get_partner_display()
            for so_line in self
        }

    #=== HOOKS ===#

    def _is_delivery(self):
        self.ensure_one()
        return False

    def _is_not_sellable_line(self):
        # True if the line is a computed line (reward, delivery, ...) that user cannot add manually
        return False

    def _get_product_catalog_lines_data(self, **kwargs):
        """ Return information about sale order lines in `self`.

        If `self` is empty, this method returns only the default value(s) needed for the product
        catalog. In this case, the quantity that equals 0.

        Otherwise, it returns a quantity and a price based on the product of the AUT(s) and whether
        the product is read-only or not.

        A product is considered read-only if the order is considered read-only (see
        ``SaleOrder._is_readonly`` for more details) or if `self` contains multiple records
        or if it has sale_line_warn == "block".

        Note: This method cannot be called with multiple records that have different products linked.

        :raise odoo.exceptions.ValueError: ``len(self.product_id) != 1``
        :rtype: dict
        :return: A dict with the following structure:
            {
                'quantity': float,
                'price': float,
                'readOnly': bool,
                'warning': String
            }
        """
        if len(self) == 1:
            res = {
                'quantity': self.product_uom_qty,
                'price': self.price_unit,
                'readOnly': (
                    self.order_id._is_readonly()
                    or self.product_id.sale_line_warn == 'block'
                    or bool(self.combo_item_id)
                ),
            }
            if self.product_id.sale_line_warn != 'no-message' and self.product_id.sale_line_warn_msg:
                res['warning'] = self.product_id.sale_line_warn_msg
            return res
        elif self:
            self.product_id.ensure_one()
            order_line = self[0]
            order = order_line.order_id
            res = {
                'readOnly': True,
                'price': order.pricelist_id._get_product_price(
                    product=order_line.product_id,
                    quantity=1.0,
                    currency=order.currency_id,
                    date=order.date_order,
                    **kwargs,
                ),
                'quantity': sum(
                    self.mapped(
                        lambda line: line.product_uom._compute_quantity(
                            qty=line.product_uom_qty,
                            to_unit=line.product_id.uom_id,
                        )
                    )
                )
            }
            if self.product_id.sale_line_warn != 'no-message' and self.product_id.sale_line_warn_msg:
                res['warning'] = self.product_id.sale_line_warn_msg
            return res
        else:
            return {
                'quantity': 0,
                # price will be computed in batch with pricelist utils so not given here
            }

    #=== TOOLING ===#

    def _convert_to_sol_currency(self, amount, currency):
        """Convert the given amount from the given currency to the SO(L) currency.

        :param float amount: the amount to convert
        :param currency: currency in which the given amount is expressed
        :type currency: `res.currency` record
        :returns: converted amount
        :rtype: float
        """
        self.ensure_one()
        to_currency = self.currency_id or self.order_id.currency_id
        if currency and to_currency and currency != to_currency:
            conversion_date = self.order_id.date_order or fields.Date.context_today(self)
            company = self.company_id or self.order_id.company_id or self.env.company
            return currency._convert(
                from_amount=amount,
                to_currency=to_currency,
                company=company,
                date=conversion_date,
                round=False,
            )
        return amount

    def has_valued_move_ids(self):
        return self.move_ids

    def _get_linked_line(self):
        """ Return the linked line of this line, if any.

        This method relies on either `linked_line_id` or `linked_virtual_id` to retrieve the linked
        line, depending on whether the linked line is saved in the DB.
        """
        self.ensure_one()
        return self.linked_line_id or (
            self.linked_virtual_id and self.order_id.order_line.filtered(
                lambda line: line.virtual_id == self.linked_virtual_id
            ).ensure_one()
        ) or self.env['sale_custom.order.line']

    def _get_linked_lines(self):
        """ Return the linked lines of this line, if any.

        This method relies on either `linked_line_id` or `linked_virtual_id` to retrieve the linked
        lines, depending on whether this line is saved in the DB.

        Note: we can't rely on `linked_line_ids` as it will only be populated when both this line
        and its linked lines are saved in the DB, which we can't ensure.
        """
        self.ensure_one()
        return (
            self._origin and self.order_id.order_line.filtered(
                lambda line: line.linked_line_id._origin == self._origin
            )
        ) or (
            self.virtual_id and self.order_id.order_line.filtered(
                lambda line: line.linked_virtual_id == self.virtual_id
            )
        ) or self.env['sale_custom.order.line']

    def _sellable_lines_domain(self):
        discount_products_ids = self.env.companies.sale_discount_product_id.ids
        domain = [('is_downpayment', '=', False)]
        if discount_products_ids:
            domain = expression.AND([
                domain,
                [('product_id', 'not in', discount_products_ids)],
            ])
        return domain

    def _get_lines_with_price(self):
        """ A combo product line always has a zero price (by design). The actual price of the combo
        product can be computed by summing the prices of its combo items (i.e. its linked lines).
        """
        return self.linked_line_ids if self.product_type == 'combo' else self
    
    @api.onchange('product_id', 'product_uom_qty','price_unit')
    def _onchange_set_oms_price_and_promotion(self):
        for line in self:
            # 1) gift/bundle: giá = 0
            if getattr(line, 'is_gift', False) or getattr(line, 'is_bundle', False):
                line.price_unit = 0.0
                line.technical_price_unit = 0.0
                line.oms_base_price = 0.0
                line.stock_value = 0.0
                line.provisional_commission = 0.0
                continue

            # 2) thiếu dữ liệu
            if not line.product_id or not line.order_id or line.product_uom_qty <= 0:
                line.price_unit = 0.0
                line.technical_price_unit = 0.0
                line.oms_base_price = 0.0
                line.stock_value = 0.0
                line.provisional_commission = 0.0
                continue

            # 3) Chỉ lấy lại base & set giá khi key đổi và KHÔNG phải giá tay
            new_key = line._make_oms_base_key()
            key_changed = (line.last_oms_base_key != new_key)
            if key_changed and not line._is_manual_price():
                base = line.get_oms_base_price_cached()
                line.price_unit = base
                # đánh dấu là giá hệ thống (không phải giá tay)
                line.technical_price_unit = base

            # 4) Luôn tính lại hoa hồng/giá vốn theo đơn giá hiện tại
            try:
                line.action_calc_provisional_commission()
            except Exception:
                line.stock_value = 0.0
                line.provisional_commission = 0.0
    
    def action_view_promotions(self):
        self.ensure_one()
        today = fields.Date.today()
        product = self.product_id
        categ_id = product.categ_id.id if product.categ_id else False

        # Lấy các khuyến mãi áp dụng theo sản phẩm hoặc danh mục
        promo_ids = set(self.env['oms.promotion'].search([
            '|',
                ('apply_product_line_ids.product_tmpl_id', '=', product.product_tmpl_id.id),
                ('apply_product_line_ids.product_category_id', '=', categ_id),
            ('valid_from', '<=', today),
            ('valid_to', '>=', today),
        ]).ids)

        # Lấy các khuyến mãi từ bundle combo nếu sản phẩm nằm trong bất kỳ combo nào
        for promo in self.env['oms.promotion'].search([('valid_from', '<=', today), ('valid_to', '>=', today)]):
            for combo in promo.bundle_combo_ids:
                if product.product_tmpl_id.id in combo.product_tmpl_ids.ids:
                    promo_ids.add(promo.id)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Khuyến mãi áp dụng',
            'res_model': 'oms.promotion',
            'view_mode': 'tree,form',
            'target': 'new',
            'domain': [('id', 'in', list(promo_ids))],
            'context': {
                'default_product_id': product.id,
            }
        }


    def get_oms_base_price_cached(self, force=False):
        """
        Trả về "giá gốc" dùng để tính KM/hiển thị.
        Thứ tự:
          1) Special price theo khách
          2) Giá theo pricelist của order (tier theo qty)
          3) OMS price table (oms.price.list.line.get_price_for_product)
          4) list_price (fallback cuối)
        Có cache theo last_oms_base_key.
        """
        self.ensure_one()
        ctx = self.env.context or {}
        log_info = bool(ctx.get("uc_log_tier_price"))  # bật log INFO khi debug
        log = _logger.info if log_info else _logger.debug

        key = self._make_oms_base_key()

        # ===== Cache hit =====
        if (not force) and self.oms_base_price and self.last_oms_base_key == key:
            log(
                "[UC_TIER] CACHE HIT line=%s order=%s product=%s qty=%s uom=%s key=%s price=%s",
                self.id,
                getattr(self.order_id, "id", None),
                getattr(self.product_id, "display_name", None),
                self.product_uom_qty,
                getattr(self.product_uom, "name", None),
                key,
                self.oms_base_price,
            )
            return self.oms_base_price

        # ===== Basic context =====
        order = self.order_id
        product = self.product_id
        qty = float(self.product_uom_qty or 0.0)
        uom = self.product_uom
        partner = getattr(order, "partner_id", False)

        order_date = (order.date_order and order.date_order.date()) or fields.Date.today()
        pl = getattr(order, "pricelist_id", False)

        log(
            "[UC_TIER] START line=%s order=%s product=%s qty=%s uom=%s date=%s pricelist=%s(%s) force=%s ctx.website_id=%s",
            self.id,
            getattr(order, "id", None),
            getattr(product, "display_name", None),
            qty,
            getattr(uom, "name", None),
            order_date,
            getattr(pl, "name", None),
            getattr(pl, "id", None),
            force,
            ctx.get("website_id"),
        )

        # ===== Gift/Bundle =====
        if getattr(self, "is_gift", False) or getattr(self, "is_bundle", False) or getattr(self, "display_type", False):
            val = 0.0
            log("[UC_TIER] GIFT/BUNDLE => 0 line=%s", self.id)

            self.last_oms_base_key = key
            self.oms_base_price = val
            return val

        val = 0.0

        # ======================================================
        # 1) Special price theo khách
        # ======================================================
        try:
            if partner and product:
                special_price = self.env["oms.special.price.line"].get_special_price_for_customer(
                    partner.id, product.id, order_date
                )
                if special_price:
                    val = float(special_price)
                    log(
                        "[UC_TIER] SPECIAL OK line=%s partner=%s product=%s price=%s",
                        self.id,
                        partner.id,
                        product.id,
                        val,
                    )
                else:
                    log("[UC_TIER] SPECIAL NONE line=%s", self.id)
        except Exception:
            _logger.exception("[UC_TIER] SPECIAL ERROR line=%s", self.id)

        # ======================================================
        # 2) Pricelist tier theo qty (order.pricelist_id)
        # ======================================================
        if not val and order and pl and product and uom:
            try:
                # cách 1: _get_product_rule + _compute_price (nếu có)
                item = False
                if hasattr(pl, "_get_product_rule"):
                    item = pl._get_product_rule(
                        product,
                        quantity=qty or 1.0,
                        uom=uom,
                        date=self._get_order_date() if hasattr(self, "_get_order_date") else fields.Date.today(),
                    )

                if item and hasattr(item, "_compute_price"):
                    val = float(
                        item._compute_price(
                            product=product.with_context(**(self._get_product_price_context() if hasattr(self, "_get_product_price_context") else {})),
                            quantity=qty or 1.0,
                            uom=uom,
                            date=self._get_order_date() if hasattr(self, "_get_order_date") else fields.Date.today(),
                            currency=self.currency_id,
                        ) or 0.0
                    )
                    log(
                        "[UC_TIER] PL RULE OK line=%s pl=%s(%s) rule=%s(%s) qty=%s price=%s",
                        self.id,
                        pl.name,
                        pl.id,
                        getattr(item, "name", None),
                        getattr(item, "id", None),
                        qty,
                        val,
                    )

                # cách 2: _get_product_price (nếu hệ bạn có)
                if not val and hasattr(pl, "_get_product_price"):
                    try:
                        val = float(
                            pl._get_product_price(
                                product,
                                qty or 1.0,
                                partner,
                                date=order.date_order,
                                uom_id=uom.id,
                            ) or 0.0
                        )
                        if val:
                            log(
                                "[UC_TIER] PL _get_product_price OK line=%s pl=%s(%s) qty=%s price=%s",
                                self.id, pl.name, pl.id, qty, val
                            )
                    except TypeError:
                        # signature khác -> bỏ qua
                        pass

                # cách 3: _compute_price_rule (fallback an toàn)
                if not val and hasattr(pl, "_compute_price_rule"):
                    try:
                        # _compute_price_rule thường nhận (products, qty, partner) tuỳ version, nên wrap try
                        res = pl._compute_price_rule(
                            [(product, qty or 1.0, uom)],
                            date=order.date_order or fields.Datetime.now(),
                            currency=self.currency_id,
                            partner=partner,
                        )
                        # res có dạng {product.id: (price, rule_id)} hoặc list
                        if isinstance(res, dict) and product.id in res:
                            pr = res[product.id]
                            val = float(pr[0] or 0.0)
                            log(
                                "[UC_TIER] PL _compute_price_rule OK line=%s pl=%s(%s) qty=%s price=%s rule_id=%s",
                                self.id, pl.name, pl.id, qty, val, pr[1]
                            )
                    except Exception:
                        # không fail toàn flow
                        _logger.exception("[UC_TIER] PL _compute_price_rule ERROR line=%s", self.id)

                if not val:
                    log("[UC_TIER] PL NONE line=%s pl=%s(%s) qty=%s", self.id, pl.name, pl.id, qty)

            except Exception:
                _logger.exception("[UC_TIER] PL ERROR line=%s pl=%s(%s)", self.id, getattr(pl, "name", None), getattr(pl, "id", None))
                val = 0.0

        is_website_order = (
            bool(ctx.get("website_id"))
            or bool(getattr(order, "website_id", False))
            or getattr(order, "channel", "") == "online"
        )
        if not val and is_website_order and product:
            try:
                combo_info = product.product_tmpl_id._get_combination_info(
                    product_id=product.id,
                    add_qty=qty or 1.0,
                )
                if (
                    combo_info.get("prevent_zero_price_sale")
                    or combo_info.get("uc_wait_sales_price")
                    or float(combo_info.get("price") or 0.0) <= 1.0
                ):
                    val = 0.0
                    self.last_oms_base_key = key
                    self.oms_base_price = val
                    log("[UC_TIER] WEBSITE CONTACT PRICE => 0 line=%s product=%s", self.id, product.id)
                    return val
            except Exception:
                _logger.exception("[UC_TIER] WEBSITE CONTACT CHECK ERROR line=%s", self.id)

        # ======================================================
        # 3) OMS price table (oms.price.list.line)
        #    IMPORTANT: ưu tiên ItemCode / code OMS trước default_code
        # ======================================================
        if not val and product:
            PriceLine = self.env["oms.price.list.line"].sudo()

            # Ưu tiên các field thường gặp trong OMS/SAP
            code_primary = (
                (getattr(product, "ItemCode", False) or "")
                or (getattr(product, "item_code", False) or "")
                or (getattr(product, "x_oms_item_code", False) or "")
                or (getattr(product, "default_code", False) or "")
                or ""
            ).strip()

            candidates = []
            if code_primary:
                candidates += [
                    code_primary,
                    code_primary.upper(),
                    code_primary.replace(".", "-"),
                    code_primary.replace("-", "."),
                    code_primary.replace(".", ""),
                    code_primary.replace("-", ""),
                ]

            # Nếu có thêm mã kiểu "KY-Y-YJ114.120" trong name, bạn có thể parse thêm ở đây (tuỳ data)
            seen = set()
            for c in candidates:
                c = (c or "").strip()
                if not c or c in seen:
                    continue
                seen.add(c)

                try:
                    price = PriceLine.get_price_for_product(c, qty or 1.0, order_date)
                except Exception:
                    price = False

                if price:
                    val = float(price)
                    log(
                        "[UC_TIER] OMS TABLE OK line=%s code=%s qty=%s date=%s price=%s",
                        self.id, c, qty, order_date, val
                    )
                    break

            if not val:
                log(
                    "[UC_TIER] OMS TABLE NONE line=%s code_primary=%s tried=%s",
                    self.id, code_primary, list(seen)
                )

        # ======================================================
        # 4) Fallback list_price
        # ======================================================
        if not val:
            val = float(product.list_price or 0.0)
            log(
                "[UC_TIER] FALLBACK list_price line=%s product=%s list_price=%s",
                self.id,
                getattr(product, "display_name", None),
                val,
            )

        # ===== Save cache =====
        self.last_oms_base_key = key
        self.oms_base_price = val

        log(
            "[UC_TIER] DONE line=%s order=%s product=%s qty=%s final_price=%s key=%s",
            self.id,
            getattr(order, "id", None),
            getattr(product, "display_name", None),
            qty,
            val,
            key,
        )
        return val
        
    def get_oms_base_price(self):
        return self.get_oms_base_price_cached(force=False)

    @api.onchange('order_line.price_unit', 'order_line.discount')
    def _onchange_select_workflow(self):
        self._select_workflow_by_discount()


    def _get_token(self):
        import requests
        try:
            payload = {"username": API_USERNAME, "password": API_PASSWORD}
            res = requests.post(API_AUTH_URL, json=payload, timeout=15)
            res.raise_for_status()
            data = res.json() if res.content else {}
            token = data.get("token")
            if not token:
                raise UserError(_("Không lấy được token xác thực: %s    " % data))
            return token
        except requests.RequestException as re:
            raise UserError(_("Lỗi kết nối đến server xác thực: %s" % str(re)))
        except Exception as e:
            raise UserError(_('Lỗi không xác định khi lấy token: %s' % str(e)))


    def _post_data_with_token(self, api_url, token, payload):
        import requests
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            res = requests.post(api_url, headers=headers, json=payload, timeout=30)
            res.raise_for_status()
            try:
                data = res.json()
                if isinstance(data, dict):
                    return data
                return {}
            except Exception:
                return {}
        except Exception:
            return {}

    def action_get_stock_value(self, whs_code):
        self.ensure_one()
        payload = {
            "ItemCode": self.product_id.default_code,
            "WhsCode": whs_code
        }
        try:
            token = self._get_token()
            response = self._post_data_with_token(API_STOCK_URL, token, payload)
            avg_price = 0.0
            if isinstance(response, dict):
                result = response.get("result") or {}
                if isinstance(result, dict):
                    avg_price = result.get("AvgPrice", 0.0)
            self.stock_value = avg_price
            return avg_price
        except Exception as e:
            self.stock_value = 0
            return 0.0

    def action_calc_provisional_commission(self, whs_code=None):
        self.ensure_one()
        qty = self.product_uom_qty or 1.0
        try:
            token = self._get_token()
            # --- Bước 1: Lấy StockValue (giá vốn kho)
            stock_payload = {
                "ItemCode": self.product_id.default_code,
                "WhsCode": "HCMVP201"  # Mặc định kho HCMVP201, có thể thay đổi nếu cần
            }
            stock_response = self._post_data_with_token(API_STOCK_URL, token, stock_payload)
            stock_value = 0.0
            if isinstance(stock_response, dict):
                result = stock_response.get("result") or {}
                if isinstance(result, dict):
                    stock_value = result.get("AvgPrice", 0.0)
            self.stock_value = stock_value

            # --- Bước 2: Gọi API Hoa hồng tạm tính
            commission_payload = {
                "BU": "AUT",
                "VoucherTypeID": "1310",
                "ItemCode": self.product_id.default_code,
                "MktCampaign": "",
                "Upselling": "",
                "DocDate": fields.Date.today().strftime("%Y-%m-%d"),
                "RealSale": self.price_reduce_taxexcl or 0.0,
                "StockValue": stock_value,
                "DocType": "B",
                "ProjectCode": ""
            }
            commission_response = self._post_data_with_token(API_COMMISSION_URL, token, commission_payload)
            commission = 0.0
            if isinstance(commission_response, dict) and commission_response.get("status") == "TRUE":
                result = commission_response.get("result") or []
                if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
                    per_unit_commission = result[0].get("ComissionAmountByCM", 0.0)
                    commission = per_unit_commission * qty
            self.provisional_commission = commission

            return {
                "stock_value": stock_value,
                "commission": commission,
                "commission_api_response": commission_response
            }
        except Exception as e:
            self.stock_value = 0
            self.provisional_commission = 0
            return {
                "stock_value": 0,
                "commission": 0,
                "error": str(e)
            }
    @api.onchange('product_id', 'product_id.categ_id')
    def _onchange_auto_promotions(self):
        for line in self:
            # Xóa các gift/bundle cũ (nếu còn)
            for gift in list(line.order_id.order_line):
                if gift.is_gift and gift.linked_line_id == line:
                    if gift.id and gift.order_id:
                        gift.unlink()
                    else:
                        line.order_id.order_line -= gift
            for bundle in list(line.order_id.order_line):
                if bundle.is_bundle and bundle.linked_line_id == line:
                    if bundle.id and bundle.order_id:
                        bundle.unlink()
                    else:
                        line.order_id.order_line -= bundle
                    
    def apply_promotions_to_line(self):
        """
        Áp dụng khuyến mãi cho từng dòng:
        - Gift: tạo line quà tặng (is_gift=True)
        - Bundle (mua kèm): KHÔNG tạo line mới. Chỉ áp discount lên các line thuộc combo khi combo đủ.
        - Discount: percent + fixed (fixed phân bổ theo qty)
        """
        variant_cache = {}
    
        def get_variant(tmpl_id):
            if tmpl_id in variant_cache:
                return variant_cache[tmpl_id]
            variant = self.env["product.product"].search([("product_tmpl_id", "=", tmpl_id)], limit=1)
            variant_cache[tmpl_id] = variant
            return variant
    
        def m2m_cmd(recordset):
            return [(6, 0, recordset.ids if recordset else [])]
    
        for line in self:
            # Không áp KM lên dòng quà/bundle (tránh loop)
            if line.is_gift or line.is_bundle:
                continue
            
            order = line.order_id
    
            # 1) Xóa gift cũ liên kết dòng này (bundle không tạo nữa nên chỉ cần xóa gift/bundle cũ nếu bạn từng tạo)
            remove_lines = order.order_line.filtered(
                lambda l: (l.is_gift or l.is_bundle) and l.linked_line_id and l.linked_line_id.id == line.id
            )
            if remove_lines:
                remove_lines.unlink()
    
            total_percent, total_fixed = 0.0, 0.0
            combo_idx = 1
    
            last_combo_id = False
            last_combo_seq = 0
    
            for promo in line.promotion_ids:
                # ✅ Chỉ cộng discount nếu promo thực sự áp được lên line này
                if hasattr(order, "_promo_applies_to_line") and not order._promo_applies_to_line(promo, line):
                    continue
                
                # 2) Cộng dồn giảm giá
                if promo.discount_type == "percent":
                    total_percent += promo.discount_percent or 0.0
                elif promo.discount_type == "fixed":
                    total_fixed += promo.discount_value or 0.0
    
                # 3) Gift: tạo line quà tặng
                for gift_combo in promo.gift_combo_ids:
                    last_combo_id = gift_combo.id
                    last_combo_seq = combo_idx
                    gift_qty = 1.0
                    purchased_qty = float(line.product_uom_qty or 0.0)
                    if hasattr(line, "_oms_get_promo_bundle_purchased_qty"):
                        try:
                            purchased_qty = float(line._oms_get_promo_bundle_purchased_qty(promo) or 0.0)
                        except Exception:
                            purchased_qty = float(line.product_uom_qty or 0.0)
                    if hasattr(promo, "_oms_get_effective_gift_qty"):
                        gift_qty = float(promo._oms_get_effective_gift_qty(purchased_qty) or 0.0)
                    gift_qty = max(gift_qty, 0.0)

                    for gift_product in gift_combo.product_tmpl_ids:
                        variant = get_variant(gift_product.id)
                        if not variant:
                            continue
                        
                        existing = self.env["sale_custom.order.line"].search([
                            ("order_id", "=", order.id),
                            ("product_id", "=", variant.id),
                            ("is_gift", "=", True),
                            ("linked_line_id", "=", line.id),
                            ("gift_combo_id", "=", gift_combo.id),
                            ("gift_combo_sequence", "=", combo_idx),
                        ], limit=1)
    
                        if gift_qty <= 0:
                            if existing:
                                existing.unlink()
                            continue

                        if existing:
                            update_vals = {}
                            if abs(float(existing.product_uom_qty or 0.0) - gift_qty) > 1e-9:
                                update_vals["product_uom_qty"] = gift_qty
                            if update_vals:
                                existing.write(update_vals)
                        else:
                            self.env["sale_custom.order.line"].create({
                                "order_id": order.id,
                                "product_id": variant.id,
                                "product_uom": (line.product_uom.id or variant.uom_id.id),
                                "product_uom_qty": gift_qty,
                                "tax_id": m2m_cmd(line.tax_id),
                                "discount": 0.0,
                                "price_unit": 0.0,
                                "technical_price_unit": 0.0,
    
                                # ✅ FIX NOT NULL (tránh lỗi bạn đang gặp)
                                "customer_lead": 0.0,
                                "qty_delivered_method": "manual",
    
                                "is_gift": True,
                                "linked_line_id": line.id,
                                "gift_combo_id": gift_combo.id,
                                "gift_combo_sequence": combo_idx,
                                "name": f"Quà tặng [{promo.code}]",
                            })
    
                    combo_idx += 1
    
                # 4) Bundle: không create line nữa -> không làm gì ở đây
                #    Bundle đã được chặn/cho phép ở _promo_applies_to_line()
    
            # 5) Tính discount cuối
            new_discount = min(total_percent, 100.0) if total_percent > 0 else 0.0
            new_fixed = total_fixed if total_fixed > 0 else 0.0
    
            base = line.oms_base_price or line.get_oms_base_price_cached()
            qty = line.product_uom_qty or 1.0
            qty_safe = max(qty, 1.0)
    
            vals_update = {
                "fixed_discount": new_fixed,
                "oms_base_price": base,
            }
    
            if last_combo_id:
                vals_update.update({
                    "gift_combo_id": last_combo_id,
                    "gift_combo_sequence": last_combo_seq,
                })
    
            # 6) Giá tay: không đụng price_unit/technical_price_unit
            if line._is_manual_price():
                vals_update["discount"] = new_discount
                line.write(vals_update)
                continue
            
            # 7) Không double-discount:
            # net_unit = base*(1 - d) - fixed/qty
            d = new_discount or 0.0
            factor = 1.0 - (d / 100.0)
    
            if factor <= 0.0:
                price_unit = 0.0
            else:
                net_unit = (base * factor) - (new_fixed / qty_safe)
                net_unit = max(net_unit, 0.0)
                price_unit = max(net_unit / factor, 0.0)
    
            vals_update.update({
                "discount": d,
                "price_unit": price_unit,
                "technical_price_unit": price_unit,
            })
    
            line.write(vals_update)
    

    
    def unlink(self):
        # Logistics không được xóa dòng nào
        if self._is_logistics_user():
            raise UserError(_("Bạn không có quyền xóa dòng sản phẩm."))
    
        # vẫn giữ logic xóa kèm gift/bundle của bạn
        gift_bundle_line_ids = set()
        for line in self:
            child_lines = self.search([
                ('linked_line_id', '=', line.id),
                '|', ('is_gift', '=', True), ('is_bundle', '=', True)
            ])
            gift_bundle_line_ids.update(child_lines.ids)
        res = super(SaleOrderLine, self).unlink()
        if gift_bundle_line_ids:
            gift_bundle_lines = self.browse(list(gift_bundle_line_ids)).exists()
            if gift_bundle_lines:
                gift_bundle_lines.unlink()
        return res

    # Khi đổi product_id trong form, không tự gán promotion để tránh nhảy % CK tạm thời.
    @api.onchange('product_id')
    def _onchange_product_id_auto_promotion(self):
        clear_vals = {
            'promotion_ids': [(5, 0, 0)],
            'promotion_selected_ids': [(5, 0, 0)],
            'fixed_discount': 0.0,
            'discount': 0.0,
        }
        if not self.product_id:
            self.update(clear_vals)
            return
        self.update(clear_vals)
        return

    # Khi xóa khuyến mãi thủ công thì cũng cập nhật lại discount
    @api.onchange('promotion_ids')
    def _onchange_promotion_ids(self):
        self.apply_promotions_to_line()

    def is_bundle_combo_unlocked(self, promotion):
        """
        Kiểm tra order hiện tại đã mua đủ các sản phẩm trong combo mua kèm chưa để unlock KM này.
        """
        order = self.order_id
        for bundle_combo in promotion.bundle_combo_ids:
            matched = True
            for combo_line in bundle_combo.product_tmpl_ids:
                qty_in_order = sum(
                    l.product_id.product_tmpl_id.id == combo_line.product_tmpl_id.id
                    for l in order.order_line
                    if not l.is_gift and not l.is_bundle
                )
                if qty_in_order < combo_line.quantity:
                    matched = False
                    break
            if matched:
                return True  # Có ít nhất 1 combo đủ điều kiện
        return False
    
    def get_valid_promotions_for_line(self):
        self.ensure_one()
        today = fields.Date.today()
        tmpl_id = self.product_id.product_tmpl_id.id
        categ_id = self.product_id.categ_id.id

        all_promos = self.env['oms.promotion'].search([
            ('valid_from', '<=', today),
            ('valid_to', '>=', today),
            '|',
                ('apply_product_line_ids', '=', False),   # <-- thêm dòng này
                '|',
                    ('apply_product_line_ids.product_tmpl_id', '=', tmpl_id),
                    ('apply_product_line_ids.product_category_id', '=', categ_id),
        ])

        result = []
        for promo in all_promos:
            if promo.bundle_combo_ids:
                if self.is_bundle_combo_unlocked(promo):
                    result.append(promo)
            else:
                result.append(promo)
        return self.env['oms.promotion'].browse([r.id for r in result])
    
    # --- Line kind (BT/BK/KM) ---

    line_kind = fields.Selection(
        [('BT', 'Bình thường'), ('BK', 'Bán kèm'), ('KM', 'Khuyến mãi')],
        string="Loại hàng", default='BT', index=True
    )

    # ---- Đồng bộ cờ đang có với Loại hàng
    @api.onchange('is_gift', 'is_bundle', 'product_id')
    def _onchange_flags_to_line_kind(self):
        for l in self:
            if l.is_gift:
                l.line_kind = 'KM'
            elif l.is_bundle:
                l.line_kind = 'BK'
            elif not l.line_kind:
                l.line_kind = 'BT'

    # ---- Đổi loại hàng => ép giá đúng quy tắc
    @api.onchange('line_kind')
    def _onchange_line_kind(self):
        for l in self:
            if l.line_kind == 'BK':
                l.is_bundle, l.is_gift = True, False
                l.price_unit = 0.0
            elif l.line_kind == 'KM':
                l.is_bundle, l.is_gift = False, True
                l.price_unit = 0.0
            else:  # BT
                l.is_bundle = l.is_gift = False

    # ---- Cấm sửa giá cho BK/KM (reset về 0 và cảnh báo)
    @api.onchange('price_unit')
    def _onchange_price_unit_guard(self):
        warn = None
        for l in self:
            if l.line_kind in ('BK', 'KM') and abs(l.price_unit or 0.0) > 1e-9:
                l.price_unit = 0.0
                warn = {
                    'title': _('Không thể sửa giá'),
                    'message': _('Loại hàng %s luôn có đơn giá = 0.') %
                               ('Bán kèm' if l.line_kind == 'BK' else 'Khuyến mãi')
                }
        if warn:
            return {'warning': warn}

    # # ---- Ràng buộc cuối cùng khi lưu
    # @api.constrains('line_kind', 'price_unit', 'product_uom_qty', 'display_type')
    # def _check_line_kind_price(self):
    #     for l in self:
    #         if l.display_type:
    #             continue
    #         if l.line_kind == 'BT':
    #             if (l.product_id and l.product_uom_qty > 0) and (l.price_unit or 0.0) <= 0.0:
    #                 raise ValidationError(_("Dòng 'Bình thường' phải có đơn giá > 0."))
    #         elif l.line_kind in ('BK', 'KM'):
    #             if abs(l.price_unit or 0.0) > 1e-9:
    #                 raise ValidationError(_("Dòng '%s' phải có đơn giá = 0.") %
    #                                       (dict(self._fields['line_kind'].selection)[l.line_kind]))
