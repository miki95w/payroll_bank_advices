# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

CLASSIFICATION_SELECTION = [
    ('head_office', 'Head Office (ዋና መ/ቤት)'),
    ('cpw', 'CPW'),
    ('permanent', 'Farm (የእርሻ)'),
    ('temporary', 'Temporary (ጊዜያዊ)'),
    ('zemach', 'Zemach / Seasonal (ዘመች)'),
    ('all', 'Default / All Classifications'),
]


class PayrollBankAdviceFooterConfig(models.Model):
    _name = 'payroll.bank.advice.footer.config'
    _description = 'Payroll Bank Advice Footer Configuration'
    _order = 'sequence, id'

    name = fields.Char(
        string='Config Name',
        required=True,
        help='Descriptive name for this footer configuration (e.g. Head Office, CPW, Farm)',
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
    )
    classification = fields.Selection(
        CLASSIFICATION_SELECTION,
        string='Employee Classification',
        required=True,
        default='head_office',
        help='The employee classification this footer configuration applies to.',
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
        string='Default Company Debit Account',
        domain="[('partner_id', '=', company_partner_id)]",
        help='Optional: Default company bank account debited for this employee classification.',
    )

    # -------------------------------------------------------------------------
    # Letterhead Page Footer Lines (Official Contact Details at Bottom of Page)
    # -------------------------------------------------------------------------
    footer_line_1 = fields.Char(
        string='Footer Line 1 (Tel / Fax)',
        default='Tel No.: +251-115-54-97-84/91/94 FAX: +251-113-69-81-00/',
        help='First line of contact info (e.g. Tel No. and Fax)',
    )
    footer_line_2 = fields.Char(
        string='Footer Line 2 (P.O. Box & Location)',
        default='P.O.BOX: 5787, Addis Ababa, Ethiopia',
        help='Second line of contact info (e.g. P.O. Box, City, Country)',
    )
    footer_line_3 = fields.Char(
        string='Footer Line 3 (E-mail & Website)',
        default='E-mail: contact@midroc-ceo.com website: www.midroc-ethiopiagroup.com',
        help='Third line of contact info (e.g. Email and Website)',
    )

    # -------------------------------------------------------------------------
    # Signatory 1: Prepared By (Printed on Paper Advice)
    # -------------------------------------------------------------------------
    prepared_by_label = fields.Char(
        string='Prepared By Label',
        default='Prepared By:',
        required=True,
    )
    prepared_by_title = fields.Char(
        string='Prepared By Title / Role',
        default='HR / Payroll Officer',
        required=True,
        help='Role or title printed on paper advice under Prepared By line',
    )

    # -------------------------------------------------------------------------
    # Signatory 2: Approved By (Printed on Paper Advice)
    # -------------------------------------------------------------------------
    approved_by_label = fields.Char(
        string='Approved By Label',
        default='Approved By:',
        required=True,
    )
    approved_by_title = fields.Char(
        string='Approved By Title / Role',
        default='General Manager (GM)',
        required=True,
        help='Role or title printed on paper advice under Approved By line',
    )

    # -------------------------------------------------------------------------
    # Footer Notes & Seal
    # -------------------------------------------------------------------------
    footer_note = fields.Text(
        string='Footer Remarks / Note',
        help='Optional memo, instructions, or legal note printed above the signature blocks.',
    )
    show_seal = fields.Boolean(
        string='Show Official Stamp Box',
        default=True,
        help='If checked, prints the Official Company Seal / Stamp indication.',
    )
    seal_text = fields.Char(
        string='Stamp Box Text',
        default='[ Official Company Seal / Stamp ]',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
    )

    _sql_constraints = [
        ('uniq_classification_company', 'unique(classification, company_id)',
         'A footer configuration already exists for this Employee Classification in this company!'),
    ]

    # -------------------------------------------------------------------------
    # Helper APIs
    # -------------------------------------------------------------------------

    @api.model
    def get_footer_for_classification(self, classification=None, company_id=None):
        """
        Retrieves the configured footer record for a given classification and company.
        Falls back to 'all' or first active record if specific config is not found.
        """
        company_id = company_id or self.env.company.id
        if classification and classification != 'all':
            config = self.sudo().search([
                ('company_id', '=', company_id),
                ('classification', '=', classification),
                ('active', '=', True),
            ], limit=1)
            if config:
                return config

        # Fallback to 'all'
        config_all = self.sudo().search([
            ('company_id', '=', company_id),
            ('classification', '=', 'all'),
            ('active', '=', True),
        ], limit=1)
        if config_all:
            return config_all

        return self.sudo().search([('company_id', '=', company_id), ('active', '=', True)], limit=1)

    @api.model
    def get_default_footer_vals(self, classification=None):
        """
        Returns fallback dictionary in case no records are in the database yet.
        """
        defaults_by_type = {
            'head_office': {
                'prepared_by_title': 'HR / Payroll Officer',
                'approved_by_title': 'General Manager (GM)',
            },
            'cpw': {
                'prepared_by_title': 'CPW Coordinator',
                'approved_by_title': 'Project Manager',
            },
            'permanent': {
                'prepared_by_title': 'Farm HR / Administrator',
                'approved_by_title': 'Farm / Project Manager',
            },
            'temporary': {
                'prepared_by_title': 'Field Timekeeper / HR',
                'approved_by_title': 'Farm / Operations Manager',
            },
            'zemach': {
                'prepared_by_title': 'Camp Admin / Timekeeper',
                'approved_by_title': 'Farm General Manager',
            },
        }

        specific = defaults_by_type.get(classification, {
            'prepared_by_title': 'HR / Payroll Officer',
            'approved_by_title': 'General Manager (GM)',
        })

        return {
            'footer_line_1': 'Tel No.: +251-115-54-97-84/91/94 FAX: +251-113-69-81-00/',
            'footer_line_2': 'P.O.BOX: 5787, Addis Ababa, Ethiopia',
            'footer_line_3': 'E-mail: contact@midroc-ceo.com website: www.midroc-ethiopiagroup.com',
            'prepared_by_label': 'Prepared By:',
            'prepared_by_title': specific['prepared_by_title'],
            'approved_by_label': 'Approved By:',
            'approved_by_title': specific['approved_by_title'],
            'footer_note': '',
            'show_seal': True,
            'seal_text': '[ Official Company Seal / Stamp ]',
        }
