from odoo import models, fields

class TwilioSMSTemplate(models.Model):
    _name = "twilio.sms.template"
    _description = "Twilio SMS Template"

    name = fields.Char("Template Name")
    message = fields.Text("Message")
    
    

class TwilioWhatsAppTemplate(models.Model):
    _name = "twilio.whatsapp.template"
    _description = "Twilio WhatsApp Template"

    name = fields.Char("Template Name")
    message = fields.Text("Message")
