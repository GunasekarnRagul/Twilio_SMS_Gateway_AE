from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class TwilioConfig(models.Model):
    _name = "twilio.config"
    _description = "Twilio Configuration"
    _rec_name = "name"

    _sql_constraints = [
        ('single_record_check', 'unique(id)', 'Only one Twilio Configuration record is allowed.')
    ]

    name = fields.Char(default="Twilio Settings")
    account_name = fields.Char(string="Account Name", readonly=True)
    account_sid = fields.Char(string="Account SID")
    auth_token = fields.Char(string="Auth Token")
    twilio_number = fields.Char(string="Twilio Number (SMS)")

    connection_status = fields.Selection(
        [('unknown', 'Unknown'), ('connected', 'Connected'), ('failed', 'Failed')],
        default='unknown',
        readonly=True
    )
    last_tested = fields.Datetime(readonly=True)
    account_type = fields.Char(readonly=True)
    account_balance = fields.Char(readonly=True)
    total_messages_sent = fields.Char(readonly=True)
    current_bill_amount = fields.Char(readonly=True)
    
    
    @api.model
    def action_open_settings(self):
        """ This method is called by the MenuItem or Server Action """
        config = self.search([], limit=1)
        if not config:
            config = self.create({'name': 'Twilio Settings'})
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Twilio Settings',
            'res_model': 'twilio.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
        }

    def action_test_connection(self):
        self.ensure_one()      
        acc_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}.json"

        try:
            res = requests.get(acc_url, auth=(self.account_sid, self.auth_token), timeout=10)
            data = res.json()

            if res.status_code != 200:
                self.write({'connection_status': 'failed'})
                raise UserError(data.get("message", "Invalid credentials."))

            self.write({
                'connection_status': 'connected',
                'last_tested': fields.Datetime.now(),
                'account_type': data.get("type", "N/A"),
                'account_name': data.get("friendly_name", "N/A"),
            })
            self.update_twilio_usage()
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        except Exception as e:
            self.write({'connection_status': 'failed'})
            raise UserError(_("Connection Failed: %s") % str(e))

    def update_twilio_usage(self):
        self.ensure_one()
        bal_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Balance.json"
        usage_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Usage/Records/Today.json"

        # Balance
        bal_res = requests.get(bal_url, auth=(self.account_sid, self.auth_token), timeout=10)
        balance, currency = "0", ""
        if bal_res.status_code == 200:
            data = bal_res.json()
            balance = data.get("balance", "0")
            currency = data.get("currency", "")

        # Usage
        usage_res = requests.get(usage_url, auth=(self.account_sid, self.auth_token), timeout=10)
        sms_count, bill = "0", "0"
        if usage_res.status_code == 200:
            for rec in usage_res.json().get("usage_records", []):
                if rec.get("category") in ["sms", "messages", "sms-outbound"]:
                    sms_count = rec.get("usage", "0")
                    bill = rec.get("price", "0")

        self.write({
            'account_balance': f"{balance} {currency}",
            'total_messages_sent': sms_count,
            'current_bill_amount': bill,
        })

    def action_disconnect(self):
        self.write({
            'account_sid': False, 'auth_token': False, 'twilio_number': False,
            'connection_status': 'unknown', 'last_tested': False, 'account_type': False,
            'account_balance': False, 'total_messages_sent': False, 'current_bill_amount': False,
            'account_name': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}