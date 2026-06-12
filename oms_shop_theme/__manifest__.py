{
    'name': 'OMS Shop Theme',
    'version': '1.0',
    'category': 'Website/Website',
    'summary': 'OMS shop frontend theme customizations',
    'depends': [
        'website_sale_custom',
    ],
    'data': [
        'views/shop_templates.xml',
        'views/product_detail_templates.xml',
        'views/cart_templates.xml',
        'views/checkout_layout_templates.xml',
        'views/pager_fix.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'oms_shop_theme/static/src/scss/shop.scss',
            'oms_shop_theme/static/src/scss/product_detail.scss',
            'oms_shop_theme/static/src/scss/cart.scss',
            'oms_shop_theme/static/src/scss/checkout.scss',
            'oms_shop_theme/static/src/js/shop.js',
            'oms_shop_theme/static/src/js/product_detail.js',
            'oms_shop_theme/static/src/js/cart.js',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
}
