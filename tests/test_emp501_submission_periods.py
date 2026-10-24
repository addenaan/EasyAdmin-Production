import os
import shutil
import tempfile
import unittest
from unittest.mock import patch


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-emp501-period-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-emp501-period-secret-key'
os.environ['EASYADMIN_CSRF_ENABLED'] = 'false'
os.environ['EASYADMIN_RATE_LIMIT_ENABLED'] = 'false'
for database_url_name in ('DATABASE_URL', 'SUPABASE_DATABASE_URL', 'POSTGRES_URL'):
    os.environ.pop(database_url_name, None)

import app as easyadmin


class _EmployeeConnection:
    def __init__(self):
        self.closed = False

    def execute(self, sql, params=()):
        if 'FROM employees' not in sql:
            raise AssertionError(f'Unexpected SQL: {sql}')
        return self

    def fetchall(self):
        return [{
            'id': 5011,
            'emp_number': 'EMP501-1',
            'name': 'Period Test Employee',
            'id_passport': '9001010000000',
            'tax_number': '1234567890',
            'paye_ref': '7123456789',
        }]

    def close(self):
        self.closed = True


class Emp501SubmissionPeriodTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def test_provisional_period_runs_from_march_to_august(self):
        period = easyadmin._emp501_reporting_period(2027, 'provisional')
        self.assertEqual(period, (2027, 'interim', '2026-03-01', '2026-08-31', 'interim reconciliation'))

    def test_annual_period_runs_through_tax_year_end(self):
        period = easyadmin._emp501_reporting_period(2027, 'annual')
        self.assertEqual(period, (2027, 'annual', '2026-03-01', '2027-02-28', 'annual reconciliation'))

    def test_annual_period_includes_leap_day(self):
        period = easyadmin._emp501_reporting_period(2028, 'annual')
        self.assertEqual(period[2:4], ('2027-03-01', '2028-02-29'))

    def test_sars_compliance_uses_the_shared_period_logic(self):
        self.assertEqual(
            easyadmin._sars_parse_period('EMP501', {
                'tax_year': 2027,
                'reconciliation_period': 'interim',
            })[:2],
            ('2026-03-01', '2026-08-31'),
        )

    def test_export_uses_selected_period_and_descriptive_filename(self):
        connection = _EmployeeConnection()
        summary = {
            'code_3601': 1000.0,
            'code_3605': 0.0,
            'code_3607': 0.0,
            'transport_unclassified': 0.0,
            'code_3699': 1000.0,
            'code_4102': 100.0,
            'code_4141': 20.0,
            'code_4142': 0.0,
            'code_4149': 120.0,
            'payslip_count': 1,
            'warning': '',
        }
        with easyadmin.app.test_request_context('/export_emp501/2027?submission_type=provisional'):
            easyadmin.session['company_id'] = 501
            with patch.object(easyadmin, '_session_has_payroll_read_access', return_value=True), \
                    patch.object(easyadmin, 'get_db_connection', return_value=connection), \
                    patch.object(easyadmin, '_payroll_tax_certificate_ledger_summary', return_value=summary) as ledger_summary, \
                    patch.object(easyadmin, 'log_action'):
                response = easyadmin.export_emp501('2027')

        self.assertEqual(response.status_code, 200)
        self.assertIn('EMP501_Provisional_2027.csv', response.headers['Content-Disposition'])
        self.assertEqual(ledger_summary.call_args.args[-2:], ('2026-03-01', '2026-08-31'))
        self.assertTrue(connection.closed)

    def test_invalid_submission_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'provisional or annual'):
            easyadmin._emp501_reporting_period(2027, 'quarterly')


if __name__ == '__main__':
    unittest.main()
