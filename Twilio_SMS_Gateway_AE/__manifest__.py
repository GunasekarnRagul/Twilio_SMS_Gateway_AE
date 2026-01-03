# -*- coding: utf-8 -*-
{
    'name': 'Odoo Twilio SMS Gateway | Advanced Edition (AE)',
    'version': '19.0.1.0.0',
    'license': 'OPL-1',
    'price': 18.0,
    'currency': 'USD',
    'support': 'cloudaddonstechnologies@gmail.com',
    'summary': 'Twilio SMS integration for Odoo with bulk messaging, templates.',
    'author': 'CloudAddons Technologies',
    'category': 'Tools',
    'images': ['static/description/main_screenshot.jpg'],
    'depends': [
        'sale', 
        'base', 
        'mail', 
        'sales_team', 
        'stock', 
        'delivery', 
        'sale_stock', 
        'sms',        # CRITICAL: Provides the 'mobile' field
        'stock_sms'   # Fixes the KeyError: 'stock.picking.sms.config'
    ],

    'assets': {
        'web.assets_backend': [
            'Twilio_SMS_Gateway_AE/static/src/style.css',
        ],
    },
   'data': [
        'security/ir.model.access.csv',
        'wizards/sms_import_wizard_views.xml',
        'views/twilio_sms_view.xml',
        'views/twilio_whatsapp_view.xml',
        'views/twilio_action.xml',
        'views/twilio_sms_group_view.xml',
        'views/twilio_sms_templates_view.xml',     
        'views/twilio_whatsapp_templates_view.xml', 
        'views/stock_sms_views.xml',
        'views/sms_log.xml',
        'views/sale_order.xml',
        'views/twilio_config_view.xml',
        'views/twilio_menu.xml',
        'data/cron.xml',
        'data/sms_group_corn.xml',
    ],


    'installable': True,
    'application': True,
    'auto_install': False,
}
