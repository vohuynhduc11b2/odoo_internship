{
    'name': 'OMS Solar Website Live Stock Warning',
    'version': '18.0.1.0.0',
    'summary': 'Hide website stock quantity and show only low-stock warning using live inventory refresh',
    'author': 'OpenAI',
    'license': 'LGPL-3',
    'category': 'Website',
    'depends': [
        'website_sale_custom',
        'oms_solar_followup_custom',
    ],
    'assets': {
        'web.assets_frontend': [
            'oms_solar_stock_live_warning/static/src/js/oms_live_stock_warning.js',
        ],
    },
    'data': [
        'views/website_templates.xml',
    ],
    'installable': True,
    'application': False,
}
