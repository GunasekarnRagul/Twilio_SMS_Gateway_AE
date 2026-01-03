import base64
import io
import openpyxl
from odoo import models, fields, _, api
from odoo.exceptions import UserError

class SmsImportWizard(models.TransientModel):
    _name = 'sms.import.wizard'
    _description = 'Import Mobile Numbers from Excel'

    file_data = fields.Binary('Excel File', required=True)
    file_name = fields.Char('File Name')

    def action_import_apply(self):
        """Parse Excel and update the active record using openpyxl"""
        self.ensure_one()
        
        # 1. Validation: Check file extension
        if not self.file_name or not self.file_name.endswith(('.xls', '.xlsx')):
            raise UserError(_("Please upload a valid Excel file (.xls or .xlsx)."))

        # 2. Decode the file
        try:
            file_content = base64.b64decode(self.file_data)
            # Use io.BytesIO to treat the binary data as a file stream
            data_stream = io.BytesIO(file_content)
            
            # Load workbook using openpyxl (data_only=True ensures we get values, not formulas)
            workbook = openpyxl.load_workbook(filename=data_stream, data_only=True)
            sheet = workbook.active  # Get the first sheet
        except Exception as e:
            raise UserError(_("Could not read the file. Error: %s") % str(e))

        # 3. Read all rows as values
        rows = list(sheet.iter_rows(values_only=True))
        
        if not rows:
            raise UserError(_("The Excel file appears to be empty."))

        # 4. Find the 'mobile_numbers' column index
        header_row = rows[0]
        target_col_index = -1
        
        # Normalize headers to lowercase to find 'mobile_numbers'
        for index, col_name in enumerate(header_row):
            if str(col_name).strip().lower() == 'mobile_numbers':
                target_col_index = index
                break
        
        if target_col_index == -1:
            raise UserError(_("Column 'mobile_numbers' not found in the first row of the Excel sheet."))

        # 5. Extract and Validate Numbers
        valid_numbers = []
        
        # Iterate through rows starting from 1 (skipping header)
        for row in rows[1:]:
            # Check if row is not empty and has enough columns
            if not row or len(row) <= target_col_index:
                continue

            raw_val = row[target_col_index]
            
            if not raw_val:
                continue

            # Convert to string and handle formatting
            str_val = str(raw_val).strip()

            # Logic: Validate Country Code
            # Must start with '+' to be considered valid per your requirement
            if str_val.startswith('+'):
                # Clean up spaces or dashes
                clean_num = str_val.replace(" ", "").replace("-", "")
                valid_numbers.append(clean_num)
            
            # Note: If it doesn't start with +, it is skipped (removed)

        if not valid_numbers:
            raise UserError(_("No valid numbers found. Make sure numbers start with a country code (e.g., +91)."))

        # 6. Update the Main Record (The screen behind the popup)
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model')
        
        if active_id and active_model:
            parent_record = self.env[active_model].browse(active_id)
            # Join with comma
            final_string = ", ".join(valid_numbers)
            parent_record.write({'recipient_multi': final_string})

        return {'type': 'ir.actions.act_window_close'}