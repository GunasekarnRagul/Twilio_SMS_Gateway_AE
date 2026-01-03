from odoo import models, fields, api
from odoo.exceptions import UserError
import requests
import json


class TwilioConfig(models.Model):
    _name = "twilio.config"
    _description = "Twilio Configuration"
    _rec_name = "name"

    # Allow only 1 record
    _sql_constraints = [
        ('single_record_check', 'unique(id)', 'Only one Twilio Configuration record is allowed.')
    ]

    name = fields.Char(default="Twilio Settings")
    
    account_name = fields.Char(string="Account Name", readonly=True)

    # Credentials
    account_sid = fields.Char(string="Account SID")
    auth_token = fields.Char(string="Auth Token")
    twilio_number = fields.Char(string="Twilio Number (SMS)")

    # Status
    connection_status = fields.Selection(
        [('unknown', 'Unknown'), ('connected', 'Connected'), ('failed', 'Failed')],
        default='unknown',
        readonly=True
    )
    last_tested = fields.Datetime(readonly=True)

    # Extra API Info
    account_type = fields.Char(readonly=True)
    account_balance = fields.Char(readonly=True)
    total_messages_sent = fields.Char(readonly=True)
    current_bill_amount = fields.Char(readonly=True)

    # ------------------------------------------------------------------------------
    # 🔥 1. Auto-refresh on form open
    # ------------------------------------------------------------------------------
    @api.model
    def action_open_settings(self):
        config = self.search([], limit=1)

        if not config:
            config = self.create({'name': 'Twilio Settings'})
        else:
            if config.connection_status == "connected":
                try:
                    config.update_twilio_usage()
                    
                except Exception:
                    pass  # do not block the form if API fails

        return {
            'type': 'ir.actions.act_window',
            'name': 'Twilio Settings',
            'res_model': 'twilio.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
            
        }

    # ------------------------------------------------------------------------------
    # 🔥 2. Fetch Live Twilio Usage (Balance, Today SMS, Bill)
    # ------------------------------------------------------------------------------
    def update_twilio_usage(self):
        self.ensure_one()

        bal_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Balance.json"
        usage_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Usage/Records/Today.json"

        # ---- Fetch Balance ----
        bal_res = requests.get(bal_url, auth=(self.account_sid, self.auth_token), timeout=10)
        balance = "0"
        currency = ""

        if bal_res.status_code == 200:
            data = bal_res.json()
            balance = data.get("balance", "0")
            currency = data.get("currency", "")

        # ---- Fetch Usage ----
        usage_res = requests.get(usage_url, auth=(self.account_sid, self.auth_token), timeout=10)
        sms_count = "0"
        bill = "0"

        if usage_res.status_code == 200:
            for rec in usage_res.json().get("usage_records", []):
                if rec.get("category") in ["sms", "messages", "sms-outbound"]:
                    sms_count = rec.get("usage", "0")
                    bill = rec.get("price", "0")

        # Save
        self.write({
            'account_balance': f"{balance} {currency}",
            'total_messages_sent': sms_count,
            'current_bill_amount': bill,
        })

    # ------------------------------------------------------------------------------
    # 🔥 3. Test Connection (fetch account + usage)
    # ------------------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()

        if not self.account_sid or not self.auth_token:
            raise UserError("Please enter both SID and Auth Token.")
        
        if not self.twilio_number:
            raise UserError("Please enter the vaild twilio_number")

        acc_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}.json"

        try:
            res = requests.get(acc_url, auth=(self.account_sid, self.auth_token), timeout=10)
            data = res.json()

            if res.status_code != 200:
                raise UserError(data.get("message", "Invalid credentials."))

            # Save basic info
            self.write({
                'connection_status': 'connected',
                'last_tested': fields.Datetime.now(),
                'account_type': data.get("type", "N/A"),
                'account_name': data.get("friendly_name", "N/A"),
            })

            # 🔥 Fetch balance + usage
            self.update_twilio_usage()

            return {'type': 'ir.actions.client', 'tag': 'reload'}

        except Exception as e:
            self.write({'connection_status': 'failed'})
            raise UserError(f"Connection Failed: {str(e)}")

    # ------------------------------------------------------------------------------
    # 🔥 4. Disconnect
    # ------------------------------------------------------------------------------
    def action_disconnect(self):
        self.write({
            'account_sid': False,
            'auth_token': False,
            'twilio_number': False,
            'connection_status': 'unknown',
            'last_tested': False,
            'account_type': False,
            'account_balance': False,
            'total_messages_sent': False,
            'current_bill_amount': False,
            'account_name': False,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}
