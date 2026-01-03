from odoo import models, fields, api

class SmsLog(models.Model):
    _name = "sms.log"
    _description = "Central SMS Log"
    _order = "create_date desc"
    _rec_name = "to_number"

    # 1. Basic Info
    create_date = fields.Datetime(string="Sent On", readonly=True)
    to_number = fields.Char(string="Mobile Number", required=True, readonly=True)
    message_body = fields.Text(string="Message Content", readonly=True)
    
    # This handles the truncation of long number lists (e.g., "+91..., +91...")
    to_number_display = fields.Char(string="Mobile Number", compute="_compute_number_display")
    
    # 2. Source Logic
    # This remains for database sorting and coloring
    source_model = fields.Selection([
        ('op_single', 'OP: Single SMS'),   
        ('op_multi', 'OP: Multiple SMS'),
        ('twilio.sms.group', 'Group SMS'),
        ('sale.order', 'Sales Order'),
        ('twilio.sms', 'Legacy SMS (Old)')
    ], string="Source Type", readonly=True)

    # NEW: This field stores specific text like "Vip: 2/2"
    custom_header = fields.Char(string="Custom Header", readonly=True)

    # NEW: This is the field we will SHOW in the list
    source_display = fields.Char(string="Source", compute="_compute_source_display")

    sale_order_id = fields.Many2one('sale.order', string="Sales Order")

    # 3. Status
    status = fields.Selection([
        ('sent', 'Sent'),
        ('failed', 'Failed')
    ], string="Status", default='sent', readonly=True)

    api_response = fields.Text(string="Twilio Response", readonly=True)

    # --------------------------------------------------------
    # SMART SOURCE DISPLAY (The Magic Part)
    # --------------------------------------------------------
    @api.depends('source_model', 'custom_header', 'sale_order_id')
    def _compute_source_display(self):
        for rec in self:
            # Priority 1: If we have a Custom Header (like Group SMS), use it
            if rec.custom_header:
                rec.source_display = rec.custom_header
            
            # Priority 2: If it's a Sales Order, show "Order S00001"
            elif rec.source_model == 'sale.order' and rec.sale_order_id:
                rec.source_display = f"Sales Order: {rec.sale_order_id.name}"
            
            # Priority 3: Fallback to the standard label
            else:
                selection_label = dict(self._fields['source_model'].selection).get(rec.source_model)
                rec.source_display = selection_label or rec.source_model

    # --------------------------------------------------------
    # NUMBER TRUNCATION
    # --------------------------------------------------------
    @api.depends('to_number')
    def _compute_number_display(self):
        for rec in self:
            # If the number list is too long, cut it off
            if rec.to_number and len(rec.to_number) > 25:
                rec.to_number_display = rec.to_number[:25] + "..."
            else:
                rec.to_number_display = rec.to_number

    @api.model
    def action_delete_all_logs(self):
        self.search([]).unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}