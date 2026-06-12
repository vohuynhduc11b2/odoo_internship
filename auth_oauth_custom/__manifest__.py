# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'OAuth2 Authentication',
    'category': 'Hidden/Tools',
    'description': """
Allow users to login through OAuth2 Provider.
=============================================
""",
    'depends': ['base', 'web', 'base_setup', 'auth_signup'],
    'data': [
        'data/auth_oauth_custom_data.xml',
        'views/auth_oauth_custom_views.xml',
        'views/res_users_views.xml',
        #'views/res_config_settings_views.xml',
        'views/auth_oauth_custom_templates.xml',
        'security/ir.model.access.csv',
    ],
    'assets': {
        'web.assets_frontend': [
            'auth_oauth_custom/static/**/*',
        ],
    },
    'license': 'LGPL-3',
}
