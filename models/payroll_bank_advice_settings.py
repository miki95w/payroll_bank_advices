# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class PayrollBankAdviceSettings(models.Model):
    _name = 'payroll.bank.advice.settings'
    _description = 'Payroll Bank Advice Settings'
    _rec_name = 'default_bank_name'

    # -------------------------------------------------------------------------
    # Fields
    # -------------------------------------------------------------------------

    default_bank_name = fields.Char(
        string='Bank Name',
        required=True,
        default='Commercial Bank of Ethiopia (CBE)',
        help='Bank name shown on the bank advice letter (e.g. Dashen Bank, Commercial Bank of Ethiopia)',
    )
    branch = fields.Char(
        string='Branch',
        required=False,
        default='',
        help='Bank branch name (e.g. Ghion Branch, Bole Branch, Signal Branch)',
    )
    bank_city = fields.Char(
        string='City',
        required=True,
        default='Addis Ababa',
        help='Bank city/location',
    )
    bank_country = fields.Char(
        string='Country',
        default='Ethiopia',
        required=True,
        help='Bank country',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
    )
    company_partner_id = fields.Many2one(
        'res.partner',
        related='company_id.partner_id',
        string='Company Partner',
        readonly=True,
    )
    bank_account_id = fields.Many2one(
        'res.partner.bank',
        string='Company Bank Account',
        domain="[('partner_id', '=', company_partner_id)]",
        help='Select from configured company bank accounts to auto-fill bank details',
    )
    company_account_name = fields.Char(
        string='Company Account Name',
        required=False,
        default='',
        help='Company account name shown on the bank advice letter',
    )
    company_account_number = fields.Char(
        string='Company Account Number',
        required=False,
        default='',
        help='Company bank account number to be debited (e.g. 0002034226011)',
    )
    reference_prefix = fields.Char(
        string='Reference Prefix',
        default='BA',
        required=True,
        help='Prefix for reference number on the letter (e.g. DASHEN/BA, CBE/BA)',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Only active bank configurations are used by the advice generator',
    )

    @api.onchange('bank_account_id')
    def _onchange_bank_account_id(self):
        if self.bank_account_id:
            if self.bank_account_id.acc_number:
                self.company_account_number = self.bank_account_id.acc_number
            if self.bank_account_id.bank_id and self.bank_account_id.bank_id.name:
                self.default_bank_name = self.bank_account_id.bank_id.name
                if self.bank_account_id.bank_id.street:
                    self.branch = self.bank_account_id.bank_id.street
            if self.bank_account_id.acc_holder_name:
                self.company_account_name = self.bank_account_id.acc_holder_name
            elif self.company_id:
                self.company_account_name = self.company_id.name

    # -------------------------------------------------------------------------
    # Helper: retrieve settings for a specific bank or fallback to active
    # -------------------------------------------------------------------------

    @api.model
    def get_bank_settings(self, bank_name=None, company_id=None):
        company_id = company_id or self.env.company.id
        domain = [('active', '=', True), ('company_id', '=', company_id)]
        records = self.sudo().search(domain)
        if bank_name and records:
            b_norm = bank_name.lower().replace(' ', '').replace('-', '')
            for s in records:
                s_norm = (s.default_bank_name or '').lower().replace(' ', '').replace('-', '')
                if s_norm and (s_norm in b_norm or b_norm in s_norm):
                    return s
        return records[:1] if records else self.sudo().search([('active', '=', True)], limit=1)

    @api.model
    def get_active_settings(self, company_id=None):
        return self.get_bank_settings(company_id=company_id)
