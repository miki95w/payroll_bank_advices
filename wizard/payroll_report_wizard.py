# -*- coding: utf-8 -*-

import io
import base64
from datetime import datetime
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import xlsxwriter


class PayrollReportWizard(models.TransientModel):
    _name = 'payroll.report.wizard'
    _description = 'Payroll Report - Salary & Deduction Breakdown'

    filter_by = fields.Selection([
        ('batch', 'Payslip Batch'),
        ('date', 'Date Range'),
    ], string='Filter By', default='batch', required=True)

    payslip_run_id = fields.Many2one(
        'hr.payslip.run',
        string='Payslip Batch',
    )
    date_from = fields.Date(
        string='Start Date',
        default=lambda self: fields.Date.today() + relativedelta(day=1),
        required=True,
    )
    date_to = fields.Date(
        string='End Date',
        default=lambda self: fields.Date.today() + relativedelta(day=1, months=1, days=-1),
        required=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
    )
    department_ids = fields.Many2many(
        'hr.department',
        string='Departments',
        help='Leave empty to include all departments.',
    )
    employee_ids = fields.Many2many(
        'hr.employee',
        string='Employees',
        help='Leave empty to include all employees.',
    )
    bank_name = fields.Char(
        string='Filter by Bank',
        help='Filter by specific bank name (e.g. CBE, Dashen). Leave empty for all banks.',
    )
    separate_sheets_by_bank = fields.Boolean(
        string='Separate Sheets by Bank',
        default=True,
        help='Generate an overall Master tab as well as separate tabs per bank.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super(PayrollReportWizard, self).default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])

        if active_model == 'hr.payslip.run' and active_ids:
            run = self.env['hr.payslip.run'].browse(active_ids[0])
            res['filter_by'] = 'batch'
            res['payslip_run_id'] = run.id
            if run.date_start:
                res['date_from'] = run.date_start
            if run.date_end:
                res['date_to'] = run.date_end
            if hasattr(run, 'company_id') and run.company_id:
                res['company_id'] = run.company_id.id
        elif active_model == 'hr.payslip' and active_ids:
            slips = self.env['hr.payslip'].browse(active_ids).filtered(lambda s: s.state != 'cancel')
            res['filter_by'] = 'date'
            if slips:
                res['employee_ids'] = [(6, 0, slips.mapped('employee_id').ids)]
                valid_dates_from = slips.mapped('date_from')
                valid_dates_to = slips.mapped('date_to')
                if valid_dates_from:
                    res['date_from'] = min(valid_dates_from)
                if valid_dates_to:
                    res['date_to'] = max(valid_dates_to)
                if slips[0].company_id:
                    res['company_id'] = slips[0].company_id.id

        return res

    def _get_target_payslips(self):
        """ Finds and filters payslips based on wizard criteria """
        self.ensure_one()
        slips = self.env['hr.payslip']

        if self.filter_by == 'batch':
            if not self.payslip_run_id:
                raise UserError(_("Please select a Payslip Batch."))
            slips = self.payslip_run_id.slip_ids.filtered(lambda s: s.state != 'cancel')
        else:
            if self.date_from > self.date_to:
                raise UserError(_("Start Date cannot be later than End Date."))
            domain = [
                ('date_from', '>=', self.date_from),
                ('date_to', '<=', self.date_to),
                ('company_id', '=', self.company_id.id),
                ('state', '!=', 'cancel'),
            ]
            slips = self.env['hr.payslip'].search(domain)

        if not slips:
            raise UserError(_("No valid payslips found for the selected criteria."))

        if self.department_ids:
            slips = slips.filtered(lambda s: s.employee_id.department_id in self.department_ids)

        if self.employee_ids:
            slips = slips.filtered(lambda s: s.employee_id in self.employee_ids)

        if self.bank_name:
            search_bank = self.bank_name.strip().lower()
            slips = slips.filtered(
                lambda s: search_bank in (s._resolve_employee_bank_details(s.employee_id).get('bank_name') or '').lower()
            )

        if not slips:
            raise UserError(_("No payslips matched your filters."))

        return slips

    def action_generate_pdf(self):
        """ Print the Salary Rule & Deductions Summary as PDF """
        self.ensure_one()
        slips = self._get_target_payslips()
        report = self.env.ref('payroll_bank_advice.action_report_salary_rule_summary')
        return report.report_action(slips)

    def action_generate_xlsx(self):
        """ Generates and downloads the Excel (.xlsx) Payroll Report """
        self.ensure_one()
        slips = self._get_target_payslips()

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # --- Styles & Formatting ---
        fmt_title = workbook.add_format({
            'bold': True,
            'font_size': 14,
            'font_name': 'Arial',
            'font_color': '#1A5276',
            'align': 'left',
            'valign': 'vcenter',
        })
        fmt_meta = workbook.add_format({
            'font_size': 9.5,
            'font_name': 'Arial',
            'font_color': '#555555',
            'valign': 'vcenter',
        })
        fmt_meta_bold = workbook.add_format({
            'bold': True,
            'font_size': 9.5,
            'font_name': 'Arial',
            'font_color': '#222222',
            'valign': 'vcenter',
        })
        # Column headers
        fmt_th_base = workbook.add_format({
            'bold': True,
            'font_size': 9.5,
            'font_name': 'Arial',
            'font_color': '#FFFFFF',
            'bg_color': '#2C3E50',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
        })
        fmt_th_earning = workbook.add_format({
            'bold': True,
            'font_size': 9.5,
            'font_name': 'Arial',
            'font_color': '#FFFFFF',
            'bg_color': '#118B46',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
        })
        fmt_th_deduction = workbook.add_format({
            'bold': True,
            'font_size': 9.5,
            'font_name': 'Arial',
            'font_color': '#FFFFFF',
            'bg_color': '#A93226',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
        })
        fmt_th_net = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Arial',
            'font_color': '#FFFFFF',
            'bg_color': '#1A5276',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
        })
        # Data cells
        fmt_td_text = workbook.add_format({
            'font_size': 9,
            'font_name': 'Arial',
            'valign': 'vcenter',
            'border': 1,
        })
        fmt_td_center = workbook.add_format({
            'font_size': 9,
            'font_name': 'Arial',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
        })
        fmt_td_num = workbook.add_format({
            'font_size': 9,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'border': 1,
        })
        fmt_td_net_num = workbook.add_format({
            'bold': True,
            'font_size': 9.5,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'border': 1,
            'bg_color': '#F4F9F4',
        })
        # Total Row
        fmt_tot_label = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'top': 1,
            'bottom': 6,
            'bg_color': '#EAEDED',
        })
        fmt_tot_num = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'top': 1,
            'bottom': 6,
            'bg_color': '#EAEDED',
        })

        # --- Determine Pay Period String ---
        if self.filter_by == 'batch' and self.payslip_run_id:
            period_title = self.payslip_run_id.name
        else:
            period_title = f"{self.date_from.strftime('%d/%m/%Y')} - {self.date_to.strftime('%d/%m/%Y')}"

        # --- Collect Rules Present in Slips ---
        rule_dict = {}
        for slip in slips:
            for line in slip.line_ids:
                rule = line.salary_rule_id
                code = line.code or (rule.code if rule else '')
                if not code:
                    continue
                name = line.name or (rule.name if rule else code)
                seq = rule.sequence if rule else 100
                cat_code = rule.category_id.code if (rule and rule.category_id) else ''
                is_ded = (cat_code == 'DED') or ('deduct' in name.lower()) or ('tax' in name.lower()) or ('pension' in name.lower()) or (line.total < 0)
                is_net = (code == 'NET')

                if code not in rule_dict:
                    rule_dict[code] = {
                        'code': code,
                        'name': name,
                        'sequence': seq,
                        'category_code': cat_code,
                        'is_deduction': is_ded,
                        'is_net': is_net,
                    }

        # Sort rules logically:
        # Non-deduction rules (Basic, Allowances, Gross), then Deductions, then NET
        def rule_sort_key(r):
            if r['is_net']:
                return (3, r['sequence'], r['code'])
            elif r['is_deduction']:
                return (2, r['sequence'], r['code'])
            else:
                return (1, r['sequence'], r['code'])

        sorted_rules = sorted(rule_dict.values(), key=rule_sort_key)

        # Helper to render a sheet
        def render_sheet(worksheet, sheet_slips, sheet_title):
            worksheet.set_paper(9)  # A4
            worksheet.set_landscape()
            worksheet.hide_gridlines(0)

            # 1. Header Information
            company_name = self.company_id.name or self.env.company.name
            worksheet.write(0, 0, company_name.upper(), fmt_title)
            worksheet.write(1, 0, f"PAYROLL REPORT - {sheet_title.upper()}", fmt_title)
            worksheet.write(2, 0, "Period / Batch:", fmt_meta_bold)
            worksheet.write(2, 1, period_title, fmt_meta)
            worksheet.write(2, 4, "Date Generated:", fmt_meta_bold)
            worksheet.write(2, 5, datetime.now().strftime('%d/%m/%Y %H:%M'), fmt_meta)
            worksheet.write(2, 7, "Currency:", fmt_meta_bold)
            worksheet.write(2, 8, self.company_id.currency_id.name or 'ETB', fmt_meta)

            row_idx = 4

            # 2. Table Column Headers
            base_headers = [
                ('#', 5, fmt_th_base),
                ('Emp. ID', 12, fmt_th_base),
                ('Employee Name', 25, fmt_th_base),
                ('Department', 20, fmt_th_base),
                ('Job Position', 18, fmt_th_base),
                ('Bank Name', 22, fmt_th_base),
                ('Branch', 18, fmt_th_base),
                ('Account Number', 18, fmt_th_base),
            ]

            col_idx = 0
            col_widths = {}
            for title, width, fmt in base_headers:
                worksheet.write(row_idx, col_idx, title, fmt)
                col_widths[col_idx] = width
                col_idx += 1

            for r in sorted_rules:
                if r['is_net']:
                    h_fmt = fmt_th_net
                elif r['is_deduction']:
                    h_fmt = fmt_th_deduction
                else:
                    h_fmt = fmt_th_earning

                header_label = f"{r['name']}\n({r['code']})"
                worksheet.write(row_idx, col_idx, header_label, h_fmt)
                col_widths[col_idx] = max(14, len(r['name']) + 2)
                col_idx += 1

            worksheet.set_row(row_idx, 30)

            # 3. Data Rows
            start_data_row = row_idx + 1
            cur_row = start_data_row

            rule_totals = {r['code']: 0.0 for r in sorted_rules}

            for idx, slip in enumerate(sheet_slips, start=1):
                emp = slip.employee_id
                bank_info = slip._resolve_employee_bank_details(emp)
                emp_ref = getattr(emp, 'fms_employee_id', False) or getattr(emp, 'barcode', False) or getattr(emp, 'identification_id', False) or str(emp.id)

                worksheet.write(cur_row, 0, idx, fmt_td_center)
                worksheet.write(cur_row, 1, emp_ref, fmt_td_center)
                worksheet.write(cur_row, 2, emp.name or '', fmt_td_text)
                worksheet.write(cur_row, 3, emp.department_id.name or '', fmt_td_text)
                worksheet.write(cur_row, 4, emp.job_title or '', fmt_td_text)
                worksheet.write(cur_row, 5, bank_info.get('bank_name') or '', fmt_td_text)
                worksheet.write(cur_row, 6, bank_info.get('branch') or '-', fmt_td_text)
                worksheet.write(cur_row, 7, bank_info.get('bank_account_number') or '-', fmt_td_center)

                # Map payslip rules
                slip_rule_map = {line.code: line.total for line in slip.line_ids}

                r_col = 8
                for r in sorted_rules:
                    amt = slip_rule_map.get(r['code'], 0.0)
                    rule_totals[r['code']] += amt
                    cell_fmt = fmt_td_net_num if r['is_net'] else fmt_td_num
                    worksheet.write(cur_row, r_col, amt, cell_fmt)
                    r_col += 1

                cur_row += 1

            # 4. Total Row
            worksheet.merge_range(cur_row, 0, cur_row, 7, "GRAND TOTAL:", fmt_tot_label)
            r_col = 8
            for r in sorted_rules:
                worksheet.write(cur_row, r_col, rule_totals[r['code']], fmt_tot_num)
                r_col += 1
            worksheet.set_row(cur_row, 22)

            # 5. Set column widths
            for c_idx, width in col_widths.items():
                worksheet.set_column(c_idx, c_idx, width)

        # Sheet 1: Master / All Employees
        ws_all = workbook.add_worksheet('All Employees')
        render_sheet(ws_all, slips, 'All Employees Summary')

        # Optional: Separate tabs per bank
        if self.separate_sheets_by_bank:
            bank_groups = {}
            for slip in slips:
                b_info = slip._resolve_employee_bank_details(slip.employee_id)
                b_name = b_info.get('short_code') or b_info.get('bank_name') or 'Other'
                if b_name not in bank_groups:
                    bank_groups[b_name] = []
                bank_groups[b_name].append(slip)

            if len(bank_groups) > 1 or (len(bank_groups) == 1 and 'All Employees' not in bank_groups):
                for b_name, b_slips in bank_groups.items():
                    # Clean sheet name (max 31 chars, no invalid chars)
                    clean_sheet_name = b_name[:28].replace('/', '-').replace('\\', '-').replace('?', '').replace('*', '')
                    ws_bank = workbook.add_worksheet(clean_sheet_name)
                    render_sheet(ws_bank, b_slips, f"{b_name} Schedule")

        workbook.close()
        xlsx_data = output.getvalue()

        # Filename
        clean_period = period_title.replace('/', '-').replace(' ', '_')
        filename = f"Payroll_Report_{clean_period}.xlsx"

        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(xlsx_data),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }
