# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Partners Geolocation',
    'version': '2.1',
    'category': 'Sales/Sales',
    'description': """
Partners Geolocation
========================
    """,
    'depends': ['base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'views/geo_provider_view.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'data/data.xml',
    ],
    'installable': True,
    'author': 'Odoo S.A.',
    'license': 'LGPL-3',
}
