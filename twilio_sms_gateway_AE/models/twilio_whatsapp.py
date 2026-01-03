from odoo import models, fields, api
from odoo.exceptions import UserError
import requests

class TwilioWhatsApp(models.Model):
    _name = "twilio.whatsapp"
    _description = "Send WhatsApp"
    _rec_name = "recipient_single"
    _order = "create_date desc"

    # 1. UI Fields
    recipient_type = fields.Selection(
        [('single', 'Single Number'), ('multi', 'Multiple Numbers')],
        string="Recipient Type", default='single', required=True
    )
    
    recipient_single = fields.Char(string="WhatsApp Number", help="e.g. +1234567890")
    recipient_multi = fields.Text(string="WhatsApp Numbers", help="Comma separated. e.g. +12345, +67890")
    
    message_body = fields.Text(string="Message", required=True)
    
    # 2. Tracking Fields
    state = fields.Selection(
        [('draft', 'Draft'), ('sent', 'Sent'), ('failed', 'Failed')],
        default='draft', string="Status", readonly=True
    )
    response_log = fields.Text(string="API Response", readonly=True)

    def action_send_whatsapp(self):
        """ Send WhatsApp Message using Twilio """
        self.ensure_one()
        
        # A. Fetch Configuration
        config = self.env['twilio.config'].search([], limit=1)
        if not config or config.connection_status != 'connected':
            raise UserError("Please connect Twilio in Settings first.")
            
        # CHECK: Ensure WhatsApp Number is configured
        if not config.whatsapp_number:
            raise UserError("❌ Error: You must save a 'WhatsApp Number' in Configuration > Settings.")
            
        # B. Prepare Numbers
        numbers_to_send = []
        if self.recipient_type == 'single':
            if not self.recipient_single: raise UserError("Enter a number.")
            numbers_to_send.append(self.recipient_single)
        else:
            if not self.recipient_multi: raise UserError("Enter numbers.")
            numbers_to_send = [x.strip() for x in self.recipient_multi.split(',') if x.strip()]

        # C. Send Loop (WhatsApp Specifics)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json"
        auth = (config.account_sid, config.auth_token)
        
        success_count = 0
        logs = []

        for number in numbers_to_send:
            # IMPORTANT: Twilio requires 'whatsapp:' prefix for source and destination
            # We use config.whatsapp_number here!
            payload = {
                'From': f"whatsapp:{config.whatsapp_number}", 
                'To': f"whatsapp:{number}",
                'Body': self.message_body
            }
            
            try:
                response = requests.post(url, data=payload, auth=auth, timeout=10)
                if response.status_code in [200, 201]:
                    success_count += 1
                    logs.append(f"✅ Sent to {number}")
                else:
                    err = response.json().get('message', 'Unknown Error')
                    logs.append(f"❌ Failed {number}: {err}")
            except Exception as e:
                logs.append(f"❌ Error {number}: {str(e)}")

        self.response_log = "\n".join(logs)
        
        if success_count == len(numbers_to_send):
            self.state = 'sent'
            return {
                'effect': {
                    'fadeout': 'slow',
                    'message': '✔ WhatsApp Messages Sent!',
                    'type': 'rainbow_man',
                }
            }
        else:
            self.state = 'failed'