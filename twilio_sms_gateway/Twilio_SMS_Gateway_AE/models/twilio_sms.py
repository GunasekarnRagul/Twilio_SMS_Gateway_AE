from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import format_datetime
from odoo.tools.misc import xlsxwriter  # Required for Excel Export
import requests
import logging
import io
import base64
import pytz  # Ensure pytz is imported

_logger = logging.getLogger(__name__)

class TwilioSMS(models.Model):
    _name = "twilio.sms"
    _description = "Send SMS"
    _rec_name = "recipient_single"
    _order = "create_date desc"
    
    create_date = fields.Datetime(string="Created On", readonly=True)

    # ---------------------------
    # UI FIELDS (Preserved)
    # ---------------------------
    recipient_type = fields.Selection(
        [('single', 'Single Number'), ('multi', 'Multiple Numbers')],
        string="Recipient Type", default='single', required=True
    )

    recipient_single = fields.Char(string="Mobile Number", help="e.g. +1234567890")
    recipient_multi = fields.Text(string="Mobile Numbers", help="Enter numbers separated by commas.")

    # Modified: Not required in DB to allow saving for Import, checked manually in action_send_sms
    message_body = fields.Text(string="Message")

    # ---------------------------
    # SCHEDULING FIELDS (Preserved)
    # ---------------------------
    schedule_datetime = fields.Datetime(
        string="Schedule Date & Time",
        help="Select when the SMS should be sent"
    )
    
    schedule_display = fields.Char(
        string="Scheduled On", 
        compute="_compute_schedule_display"
    )

    timezone = fields.Selection(
        [(tz, tz) for tz in pytz.all_timezones],
        string="Time Zone",
        default=lambda self: self.env.user.tz or "UTC",
        help="Choose your local time zone"
    )

    # ---------------------------
    # TRACKING FIELDS (Preserved & Enhanced)
    # ---------------------------
    sent_count = fields.Integer(string="Sent Count", readonly=True, default=0)
    failed_count = fields.Integer(string="Failed Count", readonly=True, default=0)

    state = fields.Selection(
        [('draft', 'Draft'), 
         ('scheduled', 'Scheduled'), 
         ('sent', 'Sent'), 
         ('partial', 'Partial'), 
         ('failed', 'Failed')],
        default='draft', string="Status", readonly=True
    )

    detailed_status = fields.Char(
        string="Delivery Report", 
        compute="_compute_detailed_status"
    )

    # Original combined log (kept for backward compatibility or overview)
    response_log = fields.Text(string="API Response", readonly=True)

    # NEW: Split logs for specific Excel exports
    log_success = fields.Text(string="Success Log", readonly=True)
    log_failure = fields.Text(string="Failure Log", readonly=True)
    
    mobile_number_display = fields.Char(
        string="Mobile Number", 
        compute="_compute_mobile_number_display"
    )

    # ---------------------------
    # COMPUTE METHODS
    # ---------------------------
    @api.depends('state', 'sent_count', 'failed_count')
    def _compute_detailed_status(self):
        for rec in self:
            if rec.state == 'draft':
                rec.detailed_status = "Draft"
            elif rec.state == 'scheduled':
                rec.detailed_status = "Scheduled"
            elif rec.state == 'sent':
                rec.detailed_status = "Sent"
            elif rec.state == 'failed':
                rec.detailed_status = "Failed"
            elif rec.state == 'partial':
                rec.detailed_status = f"Sent: {rec.sent_count} / Failed: {rec.failed_count}"
            else:
                rec.detailed_status = "-"

    @api.depends('recipient_type', 'recipient_single', 'recipient_multi')
    def _compute_mobile_number_display(self):
        for rec in self:
            if rec.recipient_type == 'single':
                rec.mobile_number_display = rec.recipient_single
            else:
                full_text = rec.recipient_multi or ""
                if len(full_text) > 25:
                    rec.mobile_number_display = full_text[:18] + "..."
                else:
                    rec.mobile_number_display = full_text

    @api.depends('schedule_datetime', 'timezone')
    def _compute_schedule_display(self):
        for rec in self:
            if rec.schedule_datetime:
                rec.schedule_display = format_datetime(
                    self.env, 
                    rec.schedule_datetime, 
                    tz=rec.timezone or self.env.user.tz or 'UTC'
                )
            else:
                rec.schedule_display = "-"

    # ---------------------------
    # VALIDATION
    # ---------------------------
    @api.constrains('schedule_datetime')
    def _check_schedule(self):
        for rec in self:
            if rec.schedule_datetime and rec.schedule_datetime < fields.Datetime.now():
                raise UserError("Scheduled time cannot be in the past.")

    # ---------------------------
    # EXCEL EXPORT LOGIC (NEW)
    # ---------------------------
    def _generate_excel(self, log_content, header_number, header_response, filename_prefix):
        """Helper function to generate Excel from text log"""
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Report')

        # Styles
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#D3D3D3', 'border': 1})
        cell_format = workbook.add_format({'align': 'left', 'border': 1})

        # Write Headers (Row 1)
        worksheet.write(0, 0, header_number, header_format)
        worksheet.write(0, 1, header_response, header_format)
        worksheet.set_column(0, 0, 25) # Width for Number
        worksheet.set_column(1, 1, 60) # Width for Response

        # Parse Text Log and Write Rows
        if log_content:
            lines = log_content.split('\n')
            row = 1
            for line in lines:
                # We expect format "Number: Message" (as formatted in _send_to_twilio)
                if ':' in line:
                    parts = line.split(':', 1) 
                    number_val = parts[0].strip()
                    response_val = parts[1].strip()
                else:
                    # Fallback for unexpected formats
                    number_val = "-"
                    response_val = line

                worksheet.write(row, 0, number_val, cell_format)
                worksheet.write(row, 1, response_val, cell_format)
                row += 1

        workbook.close()
        output.seek(0)
        
        # Create Attachment
        file_data = base64.b64encode(output.read())
        attachment = self.env['ir.attachment'].create({
            'name': f"{filename_prefix}_{self.id}.xlsx",
            'type': 'binary',
            'datas': file_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        })
        
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_export_success_excel(self):
        self.ensure_one()
        return self._generate_excel(
            self.log_success, 
            "Delivery Success Number", 
            "Api Response", 
            "Success_Report"
        )

    def action_export_failure_excel(self):
        self.ensure_one()
        return self._generate_excel(
            self.log_failure, 
            "Delivery Failures Numbers", 
            "Api Response", 
            "Failure_Report"
        )

    # ---------------------------
    # SENDING LOGIC (UPDATED: SPLIT LOGGING)
    # ---------------------------
    def _send_to_twilio(self):
        """Send SMS, collect results, populate split logs."""
        self.ensure_one()
        
        # 1. Config Check
        config = self.env['twilio.config'].search([], limit=1)
        if not config or getattr(config, 'connection_status', None) != 'connected':
            raise UserError("Please configure Twilio in settings first (connected).")

        if not getattr(config, 'account_sid', None) or not getattr(config, 'auth_token', None) or not getattr(config, 'twilio_number', None):
            raise UserError("Missing Twilio Credentials in configuration.")

        # 2. Prepare Recipient List
        if self.recipient_type == 'single':
            if not self.recipient_single:
                raise UserError("Please enter a mobile number.")
            numbers_to_send = [self.recipient_single.strip()]
            current_source = 'op_single'
        else:
            if not self.recipient_multi:
                raise UserError("Please enter mobile numbers.")
            numbers_to_send = [x.strip() for x in self.recipient_multi.split(',') if x.strip()]
            current_source = 'op_multi'

        url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json"
        auth = (config.account_sid, config.auth_token)

        # 3. Initialize tracking
        sent_numbers = []
        failed_numbers = []
        
        # Lists for internal logs (formatted for Excel splitting later)
        sent_log_lines = []   # Format: "Number: Message"
        failed_log_lines = [] # Format: "Number: Message"
        
        # Lists for display in the main "Response Log" field
        display_log_lines = []

        # 4. Loop to SEND
        for number in numbers_to_send:
            payload = {
                'From': config.twilio_number,
                'To': number,
                'Body': self.message_body
            }
            try:
                response = requests.post(url, data=payload, auth=auth, timeout=15)
                
                # --- SUCCESS CASE ---
                if response.status_code in (200, 201):
                    sent_numbers.append(number)
                    
                    # Log for Excel: "Number: Message"
                    sent_log_lines.append(f"{number}: Delivered Successfully")
                    # Log for UI Display
                    display_log_lines.append(f"✔ {number}: Delivered")

                # --- FAILURE CASE ---
                else:
                    failed_numbers.append(number)
                    try:
                        msg = response.json().get('message')
                    except:
                        msg = response.text
                    
                    # Log for Excel: "Number: Message"
                    failed_log_lines.append(f"{number}: {msg}")
                    # Log for UI Display
                    display_log_lines.append(f"❌ {number}: {msg}")

            # --- EXCEPTION CASE ---
            except Exception as e:
                failed_numbers.append(number)
                err_msg = f"Error {str(e)}"
                failed_log_lines.append(f"{number}: {err_msg}")
                display_log_lines.append(f"❌ {number}: {err_msg}")
                _logger.exception("Twilio send error")

        # 5. SAVE DATA (UPDATED)
        
        # A. Populate the new Split Fields
        self.log_success = "\n".join(sent_log_lines)
        self.log_failure = "\n".join(failed_log_lines)
        
        # B. Populate the old combined field (for display)
        self.response_log = "\n".join(display_log_lines)

        # C. Create Global History Logs (sms.log)
        if sent_numbers:
            self.env['sms.log'].create({
                'to_number': ",".join(sent_numbers),
                'message_body': self.message_body,
                'status': 'sent',
                'source_model': current_source,
                'api_response': "Batch Sent Successfully"
            })

        if failed_numbers:
            self.env['sms.log'].create({
                'to_number': ",".join(failed_numbers),
                'message_body': self.message_body,
                'status': 'failed',
                'source_model': current_source,
                'api_response': "\n".join(failed_log_lines)
            })

        # 6. Update Counts
        self.sent_count = len(sent_numbers)
        self.failed_count = len(failed_numbers)
        
        # 7. Final State Update
        if self.sent_count > 0 and self.failed_count == 0:
            self.state = 'sent'
            return True
        elif self.sent_count > 0 and self.failed_count > 0:
            self.state = 'partial' 
            return True 
        else:
            self.state = 'failed'
            return False

    # ---------------------------
    # Public: triggered by button
    # ---------------------------
    def action_send_sms(self):
        self.ensure_one()

        # Manual Check for Message Body (Since we removed required=True from DB)
        if not self.message_body:
            raise UserError(_("Please enter a message before sending."))

        # Validation
        if self.recipient_type == 'multi' and not self.recipient_multi:
             raise UserError("Please enter or import mobile numbers before sending.")

        # Scheduling
        if self.schedule_datetime and self.schedule_datetime > fields.Datetime.now():
            self.state = 'scheduled'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Scheduled',
                    'message': '⏳ SMS Scheduled Successfully!',
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                }
            }

        # Sending
        try:
            sent = self._send_to_twilio()
            
            if self.state == 'sent':
                msg_title = 'Success'
                msg_body = '✔ All Messages Sent Successfully!'
                msg_type = 'success'
            elif self.state == 'partial':
                msg_title = 'Partial Success'
                msg_body = f'⚠ Sent: {self.sent_count} / Failed: {self.failed_count}. Check logs.'
                msg_type = 'warning'
            else:
                msg_title = 'Failed'
                msg_body = '❌ All messages failed. Check Delivery Logs.'
                msg_type = 'danger'

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': msg_title,
                    'message': msg_body,
                    'type': msg_type,
                    'sticky': True if self.state != 'sent' else False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                }
            }
                
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Unexpected error in action_send_sms")
            self.response_log = str(e)
            self.state = 'failed'
            raise UserError(_("Unexpected error while sending SMS: %s") % e)
        
    def action_clear_log(self):
        self.ensure_one()
        self.response_log = ""
        self.log_success = ""
        self.log_failure = ""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Log history has been cleared.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }    
    
    # Placeholder for the old export button if called from elsewhere
    def action_export_excel(self):
        self.ensure_one()
        # Defaults to success log if clicked generically
        return self.action_export_success_excel()
        
    @api.model
    def _cron_send_scheduled_sms(self):
        now = fields.Datetime.now()
        scheduled = self.search([
            ('state', '=', 'scheduled'),
            ('schedule_datetime', '<=', now)
        ])
        for rec in scheduled:
            try:
                rec._send_to_twilio()
            except Exception:
                _logger.exception("Failed to send scheduled SMS for id %s", rec.id)
                rec.state = 'failed'