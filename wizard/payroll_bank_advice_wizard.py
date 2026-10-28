# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError
import datetime

CLASSIFICATION_SELECTION = [
    ('all', 'All Classifications'),
    ('head_office', 'Head Office (ዋና መ/ቤት)'),
    ('cpw', 'CPW'),
    ('permanent', 'Farm (የእርሻ)'),
    ('temporary', 'Temporary (ጊዜያዊ)'),
    ('zemach', 'Zemach / Seasonal (ዘመች)'),
]


class PayrollBankAdviceWizard(models.TransientModel):
    _name = 'payroll.bank.advice.wizard'
    _description = 'Payroll Bank Advice Wizard'

    all_payslip_ids = fields.Many2many(
        'hr.payslip',
        'rel_advice_wizard_all_payslips',
        string='Initial Selected Payslips',
    )
    payslip_ids = fields.Many2many(
        'hr.payslip',
        'rel_advice_wizard_filtered_payslips',
        string='Selected Payslips',
        required=True,
    )
    payslip_run_id = fields.Many2one(
        'hr.payslip.run',
        string='Payslip Batch',
        readonly=True,
    )
    payslip_count = fields.Integer(
        string='Payslips Count',
        compute='_compute_payslip_count',
    )

    # -------------------------------------------------------------------------
    # Classification & Bank Selection
    # -------------------------------------------------------------------------
    employee_classification = fields.Selection(
        CLASSIFICATION_SELECTION,
        string='Employee Classification',
        default='all',
        required=True,
        help='Select which employee category to generate the Bank Advice for.',
    )
    bank_mode = fields.Selection([
        ('all', 'All Banks (Generate Separate Letter per Bank)'),
        ('single', 'Specific Bank Only'),
    ], string='Advice Mode', default='all', required=True)

    available_banks = fields.Char(
        string='Available Banks in Selection',
        readonly=True,
    )
    target_bank_name = fields.Char(
        string='Bank Name',
        default='Commercial Bank of Ethiopia (CBE)',
        help='Name of the bank addressed on the letter (e.g. Dashen Bank, Commercial Bank of Ethiopia)',
    )
    branch = fields.Char(
        string='Branch',
        default='Main Branch',
        help='Branch name (e.g. Ghion Branch, Bole Branch, Signal Branch)',
    )
    bank_city = fields.Char(
        string='City',
        default='Addis Ababa',
        required=True,
    )
    company_account_number = fields.Char(
        string='Company Debit Account',
        help='Company bank account number maintained with this bank (e.g. 0002034226011)',
    )
    pay_period = fields.Char(
        string='Payment Month / Period',
        required=True,
        help='Month appearing in the letter (e.g. Sep, 2026)',
    )
    subject = fields.Char(
        string='Subject',
        default='Transfer of Cash',
        required=True,
        help='Letter subject (e.g. Transfer of Cash)',
    )
    reference_number = fields.Char(
        string='Reference Number',
        help='Custom reference number. Leave blank to auto-generate (e.g. CBE/BA/2026/09/001)',
    )
    print_branch = fields.Boolean(
        string='Print Bank Branch',
        default=True,
        help='If checked, prints Bank Name and Branch in separate columns on the Bank Advice PDF.',
    )
    include_salary_summary = fields.Boolean(
        string='Include Salary Rule Breakdown (Landscape)',
        default=False,
        help='Include the landscape page breaking down salary earnings and deductions per employee',
    )

    @api.depends('payslip_ids')
    def _compute_payslip_count(self):
        for rec in self:
            rec.payslip_count = len(rec.payslip_ids)

    # -------------------------------------------------------------------------
    # Default Get & Initialization
    # -------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super(PayrollBankAdviceWizard, self).default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])

        slips = self.env['hr.payslip']
        run = False
        if active_model == 'hr.payslip.run' and active_ids:
            run = self.env['hr.payslip.run'].browse(active_ids[0])
            slips = run.mapped('slip_ids').filtered(lambda s: s.state != 'cancel')
            res['payslip_run_id'] = run.id
        elif active_model == 'hr.payslip' and active_ids:
            slips = self.env['hr.payslip'].browse(active_ids).filtered(lambda s: s.state != 'cancel')
        elif self.env.context.get('default_payslip_ids'):
            slips = self.env['hr.payslip'].browse(self.env.context.get('default_payslip_ids')).filtered(lambda s: s.state != 'cancel')

        if not slips:
            raise UserError(_("No valid payslips found to generate Bank Advice."))

        res['all_payslip_ids'] = [(6, 0, slips.ids)]
        res['payslip_ids'] = [(6, 0, slips.ids)]

        # Detect classification of selected slips
        detected_types = set()
        for s in slips:
            c_type = getattr(s.employee_id, 'farm_employee_type', False) or getattr(s, 'farm_employee_type', False)
            if c_type:
                detected_types.add(c_type)

        target_classification = 'all'
        if len(detected_types) == 1:
            target_classification = list(detected_types)[0]
        res['employee_classification'] = target_classification

        # Determine pay period string e.g. "Sep, 2026"
        pay_period = ''
        if run and run.name and any(char.isdigit() for char in run.name):
            pay_period = run.name
        elif slips:
            sample_date = slips[0].date_to or slips[0].date_from or fields.Date.today()
            pay_period = sample_date.strftime('%b, %Y')
        res['pay_period'] = pay_period or fields.Date.today().strftime('%b, %Y')

        # Detect distinct banks present in these payslips
        detected_banks = []
        for slip in slips:
            bank_info = slip._resolve_employee_bank_details(slip.employee_id)
            bname = bank_info.get('bank_group_key') or bank_info.get('bank_name')
            if bname and bname not in detected_banks:
                detected_banks.append(bname)

        if detected_banks:
            res['available_banks'] = ', '.join(detected_banks)
            first_bank = detected_banks[0]
            res['target_bank_name'] = first_bank
            settings = self.env['payroll.bank.advice.settings'].get_bank_settings(first_bank)
            if settings:
                res['branch'] = settings.branch or ''
                res['bank_city'] = settings.bank_city or 'Addis Ababa'
                res['company_account_number'] = settings.company_account_number or ''
            else:
                company_partner = self.env.company.partner_id
                acc = company_partner.bank_ids.filtered(lambda a: a.bank_id and first_bank.lower() in (a.bank_id.name or '').lower())
                if acc:
                    res['company_account_number'] = acc[0].acc_number or ''
                    res['branch'] = acc[0].bank_id.street or ''

        # If footer config has preferred bank account, set it
        footer_config = self.env['payroll.bank.advice.footer.config'].get_footer_for_classification(
            target_classification, company_id=self.env.company.id
        )
        if footer_config and footer_config.bank_account_id:
            if footer_config.bank_account_id.acc_number:
                res['company_account_number'] = footer_config.bank_account_id.acc_number
            if footer_config.bank_account_id.bank_id and footer_config.bank_account_id.bank_id.name:
                res['target_bank_name'] = footer_config.bank_account_id.bank_id.name

        return res

    # -------------------------------------------------------------------------
    # Onchange: Employee Classification
    # -------------------------------------------------------------------------

    @api.onchange('employee_classification')
    def _onchange_employee_classification(self):
        # 1. Filter payslips by classification
        if self.employee_classification and self.employee_classification != 'all':
            matched_slips = self.all_payslip_ids.filtered(
                lambda s: (getattr(s.employee_id, 'farm_employee_type', False) or getattr(s, 'farm_employee_type', False)) == self.employee_classification
            )
            if matched_slips:
                self.payslip_ids = matched_slips
            else:
                self.payslip_ids = self.all_payslip_ids
        else:
            self.payslip_ids = self.all_payslip_ids

        # 2. Re-detect banks
        detected_banks = []
        for slip in self.payslip_ids:
            bank_info = slip._resolve_employee_bank_details(slip.employee_id)
            bname = bank_info.get('bank_group_key') or bank_info.get('bank_name')
            if bname and bname not in detected_banks:
                detected_banks.append(bname)

        if detected_banks:
            self.available_banks = ', '.join(detected_banks)
            if self.target_bank_name not in detected_banks:
                self.target_bank_name = detected_banks[0]
        else:
            self.available_banks = ''

        # 3. Check if footer config specifies a default debit account
        footer_config = self.env['payroll.bank.advice.footer.config'].get_footer_for_classification(
            self.employee_classification, company_id=self.env.company.id
        )
        if footer_config and footer_config.bank_account_id:
            if footer_config.bank_account_id.acc_number:
                self.company_account_number = footer_config.bank_account_id.acc_number
            if footer_config.bank_account_id.bank_id and footer_config.bank_account_id.bank_id.name:
                self.target_bank_name = footer_config.bank_account_id.bank_id.name

    @api.onchange('target_bank_name')
    def _onchange_target_bank_name(self):
        if self.target_bank_name:
            settings = self.env['payroll.bank.advice.settings'].get_bank_settings(self.target_bank_name)
            if settings:
                if settings.branch:
                    self.branch = settings.branch
                if settings.bank_city:
                    self.bank_city = settings.bank_city
                if settings.company_account_number:
                    self.company_account_number = settings.company_account_number
            else:
                company_partner = self.env.company.partner_id
                acc = company_partner.bank_ids.filtered(
                    lambda a: a.bank_id and self.target_bank_name.lower() in (a.bank_id.name or '').lower()
                )
                if acc:
                    self.company_account_number = acc[0].acc_number or ''
                    if acc[0].bank_id.street:
                        self.branch = acc[0].bank_id.street

    def action_confirm_print(self):
        self.ensure_one()
        if not self.payslip_ids:
            raise UserError(_("No payslips selected for printing."))

        wizard_vals = {
            'employee_classification': self.employee_classification,
            'bank_mode': self.bank_mode,
            'target_bank_name': self.target_bank_name,
            'branch': self.branch,
            'bank_city': self.bank_city,
            'company_account_number': self.company_account_number,
            'pay_period': self.pay_period,
            'subject': self.subject,
            'reference_number': self.reference_number,
            'print_branch': self.print_branch,
            'include_salary_summary': self.include_salary_summary,
        }
        return self.payslip_ids._render_and_merge_bank_advice(wizard_vals=wizard_vals)
