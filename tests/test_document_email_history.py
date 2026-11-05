import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-document-email-history-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-document-email-history-secret-key'
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


class DocumentEmailHistoryTests(unittest.TestCase):
    company_id = 988
    other_company_id = 989

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.conn = easyadmin.get_db_connection()
        self.conn.execute(
            'DELETE FROM document_email_history WHERE company_id IN (?, ?)',
            (self.company_id, self.other_company_id),
        )
        self.conn.execute('DELETE FROM invoices WHERE company_id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('DELETE FROM quotes WHERE company_id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('DELETE FROM employees WHERE company_id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('DELETE FROM settings WHERE company_id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('DELETE FROM companies WHERE id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute(
            'INSERT INTO companies (id, name, can_invoicing, can_payroll) VALUES (?, ?, 1, 1)',
            (self.company_id, 'Email History Test Company'),
        )
        self.conn.execute(
            'INSERT INTO companies (id, name, can_invoicing, can_payroll) VALUES (?, ?, 1, 1)',
            (self.other_company_id, 'Other Email History Company'),
        )
        self.conn.execute(
            "INSERT INTO invoices (id, company_id, invoice_number, client_name, date, total) VALUES (9881, ?, 'INV-9881', 'Test Client', '2026-10-30', 100)",
            (self.company_id,),
        )
        self.conn.execute(
            "INSERT INTO quotes (id, company_id, quote_number, client_name, date, total) VALUES (9882, ?, 'QT-9882', 'Test Client', '2026-10-30', 100)",
            (self.company_id,),
        )
        self.conn.execute(
            "INSERT INTO employees (id, company_id, name, email, status) VALUES (9883, ?, 'Test Employee', 'employee@example.com', 'Active')",
            (self.company_id,),
        )
        self.conn.executemany(
            'INSERT INTO settings (company_id, key, value) VALUES (?, ?, ?)',
            [
                (self.company_id, 'smtp_server', 'smtp.example.com'),
                (self.company_id, 'smtp_port', '465'),
                (self.company_id, 'smtp_user', 'sender@example.com'),
                (self.company_id, 'smtp_pass', 'test-password'),
            ],
        )
        self.conn.commit()

        self.client = easyadmin.app.test_client()
        with self.client.session_transaction() as flask_session:
            flask_session['logged_in'] = True
            flask_session['company_id'] = self.company_id
            flask_session['company_name'] = 'Email History Test Company'
            flask_session['username'] = 'email.history.test'
            flask_session['can_invoicing'] = 1
            flask_session['can_payroll'] = 1
            flask_session['last_activity_at'] = time.time()

    def tearDown(self):
        self.conn.close()

    def _record_attempt(self, document_type, document_key, status, **kwargs):
        with easyadmin.app.test_request_context('/'):
            easyadmin.session['company_id'] = kwargs.pop('company_id', self.company_id)
            easyadmin.session['username'] = kwargs.pop('username', 'email.history.test')
            attempt_id = easyadmin._start_document_email_attempt(
                document_type,
                document_key,
                document_id=kwargs.pop('document_id', document_key),
                document_number=kwargs.pop('document_number', 'TEST-1'),
                recipients=kwargs.pop('recipients', 'recipient@example.com'),
                subject=kwargs.pop('subject', 'Test document'),
                filename=kwargs.pop('filename', 'test.pdf'),
                content_sha256='a' * 64,
            )
            easyadmin._finish_document_email_attempt(
                attempt_id,
                status,
                recipients=['recipient@example.com'],
                message_id='<test-message@example.com>',
                error_message=kwargs.pop('error_message', None),
            )
            return attempt_id

    def test_each_send_attempt_is_kept_and_summarised(self):
        accepted_id = self._record_attempt('invoice', '9881', 'accepted')
        failed_id = self._record_attempt('invoice', '9881', 'failed', error_message='Mailbox unavailable')

        rows = self.conn.execute(
            'SELECT * FROM document_email_history WHERE company_id=? AND document_type=? AND document_key=? ORDER BY id',
            (self.company_id, 'invoice', '9881'),
        ).fetchall()
        self.assertEqual([row['id'] for row in rows], [accepted_id, failed_id])
        self.assertEqual([row['status'] for row in rows], ['accepted', 'failed'])
        self.assertEqual(rows[1]['error_message'], 'Mailbox unavailable')

        summary = easyadmin._document_email_summary_map(self.conn, self.company_id, 'invoice', ['9881'])['9881']
        self.assertEqual(summary['accepted_count'], 1)
        self.assertEqual(summary['failed_count'], 1)
        self.assertTrue(summary['last_accepted_at'])

    def test_history_api_returns_invoice_and_payslip_history(self):
        self._record_attempt('invoice', '9881', 'accepted', document_number='INV-9881')
        payslip_key = easyadmin._document_email_key('payslip', employee_id=9883, payroll_date='2026-10-31')
        self._record_attempt('payslip', payslip_key, 'failed', document_id=None, error_message='SMTP login failed')

        invoice_response = self.client.get('/api/document_email_history?document_type=invoice&document_key=9881')
        self.assertEqual(invoice_response.status_code, 200, invoice_response.get_data(as_text=True))
        invoice_payload = invoice_response.get_json()
        self.assertEqual(invoice_payload['summary']['accepted_count'], 1)
        self.assertEqual(invoice_payload['history'][0]['document_number'], 'INV-9881')

        payslip_response = self.client.get(
            '/api/document_email_history?document_type=payslip&document_key=9883%3A2026-10'
        )
        self.assertEqual(payslip_response.status_code, 200, payslip_response.get_data(as_text=True))
        payslip_payload = payslip_response.get_json()
        self.assertEqual(payslip_payload['summary']['failed_count'], 1)
        self.assertEqual(payslip_payload['history'][0]['error_message'], 'SMTP login failed')

    def test_history_api_enforces_tenant_ownership(self):
        self._record_attempt('invoice', '9891', 'accepted', company_id=self.other_company_id)
        response = self.client.get('/api/document_email_history?document_type=invoice&document_key=9891')
        self.assertEqual(response.status_code, 404)

    def test_invoicing_and_payroll_pages_render_email_history_controls(self):
        invoicing_response = self.client.get('/invoicing/invoices')
        self.assertEqual(invoicing_response.status_code, 200, invoicing_response.get_data(as_text=True))
        self.assertIn(b'documentEmailHistoryPanel', invoicing_response.data)
        self.assertIn(b'Not emailed', invoicing_response.data)

        payroll_response = self.client.get('/payroll')
        self.assertEqual(payroll_response.status_code, 200, payroll_response.get_data(as_text=True))
        self.assertIn(b'payslipEmailHistoryPanel', payroll_response.data)

    def test_pdf_sender_records_accepted_and_failed_attempts(self):
        smtp_context = mock.MagicMock()
        smtp_server = smtp_context.__enter__.return_value
        with easyadmin.app.test_request_context('/'):
            easyadmin.session['company_id'] = self.company_id
            easyadmin.session['company_name'] = 'Email History Test Company'
            easyadmin.session['username'] = 'email.history.test'
            with mock.patch.object(easyadmin, '_standard_validate_pdf_bytes', side_effect=lambda value: value), \
                 mock.patch.object(easyadmin.smtplib, 'SMTP_SSL', return_value=smtp_context):
                result, status = easyadmin._send_pdf_email(
                    b'%PDF-test-accepted', 'invoice.pdf', 'client@example.com',
                    'TAX_INVOICE_INV-9881', 'Test Client',
                    document_type='invoice', document_key='9881', document_id=9881,
                    document_number='INV-9881',
                )
            self.assertEqual(status, 200, result)
            smtp_server.send_message.assert_called_once()

            failed_context = mock.MagicMock()
            failed_context.__enter__.return_value.send_message.side_effect = OSError('SMTP connection lost')
            with mock.patch.object(easyadmin, '_standard_validate_pdf_bytes', side_effect=lambda value: value), \
                 mock.patch.object(easyadmin.smtplib, 'SMTP_SSL', return_value=failed_context):
                result, status = easyadmin._send_pdf_email(
                    b'%PDF-test-failed', 'invoice.pdf', 'client@example.com',
                    'TAX_INVOICE_INV-9881', 'Test Client',
                    document_type='invoice', document_key='9881', document_id=9881,
                    document_number='INV-9881',
                )
            self.assertEqual(status, 500, result)

        rows = self.conn.execute(
            '''SELECT status, recipient_addresses, message_id, error_message
               FROM document_email_history
               WHERE company_id=? AND document_type='invoice' AND document_key='9881'
               ORDER BY id''',
            (self.company_id,),
        ).fetchall()
        self.assertEqual([row['status'] for row in rows], ['accepted', 'failed'])
        self.assertEqual(rows[0]['recipient_addresses'], 'client@example.com')
        self.assertTrue(rows[0]['message_id'])
        self.assertIn('SMTP connection lost', rows[1]['error_message'])


if __name__ == '__main__':
    unittest.main()
