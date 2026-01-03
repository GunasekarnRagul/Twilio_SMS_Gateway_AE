from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import requests
import logging
import re

_logger = logging.getLogger(__name__)


class SaleOrderSMSConfig(models.Model):
    _name = "sale.order.sms.config"
    _description = "Sales Order SMS Configuration"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"

    name = fields.Char(string="Configuration Name", default="Sales Order SMS", required=True)
    
    # Enable/Disable Feature
    is_active = fields.Boolean(
        string="Enable SMS Notification",
        default=False,
        help="Enable or disable automatic SMS sending when sales order is confirmed",
        tracking=True
    )
    
    # SMS Template
    message_template = fields.Text(
        string="SMS Template",
        required=True,
        default="Hello {partner_name}, your order {order_name} has been confirmed. Total: {currency}{amount_total}. Thank you!",
        help="Use placeholders like {partner_name}, {order_name}, {amount_total}, {date_order}, {user_name}, {company_name}",
        tracking=True
    )
    
    # Preview
    preview_message = fields.Text(
        string="Preview",
        compute="_compute_preview_message",
        help="Preview of how the SMS will look"
    )
    
    # Statistics
    total_sent = fields.Integer(string="Total SMS Sent", readonly=True, default=0)
    total_failed = fields.Integer(string="Total SMS Failed", readonly=True, default=0)
    
    # Tracking
    create_date = fields.Datetime(string="Created On", readonly=True)
    write_date = fields.Datetime(string="Last Updated", readonly=True)

    @api.depends('message_template')
    def _compute_preview_message(self):
        """Generate a preview of the SMS template with sample data"""
        for rec in self:
            if rec.message_template:
                sample_data = {
                    'partner_name': 'John Doe',
                    'order_name': 'SO001',
                    'amount_total': '1,250.00',
                    'date_order': '2025-01-15',
                    'user_name': 'Sales Manager',
                    'company_name': 'Your Company',
                    'order_state': 'Confirmed',
                    'product_names': 'Product A, Product B',
                    'currency': '$'
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
        Strict Logic: Return the first record found where is_active is True.
        If is_active is False in the DB, this returns NOTHING.
        """
        return self.search([('is_active', '=', True)], limit=1)

    @api.model
    def action_open_sms_config(self):
     config = self.search([], limit=1)
     if not config:
        config = self.create({
            'name': 'Sales Order SMS'
        })

     return {
        'type': 'ir.actions.act_window',
        'name': 'Sales Order SMS Configuration',
        'res_model': 'sale.order.sms.config',
        'view_mode': 'form',
        'res_id': config.id,
        'target': 'current',
    }



class SaleOrder(models.Model):
    _inherit = "sale.order"

    sms_sent = fields.Boolean(string="SMS Sent", default=False, readonly=True, copy=False)
    sms_log_ids = fields.One2many(
        'sms.log', 
        'sale_order_id', 
        string="SMS Logs",
        help="SMS delivery logs for this order"
    )
    sms_log_count = fields.Integer(
        string="SMS Count",
        compute="_compute_sms_log_count"
    )

    @api.depends('sms_log_ids')
    def _compute_sms_log_count(self):
        for order in self:
            order.sms_log_count = len(order.sms_log_ids)

    def action_view_sms_logs(self):
        """Open SMS logs related to this order"""
        self.ensure_one()
        return {
            'name': _('SMS Logs - %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'sms.log',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }

    def _prepare_sms_data(self, template):
        """Prepare SMS message by replacing placeholders with actual order data"""
        self.ensure_one()
        
        # CRITICAL FIX: Filter out lines with no product (Sections/Notes)
        product_names_list = [name for name in self.order_line.mapped('product_id.name') if name]
        product_names = ", ".join(product_names_list[:3])  # First 3 products
        
        if len(product_names_list) > 3:
            product_names += f" and {len(product_names_list) - 3} more"
        
        data = {
            'partner_name': self.partner_id.name or 'Customer',
            'order_name': self.name or '',
            'amount_total': f"{self.amount_total:,.2f}",
            'date_order': self.date_order.strftime('%Y-%m-%d') if self.date_order else '',
            'user_name': self.user_id.name or '',
            'company_name': self.company_id.name or '',
            'order_state': dict(self._fields['state'].selection).get(self.state, ''),
            'product_names': product_names or 'N/A',
            'currency': self.currency_id.symbol or '',
        }
        
        try:
            message = template.format(**data)
            return message
        except KeyError as e:
            _logger.error(f"Invalid placeholder in template: {e}")
            raise UserError(_("Invalid placeholder in SMS template: %s") % e)

    def _send_order_confirmation_sms(self):
        """Send SMS notification for order confirmation"""
        self.ensure_one()
        
        _logger.info(f"=== SMS SEND ATTEMPT for order {self.name} ===")
        
        # 1. Get Config (Checks DB for True)
        sms_config = self.env['sale.order.sms.config'].get_active_config()
        
        # 2. Check: If no 'True' config found (meaning it is False), STOP.
        if not sms_config:
            _logger.info("SMS SKIP: No active SMS configuration found.")
            return False
            
        _logger.info(f"Using SMS Config: {sms_config.name}")
        
        # Check if SMS already sent
        if self.sms_sent:
            _logger.info(f"SMS already sent for order {self.name}")
            return False
        
        # Check if customer has mobile number
        if not self.partner_id.mobile and not self.partner_id.phone:
            _logger.warning(f"No mobile number found for customer {self.partner_id.name}")
            return False
        
        # Get Twilio configuration
        twilio_config = self.env['twilio.config'].search([], limit=1)
        if not twilio_config:
            _logger.error("Twilio configuration not found")
            return False
            
        _logger.info(f"Twilio Config - Status: {twilio_config.connection_status}")
        
        if twilio_config.connection_status != 'connected':
            _logger.error("Twilio is not connected")
            return False
        
        if not twilio_config.account_sid or not twilio_config.auth_token or not twilio_config.twilio_number:
            _logger.error("Twilio credentials are missing")
            return False
        
        # Prepare message
        try:
            message_body = self._prepare_sms_data(sms_config.message_template)
            _logger.info(f"SMS Message prepared: {message_body[:50]}...")
        except Exception as e:
            _logger.error(f"Failed to prepare SMS data: {e}")
            return False
        
        # Get recipient number (prefer mobile over phone)
        recipient_number = self.partner_id.mobile or self.partner_id.phone
        recipient_number = recipient_number.strip()
        _logger.info(f"Recipient number: {recipient_number}")
        
        # Send SMS via Twilio
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
            _logger.info(f"Twilio API Response: HTTP {response.status_code}")
            
            if response.status_code in (200, 201):
                # Success - Create log
                self.env['sms.log'].create({
                    'to_number': recipient_number,
                    'message_body': message_body,
                    'status': 'sent',
                    'source_model': 'sale.order',
                    'sale_order_id': self.id,
                    'api_response': f"HTTP {response.status_code} - {response.json().get('sid', 'N/A')}"
                })
                
                self.sms_sent = True
                sms_config.write({'total_sent': sms_config.total_sent + 1})
                
                _logger.info(f"SMS sent successfully for order {self.name} to {recipient_number}")
                return True
            else:
                # Failed - Create log
                error_msg = response.text
                try:
                    error_msg = response.json().get('message', response.text)
                except:
                    pass
                
                self.env['sms.log'].create({
                    'to_number': recipient_number,
                    'message_body': message_body,
                    'status': 'failed',
                    'source_model': 'sale.order',
                    'sale_order_id': self.id,
                    'api_response': f"HTTP {response.status_code} - {error_msg}"
                })
                
                sms_config.write({'total_failed': sms_config.total_failed + 1})
                _logger.error(f"Failed to send SMS for order {self.name}: {error_msg}")
                return False
                
        except Exception as e:
            # Exception - Create log
            self.env['sms.log'].create({
                'to_number': recipient_number,
                'message_body': message_body,
                'status': 'failed',
                'source_model': 'sale.order',
                'sale_order_id': self.id,
                'api_response': f"Error: {str(e)}"
            })
            
            sms_config.write({'total_failed': sms_config.total_failed + 1})
            _logger.exception(f"Exception while sending SMS for order {self.name}")
            return False

    def action_confirm(self):
        """Override action_confirm to send SMS after order confirmation"""
        res = super(SaleOrder, self).action_confirm()
        
        # Send SMS for each confirmed order
        for order in self:
            try:
                order._send_order_confirmation_sms()
            except Exception as e:
                # Don't block order confirmation if SMS fails
                _logger.exception(f"Failed to send SMS for order {order.name}: {e}")
        
        return res

    def action_send_sms_manually(self):
        """Manually send SMS for this order"""
        self.ensure_one()
        
        if self.state not in ['sale', 'done']:
            raise UserError(_("SMS can only be sent for confirmed orders."))
        
        # Reset sms_sent to allow resending
        self.sms_sent = False
        
        success = self._send_order_confirmation_sms()
        
        if success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('SMS sent successfully to %s') % self.partner_id.name,
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Failed'),
                    'message': _('Failed to send SMS. Please enable Auto SMS.'),
                    'type': 'danger',
                    'sticky': True,
                }
            }


class SMSLog(models.Model):
    _inherit = "sms.log"

    # EXTEND selection field to allow 'sale.order'
    source_model = fields.Selection(
        selection_add=[('sale.order', 'Sales Order')],
        ondelete={'sale.order': 'cascade'}
    )

    sale_order_id = fields.Many2one(
        'sale.order',
        string="Sales Order",
        ondelete='cascade',
        help="Related sales order"
    )

    