from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

# 1. CONFIGURATION MODEL
class StockPickingSMSConfig(models.Model):
    _name = "stock.picking.sms.config"
    _description = "Delivery SMS Configuration"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"

    name = fields.Char(string="Configuration Name", default="Delivery SMS", required=True)
    
    is_active = fields.Boolean(
        string="Enable SMS Notification",
        help="Enable automatic SMS when Delivery Order is Validated",
        tracking=True
    )
    
    message_template = fields.Text(
        string="SMS Template",
        required=True,
        default="Hello {partner_name}, your delivery {picking_name} has been shipped via {carrier}. Tracking: {tracking_ref}. Thank you!",
        help="Placeholders: {partner_name}, {picking_name}, {origin}, {carrier}, {tracking_ref}, {company_name}, {scheduled_date}",
        tracking=True
    )
    
    preview_message = fields.Text(
        string="Preview",
        compute="_compute_preview_message",
        help="Preview of how the SMS will look"
    )
    
    total_sent = fields.Integer(string="Total SMS Sent", readonly=True, default=0)
    total_failed = fields.Integer(string="Total SMS Failed", readonly=True, default=0)

    @api.depends('message_template')
    def _compute_preview_message(self):
        for rec in self:
            if rec.message_template:
                sample_data = {
                    'partner_name': 'Jane Doe',
                    'picking_name': 'WH/OUT/0001',
                    'origin': 'SO001',
                    'carrier': 'FedEx',
                    'tracking_ref': '1234567890',
                    'company_name': 'My Company',
                    'scheduled_date': '2025-01-20',
                    'state': 'Done'
                }
                try:
                    rec.preview_message = rec.message_template.format(**sample_data)
                except KeyError as e:
                    rec.preview_message = f"Error in template: Invalid placeholder {e}"
            else:
                rec.preview_message = ""

    @api.model
    def get_active_config(self):
        """
        Get the active SMS configuration.
        Logic: Search for the NEWEST record where is_active=True.
        If no active record exists, return empty (None).
        """
        return self.search([('is_active', '=', True)], order='id desc', limit=1)

    @api.model
    def action_open_delivery_sms_config(self):
     config = self.search([], limit=1)
     if not config:
        config = self.create({
            'name': 'Delivery SMS'
        })

     return {
        'type': 'ir.actions.act_window',
        'name': 'Delivery SMS Configuration',
        'res_model': 'stock.picking.sms.config',
        'view_mode': 'form',
        'res_id': config.id,
        'target': 'current',
    }



# 2. STOCK PICKING MODEL (INHERIT)
class StockPicking(models.Model):
    _inherit = "stock.picking"

    sms_sent = fields.Boolean(string="SMS Sent", default=False, readonly=True, copy=False)
    sms_log_ids = fields.One2many('sms.log', 'picking_id', string="SMS Logs")
    sms_log_count = fields.Integer(compute="_compute_sms_log_count")

    @api.depends('sms_log_ids')
    def _compute_sms_log_count(self):
        for pick in self:
            pick.sms_log_count = len(pick.sms_log_ids)

    def action_view_sms_logs(self):
        self.ensure_one()
        return {
            'name': _('SMS Logs - %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'sms.log',
            'view_mode': 'tree,form',
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id},
        }

    def _prepare_sms_data(self, template):
        self.ensure_one()
        
        # Safe access to Carrier fields
        carrier = getattr(self, 'carrier_id', None)
        carrier_name = carrier.name if carrier else 'Delivery Service'
        
        tracking_ref = getattr(self, 'carrier_tracking_ref', 'N/A')
        if not tracking_ref:
            tracking_ref = 'N/A'
        
        data = {
            'partner_name': self.partner_id.name or 'Customer',
            'picking_name': self.name or '',
            'origin': self.origin or 'N/A',
            'carrier': carrier_name,
            'tracking_ref': tracking_ref,
            'company_name': self.company_id.name or '',
            'scheduled_date': self.scheduled_date.strftime('%Y-%m-%d') if self.scheduled_date else '',
            'state': 'Done',
        }
        try:
            return template.format(**data)
        except KeyError as e:
            _logger.error(f"Template Error: {e}")
            raise UserError(f"SMS Template Error: Invalid placeholder {e}")

    def _send_delivery_sms(self):
        self.ensure_one()
        
        # 1. Get Active Config (Strict Check)
        sms_config = self.env['stock.picking.sms.config'].get_active_config()
        
        # 2. Logic: If NO active config, STOP (Return False).
        if not sms_config:
            _logger.info("SMS SKIP: No active delivery SMS configuration found.")
            return False
            
        if self.sms_sent:
            return False

        if not self.partner_id.mobile and not self.partner_id.phone:
            # We log a warning but don't crash the delivery validation
            _logger.warning(f"SMS SKIP: No Phone/Mobile found for {self.partner_id.name}")
            return False

        # Check Twilio Configuration
        twilio_config = self.env['twilio.config'].search([], limit=1)
        if not twilio_config:
            _logger.error("Twilio configuration not found")
            return False
            
        if not twilio_config.account_sid or not twilio_config.auth_token:
             _logger.error("Twilio Credentials (SID/Token) are missing.")
             return False

        # Prepare Data
        try:
            message_body = self._prepare_sms_data(sms_config.message_template)
        except Exception as e:
            _logger.error(str(e))
            return False

        recipient_number = self.partner_id.mobile or self.partner_id.phone
        recipient_number = recipient_number.strip()

        url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_config.account_sid}/Messages.json"
        auth = (twilio_config.account_sid, twilio_config.auth_token)
        payload = {
            'From': twilio_config.twilio_number,
            'To': recipient_number,
            'Body': message_body
        }

        try:
            _logger.info("Sending SMS via Twilio API...")
            response = requests.post(url, data=payload, auth=auth, timeout=15)
            
            if response.status_code in (200, 201):
                self.env['sms.log'].create({
                    'to_number': recipient_number,
                    'message_body': message_body,
                    'status': 'sent',
                    'source_model': 'stock.picking',
                    'picking_id': self.id,
                    'api_response': f"HTTP {response.status_code}"
                })
                self.sms_sent = True
                sms_config.write({'total_sent': sms_config.total_sent + 1})
                return True
            else:
                error_msg = response.json().get('message', response.text)
                self.env['sms.log'].create({
                    'to_number': recipient_number,
                    'message_body': message_body,
                    'status': 'failed',
                    'source_model': 'stock.picking',
                    'picking_id': self.id,
                    'api_response': f"HTTP {response.status_code} - {error_msg}"
                })
                sms_config.write({'total_failed': sms_config.total_failed + 1})
                _logger.error(f"Twilio API Failed: {error_msg}")
                return False

        except Exception as e:
            _logger.exception("System Error while connecting to Twilio")
            return False

    def _action_done(self):
        """
        Trigger SMS when transfer is validated
        """
        res = super(StockPicking, self)._action_done()

        for picking in self:
            # Only for Delivery Orders (outgoing) and if not sent yet
            if picking.picking_type_id.code == 'outgoing' and not picking.sms_sent:
                try:
                    picking._send_delivery_sms()
                except Exception as e:
                    # Don't block the delivery validation if SMS fails
                    _logger.exception(f"SMS Failed for picking {picking.name}: {e}")
        
        return res


# 3. LOG MODEL
class SMSLog(models.Model):
    _inherit = "sms.log"

    source_model = fields.Selection(
        selection_add=[('stock.picking', 'Delivery Order')],
        ondelete={'stock.picking': 'cascade'}
    )

    picking_id = fields.Many2one(
        'stock.picking',
        string="Delivery Order",
        ondelete='cascade'
    )