# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError
import io
import base64
import datetime


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    # -------------------------------------------------------------------------
    # Private helpers: Bank & Branch normalization for Ethiopian Banking
    # -------------------------------------------------------------------------

    @api.model
    def _resolve_employee_bank_details(self, employee):
        """
        Dynamically extracts and standardizes bank and branch details for an employee.
        Handles:
          - CBE branches (e.g. Signal, Ashewa Tera, Betel, Addisu Gebeya, Ayat, Balderas)
          - Hyphenated / spaced names (e.g. 'CBE-bole', 'cbe signal', 'Awash Bole', 'Dashen - Ghion')
          - Standard res.partner.bank with res.bank records
        """
        bank_acc = False

        # 1. Direct employee bank_account_id
        if hasattr(employee, 'bank_account_id') and employee.bank_account_id:
            bank_acc = employee.bank_account_id

        # 2. Check employee bank_account_ids
        if not bank_acc and hasattr(employee, 'bank_account_ids') and employee.bank_account_ids:
            primary = getattr(employee, 'primary_bank_account_id', False)
            bank_acc = primary or employee.bank_account_ids[0]

        # 3. Check address_home_id (private partner bank accounts)
        if not bank_acc and hasattr(employee, 'address_home_id') and employee.address_home_id:
            partner = employee.address_home_id
            if partner.bank_ids:
                bank_acc = partner.bank_ids[0]

        # 4. Check active contracts
        if not bank_acc and hasattr(employee, 'contract_ids') and employee.contract_ids:
            for contract in employee.contract_ids:
                if hasattr(contract, 'bank_account_id') and contract.bank_account_id:
                    bank_acc = contract.bank_account_id
                    break

        account_number = bank_acc.acc_number if bank_acc else ''
        raw_bank_name = ''
        raw_branch = ''

        if bank_acc:
            if getattr(bank_acc, 'branch', False):
                raw_branch = (bank_acc.branch or '').strip()
            if not raw_branch and hasattr(employee, 'bank_branch') and employee.bank_branch:
                raw_branch = (employee.bank_branch or '').strip()
            if bank_acc.bank_id:
                raw_bank_name = (bank_acc.bank_id.name or '').strip()
                if not raw_branch:
                    raw_branch = (getattr(bank_acc.bank_id, 'branch', '') or getattr(bank_acc.bank_id, 'street', '') or '').strip()
            if not raw_bank_name:
                raw_bank_name = (bank_acc.bank_name or '').strip()
        elif hasattr(employee, 'bank_branch') and employee.bank_branch:
            raw_branch = (employee.bank_branch or '').strip()

        # Known Ethiopian Bank Families
        KNOWN_ETHIOPIAN_BANKS = [
            ('commercial bank of ethiopia', 'Commercial Bank of Ethiopia (CBE)', 'CBE'),
            ('cbe', 'Commercial Bank of Ethiopia (CBE)', 'CBE'),
            ('dashen', 'Dashen Bank', 'Dashen'),
            ('awash', 'Awash Bank', 'Awash'),
            ('abyssinia', 'Bank of Abyssinia', 'Abyssinia'),
            ('boa', 'Bank of Abyssinia', 'Abyssinia'),
            ('hibret', 'Hibret Bank', 'Hibret'),
            ('united bank', 'Hibret Bank', 'Hibret'),
            ('wegagen', 'Wegagen Bank', 'Wegagen'),
            ('nib', 'Nib International Bank', 'Nib'),
            ('cooperative bank of oromia', 'Cooperative Bank of Oromia', 'COOP'),
            ('coop', 'Cooperative Bank of Oromia', 'COOP'),
            ('lion', 'Lion International Bank', 'Lion'),
            ('anbessa', 'Lion International Bank', 'Lion'),
            ('siinqee', 'Siinqee Bank', 'Siinqee'),
            ('sinquee', 'Siinqee Bank', 'Siinqee'),
            ('zamzam', 'ZamZam Bank', 'ZamZam'),
            ('hijra', 'Hijra Bank', 'Hijra'),
            ('tsedey', 'Tsedey Bank', 'Tsedey'),
            ('telebirr', 'Ethio Telecom (telebirr)', 'telebirr'),
        ]

        CBE_BRANCHES = [
            'ashewa tera', 'signal', 'betel', 'addisu gebeya', 'balderas', 'ayat',
            'bole', 'megenagna', 'sarbet', 'gotera', 'mexico', 'piassa', 'kazanchis',
            'lebu', 'jemo', 'torhailoch', 'merkato', 'autobis tera', 'meskel flower',
            'cmc', 'gerji', 'kality', 'akaki', 'hayahulet', '22', '4 kilo', '6 kilo'
        ]

        raw_lower = raw_bank_name.lower()
        branch_lower = raw_branch.lower()

        bank_group_key = 'Commercial Bank of Ethiopia (CBE)'
        short_code = 'CBE'
        branch = raw_branch

        matched = False
        for pattern, full_name, short in KNOWN_ETHIOPIAN_BANKS:
            if pattern in raw_lower:
                bank_group_key = full_name
                short_code = short
                matched = True
                # Extract branch if raw_bank_name has hyphen or space (e.g. CBE-bole or Awash Bole)
                if '-' in raw_bank_name:
                    parts = raw_bank_name.split('-', 1)
                    branch = parts[1].strip().title()
                elif ' ' in raw_bank_name and pattern in ['cbe', 'awash', 'dashen', 'boa']:
                    parts = raw_bank_name.split(None, 1)
                    if len(parts) > 1 and parts[1].lower() not in ['bank', 'of', 'ethiopia']:
                        branch = parts[1].strip().title()
                break

        if not matched:
            # Check if raw_bank_name is itself a known CBE branch name (e.g. Ashewa Tera, Signal, Betel)
            is_cbe_branch = any(b in raw_lower for b in CBE_BRANCHES)
            if is_cbe_branch:
                bank_group_key = 'Commercial Bank of Ethiopia (CBE)'
                short_code = 'CBE'
                branch = raw_bank_name.title()
            elif raw_bank_name:
                bank_group_key = raw_bank_name.title()
                short_code = raw_bank_name[:6].upper()

        if not branch and raw_branch:
            branch = raw_branch.title()
        if not branch and branch_lower in CBE_BRANCHES:
            branch = raw_branch.title()

        # Format display string for table (e.g. 'CBE - Bole', 'CBE - Signal', 'Dashen - Ghion Branch')
        if branch:
            if branch.lower().endswith('branch'):
                display_branch = branch
            else:
                display_branch = branch
            display_bank_branch = f"{short_code} - {display_branch}"
        else:
            display_bank_branch = bank_group_key

        return {
            'bank_group_key': bank_group_key,
            'short_code': short_code,
            'bank_name': bank_group_key,
            'branch': branch or raw_branch or '-',
            'display_bank_branch': display_bank_branch,
            'bank_account_number': account_number or '-',
        }

    def _get_payslip_data(self):
        self.ensure_one()
        employee = self.employee_id
        bank_info = self._resolve_employee_bank_details(employee)

        net_salary = 0.0
        if hasattr(self, 'net_wage') and self.net_wage:
            net_salary = self.net_wage
        elif hasattr(self, 'net') and self.net:
            net_salary = self.net
        else:
            net_line = self.line_ids.filtered(lambda l: l.code == 'NET')
            if net_line:
                net_salary = net_line[0].total

        payslip_number = ''
        if hasattr(self, 'x_studio_slip_number') and self.x_studio_slip_number:
            payslip_number = self.x_studio_slip_number
        elif hasattr(self, 'number') and self.number:
            payslip_number = self.number
        else:
            payslip_number = self.name or ''

        emp_ref = getattr(employee, 'fms_employee_id', False) or getattr(employee, 'barcode', False) or getattr(employee, 'identification_id', False) or str(employee.id)
        emp_classification = getattr(employee, 'farm_employee_type', False) or getattr(self, 'farm_employee_type', False) or 'head_office'

        return {
            'payslip_id': self.id,
            'payslip_number': payslip_number,
            'employee_ref': emp_ref,
            'employee_name': employee.name or '',
            'classification': emp_classification,
            'bank_group_key': bank_info['bank_group_key'],
            'short_code': bank_info['short_code'],
            'bank_name': bank_info['bank_name'],
            'branch': bank_info['branch'],
            'bank_and_branch': bank_info['display_bank_branch'],
            'bank_account_number': bank_info['bank_account_number'],
            'net_salary': net_salary,
        }

    # -------------------------------------------------------------------------
    # Ethiopian Banking Amount to Words Converter
    # Formats as: "Three Million Five Hundred Forty Six Thousand Eight Hundred Seventy One & 55/100"
    # -------------------------------------------------------------------------

    @api.model
    def _amount_to_words(self, amount, currency='Birr'):
        if not amount:
            return 'Zero & 00/100'

        _ones = [
            '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
            'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen',
            'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen',
        ]
        _tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
                 'Sixty', 'Seventy', 'Eighty', 'Ninety']

        def _nn(n):
            if n < 20:
                return _ones[n]
            t = _tens[n // 10]
            u = _ones[n % 10]
            return (t + ' ' + u).strip()

        def _nnn(n):
            if n < 100:
                return _nn(n)
            h = _ones[n // 100] + ' Hundred'
            rem = n % 100
            if rem:
                return h + ' ' + _nn(rem)
            return h

        def _to_words(n):
            if n < 1000:
                return _nnn(n)
            if n < 1_000_000:
                rem = n % 1000
                thousands = _nnn(n // 1000) + ' Thousand'
                return (thousands + ' ' + _to_words(rem)).strip() if rem else thousands
            if n < 1_000_000_000:
                rem = n % 1_000_000
                millions = _to_words(n // 1_000_000) + ' Million'
                return (millions + ' ' + _to_words(rem)).strip() if rem else millions
            rem = n % 1_000_000_000
            billions = _to_words(n // 1_000_000_000) + ' Billion'
            return (billions + ' ' + _to_words(rem)).strip() if rem else billions

        amount_rounded = round(amount, 2)
        amount_int = int(amount_rounded)
        cents = round((amount_rounded - amount_int) * 100)

        words = _to_words(amount_int) if amount_int else 'Zero'
        return f"{words} & {cents:02d}/100"

    # -------------------------------------------------------------------------
    # Company Header & Footer info
    # -------------------------------------------------------------------------

    def _get_company_info(self):
        company = (self.company_id or self.env.company).sudo()
        partner = company.partner_id.sudo() if company and company.partner_id else False

        address = ''
        if partner and partner.contact_address:
            address = partner.contact_address.replace('\n', ', ').strip()

        if not address:
            street = company.street or (partner.street if partner else '')
            street2 = company.street2 or (partner.street2 if partner else '')
            city = company.city or (partner.city if partner else '')
            state_name = company.state_id.name if company.state_id else (partner.state_id.name if partner and partner.state_id else '')
            zip_code = company.zip or (partner.zip if partner else '')
            country_name = company.country_id.name if company.country_id else (partner.country_id.name if partner and partner.country_id else '')
            address_parts = list(filter(None, [street, street2, city, state_name, zip_code, country_name]))
            address = ', '.join(address_parts)

        phone = company.phone or (partner.phone if partner else '')
        email = company.email or (partner.email if partner else '')
        website = company.website or (partner.website if partner else '')
        vat = company.vat or (partner.vat if partner else '')

        logo_src = ''
        logo_field = company.logo_web or company.logo
        if logo_field:
            if isinstance(logo_field, bytes):
                logo_field = logo_field.decode('utf-8')
            try:
                decoded_once = base64.b64decode(logo_field)
                if decoded_once.startswith(b'PHN2Z') or decoded_once.startswith(b'<svg'):
                    actual_b64 = decoded_once.decode('utf-8') if decoded_once.startswith(b'PHN2Z') else base64.b64encode(decoded_once).decode('utf-8')
                else:
                    actual_b64 = logo_field
            except Exception:
                actual_b64 = logo_field

            try:
                img_bytes = base64.b64decode(actual_b64[:32])
            except Exception:
                img_bytes = b''

            if img_bytes[:4] == b'\x89PNG':
                mime = 'image/png'
            elif img_bytes[:3] == b'\xff\xd8\xff':
                mime = 'image/jpeg'
            elif img_bytes[:4] == b'GIF8':
                mime = 'image/gif'
            elif img_bytes[:5] == b'<?xml' or img_bytes[:4] == b'<svg':
                mime = 'image/svg+xml'
            else:
                mime = 'image/png'

            logo_src = f"data:{mime};base64,{actual_b64}"

        return {
            'company_name': company.name or '',
            'company_address': address or '',
            'company_phone': phone or '',
            'company_email': email or '',
            'company_website': website or '',
            'company_vat': vat or '',
            'logo_src': logo_src,
        }

    # -------------------------------------------------------------------------
    # Landscape Salary Rule Summary per group
    # -------------------------------------------------------------------------

    def _prepare_salary_rule_summary_for_slips(self, slips):
        if not slips:
            return {'rules': [], 'lines': [], 'totals': {}}

        rule_dict = {}
        employee_rule_data = {}
        for slip in slips:
            emp = slip.employee_id
            emp_key = slip.id
            emp_ref = getattr(emp, 'fms_employee_id', False) or getattr(emp, 'barcode', False) or getattr(emp, 'identification_id', False) or str(emp.id)

            if emp_key not in employee_rule_data:
                employee_rule_data[emp_key] = {
                    'employee_name': emp.name,
                    'employee_ref': emp_ref,
                    'payslip_number': slip.number or slip.name or '',
                    'rules': {},
                }

            for line in slip.line_ids:
                if not line.salary_rule_id:
                    continue
                code = line.code or line.salary_rule_id.code
                name = line.name or line.salary_rule_id.name
                seq = line.salary_rule_id.sequence or 100

                if code not in rule_dict:
                    rule_dict[code] = {
                        'code': code,
                        'name': name,
                        'sequence': seq,
                        'total': 0.0,
                    }
                rule_dict[code]['total'] += line.total
                employee_rule_data[emp_key]['rules'][code] = (
                    employee_rule_data[emp_key]['rules'].get(code, 0.0) + line.total
                )

        sorted_rules = sorted(
            rule_dict.values(),
            key=lambda r: (r['sequence'], r['code'])
        )

        matrix_lines = []
        for emp_key, emp_info in employee_rule_data.items():
            line_amounts = [
                emp_info['rules'].get(r['code'], 0.0) for r in sorted_rules
            ]
            matrix_lines.append({
                'employee_name': emp_info['employee_name'],
                'employee_ref': emp_info['employee_ref'],
                'payslip_number': emp_info['payslip_number'],
                'amounts': line_amounts,
            })

        rule_totals = [r['total'] for r in sorted_rules]
        return {
            'rules': sorted_rules,
            'lines': matrix_lines,
            'totals': rule_totals,
        }

    # -------------------------------------------------------------------------
    # Main Report Data Builder: Multi-Bank & Dynamic Letter Builder
    # -------------------------------------------------------------------------

    def _prepare_bank_advice_report_data(self, wizard_vals=None):
        if not self:
            return {'bank_groups': [], 'company': self.env['hr.payslip']._get_company_info()}

        wizard_vals = wizard_vals or self.env.context.get('bank_advice_wizard_vals', {})

        # Determine pay period string e.g. "Sep, 2026"
        pay_period = wizard_vals.get('pay_period')
        if not pay_period:
            if hasattr(self[0], 'payslip_run_id') and self[0].payslip_run_id and self[0].payslip_run_id.name:
                pay_period = self[0].payslip_run_id.name
            else:
                sample_date = self[0].date_to or self[0].date_from or fields.Date.today()
                pay_period = sample_date.strftime('%b, %Y')

        subject = wizard_vals.get('subject') or 'Transfer of Cash'
        report_date = fields.Date.today()
        report_date_str = report_date.strftime('%d/%m/%Y')

        company_info = self[0]._get_company_info()
        company = (self[0].company_id or self.env.company).sudo()

        # 1. Gather all payslips data (filtered by classification if requested)
        target_classification = wizard_vals.get('employee_classification')
        if target_classification and target_classification != 'all':
            slips_to_process = self.filtered(
                lambda s: (getattr(s.employee_id, 'farm_employee_type', False) or getattr(s, 'farm_employee_type', False)) == target_classification
            )
            if not slips_to_process:
                slips_to_process = self
        else:
            slips_to_process = self

        all_payslip_data = [slip._get_payslip_data() for slip in slips_to_process]

        print_branch = wizard_vals.get('print_branch', True)
        if 'print_branch' not in wizard_vals and 'print_branch' in self.env.context:
            print_branch = self.env.context['print_branch']

        # 2. Filter by single bank if requested
        bank_mode = wizard_vals.get('bank_mode', 'all')
        target_bank = wizard_vals.get('target_bank_name')
        if bank_mode == 'single' and target_bank:
            all_payslip_data = [
                p for p in all_payslip_data
                if target_bank.lower() in p['bank_group_key'].lower() or p['bank_group_key'].lower() in target_bank.lower()
            ]

        # 3. Group by bank_group_key
        groups_dict = {}
        for p in all_payslip_data:
            b_key = p['bank_group_key']
            if b_key not in groups_dict:
                groups_dict[b_key] = []
            groups_dict[b_key].append(p)

        bank_groups = []
        group_idx = 1
        for b_key, slips_data in groups_dict.items():
            group_slips_records = slips_to_process.filtered(lambda s: s.id in [p['payslip_id'] for p in slips_data])

            total_amount = sum(p['net_salary'] for p in slips_data)
            amount_in_words = self._amount_to_words(total_amount)

            # Determine company bank account & branch for this specific bank
            settings = self.env['payroll.bank.advice.settings'].get_bank_settings(b_key, company_id=company.id)

            branch = ''
            bank_city = 'Addis Ababa'
            company_account_number = ''
            ref_prefix = 'BA'

            if bank_mode == 'single' and wizard_vals.get('company_account_number'):
                company_account_number = wizard_vals.get('company_account_number')
                branch = wizard_vals.get('branch') or ''
                bank_city = wizard_vals.get('bank_city') or 'Addis Ababa'
            elif settings:
                branch = settings.branch or ''
                bank_city = settings.bank_city or 'Addis Ababa'
                company_account_number = settings.company_account_number or ''
                ref_prefix = settings.reference_prefix or 'BA'

            if not company_account_number:
                # Check company partner banks matching this bank
                b_norm = b_key.lower().replace(' ', '').replace('-', '')
                for acc in company.partner_id.bank_ids:
                    acc_bname = (acc.bank_id.name or acc.bank_name or '').lower().replace(' ', '').replace('-', '')
                    if acc_bname and (acc_bname in b_norm or b_norm in acc_bname):
                        company_account_number = acc.acc_number or ''
                        if not branch and acc.bank_id and acc.bank_id.street:
                            branch = acc.bank_id.street
                        break

            if not branch:
                # Use branch of first employee or 'Main Branch'
                first_branch = slips_data[0].get('branch') if slips_data else ''
                branch = (first_branch + ' Branch') if first_branch and not first_branch.lower().endswith('branch') else (first_branch or 'Main Branch')

            # Reference number
            custom_ref = wizard_vals.get('reference_number')
            if custom_ref:
                reference_number = custom_ref
            else:
                date_prefix = report_date.strftime('%Y/%m')
                prefix_clean = (slips_data[0].get('short_code') or ref_prefix).upper()
                reference_number = f"{prefix_clean}/BA/{date_prefix}/{group_idx:03d}"

            # Prepare sequential item numbers
            numbered_slips = []
            for i, p in enumerate(slips_data, start=1):
                p_copy = dict(p)
                p_copy['item_no'] = i
                numbered_slips.append(p_copy)

            # Salary breakdown for this bank group
            summary = self._prepare_salary_rule_summary_for_slips(group_slips_records)

            # Footer resolution based on employee classification
            first_slip_class = slips_data[0].get('classification') if slips_data else 'head_office'
            effective_class = target_classification if (target_classification and target_classification != 'all') else first_slip_class

            footer_config = self.env['payroll.bank.advice.footer.config'].get_footer_for_classification(
                effective_class, company_id=company.id
            )
            defaults = self.env['payroll.bank.advice.footer.config'].get_default_footer_vals(effective_class)

            prep_label = (footer_config.prepared_by_label if footer_config else False) or defaults['prepared_by_label']
            prep_title = (footer_config.prepared_by_title if footer_config else False) or defaults['prepared_by_title']
            appr_label = (footer_config.approved_by_label if footer_config else False) or defaults['approved_by_label']
            appr_title = (footer_config.approved_by_title if footer_config else False) or defaults['approved_by_title']
            footer_note = (footer_config.footer_note if footer_config else False) or ''
            show_seal = footer_config.show_seal if footer_config else True
            seal_text = (footer_config.seal_text if footer_config else False) or '[ Official Company Seal / Stamp ]'

            # Letterhead Page Footer Lines (as in user screenshot)
            footer_line_1 = (footer_config.footer_line_1 if footer_config else False) or defaults['footer_line_1']
            footer_line_2 = (footer_config.footer_line_2 if footer_config else False) or defaults['footer_line_2']
            footer_line_3 = (footer_config.footer_line_3 if footer_config else False) or defaults['footer_line_3']

            bank_groups.append({
                'bank_name': b_key,
                'short_code': slips_data[0].get('short_code', 'CBE'),
                'branch': branch,
                'bank_city': bank_city,
                'company_account_number': company_account_number or '___________________',
                'company_account_name': company.name or '',
                'reference_number': reference_number,
                'report_date': report_date_str,
                'pay_period': pay_period,
                'subject': subject,
                'total_amount': total_amount,
                'amount_in_words': amount_in_words,
                'total_employees': len(slips_data),
                'payslips': numbered_slips,
                'salary_rule_summary': summary,
                'print_branch': print_branch,
                'prepared_by_label': prep_label,
                'prepared_by_title': prep_title,
                'approved_by_label': appr_label,
                'approved_by_title': appr_title,
                'footer_note': footer_note or '',
                'show_seal': show_seal,
                'seal_text': seal_text,
                'classification': effective_class,
                'footer_line_1': footer_line_1,
                'footer_line_2': footer_line_2,
                'footer_line_3': footer_line_3,
            })
            group_idx += 1

        top_footer_config = self.env['payroll.bank.advice.footer.config'].get_footer_for_classification(
            target_classification, company_id=company.id
        )
        top_defaults = self.env['payroll.bank.advice.footer.config'].get_default_footer_vals(target_classification)
        footer_info = {
            'footer_line_1': (top_footer_config.footer_line_1 if top_footer_config else False) or top_defaults['footer_line_1'],
            'footer_line_2': (top_footer_config.footer_line_2 if top_footer_config else False) or top_defaults['footer_line_2'],
            'footer_line_3': (top_footer_config.footer_line_3 if top_footer_config else False) or top_defaults['footer_line_3'],
        }

        return {
            'company': company_info,
            'footer_info': footer_info,
            'pay_period': pay_period,
            'subject': subject,
            'report_date': report_date_str,
            'print_branch': print_branch,
            'bank_groups': bank_groups,
        }

    # -------------------------------------------------------------------------
    # Actions: Print Bank Advice via Wizard or direct PDF generation
    # -------------------------------------------------------------------------

    def action_print_bank_advice(self):
        """ Opens the interactive Print Bank Advice Wizard """
        has_access = (
            self.env.user.has_group('payroll_bank_advice.group_payroll_bank_advice_manager') or
            self.env.user.has_group('payroll_bank_advice.group_payroll_bank_advice_user') or
            self.env.user.has_group('hr_payroll.group_hr_payroll_manager') or
            self.env.user.has_group('hr_payroll.group_hr_payroll_user')
        )
        if not has_access:
            raise AccessError(_('You do not have access to print Bank Advice.'))

        valid_slips = self.filtered(lambda p: p.state != 'cancel')
        if not valid_slips:
            raise UserError(_('Please select at least one valid payslip to print Bank Advice.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Print Bank Advice'),
            'res_model': 'payroll.bank.advice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_payslip_ids': valid_slips.ids,
                'active_ids': valid_slips.ids,
                'active_model': 'hr.payslip',
            }
        }

    def action_open_payroll_report(self):
        """ Open Payroll Report Wizard for selected payslips """
        valid_slips = self.filtered(lambda p: p.state != 'cancel')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payroll Report'),
            'res_model': 'payroll.report.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_filter_by': 'date',
                'default_employee_ids': valid_slips.mapped('employee_id').ids,
                'default_date_from': min(valid_slips.mapped('date_from')) if valid_slips else fields.Date.today(),
                'default_date_to': max(valid_slips.mapped('date_to')) if valid_slips else fields.Date.today(),
            }
        }

    def _render_and_merge_bank_advice(self, wizard_vals=None):
        """
        Generates the Bank Advice Letter PDF, with separate Bank and Branch columns.
        If include_salary_summary is requested, appends the Landscape Salary Summary.
        """
        valid_slips = self.filtered(lambda p: p.state != 'cancel')
        if not valid_slips:
            raise UserError(_('Please select at least one valid payslip to print Bank Advice.'))

        context = dict(self.env.context, bank_advice_wizard_vals=wizard_vals or {})

        # Generate Portrait Bank Advice Letter PDF
        bank_advice_report = self.env.ref('payroll_bank_advice.action_report_payroll_bank_advice')
        bank_advice_pdf, _ = bank_advice_report.with_context(context).sudo()._render_qweb_pdf(
            bank_advice_report, valid_slips.ids
        )

        merged_pdf_bytes = bank_advice_pdf
        include_summary = (wizard_vals or {}).get('include_salary_summary', False)
        if include_summary:
            summary_report = self.env.ref('payroll_bank_advice.action_report_salary_rule_summary')
            summary_pdf, _ = summary_report.with_context(context).sudo()._render_qweb_pdf(
                summary_report, valid_slips.ids
            )
            try:
                from pypdf import PdfWriter, PdfReader
            except ImportError:
                try:
                    from PyPDF2 import PdfWriter, PdfReader
                except ImportError:
                    from PyPDF2 import PdfFileWriter as PdfWriter, PdfFileReader as PdfReader

            writer = PdfWriter()
            for pdf_bytes in [bank_advice_pdf, summary_pdf]:
                reader = PdfReader(io.BytesIO(pdf_bytes))
                pages = reader.pages if hasattr(reader, 'pages') else [reader.getPage(i) for i in range(reader.getNumPages())]
                for page in pages:
                    if hasattr(writer, 'add_page'):
                        writer.add_page(page)
                    else:
                        writer.addPage(page)

            out_stream = io.BytesIO()
            writer.write(out_stream)
            merged_pdf_bytes = out_stream.getvalue()

        today_str = fields.Date.today().strftime('%Y%m%d')
        filename = f"Bank_Advice_{today_str}.pdf"

        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(merged_pdf_bytes),
            'res_model': 'hr.payslip',
            'res_id': valid_slips.ids[0] if valid_slips else False,
            'mimetype': 'application/pdf',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }


class HrPayslipRun(models.Model):
    _inherit = 'hr.payslip.run'

    def action_print_bank_advice(self):
        """ Open Bank Advice Wizard for selected payslip batch(es) """
        valid_slips = self.mapped('slip_ids').filtered(lambda p: p.state != 'cancel')
        if not valid_slips:
            raise UserError(_("No valid payslips found in the selected batch(es) to generate Bank Advice."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Print Bank Advice'),
            'res_model': 'payroll.bank.advice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_payslip_ids': valid_slips.ids,
                'default_payslip_run_id': self.id if len(self) == 1 else False,
                'active_ids': valid_slips.ids,
                'active_model': 'hr.payslip',
            }
        }

    def action_open_payroll_report(self):
        """ Open Payroll Report Wizard for selected payslip batch """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payroll Report'),
            'res_model': 'payroll.report.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_filter_by': 'batch',
                'default_payslip_run_id': self.id,
                'default_company_id': self.company_id.id if hasattr(self, 'company_id') and self.company_id else self.env.company.id,
            }
        }
