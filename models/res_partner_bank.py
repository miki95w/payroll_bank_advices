# -*- coding: utf-8 -*-

from odoo import fields, models


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    branch = fields.Char(
        string='Branch / ቅርንጫፍ',
        help='Bank branch name (e.g. Bole Branch, Ghion Branch, Signal Branch)'
    )


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    bank_branch = fields.Char(
        string='Bank Branch',
        related='bank_account_id.branch',
        readonly=False,
        store=True,
        help='Bank branch of the employee bank account'
    )
