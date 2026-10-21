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
        for table in ('payslips', 'bookings', 'employees'):
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


if __name__ == '__main__':
    unittest.main()
