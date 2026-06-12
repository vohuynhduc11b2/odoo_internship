# Part of Odoo. See LICENSE file for full copyright and licensing details.

import binascii

from odoo import fields, http, _
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.fields import Command
from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.controllers import portal as payment_portal
from odoo.addons.portal.controllers.portal import pager as portal_pager

import logging
import requests

def _as_float(v, default=0.0):
    try:
        if v in (None, False, ""):
            return default
        return float(v)
    except Exception:
        return default

def oms_get_token():
    """
    Lấy Bearer token từ Auth server (dùng được trong controller).
    Nên chuyển username/password sang System Parameter thay vì hardcode.
    """
    ICP = request.env["ir.config_parameter"].sudo()

    username = ICP.get_param("oms.auth.username") or "trungtq"
    password = ICP.get_param("oms.auth.password") or "Trung@2025"
    auth_url = ICP.get_param("oms.auth.url") or "https://auth.datgroup.com.vn/api/auth/login"

    attempts = [
        ("json", {"username": username, "password": password}),
        ("json", {"userName": username, "password": password}),
        ("json", {"email": username, "password": password}),
        ("form", {"username": username, "password": password}),
    ]
    last_err = None

    for mode, payload in attempts:
        try:
            if mode == "json":
                r = requests.post(auth_url, json=payload, timeout=15)
            else:
                r = requests.post(auth_url, data=payload, timeout=15)
        except Exception as e:
            last_err = f"Lỗi kết nối: {e}"
            continue

        if not r.ok:
            last_err = f"Auth HTTP {r.status_code}: {r.text[:500]}"
            continue

        try:
            body = r.json()
        except Exception:
            last_err = f"Phản hồi không phải JSON: {r.text[:500]}"
            continue

        token = (
            body.get("token")
            or body.get("access_token")
            or body.get("accessToken")
            or (body.get("data") or {}).get("access_token")
            or (body.get("Data") or {}).get("Token")
        )
        if token:
            return token

        last_err = f"Không tìm thấy token trong phản hồi: {body}"

    raise UserError(_("Không lấy được token: %s") % last_err)
    
def oms_get_customer_info(cardcode: str) -> dict:
    cardcode = (cardcode or "").strip()
    if not cardcode:
        return {}

    ICP = request.env["ir.config_parameter"].sudo()
    base = (ICP.get_param("oms.api_base_url") or "https://api-dat.datgroup.com.vn").rstrip("/")
    url = f"{base}/OMS/CustomerInfo"

    token = oms_get_token()
    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.get(url, params={"cardcode": cardcode}, headers=headers, timeout=20)
        resp.raise_for_status()
        js = resp.json() or {}
    except Exception as e:
        _logger.warning("OMS/CustomerInfo call failed cardcode=%s err=%s", cardcode, e)
        return {}

    ok = str(js.get("status") or "").upper() in ("TRUE", "1", "OK", "SUCCESS")
    if not ok:
        return {}

    result = js.get("result") or []
    if isinstance(result, list) and result:
        return result[0] or {}
    if isinstance(result, dict):
        return result
    return {}

_logger = logging.getLogger(__name__)

