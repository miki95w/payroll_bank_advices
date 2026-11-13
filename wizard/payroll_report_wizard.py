# -*- coding: utf-8 -*-

import io
import base64
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import xlsxwriter

from . import ethiopian_date_utils as eth_utils


class PayrollReportWizard(models.TransientModel):
    _name = 'payroll.report.wizard'
    _description = 'Payroll Report - Salary & Deduction Breakdown'

    report_category = fields.Selection([
        ('all', 'Full Salary & Deductions Breakdown (Master)'),
        ('allowance', 'Allowance Report (የአበል ሪፖርት)'),
        ('deduction', 'Deduction Report (የተቀናሽ ሪፖርት)'),
        ('attendance', 'Attendance & Overtime Report (የትርፍ ሰዓት / የቀሪ ሰራተኞች)'),
    ], string='Report Category', default='all', required=True)

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
    bank_id = fields.Many2one(
        'res.bank',
        string='Filter by Bank',
        help='Filter by specific bank (e.g. Dashen Bank, Commercial Bank of Ethiopia). Leave empty for all banks.',
    )
    bank_name = fields.Char(
        string='Bank Name / Keyword',
        help='Filter by specific bank name (e.g. CBE, Dashen). Leave empty for all banks.',
    )

    @api.onchange('bank_id')
    def _onchange_bank_id(self):
        if self.bank_id:
            self.bank_name = self.bank_id.name

    separate_sheets_by_bank = fields.Boolean(
        string='Separate Sheets by Bank',
        default=True,
        help='Generate an overall Master tab as well as separate tabs per bank.',
    )

    # ---------------------------------------------------------
    # Category Specific Pickers (Allowance, Deduction, Attendance)
    # ---------------------------------------------------------
    allowance_rule_ids = fields.Many2many(
        'hr.salary.rule',
        'rel_payroll_report_wiz_alw_rules',
        'wizard_id', 'rule_id',
        string='Select Allowance(s) to Print',
        domain="[('category_id.code', '=', 'ALW')]",
        help='Pick specific allowances to include as columns (e.g. Acting, Cash Indemnity, Service, Transport). Leave empty for standard allowances.',
    )

    deduction_rule_ids = fields.Many2many(
        'hr.salary.rule',
        'rel_payroll_report_wiz_ded_rules',
        'wizard_id', 'rule_id',
        string='Select Deduction(s) to Print',
        domain="[('category_id.code', '=', 'DED')]",
        help='Pick specific deductions to include (e.g. Oil Deduction, Floor Deduction, Labor Union). If 1 is picked, generates single-deduction schedule.',
    )

    attendance_sub_type = fields.Selection([
        ('overtime', 'Overtime Breakdown (Normal, Night, Weekend, Holiday)'),
        ('absent', 'Absent Days & Deductions List'),
        ('both', 'Both Overtime and Absent Lists'),
    ], string='Attendance Content', default='overtime')

    include_normal_ot = fields.Boolean(string='Normal Hour (1.5x)', default=True)
    include_night_ot = fields.Boolean(string='Night Hour (1.75x)', default=True)
    include_weekend_ot = fields.Boolean(string='Weekend Hour (2.0x)', default=True)
    include_holiday_ot = fields.Boolean(string='Holiday Hour (2.5x)', default=True)

    only_with_values = fields.Boolean(
        string='Only Employees with Values',
        default=True,
        help='If checked, only employees who have values for the selected report will be listed.',
    )

    # Ethiopian Date & Title Settings
    ethiopian_month = fields.Selection(
        eth_utils.ETHIOPIAN_MONTH_SELECTION,
        string='Ethiopian Month',
        default='1',
    )
    ethiopian_year = fields.Integer(
        string='Ethiopian Year',
        default=2019,
    )
    report_title = fields.Char(
        string='Report Title Header',
        help='Header printed at the top of the report.',
    )

    # Signatures
    prepared_by = fields.Char(string='Prepared By', default='')
    checked_by = fields.Char(string='Checked By', default='')
    verified_by = fields.Char(string='Verified By', default='')
    approved_by = fields.Char(string='Approved By', default='')

    @api.model
    def default_get(self, fields_list):
        res = super(PayrollReportWizard, self).default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])

        default_date = date.today()
        if active_model == 'hr.payslip.run' and active_ids:
            run = self.env['hr.payslip.run'].browse(active_ids[0])
            res['filter_by'] = 'batch'
            res['payslip_run_id'] = run.id
            if run.date_start:
                res['date_from'] = run.date_start
                default_date = run.date_start
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
                    default_date = min(valid_dates_from)
                if valid_dates_to:
                    res['date_to'] = max(valid_dates_to)
                if slips[0].company_id:
                    res['company_id'] = slips[0].company_id.id

        # Ethiopian date conversion
        eth_conv = eth_utils.gregorian_to_ethiopian(default_date)
        if eth_conv:
            res['ethiopian_year'] = eth_conv[0]
            res['ethiopian_month'] = str(eth_conv[1])

        oil_rule = self.env['hr.salary.rule'].search([('code', '=', 'DED_OIL')], limit=1)
        if oil_rule and res.get('report_category') == 'deduction':
            res['deduction_rule_ids'] = [(6, 0, [oil_rule.id])]

        m_idx = int(res.get('ethiopian_month', '1'))
        m_name = eth_utils.ETHIOPIAN_MONTHS_AMHARIC[m_idx] if 1 <= m_idx <= 13 else 'መስከረም'
        yr = res.get('ethiopian_year', 2019)
        rcat = res.get('report_category', 'all')
        res['report_title'] = self._build_title(rcat, m_name, yr, oil_rule if rcat == 'deduction' else None)

        return res

    @api.onchange('filter_by', 'payslip_run_id', 'date_from')
    def _onchange_dates_or_batch(self):
        ref_date = self.date_from
        if self.filter_by == 'batch' and self.payslip_run_id:
            if self.payslip_run_id.date_start:
                self.date_from = self.payslip_run_id.date_start
                ref_date = self.payslip_run_id.date_start
            if self.payslip_run_id.date_end:
                self.date_to = self.payslip_run_id.date_end
        if ref_date:
            eth_conv = eth_utils.gregorian_to_ethiopian(ref_date)
            if eth_conv:
                self.ethiopian_year = eth_conv[0]
                self.ethiopian_month = str(eth_conv[1])
        self._update_report_title()

    @api.onchange('report_category', 'ethiopian_month', 'ethiopian_year', 'deduction_rule_ids', 'allowance_rule_ids', 'attendance_sub_type')
    def _onchange_report_settings(self):
        self._update_report_title()

    def _update_report_title(self):
        m_idx = int(self.ethiopian_month or '1')
        m_name = eth_utils.ETHIOPIAN_MONTHS_AMHARIC[m_idx] if 1 <= m_idx <= 13 else 'መስከረም'
        yr = self.ethiopian_year or 2019
        self.report_title = self._build_title(self.report_category, m_name, yr, self.deduction_rule_ids, self.allowance_rule_ids)

    def _build_title(self, rcat, m_name, yr, deduction_rules=None, allowance_rules=None):
        if rcat == 'allowance':
            if allowance_rules and len(allowance_rules) <= 3:
                names = ", ".join([r.name.split('(')[0].strip() for r in allowance_rules])
                return f"Payroll Allowance ({names}) for the Month of {m_name} {yr}"
            return f"Payroll Allowance for the Month of {m_name} {yr}"
        elif rcat == 'deduction':
            if deduction_rules and len(deduction_rules) == 1:
                d_name = deduction_rules[0].name.split('(')[0].strip()
                return f"Payroll Deduction ({d_name}) for the Month of {m_name} {yr}"
            elif deduction_rules and len(deduction_rules) <= 3:
                names = ", ".join([r.name.split('(')[0].strip() for r in deduction_rules])
                return f"Payroll Deduction ({names}) for the Month of {m_name} {yr}"
            return f"Payroll Deduction for the Month of {m_name} {yr}"
        elif rcat == 'attendance':
            return f"Overtime done for the Month of {m_name} {yr}"
        else:
            return f"Payroll Report for the Month of {m_name} {yr}"

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

        # Enforce employee classification security
        user = self.env.user
        if hasattr(user, '_get_allowed_farm_employee_types'):
            allowed = user._get_allowed_farm_employee_types()
            if allowed and 'all' not in allowed:
                slips = slips.filtered(lambda s: getattr(s.employee_id, 'farm_employee_type', False) in allowed)

        if not slips:
            raise UserError(_("No payslips matched your filters."))

        return slips.sorted(key=lambda s: (s.employee_id.department_id.name or '', s.employee_id.name or ''))

    def _get_overtime_data(self, slips):
        """ Calculates Overtime rows with optional column toggles """
        rows = []
        tot_salary = 0.0
        tot_norm_h = 0.0
        tot_norm_amt = 0.0
        tot_night_h = 0.0
        tot_night_amt = 0.0
        tot_wk_h = 0.0
        tot_wk_amt = 0.0
        tot_hol_h = 0.0
        tot_hol_amt = 0.0
        tot_grand = 0.0

        idx = 1
        for slip in slips:
            emp = slip.employee_id
            contract = slip.contract_id
            salary = contract.wage if contract else (slip.wage or 0.0)

            # Hours respecting toggles (safe getattr)
            norm_h = (getattr(slip, 'ot_day_hours', 0.0) or 0.0) if self.include_normal_ot else 0.0
            night_h = (getattr(slip, 'ot_night_hours', 0.0) or 0.0) if self.include_night_ot else 0.0
            wk_h = (getattr(slip, 'ot_weekend_hours', 0.0) or 0.0) if self.include_weekend_ot else 0.0
            hol_h = (getattr(slip, 'ot_holiday_hours', 0.0) or 0.0) if self.include_holiday_ot else 0.0
            tot_h = norm_h + night_h + wk_h + hol_h

            work_hours = getattr(contract, 'monthly_working_hours', 208.0) or 208.0 if contract else 208.0
            base_hourly = (salary / work_hours) if work_hours else 0.0
            norm_rate = getattr(contract, 'ot_day_rate', 0.0) or (base_hourly * 1.5) if contract else (base_hourly * 1.5)
            night_rate = getattr(contract, 'ot_night_rate', 0.0) or (base_hourly * 1.75) if contract else (base_hourly * 1.75)
            wk_rate = getattr(contract, 'ot_weekend_rate', 0.0) or (base_hourly * 2.0) if contract else (base_hourly * 2.0)
            hol_rate = getattr(contract, 'ot_holiday_rate', 0.0) or (base_hourly * 2.5) if contract else (base_hourly * 2.5)

            norm_amt = round(norm_h * norm_rate, 2)
            night_amt = round(night_h * night_rate, 2)
            wk_amt = round(wk_h * wk_rate, 2)
            hol_amt = round(hol_h * hol_rate, 2)
            row_tot = norm_amt + night_amt + wk_amt + hol_amt

            # If salary rule line exists on slip for overtime, fall back if calculated is 0
            if row_tot == 0.0 and slip.line_ids:
                slip_rule_map = {line.code: line.total for line in slip.line_ids}
                ot_rule_amt = slip_rule_map.get('OVERTIME', 0.0) or slip_rule_map.get('OT', 0.0) or slip_rule_map.get('OT_NORMAL', 0.0)
                if ot_rule_amt:
                    norm_amt = ot_rule_amt
                    row_tot = ot_rule_amt

            if self.only_with_values and row_tot <= 0.0 and tot_h <= 0.0:
                continue

            rows.append({
                'no': idx,
                'name': emp.name or '',
                'salary': salary,
                'norm_h': norm_h,
                'norm_amt': norm_amt,
                'night_h': night_h,
                'night_amt': night_amt,
                'wk_h': wk_h,
                'wk_amt': wk_amt,
                'hol_h': hol_h,
                'hol_amt': hol_amt,
                'total': row_tot,
            })

            tot_salary += salary
            tot_norm_h += norm_h
            tot_norm_amt += norm_amt
            tot_night_h += night_h
            tot_night_amt += night_amt
            tot_wk_h += wk_h
            tot_wk_amt += wk_amt
            tot_hol_h += hol_h
            tot_hol_amt += hol_amt
            tot_grand += row_tot
            idx += 1

        totals = {
            'tot_salary': tot_salary,
            'tot_norm_h': tot_norm_h,
            'tot_norm_amt': tot_norm_amt,
            'tot_night_h': tot_night_h,
            'tot_night_amt': tot_night_amt,
            'tot_wk_h': tot_wk_h,
            'tot_wk_amt': tot_wk_amt,
            'tot_hol_h': tot_hol_h,
            'tot_hol_amt': tot_hol_amt,
            'tot_grand': tot_grand,
        }
        return {'rows': rows, 'totals': totals}

    def _get_allowance_data(self, slips):
        """ Dynamically extracts allowances based on user-selected allowance_rule_ids """
        if self.allowance_rule_ids:
            columns = [
                {
                    'key': rule.code,
                    'title': rule.name.split('(')[0].strip(),
                    'codes': [rule.code],
                }
                for rule in self.allowance_rule_ids
            ]
        else:
            columns = [
                {'key': 'acting', 'title': 'Acting', 'codes': ['ALW_ACTING_POSITION', 'ACTING', 'ACT']},
                {'key': 'indemnity', 'title': 'Indeminity Allowance', 'codes': ['ALW_CASH_INDEMNITY', 'CASH_INDEMNITY', 'INDEMNITY']},
                {'key': 'service', 'title': 'Service', 'codes': ['ALW_SERVICE', 'SERVICE_ALLOWANCE', 'DED_SERVICE_CHARGE']},
                {'key': 'transp_repl', 'title': 'transp repl', 'codes': ['ALW_TRANSPORT_REPL', 'CA', 'CAGG', 'TRANS_REPL']},
                {'key': 'untaxed_transp', 'title': 'Untaxed Transportation Allowance', 'codes': ['ALW_TRANSPORT', 'TTRA_EXEMPT', 'TRANSPORT', 'UNTAXED_TRANS']},
            ]

        rows = []
        col_totals = {col['key']: 0.0 for col in columns}
        grand_total = 0.0

        idx = 1
        for slip in slips:
            emp = slip.employee_id
            slip_rule_map = {line.code: line.total for line in slip.line_ids}

            row_vals = {}
            row_sum = 0.0
            for col in columns:
                val = 0.0
                for c in col['codes']:
                    if c in slip_rule_map:
                        val += slip_rule_map[c]

                if val == 0.0 and slip.contract_id:
                    if col['key'] in ('acting', 'ALW_ACTING_POSITION') and hasattr(slip.contract_id, 'acting_allowance'):
                        val = getattr(slip.contract_id, 'acting_allowance') or 0.0
                    elif col['key'] in ('indemnity', 'ALW_CASH_INDEMNITY') and hasattr(slip.contract_id, 'cash_indemnity'):
                        val = getattr(slip.contract_id, 'cash_indemnity') or 0.0
                    elif col['key'] in ('untaxed_transp', 'ALW_TRANSPORT') and hasattr(slip.contract_id, 'transport_allowance'):
                        val = getattr(slip.contract_id, 'transport_allowance') or 0.0

                row_vals[col['key']] = round(val, 2)
                row_sum += val

            if self.only_with_values and row_sum <= 0.0:
                continue

            rows.append({
                'no': idx,
                'name': emp.name or '',
                'values': row_vals,
                'total': round(row_sum, 2),
            })

            for col in columns:
                col_totals[col['key']] += row_vals[col['key']]
            grand_total += row_sum
            idx += 1

        return {
            'columns': columns,
            'rows': rows,
            'col_totals': col_totals,
            'grand_total': round(grand_total, 2),
        }

    def _get_absent_data(self, slips):
        """ Calculates Absent rows: No., Name, Basic salary, Absent days, Absent deduction """
        rows = []
        tot_salary = 0.0
        tot_days = 0.0
        tot_deduction = 0.0

        idx = 1
        for slip in slips:
            emp = slip.employee_id
            contract = slip.contract_id
            basic_salary = contract.wage if contract else (slip.wage or 0.0)

            abs_days = getattr(slip, 'approved_absent_days', 0.0) or 0.0
            if abs_days == 0.0 and getattr(slip, 'approved_absent_hours', 0.0):
                abs_days = round(slip.approved_absent_hours / 8.0, 1)

            ded_amt = getattr(slip, 'absent_deduction_amount', 0.0) or 0.0
            if not ded_amt and slip.line_ids:
                slip_rule_map = {line.code: line.total for line in slip.line_ids}
                ded_amt = abs(slip_rule_map.get('DED_ABSENT', 0.0) or slip_rule_map.get('ABSENT', 0.0) or slip_rule_map.get('UNPAID', 0.0))
            if not ded_amt and abs_days > 0:
                ded_amt = round((basic_salary / 26.0) * abs_days, 2)

            if self.only_with_values and abs_days <= 0.0 and ded_amt <= 0.0:
                continue

            rows.append({
                'no': idx,
                'name': emp.name or '',
                'salary': basic_salary,
                'absent_days': abs_days,
                'absent_deduction': ded_amt,
            })

            tot_salary += basic_salary
            tot_days += abs_days
            tot_deduction += ded_amt
            idx += 1

        totals = {
            'tot_salary': tot_salary,
            'tot_days': tot_days,
            'tot_deduction': tot_deduction,
        }
        return {'rows': rows, 'totals': totals}

    def _get_deduction_data(self, slips):
        """ Dynamically extracts deductions based on user-selected deduction_rule_ids """
        if self.deduction_rule_ids:
            columns = [
                {
                    'key': rule.code,
                    'title': rule.name.split('(')[0].strip(),
                    'code': rule.code,
                }
                for rule in self.deduction_rule_ids
            ]
        else:
            oil_rule = self.env['hr.salary.rule'].search([('code', '=', 'DED_OIL')], limit=1)
            title = oil_rule.name.split('(')[0].strip() if oil_rule else 'Oil Deduction'
            columns = [{'key': 'DED_OIL', 'title': title, 'code': 'DED_OIL'}]

        is_single = len(columns) == 1
        rows = []
        col_totals = {col['key']: 0.0 for col in columns}
        grand_total = 0.0

        idx = 1
        for slip in slips:
            emp = slip.employee_id
            slip_rule_map = {line.code: line.total for line in slip.line_ids}

            row_vals = {}
            row_sum = 0.0
            for col in columns:
                amt = abs(slip_rule_map.get(col['code'], 0.0))
                if amt == 0.0 and slip.contract_id and col['code'] == 'DED_OIL' and hasattr(slip.contract_id, 'oil_deduction'):
                    amt = abs(getattr(slip.contract_id, 'oil_deduction') or 0.0)

                row_vals[col['key']] = round(amt, 2)
                row_sum += amt

            if self.only_with_values and row_sum <= 0.0:
                continue

            rows.append({
                'no': idx,
                'name': emp.name or '',
                'values': row_vals,
                'total': round(row_sum, 2),
                'amount': row_vals[columns[0]['key']] if is_single else row_sum,
            })

            for col in columns:
                col_totals[col['key']] += row_vals[col['key']]
            grand_total += row_sum
            idx += 1

        return {
            'is_single': is_single,
            'columns': columns,
            'rule_label': columns[0]['title'] if is_single else "Deductions",
            'rows': rows,
            'col_totals': col_totals,
            'grand_total': round(grand_total, 2),
            'tot_amt': round(grand_total, 2),
        }

    def _action_generate_category_pdf(self, slips):
        """ Generate QWeb PDF for the selected category """
        data = {
            'report_title': self.report_title,
            'report_type': 'allowance' if self.report_category == 'allowance' else ('deduction' if self.report_category == 'deduction' else 'overtime'),
            'attendance_report_type': self.attendance_sub_type,
            'prepared_by': self.prepared_by or '',
            'checked_by': self.checked_by or '',
            'verified_by': self.verified_by or '',
            'approved_by': self.approved_by or '',
        }
        if self.report_category == 'attendance':
            if self.attendance_sub_type in ('overtime', 'both'):
                data['ot_data'] = self._get_overtime_data(slips)
            if self.attendance_sub_type in ('absent', 'both'):
                data['abs_data'] = self._get_absent_data(slips)
        elif self.report_category == 'allowance':
            data['alw_data'] = self._get_allowance_data(slips)
        elif self.report_category == 'deduction':
            data['ded_data'] = self._get_deduction_data(slips)

        report = self.env.ref('payroll_bank_advice.action_report_monthly_payroll_statement', raise_if_not_found=False)
        if not report:
            report = self.env.ref('attendance_ot_portal_approval.action_report_monthly_payroll_statement', raise_if_not_found=False)
        if not report:
            raise UserError(_("Monthly payroll report template not found. Please upgrade the module."))
        return report.report_action(self, data=data)

    def _action_export_category_xlsx(self, slips):
        """ Generates and downloads Excel (.xlsx) report matching chosen categories & rules """
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # --- Formats & Styles ---
        fmt_title = workbook.add_format({
            'bold': True,
            'font_size': 16,
            'font_name': 'Times New Roman',
            'align': 'center',
            'valign': 'vcenter',
        })
        fmt_th = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
            'bg_color': '#FFFFFF',
        })
        fmt_th_highlight = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
            'text_wrap': True,
            'bg_color': '#C8D6AF',
        })
        fmt_td_center = workbook.add_format({
            'font_size': 9.5,
            'font_name': 'Times New Roman',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1,
        })
        fmt_td_name = workbook.add_format({
            'font_size': 9.5,
            'font_name': 'Times New Roman',
            'align': 'left',
            'valign': 'vcenter',
            'border': 1,
        })
        fmt_td_num = workbook.add_format({
            'font_size': 9.5,
            'font_name': 'Times New Roman',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'border': 1,
        })
        fmt_td_dec = workbook.add_format({
            'font_size': 9.5,
            'font_name': 'Times New Roman',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.00',
            'border': 1,
        })
        fmt_tot_label = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'align': 'right',
            'valign': 'vcenter',
            'border': 1,
        })
        fmt_tot_num = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'border': 1,
        })
        fmt_tot_dec = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '0.00',
            'border': 1,
        })
        fmt_sign_label = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Times New Roman',
            'valign': 'vcenter',
        })

        def write_signatures(ws, row, last_col):
            row += 2
            col_gap = max(1, last_col // 4)
            signs = [
                ('Prepared BY:', self.prepared_by or '___________________'),
                ('Checked BY:', self.checked_by or '___________________'),
                ('Verified BY:', self.verified_by or '___________________'),
                ('Approved By:', self.approved_by or '___________________'),
            ]
            c = 0
            for label, val in signs:
                ws.write(row, c, f"{label} {val}", fmt_sign_label)
                c += col_gap

        # --- ATTENDANCE: OVERTIME ---
        if self.report_category == 'attendance' and self.attendance_sub_type in ('overtime', 'both'):
            ws = workbook.add_worksheet('Overtime Report')
            ot_data = self._get_overtime_data(slips)
            rows = ot_data['rows']
            totals = ot_data['totals']

            ot_title = self.report_title or f"Overtime done for the Month of {eth_utils.ETHIOPIAN_MONTHS_AMHARIC[int(self.ethiopian_month or '1')]} {self.ethiopian_year}"
            ws.merge_range(1, 0, 1, 11, ot_title, fmt_title)
            ws.set_row(1, 30)

            ws.merge_range(3, 0, 4, 0, "No.", fmt_th)
            ws.merge_range(3, 1, 4, 1, "Name", fmt_th)
            ws.merge_range(3, 2, 4, 2, "salary", fmt_th)
            ws.merge_range(3, 3, 3, 4, "Normal Hour", fmt_th)
            ws.write(4, 3, "Hour", fmt_th)
            ws.write(4, 4, "Amount", fmt_th)
            ws.merge_range(3, 5, 3, 6, "Night Hour", fmt_th)
            ws.write(4, 5, "Hour", fmt_th)
            ws.write(4, 6, "Amount", fmt_th)
            ws.merge_range(3, 7, 3, 8, "Weekend hour", fmt_th)
            ws.write(4, 7, "Hour", fmt_th)
            ws.write(4, 8, "Amount", fmt_th)
            ws.merge_range(3, 9, 3, 10, "Holiday Hour", fmt_th)
            ws.write(4, 9, "Hour", fmt_th)
            ws.write(4, 10, "Amount", fmt_th)
            ws.merge_range(3, 11, 4, 11, "Total", fmt_th)

            r = 5
            for row in rows:
                ws.write(r, 0, row['no'], fmt_td_center)
                ws.write(r, 1, row['name'], fmt_td_name)
                ws.write(r, 2, row['salary'], fmt_td_num)
                ws.write(r, 3, row['norm_h'], fmt_td_dec)
                ws.write(r, 4, row['norm_amt'], fmt_td_num)
                ws.write(r, 5, row['night_h'], fmt_td_dec)
                ws.write(r, 6, row['night_amt'], fmt_td_num)
                ws.write(r, 7, row['wk_h'], fmt_td_dec)
                ws.write(r, 8, row['wk_amt'], fmt_td_num)
                ws.write(r, 9, row['hol_h'], fmt_td_dec)
                ws.write(r, 10, row['hol_amt'], fmt_td_num)
                ws.write(r, 11, row['total'], fmt_td_num)
                r += 1

            ws.merge_range(r, 0, r, 1, "Total", fmt_tot_label)
            ws.write(r, 2, totals['tot_salary'], fmt_tot_num)
            ws.write(r, 3, totals['tot_norm_h'], fmt_tot_dec)
            ws.write(r, 4, totals['tot_norm_amt'], fmt_tot_num)
            ws.write(r, 5, totals['tot_night_h'], fmt_tot_dec)
            ws.write(r, 6, totals['tot_night_amt'], fmt_tot_num)
            ws.write(r, 7, totals['tot_wk_h'], fmt_tot_dec)
            ws.write(r, 8, totals['tot_wk_amt'], fmt_tot_num)
            ws.write(r, 9, totals['tot_hol_h'], fmt_tot_dec)
            ws.write(r, 10, totals['tot_hol_amt'], fmt_tot_num)
            ws.write(r, 11, totals['tot_grand'], fmt_tot_num)

            ws.set_column(0, 0, 6)
            ws.set_column(1, 1, 24)
            ws.set_column(2, 2, 13)
            ws.set_column(3, 10, 11)
            ws.set_column(11, 11, 14)
            write_signatures(ws, r, 11)

        # --- ATTENDANCE: ABSENT ---
        if self.report_category == 'attendance' and self.attendance_sub_type in ('absent', 'both'):
            ws = workbook.add_worksheet('Absent List')
            abs_data = self._get_absent_data(slips)
            rows = abs_data['rows']
            totals = abs_data['totals']

            abs_title = self.report_title or f"Absent list for the Month of {eth_utils.ETHIOPIAN_MONTHS_AMHARIC[int(self.ethiopian_month or '1')]} {self.ethiopian_year}"
            ws.merge_range(1, 0, 1, 4, abs_title, fmt_title)
            ws.set_row(1, 30)

            ws.write(3, 0, "No.", fmt_th)
            ws.write(3, 1, "Name", fmt_th)
            ws.write(3, 2, "Basic salary", fmt_th)
            ws.write(3, 3, "Absent days", fmt_th)
            ws.write(3, 4, "Absent deduction", fmt_th)

            r = 4
            for row in rows:
                ws.write(r, 0, row['no'], fmt_td_center)
                ws.write(r, 1, row['name'], fmt_td_name)
                ws.write(r, 2, row['salary'], fmt_td_num)
                ws.write(r, 3, row['absent_days'], fmt_td_dec)
                ws.write(r, 4, row['absent_deduction'], fmt_td_num)
                r += 1

            ws.merge_range(r, 0, r, 1, "Total", fmt_tot_label)
            ws.write(r, 2, totals['tot_salary'], fmt_tot_num)
            ws.write(r, 3, totals['tot_days'], fmt_td_dec)
            ws.write(r, 4, totals['tot_deduction'], fmt_tot_num)

            ws.set_column(0, 0, 6)
            ws.set_column(1, 1, 25)
            ws.set_column(2, 2, 16)
            ws.set_column(3, 3, 14)
            ws.set_column(4, 4, 18)
            write_signatures(ws, r, 4)

        # --- ALLOWANCE SHEET ---
        if self.report_category == 'allowance':
            ws = workbook.add_worksheet('Allowance Report')
            alw_data = self._get_allowance_data(slips)
            cols = alw_data['columns']
            rows = alw_data['rows']
            num_cols = len(cols)

            alw_title = self.report_title or f"Payroll Allowance for the Month of {eth_utils.ETHIOPIAN_MONTHS_AMHARIC[int(self.ethiopian_month or '1')]} {self.ethiopian_year}"
            ws.merge_range(1, 0, 1, 2 + num_cols, alw_title, fmt_title)
            ws.set_row(1, 30)

            ws.merge_range(3, 0, 4, 0, "No", fmt_th)
            ws.merge_range(3, 1, 4, 1, "Employee", fmt_th)
            ws.merge_range(3, 2, 3, 1 + num_cols, "Allowance", fmt_th)
            for c_idx, col in enumerate(cols):
                ws.write(4, 2 + c_idx, col['title'], fmt_th)
            ws.merge_range(3, 2 + num_cols, 4, 2 + num_cols, "Total", fmt_th)

            r = 5
            for row in rows:
                ws.write(r, 0, row['no'], fmt_td_center)
                ws.write(r, 1, row['name'], fmt_td_name)
                for c_idx, col in enumerate(cols):
                    val = row['values'][col['key']]
                    if val > 0:
                        ws.write(r, 2 + c_idx, val, fmt_td_num)
                    else:
                        ws.write(r, 2 + c_idx, "", fmt_td_center)
                ws.write(r, 2 + num_cols, row['total'], fmt_td_num)
                r += 1

            ws.merge_range(r, 0, r, 1, "Total", fmt_tot_label)
            for c_idx, col in enumerate(cols):
                ws.write(r, 2 + c_idx, alw_data['col_totals'][col['key']], fmt_tot_num)
            ws.write(r, 2 + num_cols, alw_data['grand_total'], fmt_tot_num)

            ws.set_column(0, 0, 6)
            ws.set_column(1, 1, 24)
            for c_idx in range(num_cols):
                ws.set_column(2 + c_idx, 2 + c_idx, 16)
            ws.set_column(2 + num_cols, 2 + num_cols, 14)
            write_signatures(ws, r, 2 + num_cols)

        # --- DEDUCTION SHEET ---
        if self.report_category == 'deduction':
            ws = workbook.add_worksheet('Deduction Report')
            ded_data = self._get_deduction_data(slips)
            cols = ded_data['columns']
            rows = ded_data['rows']
            is_single = ded_data['is_single']

            last_c = 2 if is_single else (1 + len(cols) + 1)
            ded_title = self.report_title or f"Payroll Deduction for the Month of {eth_utils.ETHIOPIAN_MONTHS_AMHARIC[int(self.ethiopian_month or '1')]} {self.ethiopian_year}"
            ws.merge_range(1, 0, 1, last_c, ded_title, fmt_title)
            ws.set_row(1, 30)

            if is_single:
                ws.write(3, 0, "No", fmt_th_highlight)
                ws.write(3, 1, "Employee", fmt_th_highlight)
                ws.write(3, 2, cols[0]['title'], fmt_th_highlight)

                r = 4
                for row in rows:
                    ws.write(r, 0, row['no'], fmt_td_center)
                    ws.write(r, 1, row['name'], fmt_td_name)
                    ws.write(r, 2, row['amount'], fmt_td_num)
                    r += 1

                ws.merge_range(r, 0, r, 1, "Total", fmt_tot_label)
                ws.write(r, 2, ded_data['tot_amt'], fmt_tot_num)
                ws.set_column(0, 0, 6)
                ws.set_column(1, 1, 28)
                ws.set_column(2, 2, 18)
                write_signatures(ws, r, 2)
            else:
                ws.merge_range(3, 0, 4, 0, "No", fmt_th_highlight)
                ws.merge_range(3, 1, 4, 1, "Employee", fmt_th_highlight)
                ws.merge_range(3, 2, 3, 1 + len(cols), "Deductions", fmt_th_highlight)
                for c_idx, col in enumerate(cols):
                    ws.write(4, 2 + c_idx, col['title'], fmt_th_highlight)
                ws.merge_range(3, 2 + len(cols), 4, 2 + len(cols), "Total", fmt_th_highlight)

                r = 5
                for row in rows:
                    ws.write(r, 0, row['no'], fmt_td_center)
                    ws.write(r, 1, row['name'], fmt_td_name)
                    for c_idx, col in enumerate(cols):
                        ws.write(r, 2 + c_idx, row['values'][col['key']], fmt_td_num)
                    ws.write(r, 2 + len(cols), row['total'], fmt_td_num)
                    r += 1

                ws.merge_range(r, 0, r, 1, "Total", fmt_tot_label)
                for c_idx, col in enumerate(cols):
                    ws.write(r, 2 + c_idx, ded_data['col_totals'][col['key']], fmt_tot_num)
                ws.write(r, 2 + len(cols), ded_data['grand_total'], fmt_tot_num)

                ws.set_column(0, 0, 6)
                ws.set_column(1, 1, 26)
                for c_idx in range(len(cols)):
                    ws.set_column(2 + c_idx, 2 + c_idx, 16)
                ws.set_column(2 + len(cols), 2 + len(cols), 14)
                write_signatures(ws, r, 2 + len(cols))

        workbook.close()
        xlsx_data = output.getvalue()

        clean_name = (self.report_title or 'Payroll_Report').replace('/', '-').replace(' ', '_').replace(':', '')
        filename = f"{clean_name}.xlsx"

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

    def action_generate_pdf(self):
        """ Print the Report as PDF """
        self.ensure_one()
        slips = self._get_target_payslips()

        # If specific category chosen, route to monthly statement PDF
        if self.report_category in ('allowance', 'deduction', 'attendance'):
            return self._action_generate_category_pdf(slips)

        # Otherwise Master PDF summary
        report = self.env.ref('payroll_bank_advice.action_report_salary_rule_summary')
        return report.report_action(slips)

    def action_generate_xlsx(self):
        """ Generates and downloads the Excel (.xlsx) Payroll Report """
        self.ensure_one()
        slips = self._get_target_payslips()

        # If category is Allowance, Deduction, or Attendance, route to dedicated category export
        if self.report_category in ('allowance', 'deduction', 'attendance'):
            return self._action_export_category_xlsx(slips)

        # Otherwise, original Master Breakdown spreadsheet:
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
            'bg_color': '#EBF5FB',
        })
        fmt_tot_label = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'border': 1,
            'bg_color': '#E5E7E9',
        })
        fmt_tot_num = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'font_name': 'Arial',
            'align': 'right',
            'valign': 'vcenter',
            'num_format': '#,##0.00',
            'border': 1,
            'bg_color': '#E5E7E9',
        })

        # --- Dynamic Columns Discovery ---
        all_lines = slips.mapped('line_ids')
        used_rule_codes = set(all_lines.mapped('code'))

        all_rules = self.env['hr.salary.rule'].search([
            ('code', 'in', list(used_rule_codes)),
            ('appears_on_payslip', '=', True)
        ])

        rule_meta = {}
        for r in all_rules:
            cat_code = r.category_id.code if r.category_id else 'OTHER'
            rule_meta[r.code] = {
                'id': r.id,
                'name': r.name,
                'code': r.code,
                'sequence': r.sequence,
                'category_code': cat_code,
                'is_earning': cat_code in ('BASIC', 'ALW', 'GROSS'),
                'is_deduction': cat_code in ('DED',),
                'is_net': cat_code in ('NET',),
            }

        for c in used_rule_codes:
            if c not in rule_meta:
                matching_lines = all_lines.filtered(lambda l: l.code == c)
                l_name = matching_lines[0].name if matching_lines else c
                l_cat = matching_lines[0].category_id.code if (matching_lines and matching_lines[0].category_id) else 'OTHER'
                rule_meta[c] = {
                    'id': False,
                    'name': l_name,
                    'code': c,
                    'sequence': 999,
                    'category_code': l_cat,
                    'is_earning': l_cat in ('BASIC', 'ALW', 'GROSS'),
                    'is_deduction': l_cat in ('DED',),
                    'is_net': l_cat in ('NET',),
                }

        earnings_rules = sorted([r for r in rule_meta.values() if r['is_earning']], key=lambda x: x['sequence'])
        deduction_rules = sorted([r for r in rule_meta.values() if r['is_deduction']], key=lambda x: x['sequence'])
        net_rules = sorted([r for r in rule_meta.values() if r['is_net']], key=lambda x: x['sequence'])
        other_rules = sorted([r for r in rule_meta.values() if not r['is_earning'] and not r['is_deduction'] and not r['is_net']], key=lambda x: x['sequence'])

        sorted_rules = earnings_rules + other_rules + deduction_rules + net_rules

        period_title = self.payslip_run_id.name if (self.filter_by == 'batch' and self.payslip_run_id) else f"{self.date_from} to {self.date_to}"

        def render_sheet(worksheet, sheet_slips, sheet_title):
            worksheet.set_paper(9)
            worksheet.set_landscape()

            worksheet.write(0, 0, f"{self.company_id.name} - Payroll Breakdown Report", fmt_title)
            worksheet.write(1, 0, f"Period: {period_title}  |  Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}", fmt_meta)
            worksheet.set_row(0, 24)
            worksheet.set_row(1, 18)

            headers_base = ['#', 'ID / Ref', 'Employee Name', 'Department', 'Job Position', 'Bank', 'Branch', 'Account Number']
            col_widths = {0: 5, 1: 14, 2: 24, 3: 18, 4: 18, 5: 18, 6: 14, 7: 18}

            for idx, h in enumerate(headers_base):
                worksheet.write(3, idx, h, fmt_th_base)

            col_idx = 8
            for r in sorted_rules:
                if r['is_net']:
                    th_fmt = fmt_th_net
                elif r['is_deduction']:
                    th_fmt = fmt_th_deduction
                elif r['is_earning']:
                    th_fmt = fmt_th_earning
                else:
                    th_fmt = fmt_th_base

                worksheet.write(3, col_idx, f"{r['name']}\n({r['code']})", th_fmt)
                col_widths[col_idx] = max(13, len(r['name']) // 2 + 5)
                col_idx += 1

            worksheet.set_row(3, 30)

            cur_row = 4
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

                slip_rule_map = {line.code: line.total for line in slip.line_ids}

                r_col = 8
                for r in sorted_rules:
                    amt = slip_rule_map.get(r['code'], 0.0)
                    rule_totals[r['code']] += amt
                    cell_fmt = fmt_td_net_num if r['is_net'] else fmt_td_num
                    worksheet.write(cur_row, r_col, amt, cell_fmt)
                    r_col += 1

                cur_row += 1

            worksheet.merge_range(cur_row, 0, cur_row, 7, "GRAND TOTAL:", fmt_tot_label)
            r_col = 8
            for r in sorted_rules:
                worksheet.write(cur_row, r_col, rule_totals[r['code']], fmt_tot_num)
                r_col += 1
            worksheet.set_row(cur_row, 22)

            for c_idx, width in col_widths.items():
                worksheet.set_column(c_idx, c_idx, width)

        ws_all = workbook.add_worksheet('All Employees')
        render_sheet(ws_all, slips, 'All Employees Summary')

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
                    clean_sheet_name = b_name[:28].replace('/', '-').replace('\\', '-').replace('?', '').replace('*', '')
                    ws_bank = workbook.add_worksheet(clean_sheet_name)
                    render_sheet(ws_bank, b_slips, f"{b_name} Schedule")

        workbook.close()
        xlsx_data = output.getvalue()

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
