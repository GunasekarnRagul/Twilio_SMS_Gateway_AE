from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import format_datetime # <--- IMPORTANT IMPORT
import requests
import logging
import pytz

_logger = logging.getLogger(__name__)

class TwilioSmsGroup(models.Model):
    _name = "twilio.sms.group"
    _description = "SMS Recipient Group"
    _rec_name = "name"

    name = fields.Char()
    description = fields.Text()
    message_body_group = fields.Text(string="Message")

    recipient_ids = fields.Many2many(
        'res.partner',
        'twilio_sms_group_recipient_rel',  # <--- UNIQUE TABLE NAME
        'group_id',                        # Column for this model
        'partner_id',                      # Column for res.partner
        string="Recipients"
    )    # member_count = fields.Integer(compute="_compute_member_count", store=True)
    member_count = fields.Char(string="Recipients", compute="_compute_member_count",store=True)

    sms_log = fields.Text(string="SMS Log", readonly=True, help="Latest delivery logs") 
    
    
    
    

    # -------------------------------------------------------------
    # ROBUST TIMEZONE DETECTION
    # -------------------------------------------------------------
    @api.model
    def _get_default_timezone(self):
        """
        Detects timezone in this order:
        1. Current User's specific timezone setting.
        2. The browser context (if passed by Odoo).
        3. The Current Company's timezone.
        4. Default to UTC.
        """
        user_tz = self.env.user.tz
        if user_tz:
            return user_tz
        
        context_tz = self._context.get('tz')
        if context_tz:
            return context_tz
            
        company_tz = self.env.company.partner_id.tz
        if company_tz:
            return company_tz
            
        return 'UTC'

    # -------------------------------------------------------------
    # SCHEDULE FIELDS
    # -------------------------------------------------------------
    schedule_datetime = fields.Datetime(
        string="Schedule Date & Time",
        help="Choose when the SMS should be sent (stored as UTC)"
    )
    timezone = fields.Selection(
        [(tz, tz) for tz in pytz.all_timezones],
        string="Time Zone",
        default=lambda self: self.env.user.tz or "UTC",
        required=True
    )

    schedule_display = fields.Char(
        string="Scheduled On", 
        compute="_compute_schedule_display"
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('scheduled', 'Scheduled'),
            ('sent', 'Sent'),
            ('failed', 'Failed')
        ],
        string="State",
        default="draft",
        readonly=True,
    )
    
    
        # 2. Add the compute logic
    @api.depends('schedule_datetime', 'timezone')
    def _compute_schedule_display(self):
     for rec in self:
        if rec.schedule_datetime:
            # Formats the date using the record's specific timezone (if selected) 
            # or defaults to the Context/User's timezone.
            rec.schedule_display = format_datetime(
                self.env, 
                rec.schedule_datetime, 
                tz=rec.timezone or self.env.user.tz or 'UTC'
            )
        else:
            rec.schedule_display = "-"

    # -------------------------------------------------------------
    # COMPUTE MEMBER COUNT
    # -------------------------------------------------------------
    @api.depends('recipient_ids')
    def _compute_member_count(self):
        for rec in self:
            # This ensures it shows "0" if empty, or the count otherwise
            count = len(rec.recipient_ids)
            rec.member_count = str(count)

    # -------------------------------------------------------------
    # VALIDATION: SCHEDULE DATE
    # -------------------------------------------------------------
    @api.constrains('schedule_datetime')
    def _check_schedule(self):
        for rec in self:
            # Odoo Datetime fields are compared in UTC. 
            # fields.Datetime.now() returns UTC.
            if rec.schedule_datetime and rec.schedule_datetime < fields.Datetime.now():
                raise UserError("Scheduled time cannot be in the past.")

    # -------------------------------------------------------------
    # INTERNAL: SEND TO TWILIO 
    # -------------------------------------------------------------
    def _send_one(self, recipient, config):
        # Setup Twilio Config
        url = f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json"
        auth = (config.account_sid, config.auth_token)

        # Validation
        if not recipient.mobile:
            return False, "❌ No Mobile", recipient.name

        # Clean Number
        clean_mobile = recipient.mobile.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        country_code = str(recipient.country_id.phone_code) if recipient.country_id else ""

        if clean_mobile.startswith("+"):
            formatted = clean_mobile
        elif country_code and clean_mobile.startswith(country_code):
            formatted = f"+{clean_mobile}"
        else:
            formatted = f"+{country_code}{clean_mobile}"

        payload = {
            'From': config.twilio_number,
            'To': formatted,
            'Body': self.message_body_group
        }

        try:
            # Send Request
            resp = requests.post(url, data=payload, auth=auth, timeout=15)
            
            # --- IMPORTANT: I REMOVED "sms.log.create" FROM HERE ---
            
            if resp.status_code in (200, 201):
                return True, "✔ Sent", formatted, 
            else:
                return False, f"❌ Failed ({resp.status_code})", formatted
        except Exception as e:
            return False, f"❌ Error: {str(e)}", formatted

    # -------------------------------------------------------------
    # MAIN BUTTON ACTION
    # -------------------------------------------------------------
    def action_send_now(self):
        self.ensure_one()
        
        if not self.name:
           raise UserError(_("Please enter a name before sending."))
       
        if not self.message_body_group:
           raise UserError(_("Please enter a message before sending."))



        # 1. CHECK SCHEDULE
        # If a date is set AND it is in the future
        if self.schedule_datetime and self.schedule_datetime > fields.Datetime.now():
            self.state = "scheduled"
            
            # STOP HERE! Do not run _send_now_execute(). 
            # The Cron job will pick this up later.
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Scheduled',
                    'message': 'SMS Scheduled Successfully! It will be sent automatically.',
                    'type': 'success',
                    'sticky': False,  # Notification disappears automatically
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                }
            }

        # 2. SEND IMMEDIATELY
        # Only runs if date is empty OR date is in the past
        return self._send_now_execute()

    # -------------------------------------------------------------
    # ACTUAL SENDING LOGIC 
    # -------------------------------------------------------------
    def _send_now_execute(self):
        # self.ensure_one()
        
        if not self.recipient_ids:
            raise UserError("Add recipients before sending SMS.")

        config = self.env['twilio.config'].search([], limit=1)
        if not config or config.connection_status != "connected":
            raise UserError("Twilio is not connected.")

        # Prepare Data
        log_summary_list = []
        sent_numbers_list = []
        success_count = 0

        for r in self.recipient_ids:
            is_success, status_msg, phone_number = self._send_one(r, config)
            
            log_summary_list.append(f"{status_msg} -> {r.name} ({phone_number})")
            
            if phone_number:
                sent_numbers_list.append(phone_number)
            
            if is_success:
                success_count += 1

        # ---------------------------------------------------------
        # CREATE THE LOG WITH YOUR SPECIFIC FORMAT
        # ---------------------------------------------------------
        full_report = "\n".join(log_summary_list)
        numbers_display = ", ".join(sent_numbers_list)

        # Create the text: "Group SMS: Vip 2/2"
        group_header_text = f"Group SMS: {self.name} {success_count}/{len(self.recipient_ids)}"

        self.env['sms.log'].create({
            'to_number': numbers_display,      # <--- Shows Real Numbers
            'custom_header': group_header_text,# <--- Shows "Group SMS: Vip 2/2" in Source
            'message_body': self.message_body_group,
            'source_model': 'twilio.sms.group', # Used for Color (Orange)
            'status': 'sent' if success_count > 0 else 'failed',
            'api_response': full_report
        })
        # ---------------------------------------------------------

        old_log = self.sms_log or ""
        self.sms_log = f"--- Batch {fields.Datetime.now()} ---\n{full_report}\n\n{old_log}"
        self.state = "sent" if success_count == len(self.recipient_ids) else "failed"

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Batch Complete',
                'message': f"Processed {len(self.recipient_ids)} recipients.",
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }
    # -------------------------------------------------------------
    # ACTION: CLEAR LOGS
    # -------------------------------------------------------------
    def action_clear_log(self):
        self.ensure_one()
        self.sms_log = ""  # Wipes the data
        
        # Optional: Show a small notification that it worked
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Logs have been cleared.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }
    # -------------------------------------------------------------
    # CRON JOB
    # -------------------------------------------------------------
    @api.model
    def _cron_send_group_sms(self):
        now = fields.Datetime.now()
        groups = self.search([
            ('state', '=', 'scheduled'),
            ('schedule_datetime', '<=', now)
        ])
        for group in groups:
            try:
                group._send_now_execute()
            except Exception:
                _logger.exception("Scheduled group SMS error")
                group.state = "failed"
                
# class ResPartnerFix(models.Model):
#     _inherit = 'res.partner'

#     @api.depends('vat', 'l10n_in_pan')
#     def _compute_display_pan_warning(self):
#         """
#         Overrides the broken standard Odoo function to fix the
#         'Expected Singleton' error when selecting multiple partners.
#         """
#         for record in self:
#             if record.vat and record.l10n_in_pan:
#                 record.display_pan_warning = record.l10n_in_pan != record.vat[2:12]
#             else:
#                 record.display_pan_warning = False                