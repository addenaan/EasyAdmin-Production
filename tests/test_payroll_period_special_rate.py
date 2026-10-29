import os
import shutil
import tempfile
import unittest


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-payroll-period-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-payroll-period-secret-key'
os.environ['EASYADMIN_CSRF_ENABLED'] = 'false'
os.environ['EASYADMIN_RATE_LIMIT_ENABLED'] = 'false'
for database_url_name in ('DATABASE_URL', 'SUPABASE_DATABASE_URL', 'POSTGRES_URL'):
    os.environ.pop(database_url_name, None)

import app as easyadmin


class PayrollPeriodAndSpecialRateTests(unittest.TestCase):
    company_id = 971

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.conn = easyadmin.get_db_connection()
        for table in ('payslips', 'leave_records', 'bookings', 'employees'):
            self.conn.execute(f'DELETE FROM {table} WHERE company_id=?', (self.company_id,))
        self.conn.execute('DELETE FROM companies WHERE id=?', (self.company_id,))
        self.conn.execute(
            'INSERT INTO companies (id, name, can_payroll, sdl_applicable, transport_policy) VALUES (?, ?, 1, 0, ?)',
            (self.company_id, 'Payroll Period Test Company', 'no'),
        )
        tax_year = easyadmin.get_sars_tax_year('2026-10-31')
        if not self.conn.execute('SELECT 1 FROM tax_brackets WHERE tax_year=? LIMIT 1', (tax_year,)).fetchone():
            self.conn.execute(
                'INSERT INTO tax_brackets (tax_year, min_income, max_income, base_tax, rate) VALUES (?, 0, 999999999, 0, 0)',
                (tax_year,),
            )
        if not self.conn.execute('SELECT 1 FROM tax_rebates WHERE tax_year=? LIMIT 1', (tax_year,)).fetchone():
            self.conn.execute(
                '''INSERT INTO tax_rebates
                   (tax_year, primary_rebate, secondary_rebate, tertiary_rebate,
                    threshold_under_65, threshold_65_to_74, threshold_75_plus)
                   VALUES (?, 0, 0, 0, 0, 0, 0)''',
                (tax_year,),
            )
        self.conn.execute(
            '''INSERT INTO employees
               (id, company_id, name, emp_type, status, start_date, date_of_birth,
                gross_salary, half_day_rate, special_daily_rate, workday_hours, uif_contributor)
               VALUES (9711, ?, 'Contract Employee', 'Contract >25 Hrs', 'Active',
                       '2026-01-01', '1990-01-01', 500, 300, 750, 8, 'Yes')''',
            (self.company_id,),
        )
        self.conn.execute(
            '''INSERT INTO employees
               (id, company_id, name, emp_type, status, start_date, date_of_birth,
                gross_salary, workday_hours, uif_contributor)
               VALUES (9712, ?, 'Shift Employee', 'Shift Worker', 'Active',
                       '2026-01-01', '1990-01-01', 10000, 8, 'Yes')''',
            (self.company_id,),
        )
        self.conn.execute(
            '''INSERT INTO employees
               (id, company_id, name, emp_type, status, start_date, date_of_birth,
                gross_salary, workday_hours, uif_contributor)
               VALUES (9713, ?, 'Monthly Employee', 'Full-time (5 Days)', 'Active',
                       '2025-01-01', '1990-01-01', 10000, 8, 'Yes')''',
            (self.company_id,),
        )
        self.conn.execute(
            '''INSERT INTO employees
               (id, company_id, name, emp_type, status, start_date, inactive_date,
                date_of_birth, gross_salary, workday_hours, uif_contributor)
               VALUES (9714, ?, 'Departing Employee', 'Full-time (5 Days)', 'Resigned',
                       '2025-01-01', '2026-10-15', '1990-01-01', 10000, 8, 'Yes')''',
            (self.company_id,),
        )
        booking_rows = [
            (97101, '2026-09-26T08:00', 'Contract Employee', 0, 0),
            (97102, '2026-09-27T08:00', 'Contract Employee', 0, 0),
            (97103, '2026-10-10T08:00', 'Contract Employee', 1, 0),
            (97104, '2026-10-20T08:00', 'Contract Employee', 0, 1),
            (97105, '2026-10-27T08:00', 'Contract Employee', 0, 0),
            (97106, '2026-09-27T08:00', 'Shift Employee', 0, 1),
        ]
        self.conn.executemany(
            '''INSERT INTO bookings
               (id, company_id, title, start, employee, booking_type, transport,
                overtime_hours, is_half_day, is_special_rate)
               VALUES (?, ?, 'Test Client', ?, ?, 'Cleaning', '', 0, ?, ?)''',
            [(row[0], self.company_id, row[1], row[2], row[3], row[4]) for row in booking_rows],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _draft(self, employee_id, **extra):
        employee = self.conn.execute(
            'SELECT * FROM employees WHERE id=? AND company_id=?',
            (employee_id, self.company_id),
        ).fetchone()
        company = self.conn.execute('SELECT * FROM companies WHERE id=?', (self.company_id,)).fetchone()
        payload = {
            'date': '2026-10-31',
            'payroll_start_date': '2026-09-27',
            'payroll_end_date': '2026-10-26',
            'bonus': 0,
            'reimbursable_expenses': 0,
            'loan_repayment': 0,
        }
        payload.update(extra)
        with easyadmin.app.test_request_context('/'):
            easyadmin.session['company_id'] = self.company_id
            return easyadmin._calculate_draft_payslip(self.conn, employee, company, payload)

    def test_contract_period_includes_only_selected_booking_dates_and_rates(self):
        result = self._draft(9711)
        self.assertEqual(result['raw']['gross'], 1550.0)
        self.assertEqual(result['raw']['booking_period_start'], '2026-09-27')
        self.assertEqual(result['raw']['booking_period_end'], '2026-10-26')
        self.assertIn('1 full day @ R500.00', result['payslip']['days_worked_display'])
        self.assertIn('1 half day @ R300.00', result['payslip']['days_worked_display'])
        self.assertIn('1 special day @ R750.00', result['payslip']['days_worked_display'])

    def test_shift_worker_keeps_existing_calendar_month_salary_logic(self):
        result = self._draft(9712)
        self.assertEqual(result['raw']['gross'], 10000.0)
        self.assertEqual(result['raw']['booking_period_start'], '2026-10-01')
        self.assertEqual(result['raw']['booking_period_end'], '2026-10-31')

    def test_half_day_and_special_rate_are_mutually_exclusive(self):
        valid, message = easyadmin.validate_booking_rate_options(
            self.conn, self.company_id, [{'employee': 'Contract Employee'}], 1, 1
        )
        self.assertFalse(valid)
        self.assertIn('cannot be both', message)

    def test_special_rate_booking_requires_configured_contract_rate(self):
        self.conn.execute('UPDATE employees SET special_daily_rate=NULL WHERE id=9711')
        valid, message = easyadmin.validate_booking_rate_options(
            self.conn, self.company_id, [{'employee': 'Contract Employee'}], 0, 1
        )
        self.assertFalse(valid)
        self.assertIn('Special Daily Rate', message)

    def test_contract_period_end_cannot_be_after_target_payroll_date(self):
        employee = self.conn.execute('SELECT * FROM employees WHERE id=9711').fetchone()
        with self.assertRaisesRegex(ValueError, 'cannot be after the Target Payroll Date'):
            easyadmin.resolve_contract_booking_period(employee, '2026-10-25', {
                'payroll_start_date': '2026-09-27',
                'payroll_end_date': '2026-10-26',
            })

    def test_contract_paid_annual_and_sick_leave_are_added_to_gross(self):
        self.conn.executemany(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9711, ?, ?, ?)''',
            [
                (self.company_id, '2026-10-15', 1, 'Annual Leave'),
                (self.company_id, '2026-10-16', 0.5, 'Sick Leave'),
            ],
        )
        self.conn.commit()

        result = self._draft(9711)

        self.assertEqual(result['raw']['gross'], 2300.0)
        self.assertEqual(result['raw']['paid_annual_leave_days'], 1.0)
        self.assertEqual(result['raw']['paid_sick_leave_days'], 0.5)
        self.assertEqual(result['raw']['paid_leave_amount'], 750.0)
        self.assertEqual(result['raw']['worked_gross_amount'], 1550.0)
        self.assertEqual(result['payslip']['gross'], 'R 1550.00')
        self.assertNotIn('paid annual', result['payslip']['days_worked_display'])
        self.assertIn('paid leave day(s)', result['payslip']['paid_leave_detail'])

    def test_monthly_salary_includes_paid_leave_without_extra_earnings(self):
        self.conn.executemany(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9713, ?, ?, ?)''',
            [
                (self.company_id, '2026-10-05', 1, 'Annual Leave'),
                (self.company_id, '2026-10-06', 1, 'Sick Leave'),
            ],
        )
        self.conn.commit()

        result = self._draft(9713)

        self.assertEqual(result['raw']['gross'], 10000.0)
        self.assertEqual(result['raw']['paid_leave_days'], 2.0)
        self.assertEqual(result['raw']['paid_leave_amount'], 0.0)
        self.assertEqual(result['raw']['paid_leave_included_in_monthly_salary'], 1)
        self.assertEqual(result['raw']['worked_gross_amount'], 10000.0)
        self.assertNotIn('paid leave', result['payslip']['days_worked_display'].lower())
        self.assertIn('included in monthly salary', result['payslip']['paid_leave_detail'])

    def test_contract_leave_is_not_paid_twice_when_booking_exists_same_day(self):
        self.conn.execute(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9711, '2026-10-10', 1, 'Annual Leave')''',
            (self.company_id,),
        )
        self.conn.commit()

        result = self._draft(9711)

        self.assertEqual(result['raw']['paid_annual_leave_days'], 0.5)
        self.assertEqual(result['raw']['paid_leave_overlap_days'], 0.5)
        self.assertEqual(result['raw']['paid_leave_amount'], 250.0)
        self.assertEqual(result['raw']['gross'], 1800.0)

    def test_multi_day_leave_is_limited_to_selected_payroll_period(self):
        self.conn.execute(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9711, '2026-10-26', 2, 'Annual Leave')''',
            (self.company_id,),
        )
        self.conn.commit()

        result = self._draft(9711)

        self.assertEqual(result['raw']['paid_annual_leave_days'], 1.0)
        self.assertEqual(result['raw']['paid_leave_amount'], 500.0)
        self.assertEqual(result['raw']['gross'], 2050.0)

    def test_inactive_date_exposes_automatic_termination_settlement(self):
        result = self._draft(9714)

        self.assertEqual(result['raw']['termination_settlement_detected'], 1)
        self.assertEqual(result['raw']['leave_payout_date'], '2026-10-15')
        self.assertGreater(result['raw']['leave_payout_days'], 0)
        self.assertIn('Last working day 2026-10-15', result['raw']['termination_settlement_status'])

    def test_finalized_payslip_preserves_paid_leave_snapshot(self):
        self.conn.execute(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9711, '2026-10-15', 1, 'Annual Leave')''',
            (self.company_id,),
        )
        self.conn.commit()
        payload = {
            'employee_id': 9711,
            'date': '2026-10-31',
            'payroll_start_date': '2026-09-27',
            'payroll_end_date': '2026-10-26',
            'bonus': 0,
            'reimbursable_expenses': 0,
            'loan_repayment': 0,
        }

        with easyadmin.app.test_request_context('/api/save_payslip', method='POST', json=payload):
            easyadmin.session['company_id'] = self.company_id
            easyadmin.session['can_payroll'] = True
            response = easyadmin.save_payslip()

        self.assertEqual(response.status_code, 200)
        saved = self.conn.execute(
            '''SELECT gross_salary, worked_gross_amount, paid_annual_leave_days,
                      paid_sick_leave_days, paid_leave_amount, paid_leave_detail
               FROM payslips WHERE company_id=? AND employee_id=9711''',
            (self.company_id,),
        ).fetchone()
        self.assertIsNotNone(saved)
        self.assertEqual(float(saved['paid_annual_leave_days']), 1.0)
        self.assertEqual(float(saved['paid_sick_leave_days']), 0.0)
        self.assertEqual(float(saved['paid_leave_amount']), 500.0)
        self.assertEqual(float(saved['worked_gross_amount']), 1550.0)
        self.assertEqual(float(saved['gross_salary']), 2050.0)
        self.assertIn('paid leave', saved['paid_leave_detail'])

        summary = easyadmin._payroll_tax_certificate_ledger_summary(
            self.conn, self.company_id, 9711, '2026-03-01', '2027-02-28'
        )
        self.assertEqual(summary['code_3601'], 2050.0)

    def test_termination_leave_payout_remains_code_3605_and_in_net_pay(self):
        payload = {
            'employee_id': 9714,
            'date': '2026-10-31',
            'bonus': 0,
            'reimbursable_expenses': 0,
            'loan_repayment': 0,
        }
        with easyadmin.app.test_request_context('/api/save_payslip', method='POST', json=payload):
            easyadmin.session['company_id'] = self.company_id
            easyadmin.session['can_payroll'] = True
            response = easyadmin.save_payslip()

        self.assertEqual(response.status_code, 200)
        saved_values = response.get_json()
        self.assertGreater(saved_values['leave_payout_amount'], 0)
        expected_net = round(
            saved_values['gross'] + saved_values['overtime'] + saved_values['bonus']
            + saved_values['leave_payout_amount'] + saved_values['transport']
            + saved_values['reimbursable_expenses'] - saved_values['uif']
            - saved_values['paye'] - saved_values['loan_repayment'],
            2,
        )
        self.assertEqual(saved_values['net'], expected_net)
        summary = easyadmin._payroll_tax_certificate_ledger_summary(
            self.conn, self.company_id, 9714, '2026-03-01', '2027-02-28'
        )
        self.assertEqual(summary['code_3601'], saved_values['gross'])
        self.assertEqual(summary['code_3605'], saved_values['leave_payout_amount'])

    def test_gross_adjustment_updates_worked_gross_without_absorbing_paid_leave_line(self):
        self.conn.execute(
            '''INSERT INTO leave_records
               (company_id, employee_id, date_taken, days, leave_type)
               VALUES (?, 9711, '2026-10-15', 1, 'Sick Leave')''',
            (self.company_id,),
        )
        self.conn.commit()
        payroll_payload = {
            'employee_id': 9711,
            'date': '2026-10-31',
            'payroll_start_date': '2026-09-27',
            'payroll_end_date': '2026-10-26',
            'bonus': 0,
            'reimbursable_expenses': 0,
            'loan_repayment': 0,
        }
        with easyadmin.app.test_request_context('/api/save_payslip', method='POST', json=payroll_payload):
            easyadmin.session['company_id'] = self.company_id
            easyadmin.session['can_payroll'] = True
            response = easyadmin.save_payslip()
        self.assertEqual(response.status_code, 200)

        adjustment_payload = {
            'employee_id': 9711,
            'date': '2026-10-31',
            'adjustment_reason': 'Worked gross correction',
            'gross': 100,
        }
        with easyadmin.app.test_request_context('/api/save_adjustment_payslip', method='POST', json=adjustment_payload):
            easyadmin.session['company_id'] = self.company_id
            easyadmin.session['can_payroll'] = True
            response = easyadmin.save_adjustment_payslip()
        self.assertEqual(response.status_code, 200)

        saved = self.conn.execute(
            '''SELECT gross_salary, worked_gross_amount, paid_leave_amount
               FROM payslips WHERE company_id=? AND employee_id=9711
                 AND COALESCE(payslip_type, 'regular')='regular' ''',
            (self.company_id,),
        ).fetchone()
        self.assertEqual(float(saved['worked_gross_amount']), 1650.0)
        self.assertEqual(float(saved['paid_leave_amount']), 500.0)
        self.assertEqual(float(saved['gross_salary']), 2150.0)


if __name__ == '__main__':
    unittest.main()
