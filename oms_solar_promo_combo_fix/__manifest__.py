{
    'name': 'OMS Solar Promo Combo Rules',
    'version': '18.0.4.1.0',
    'summary': 'Gift combo selection and promo prices for main/accessory products',
    'author': 'OpenAI',
    'license': 'LGPL-3',
    'category': 'Sales',
    'depends': ['oms_solar_followup_custom'],
    'data': [
        'security/ir.model.access.csv',
        'views/oms_promotion_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'oms_solar_promo_combo_fix/static/src/js/gift_combo_checkout.js',
        ],
    },
    'installable': True,
    'application': False,
}
