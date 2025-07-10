{
    'name': 'Uber Eats Integration',
    'version': '15.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Integrate Odoo POS with Uber Eats Marketplace',
    'description': """
        This module provides integration between Odoo POS and Uber Eats Marketplace API.
        Features:
        - OAuth 2.0 Authentication
        - Store Connection Management
        - Token Management
    """,
    'author': 'Alvin Lien',
    'depends': ['point_of_sale', 'base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'security/ubereats_security.xml',
        'views/ubereats_config_views.xml',
        'views/res_config_settings_views.xml',
        'views/pos_config_views.xml',
        'views/ubereats_store_views.xml',
        'views/templates.xml',
        'views/ubereats_menu.xml',
        'wizard/ubereats_auth_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'uber_marketplace/static/src/js/ubereats_auth.js',
            'uber_marketplace/static/src/js/ubereats_config_form.js',
        ],
    },
    'installable': True,
    'application': False,
}