class CustomerPortal(payment_portal.PaymentPortal):
    _items_per_page = 20

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id

        SaleOrder = request.env['sale_custom.order'].sudo()
        if 'quotation_count' in counters:
            values['quotation_count'] = SaleOrder.search_count(self._prepare_quotations_domain(partner))
        if 'order_count' in counters:
            values['order_count'] = SaleOrder.search_count(self._prepare_orders_domain(partner))

        return values


    def _prepare_quotations_domain(self, partner):
        commercial = partner.commercial_partner_id
        return [
            ('partner_id', 'child_of', [commercial.id]),
            ('state', 'in', ['draft', 'sent']),
        ]
    
    def _prepare_orders_domain(self, partner):
        commercial = partner.commercial_partner_id
        return [
            ('partner_id', 'child_of', [commercial.id]),
            ('state', 'in', ['draft', 'sent', 'to_approve', 'waiting_approval', 'approved', 'sale', 'done']),
        ]
    
    def _get_sale_searchbar_sortings(self):
        return {
            'date': {'label': _('Mới nhất'), 'order': 'date_order desc'},
            'date_asc': {'label': _('Cũ nhất'), 'order': 'date_order asc'},
            'amount': {'label': _('Doanh số cao nhất'), 'order': 'amount_total desc'},
            'amount_asc': {'label': _('Doanh số thấp nhất'), 'order': 'amount_total asc'},
        }

    def _get_sale_searchbar_filters(self, quotation_page=False):
        """Filters mapped onto the stored `state` field (domain-searchable).

        Payment-status filtering is done client-side (payment_status is a
        non-stored computed field) — see sale_portal_filters.js.
        """
        if quotation_page:
            return {
                'all': {'label': _('Tất cả'), 'domain': []},
                'draft': {'label': _('Nháp'), 'domain': [('state', '=', 'draft')]},
                'sent': {'label': _('Đã gửi'), 'domain': [('state', '=', 'sent')]},
            }
        return {
            'all': {'label': _('Tất cả'), 'domain': []},
            'quote': {'label': _('Báo giá'), 'domain': [('state', 'in', ['draft', 'sent'])]},
            'approving': {'label': _('Chờ duyệt'), 'domain': [('state', 'in', ['to_approve', 'waiting_approval'])]},
            'confirmed': {'label': _('Đã xác nhận'), 'domain': [('state', 'in', ['approved', 'sale'])]},
            'done': {'label': _('Hoàn tất'), 'domain': [('state', '=', 'done')]},
        }

    def _prepare_sale_portal_rendering_values(
            self, page=1, date_begin=None, date_end=None, sortby=None, filterby=None, quotation_page=False, **kwargs
        ):
            SaleOrder = request.env['sale_custom.order'].sudo()

            if not sortby:
                sortby = 'date'

            partner = request.env.user.partner_id
            values = self._prepare_portal_layout_values()

            if quotation_page:
                url = "/my/quotes"
                domain = self._prepare_quotations_domain(partner)
            else:
                url = "/my/orders"
                domain = self._prepare_orders_domain(partner)

            searchbar_sortings = self._get_sale_searchbar_sortings()
            if sortby not in searchbar_sortings:
                sortby = 'date'
            sort_order = searchbar_sortings[sortby]['order']

            searchbar_filters = self._get_sale_searchbar_filters(quotation_page=quotation_page)
            if not filterby or filterby not in searchbar_filters:
                filterby = 'all'
            domain += searchbar_filters[filterby]['domain']

            if date_begin and date_end:
                domain += [('create_date', '>', date_begin), ('create_date', '<=', date_end)]

            url_args = {'date_begin': date_begin, 'date_end': date_end, 'sortby': sortby, 'filterby': filterby}

            total = SaleOrder.search_count(domain)
            _logger.info("[PORTAL] user=%s partner=%s commercial=%s quotation_page=%s",
                         request.env.user.login,
                         partner.id,
                         partner.commercial_partner_id.id,
                         quotation_page)
            _logger.info("[PORTAL] domain=%s", domain)
            _logger.info("[PORTAL] total=%s", SaleOrder.sudo().search_count(domain))

            pager_values = portal_pager(
                url=url,
                total=total,
                page=page,
                step=self._items_per_page,
                url_args=url_args,
            )

            records = SaleOrder.search(domain, order=sort_order, limit=self._items_per_page, offset=pager_values['offset'])

            empty = SaleOrder.browse()

            values.update({
                'date': date_begin,
                'quotations': records if quotation_page else empty,
                'orders': records if not quotation_page else empty,
                'page_name': 'quote' if quotation_page else 'order',
                'pager': pager_values,
                'default_url': url,
                'sortby': sortby,
                'searchbar_sortings': searchbar_sortings,
                'filterby': filterby,
                'searchbar_filters': searchbar_filters,
            })

            return values


    @http.route(['/my/quotes', '/my/quotes/page/<int:page>'], type='http', auth="user", website=True)
    def portal_my_quotes(self, **kwargs):
        values = self._prepare_sale_portal_rendering_values(quotation_page=True, **kwargs)
        request.session['my_quotations_history'] = values['quotations'].ids[:100]
        return request.render("sale_custom.portal_my_quotations", values)

    @http.route(['/my/orders', '/my/orders/page/<int:page>'], type='http', auth="user", website=True)
    def portal_my_orders(self, **kwargs):
        values = self._prepare_sale_portal_rendering_values(quotation_page=False, **kwargs)

        # ====== OMS CustomerInfo: gọi mỗi lần vào trang /my/orders ======
        partner = request.env.user.partner_id.commercial_partner_id
        cardcode = (partner.ref or "").strip()

        info = oms_get_customer_info(cardcode) if cardcode else {}
        _logger.info(
            "[OMS_CUSTOMER_INFO] portal_my_orders START | user=%s | partner_id=%s | partner=%s | cardcode=%s",
            request.env.user.id,
            partner.id,
            partner.display_name,
            cardcode,
        )
    
        info = {}
        try:
            if cardcode:
                _logger.info(
                    "[OMS_CUSTOMER_INFO] calling oms_get_customer_info(cardcode=%s)",
                    cardcode,
                )
                info = oms_get_customer_info(cardcode) or {}
                _logger.info(
                    "[OMS_CUSTOMER_INFO] response type=%s | data=%s",
                    type(info).__name__,
                    info,
                )
            else:
                _logger.warning(
                    "[OMS_CUSTOMER_INFO] skip call because cardcode is empty | partner_id=%s | partner=%s | ref=%s",
                    partner.id,
                    partner.display_name,
                    partner.ref,
                )
        except Exception:
            _logger.exception(
                "[OMS_CUSTOMER_INFO] ERROR when calling oms_get_customer_info(cardcode=%s)",
                cardcode,
            )
            info = {}
        portal_credit_limit = _as_float(info.get("HanMucTinDung"), 0.0)
        portal_total_debt = _as_float(info.get("TongCongNo"), 0.0)
        portal_total_advance = _as_float(info.get("TongThuUng"), 0.0)
        portal_total_sales = _as_float(info.get("TongDoanhSo"), 0.0)

        # Hạn mức khả dụng (anh đang hiển thị ở UI)
        portal_available_credit = portal_credit_limit - max(portal_total_debt - portal_total_advance, 0.0)

        values.update({
            "portal_customer_name": partner.display_name,
            "portal_view_date": fields.Date.context_today(request.env.user),
            "portal_credit_limit": portal_credit_limit,
            "portal_total_debt": portal_total_debt,
            "portal_total_advance": portal_total_advance,
            "portal_total_sales": portal_total_sales,
            "portal_available_credit": portal_available_credit,
            "portal_company_currency": request.env.company.currency_id,
        })
        # ===============================================================

        request.session['my_orders_history'] = values['orders'].ids[:100]
        return request.render("sale_custom.portal_my_orders", values)


    @http.route(['/my/orders/<int:order_id>'], type='http', auth="public", website=True)
    def portal_order_page(
        self,
        order_id,
        report_type=None,
        access_token=None,
        message=False,
        download=False,
        downpayment=None,
        **kw
    ):
        try:
            order_sudo = self._document_check_access('sale_custom.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')

        if report_type in ('html', 'pdf', 'text'):
            return self._show_report(
                model=order_sudo,
                report_type=report_type,
                report_ref='sale_custom.action_report_saleorder',
                download=download,
            )

        if downpayment is not None:
            is_downpayment = str(downpayment).strip().lower() in ("1", "true", "yes", "deposit", "coc")
            request.session["sepay_mode"] = "qr"
            request.session["oms_payment_mode"] = "deposit" if is_downpayment else "full"
            request.session["website_payment_type"] = "deposit" if is_downpayment else "full"
            request.session["uc_is_deposit"] = 1 if is_downpayment else 0
            request.session["oms_is_deposit"] = 1 if is_downpayment else 0
            request.session["oms_deposit30"] = 1 if is_downpayment else 0
            try:
                pct = float(getattr(order_sudo, "prepayment_percent", 0.0) or 0.0) * 100.0
                if pct > 0:
                    request.session["uc_deposit_percent"] = pct
                    request.session["oms_deposit_percent"] = pct
            except Exception:
                pass

        if request.env.user.share and access_token:
            # If a public/portal user accesses the order with the access token
            # Log a note on the chatter.
            today = fields.Date.today().isoformat()
            session_obj_date = request.session.get('view_quote_%s' % order_sudo.id)
            if session_obj_date != today:
                # store the date as a string in the session to allow serialization
                request.session['view_quote_%s' % order_sudo.id] = today
                # The "Quotation viewed by customer" log note is an information
                # dedicated to the salesman and shouldn't be translated in the customer/website lgg
                context = {'lang': order_sudo.user_id.partner_id.lang or order_sudo.company_id.partner_id.lang}
                author = order_sudo.partner_id if request.env.user._is_public() else request.env.user.partner_id
                msg = _('Quotation viewed by customer %s', author.name)
                del context
                order_sudo.message_post(
                    author_id=author.id,
                    body=msg,
                    message_type="notification",
                    subtype_xmlid="sale_custom.mt_order_viewed",
                )

        backend_url = f'/odoo/action-{order_sudo._get_portal_return_action().id}/{order_sudo.id}'
        values = {
            'sale_order_custom': order_sudo,
            'product_documents': order_sudo._get_product_documents(),
            'message': message,
            'report_type': 'html',
            'backend_url': backend_url,
            'res_company': order_sudo.company_id,  # Used to display correct company logo
        }

        # Payment values
        if order_sudo._has_to_be_paid():
            values.update(
                self._get_payment_values(
                    order_sudo,
                    downpayment=downpayment == 'true' if downpayment is not None else order_sudo.prepayment_percent < 1.0
                )
            )

        if order_sudo.state in ('draft', 'sent', 'cancel'):
            history_session_key = 'my_quotations_history'
        else:
            history_session_key = 'my_orders_history'

        values = self._get_page_view_values(
            order_sudo, access_token, values, history_session_key, False)

        return request.render('sale_custom.sale_order_portal_template', values)

    def _get_payment_values(self, order_sudo, downpayment=False, **kwargs):
        """ Return the payment-specific QWeb context values.

        :param sale_custom.order order_sudo: The sales order being paid.
        :param bool downpayment: Whether the current payment is a downpayment.
        :param dict kwargs: Locally unused data passed to `_get_compatible_providers` and
                            `_get_available_tokens`.
        :return: The payment-specific values.
        :rtype: dict
        """
        logged_in = not request.env.user._is_public()
        partner_sudo = request.env.user.partner_id if logged_in else order_sudo.partner_id
        company = order_sudo.company_id
        if downpayment:
            amount = order_sudo._get_prepayment_required_amount()
        else:
            amount = order_sudo.amount_total - order_sudo.amount_paid
        currency = order_sudo.currency_id

        availability_report = {}
        # Select all the payment methods and tokens that match the payment context.
        providers_sudo = request.env['payment.provider'].sudo()._get_compatible_providers(
            company.id,
            partner_sudo.id,
            amount,
            currency_id=currency.id,
            sale_order_id=order_sudo.id,
            report=availability_report,
            **kwargs,
        )  # In sudo mode to read the fields of providers and partner (if logged out).
        payment_methods_sudo = request.env['payment.method'].sudo()._get_compatible_payment_methods(
            providers_sudo.ids,
            partner_sudo.id,
            currency_id=currency.id,
            sale_order_id=order_sudo.id,
            report=availability_report,
            **kwargs,
        )  # In sudo mode to read the fields of providers.
        tokens_sudo = request.env['payment.token'].sudo()._get_available_tokens(
            providers_sudo.ids, partner_sudo.id, **kwargs
        )  # In sudo mode to read the partner's tokens (if logged out) and provider fields.

        # Make sure that the partner's company matches the invoice's company.
        company_mismatch = not payment_portal.PaymentPortal._can_partner_pay_in_company(
            partner_sudo, company
        )

        portal_page_values = {
            'company_mismatch': company_mismatch,
            'expected_company': company,
        }
        payment_form_values = {
            'show_tokenize_input_mapping': PaymentPortal._compute_show_tokenize_input_mapping(
                providers_sudo, sale_order_id=order_sudo.id
            ),
        }
        payment_context = {
            'amount': amount,
            'currency': currency,
            'partner_id': partner_sudo.id,
            'providers_sudo': providers_sudo,
            'payment_methods_sudo': payment_methods_sudo,
            'tokens_sudo': tokens_sudo,
            'availability_report': availability_report,
            'transaction_route': order_sudo.get_portal_url(suffix='/transaction'),
            'landing_route': order_sudo.get_portal_url(),
            'access_token': order_sudo._portal_ensure_token(),
        }
        return {
            **portal_page_values,
            **payment_form_values,
            **payment_context,
            **self._get_extra_payment_form_values(**kwargs),
        }

    @http.route(['/my/orders/<int:order_id>/accept'], type='json', auth="public", website=True)
    def portal_quote_accept(self, order_id, access_token=None, name=None, signature=None):
        # get from query string if not on json param
        access_token = access_token or request.httprequest.args.get('access_token')
        try:
            order_sudo = self._document_check_access('sale_custom.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return {'error': _('Invalid order.')}

        if not order_sudo._has_to_be_signed():
            return {'error': _('The order is not in a state requiring customer signature.')}
        if not signature:
            return {'error': _('Signature is missing.')}

        try:
            order_sudo.write({
                'signed_by': name,
                'signed_on': fields.Datetime.now(),
                'signature': signature,
            })
            request.env.cr.commit()
        except (TypeError, binascii.Error) as e:
            return {'error': _('Invalid signature data.')}

        if not order_sudo._has_to_be_paid():
            order_sudo._validate_order()

        pdf = request.env['ir.actions.report'].sudo()._render_qweb_pdf('sale_custom.action_report_saleorder', [order_sudo.id])[0]

        order_sudo.message_post(
            attachments=[('%s.pdf' % order_sudo.name, pdf)],
            author_id=(
                order_sudo.partner_id.id
                if request.env.user._is_public()
                else request.env.user.partner_id.id
            ),
            body=_('Order signed by %s', name),
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        query_string = '&message=sign_ok'
        if order_sudo._has_to_be_paid():
            query_string += '&allow_payment=yes'
        return {
            'force_refresh': True,
            'redirect_url': order_sudo.get_portal_url(query_string=query_string),
        }

    @http.route(['/my/orders/<int:order_id>/decline'], type='http', auth="public", methods=['POST'], website=True)
    def portal_quote_decline(self, order_id, access_token=None, decline_message=None, **kwargs):
        try:
            order_sudo = self._document_check_access('sale_custom.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')

        if order_sudo._has_to_be_signed() and decline_message:
            order_sudo._action_cancel()
            # The currency is manually cached while in a sudoed environment to prevent an
            # AccessError. The state of the Sales Order is a dependency of
            # `untaxed_amount_to_invoice`, which is a monetary field. They require the currency to
            # ensure the values are saved in the correct format. However, the currency cannot be
            # read directly during the flush due to access rights, necessitating manual caching.
            order_sudo.order_line.currency_id

            order_sudo.message_post(
                author_id=(
                    order_sudo.partner_id.id
                    if request.env.user._is_public()
                    else request.env.user.partner_id.id
                ),
                body=decline_message,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            redirect_url = order_sudo.get_portal_url()
        else:
            redirect_url = order_sudo.get_portal_url(query_string="&message=cant_reject")

        return request.redirect(redirect_url)

    @http.route('/my/orders/<int:order_id>/document/<int:document_id>', type='http', auth='public')
    def portal_quote_document(self, order_id, document_id, access_token):
        try:
            order_sudo = self._document_check_access('sale_custom.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')

        document = request.env['product.document'].browse(document_id).sudo().exists()
        if not document or not document.active:
            return request.redirect('/my')

        if document not in order_sudo._get_product_documents():
            return request.redirect('/my')

        return request.env['ir.binary']._get_stream_from(
            document.ir_attachment_id,
        ).get_response(as_attachment=True)


class PaymentPortal(payment_portal.PaymentPortal):

    @http.route('/my/orders/<int:order_id>/transaction', type='json', auth='public')
    def portal_order_transaction(self, order_id, access_token, **kwargs):
        """ Create a draft transaction and return its processing values.

        :param int order_id: The sales order to pay, as a `sale_custom.order` id
        :param str access_token: The access token used to authenticate the request
        :param dict kwargs: Locally unused data passed to `_create_transaction`
        :return: The mandatory values for the processing of the transaction
        :rtype: dict
        :raise: ValidationError if the invoice id or the access token is invalid
        """
        # Check the order id and the access token
        try:
            order_sudo = self._document_check_access('sale_custom.order', order_id, access_token)
        except MissingError as error:
            raise error
        except AccessError:
            raise ValidationError(_("The access token is invalid."))

        logged_in = not request.env.user._is_public()
        partner_sudo = request.env.user.partner_id if logged_in else order_sudo.partner_invoice_id
        self._validate_transaction_kwargs(kwargs)
        kwargs.update({
            'partner_id': partner_sudo.id,
            'currency_id': order_sudo.currency_id.id,
            'sale_order_id': order_id,  # Include the SO to allow Subscriptions tokenizing the tx
        })
        tx_sudo = self._create_transaction(
            custom_create_values={'sale_order_ids': [Command.set([order_id])]}, **kwargs,
        )

        return tx_sudo._get_processing_values()

    # Payment overrides

    @http.route()
    def payment_pay(self, *args, amount=None, sale_order_id=None, access_token=None, **kwargs):
        """ Override of `payment` to replace the missing transaction values by that of the sales
        order.

        :param str amount: The (possibly partial) amount to pay used to check the access token
        :param str sale_order_id: The sale order for which a payment id made, as a `sale_custom.order` id
        :param str access_token: The access token used to authenticate the partner
        :return: The result of the parent method
        :rtype: str
        :raise: ValidationError if the order id is invalid
        """
        # Cast numeric parameters as int or float and void them if their str value is malformed
        amount = self._cast_as_float(amount)
        sale_order_id = self._cast_as_int(sale_order_id)
        if sale_order_id:
            order_sudo = request.env['sale_custom.order'].sudo().browse(sale_order_id).exists()
            if not order_sudo:
                raise ValidationError(_("The provided parameters are invalid."))

            # Check the access token against the order values. Done after fetching the order as we
            # need the order fields to check the access token.
            if not payment_utils.check_access_token(
                access_token, order_sudo.partner_invoice_id.id, amount, order_sudo.currency_id.id
            ):
                raise ValidationError(_("The provided parameters are invalid."))

            kwargs.update({
                # To display on the payment form; will be later overwritten when creating the tx.
                'reference': order_sudo.name,
                # To fix the currency if incorrect and avoid mismatches when creating the tx.
                'currency_id': order_sudo.currency_id.id,
                # To fix the partner if incorrect and avoid mismatches when creating the tx.
                'partner_id': order_sudo.partner_invoice_id.id,
                'company_id': order_sudo.company_id.id,
                'sale_order_id': sale_order_id,
            })
        return super().payment_pay(*args, amount=amount, access_token=access_token, **kwargs)

    def _get_extra_payment_form_values(self, sale_order_id=None, access_token=None, **kwargs):
        """ Override of `payment` to reroute the payment flow to the portal view of the sales order.

        :param str sale_order_id: The sale order for which a payment is made, as a `sale_custom.order` id.
        :param str access_token: The portal or payment access token, respectively if we are in a
                                 portal or payment link flow.
        :return: The extended rendering context values.
        :rtype: dict
        """
        form_values = super()._get_extra_payment_form_values(
            sale_order_id=sale_order_id, access_token=access_token, **kwargs
        )
        if sale_order_id:
            sale_order_id = self._cast_as_int(sale_order_id)

            try:  # Check document access against what could be a portal access token.
                order_sudo = self._document_check_access('sale_custom.order', sale_order_id, access_token)
            except AccessError:  # It is a payment access token computed on the payment context.
                if not payment_utils.check_access_token(
                    access_token,
                    kwargs.get('partner_id'),
                    kwargs.get('amount'),
                    kwargs.get('currency_id'),
                ):
                    raise
                order_sudo = request.env['sale_custom.order'].sudo().browse(sale_order_id)

            # Interrupt the payment flow if the sales order has been canceled.
            if order_sudo.state == 'cancel':
                form_values['amount'] = 0.0

            # Reroute the next steps of the payment flow to the portal view of the sales order.
            form_values.update({
                'transaction_route': order_sudo.get_portal_url(suffix='/transaction'),
                'landing_route': order_sudo.get_portal_url(),
                'access_token': order_sudo.access_token,
            })
        return form_values

    @http.route(['/my/orders/<int:order_id>/cancel_request'], type='http', auth='public', website=True, methods=['POST'])
    def portal_order_cancel_request(self, order_id, access_token=None, reason=None, **kw):
        order_sudo = self._document_check_access('sale_custom.order', order_id, access_token=access_token)

        # portal user đã login thì request.env.user có sẵn
        requested_by = request.env.user.id if request.env.user else None

        order_sudo.action_portal_request_cancel(reason=reason, requested_by=requested_by)
        return request.redirect(order_sudo.get_portal_url())

    @http.route(['/my/orders/<int:order_id>/reorder'], type='http', auth='public', website=True, methods=['POST'])
    def portal_order_reorder(self, order_id, access_token=None, **kw):
        # Check access bằng portal token hoặc quyền user đang login
        order = self._document_check_access('sale_custom.order', order_id, access_token=access_token)

        website = request.website
        cart = website.sale_get_order(force_create=True)

        # Copy các dòng sản phẩm (bỏ dòng ghi chú/section, shipping, downpayment, gift/bundle nếu có)
        for line in order.order_line:
            if not line.product_id or line.display_type:
                continue
            if getattr(line, 'is_delivery', False):
                continue
            if getattr(line, 'is_downpayment', False):
                continue
            if getattr(line, 'is_gift', False) or getattr(line, 'is_bundle', False):
                continue

            qty = float(line.product_uom_qty or 0.0)
            if qty <= 0:
                continue

            # Add vào giỏ (cộng dồn nếu đã có)
            cart._cart_update(
                product_id=line.product_id.id,
                add_qty=qty,
                set_qty=None,
            )

        return request.redirect('/shop/cart')


class SaleOrderPortalController(http.Controller):
    @http.route(['/my/orders/<int:order_id>/change_report_type'], type='http', auth='user', website=True, csrf=True)
    def change_report_type(self, order_id, report_type=None, **kwargs):
        order = request.env['sale_custom.order'].sudo().browse(order_id)
        if report_type and report_type in ['co_tu', 'khong_tu', 'san_xuat']:
            order.write({'report_type': report_type})
        return request.redirect('/my/orders/%d?access_token=%s' % (order_id, order.access_token))
