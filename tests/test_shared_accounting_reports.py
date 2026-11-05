import os
import shutil
import sys
import tempfile
import time
import unittest


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-shared-accounting-reports-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-shared-accounting-reports-secret-key'
os.environ['EASYADMIN_CSRF_ENABLED'] = 'false'
os.environ['EASYADMIN_RATE_LIMIT_ENABLED'] = 'false'
for database_url_name in ('DATABASE_URL', 'SUPABASE_DATABASE_URL', 'POSTGRES_URL'):
    os.environ.pop(database_url_name, None)

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TEST_DEPS = os.path.join(PROJECT_ROOT, '.testdeps_client_address')
if os.path.isdir(TEST_DEPS):
    sys.path.insert(0, TEST_DEPS)
sys.path.insert(0, PROJECT_ROOT)
import app as easyadmin


class SharedAccountingReportsTests(unittest.TestCase):
    company_a = 9961
    company_b = 9962
    outside_company = 9963
    franchise_group_id = 9961

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.conn = easyadmin.get_db_connection()
        company_ids = (self.company_a, self.company_b, self.outside_company)
        placeholders = ','.join('?' for _ in company_ids)
        for table in (
            'accounting_bank_reconciliation_lines', 'accounting_bank_reconciliations',
            'accounting_journal_lines', 'accounting_journals', 'accounting_settings',
            'accounting_accounts', 'invoice_credit_notes', 'invoices',
        ):
            self.conn.execute(f'DELETE FROM {table} WHERE company_id IN ({placeholders})', company_ids)
        self.conn.execute('DELETE FROM franchise_group_companies WHERE franchise_group_id=?', (self.franchise_group_id,))
        self.conn.execute('DELETE FROM franchise_groups WHERE id=?', (self.franchise_group_id,))
        self.conn.execute(f'DELETE FROM companies WHERE id IN ({placeholders})', company_ids)
        self.conn.executemany(
            'INSERT INTO companies (id, name, can_accounting, can_franchise_reports) VALUES (?, ?, ?, 1)',
            [
                (self.company_a, 'Franchise Alpha', 1),
                (self.company_b, 'Franchise Beta', 1),
                (self.outside_company, 'Outside Tenant', 1),
            ],
        )
        self.conn.execute(
            'INSERT INTO franchise_groups (id, name, active) VALUES (?, ?, 1)',
            (self.franchise_group_id, 'Accounting Test Franchise'),
        )
        self.conn.executemany(
            'INSERT INTO franchise_group_companies (franchise_group_id, company_id) VALUES (?, ?)',
            [
                (self.franchise_group_id, self.company_a),
                (self.franchise_group_id, self.company_b),
            ],
        )
        self._seed_company(self.company_a, revenue=1000, expense=200, invoice_total=115, invoice_vat=15)
        self._seed_company(self.company_b, revenue=500, expense=100, invoice_total=230, invoice_vat=30)
        self._seed_company(self.outside_company, revenue=9999, expense=0, invoice_total=0, invoice_vat=0)
        self.conn.commit()

        self.client = easyadmin.app.test_client()
        self._login_accounting(self.company_a)

    def tearDown(self):
        self.conn.close()

    def _seed_company(self, company_id, revenue, expense, invoice_total, invoice_vat):
        easyadmin.ensure_accounting_posting_defaults(self.conn, company_id)
        account_rows = self.conn.execute(
            "SELECT id, account_code FROM accounting_accounts WHERE company_id=? AND account_code IN ('1000', '4000', '6000')",
            (company_id,),
        ).fetchall()
        account_ids = {row['account_code']: int(row['id']) for row in account_rows}

        first_journal_id = int(self.conn.execute(
            """INSERT INTO accounting_journals
               (company_id, journal_date, reference, description, source_module, status)
               VALUES (?, '2026-10-05', ?, 'Revenue received', 'cash_book', 'posted')""",
            (company_id, f'REV-{company_id}'),
        ).lastrowid)
        self.conn.executemany(
            """INSERT INTO accounting_journal_lines
               (company_id, journal_id, line_no, account_id, description, debit, credit, cash_flow_section)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'operating')""",
            [
                (company_id, first_journal_id, 1, account_ids['1000'], 'Customer receipt', revenue, 0),
                (company_id, first_journal_id, 2, account_ids['4000'], 'Sales revenue', 0, revenue),
            ],
        )

        if expense:
            second_journal_id = int(self.conn.execute(
                """INSERT INTO accounting_journals
                   (company_id, journal_date, reference, description, source_module, status)
                   VALUES (?, '2026-10-10', ?, 'Payroll expense paid', 'cash_book', 'posted')""",
                (company_id, f'EXP-{company_id}'),
            ).lastrowid)
            self.conn.executemany(
                """INSERT INTO accounting_journal_lines
                   (company_id, journal_id, line_no, account_id, description, debit, credit, cash_flow_section)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'operating')""",
                [
                    (company_id, second_journal_id, 1, account_ids['6000'], 'Payroll expense', expense, 0),
                    (company_id, second_journal_id, 2, account_ids['1000'], 'Bank payment', 0, expense),
                ],
            )

        if invoice_total:
            self.conn.execute(
                """INSERT INTO invoices
                   (company_id, client_name, date, due_date, subtotal, vat_amount, total, status,
                    accounting_status, accounting_journal_id)
                   VALUES (?, 'Accounting Report Client', '2026-10-15', '2026-10-31', ?, ?, ?, 'Unpaid', 'posted', ?)""",
                (company_id, invoice_total - invoice_vat, invoice_vat, invoice_total, first_journal_id),
            )

    def _login_accounting(self, company_id):
        with self.client.session_transaction() as flask_session:
            flask_session.clear()
            flask_session['logged_in'] = True
            flask_session['company_id'] = company_id
            flask_session['company_name'] = 'Franchise Alpha'
            flask_session['username'] = 'accounting.test'
            flask_session['can_accounting'] = 1
            flask_session['last_activity_at'] = time.time()

    def _login_franchise(self):
        with self.client.session_transaction() as flask_session:
            flask_session.clear()
            flask_session['logged_in'] = True
            flask_session['company_id'] = self.company_a
            flask_session['company_name'] = 'Franchise Alpha'
            flask_session['username'] = 'franchise.reporting.test'
            flask_session['can_franchise_reports'] = 1
            flask_session['comp_can_franchise_reports'] = 1
            flask_session['is_franchise_reporting'] = 1
            flask_session['franchise_group_id'] = self.franchise_group_id
            flask_session['last_activity_at'] = time.time()

    @staticmethod
    def _period_payload(report_type, **extra):
        payload = {
            'report_type': report_type,
            'start_date': '2026-10-01',
            'end_date': '2026-10-31',
        }
        payload.update(extra)
        return payload

    def test_reports_app_exposes_all_accounting_reports_and_reuses_ledger_values(self):
        page_response = self.client.get('/reports')
        self.assertEqual(page_response.status_code, 200, page_response.get_data(as_text=True))
        page = page_response.get_data(as_text=True)
        for title in (
            'Trial Balance', 'Income Statement', 'Balance Sheet', 'Cash Flow Statement',
            'General Ledger', 'Sales Report', 'Chart of Accounts Report', 'VAT Report',
            'Bank Reconciliation',
        ):
            self.assertIn(title, page)
        self.assertIn('Statement Closing Balance', page)

        expected_statuses = {}
        for report_type in easyadmin.ACCOUNTING_SHARED_REPORT_TYPES:
            extra = {'statement_balance': '800'} if report_type == 'accounting_bank_reconciliation' else {}
            response = self.client.post('/api/reports/run', json=self._period_payload(report_type, **extra))
            expected_statuses[report_type] = response.status_code
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            body = response.get_json()
            self.assertEqual(body['status'], 'success')
            self.assertGreater(len(body['columns']), 0)

        trial_balance = self.client.post(
            '/api/reports/run', json=self._period_payload('accounting_trial_balance')
        ).get_json()
        self.assertEqual(trial_balance['summary']['debit_total'], 1000.0)
        self.assertEqual(trial_balance['summary']['credit_total'], 1000.0)
        self.assertEqual(trial_balance['summary']['balance_difference'], 0.0)

        income_statement = self.client.post(
            '/api/reports/run', json=self._period_payload('accounting_income_statement')
        ).get_json()
        self.assertEqual(income_statement['summary']['profit_loss'], 800.0)

        bank_reconciliation = self.client.post(
            '/api/reports/run',
            json=self._period_payload('accounting_bank_reconciliation', statement_balance='800'),
        ).get_json()
        self.assertEqual(bank_reconciliation['summary']['gl_balance'], 800.0)
        self.assertEqual(bank_reconciliation['summary']['difference_amount'], 0.0)

    def test_accounting_report_access_requires_accounting_permission(self):
        with self.client.session_transaction() as flask_session:
            flask_session['can_accounting'] = 0
            flask_session['can_booking'] = 1
        response = self.client.post(
            '/api/reports/run', json=self._period_payload('accounting_trial_balance')
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('permission', response.get_json()['message'].lower())

    def test_franchise_reports_consolidate_only_linked_accounting_tenants(self):
        self._login_franchise()
        page_response = self.client.get('/franchise_reports')
        self.assertEqual(page_response.status_code, 200, page_response.get_data(as_text=True))
        page = page_response.get_data(as_text=True)
        self.assertIn('Trial Balance', page)
        self.assertIn('Statement Closing Balance', page)

        response = self.client.post(
            '/api/franchise_reports/run',
            json=self._period_payload('accounting_income_statement'),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['columns'][0], 'Franchisee')
        self.assertEqual(body['summary']['accounting_company_count'], 2)
        company_names = {row[0] for row in body['rows']}
        self.assertEqual(company_names, {'Franchise Alpha', 'Franchise Beta'})
        self.assertNotIn('Outside Tenant', company_names)
        company_summaries = {
            item['company_name']: item['profit_loss']
            for item in body['summary']['accounting_companies']
        }
        self.assertEqual(company_summaries, {'Franchise Alpha': 800.0, 'Franchise Beta': 400.0})

    def test_franchise_bank_reconciliation_requires_one_linked_company(self):
        self._login_franchise()
        response = self.client.post(
            '/api/franchise_reports/run',
            json=self._period_payload('accounting_bank_reconciliation', statement_balance='800'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Select one franchisee', response.get_json()['message'])

        response = self.client.post(
            '/api/franchise_reports/run',
            json=self._period_payload(
                'accounting_bank_reconciliation',
                company_id=self.company_a,
                statement_balance='800',
            ),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['summary']['accounting_company_count'], 1)
        self.assertEqual(body['summary']['accounting_companies'][0]['difference_amount'], 0.0)
        self.assertTrue(all(row[0] == 'Franchise Alpha' for row in body['rows']))

    def test_csv_exports_use_the_shared_report_payload(self):
        response = self.client.get(
            '/export/reports/accounting_trial_balance.csv?start_date=2026-10-01&end_date=2026-10-31'
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        csv_text = response.get_data(as_text=True)
        self.assertIn('Trial Balance', csv_text)
        self.assertIn('1000,Bank,800.0,0.0', csv_text)
        self.assertIn('Totals,1000.0,1000.0', csv_text)


if __name__ == '__main__':
    unittest.main()
