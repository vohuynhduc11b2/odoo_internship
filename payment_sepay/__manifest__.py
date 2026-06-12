# -*- coding: utf-8 -*-
{
    "name": "UC SePay Payment Provider",
    "version": "18.0.1.0.0",
    # "category": "Accounting",   # bỏ hẳn để tránh tạo category mới bị trùng
    "summary": "SePay payment provider integration for Odoo 18 (QR + webhook + polling)",
    "author": "UC",
    "license": "LGPL-3",
    "depends": [
        "payment",
        "website_sale_custom",
        "account",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/payment_provider_data.xml",
        "data/payment_provider_logo.xml",
        "views/payment_provider_views.xml",
        "static/src/xml/sepay_templates.xml",
        "views/website_sale_payment_inherit.xml",
        "views/sepay_unc_thankyou.xml",
        "views/sepay_credit_pending.xml",
        "views/sepay_redirect_form.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "payment_sepay/static/src/scss/sepay_checkout.scss",
            "payment_sepay/static/src/js/sepay_payment_page.js",
            "payment_sepay/static/src/js/website_sale_partial_payment.js",
            "payment_sepay/static/src/js/partial_payment_inject.js",
            "payment_sepay/static/src/js/payment_deposit_toggle.js",
            "payment_sepay/static/src/js/sepay_redirect_patch.js",
            "payment_sepay/static/src/js/sepay_unc_upload.js",
            "payment_sepay/static/src/js/checkout_unc_require_file.js",
            "payment_sepay/static/src/js/sepay_unc_payment_form_patch.js",
        ],
    },
    "installable": True,
    "application": False,
}
