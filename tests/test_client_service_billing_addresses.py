import os
import shutil
import tempfile
import time
import unittest


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-client-addresses-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-client-address-secret-key'
os.environ['EASYADMIN_CSRF_ENABLED'] = 'false'
os.environ['EASYADMIN_RATE_LIMIT_ENABLED'] = 'false'
for database_url_name in ('DATABASE_URL', 'SUPABASE_DATABASE_URL', 'POSTGRES_URL'):
    os.environ.pop(database_url_name, None)

import app as easyadmin


class ClientServiceAndBillingAddressTests(unittest.TestCase):
    company_id = 981

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.conn = easyadmin.get_db_connection()
        for table in ('attachments', 'invoice_items', 'quote_items', 'invoices', 'quotes', 'projects', 'bookings', 'clients'):
            if table in ('invoice_items', 'quote_items'):
                self.conn.execute(f'DELETE FROM {table}')
            else:
                self.conn.execute(f'DELETE FROM {table} WHERE company_id=?', (self.company_id,))
        self.conn.execute('DELETE FROM companies WHERE id=?', (self.company_id,))
        self.conn.execute(
            'INSERT INTO companies (id, name, can_booking, can_invoicing) VALUES (?, ?, 1, 1)',
            (self.company_id, 'Client Address Test Company'),
        )
        self.conn.commit()
        self.client = easyadmin.app.test_client()
        with self.client.session_transaction() as flask_session:
            flask_session['logged_in'] = True
            flask_session['company_id'] = self.company_id
            flask_session['company_name'] = 'Client Address Test Company'
            flask_session['username'] = 'client.address.test'
            flask_session['can_booking'] = 1
            flask_session['can_invoicing'] = 1
            flask_session['last_activity_at'] = time.time()

    def tearDown(self):
        self.conn.close()

    def _create_company_client(self, billing_address=''):
        response = self.client.post('/update_client', json={
            'name': '',
            'surname': '',
            'company_name': 'Acme Services (Pty) Ltd',
            'client_type': 'Ad hoc',
            'service_address': '12 Main Road, Sea Point, Cape Town, 8005',
            'billing_address': billing_address,
            'email': 'accounts@example.com',
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return int(response.get_json()['client_id'])

    def test_company_name_can_be_used_without_first_name(self):
        client_id = self._create_company_client('PO Box 10, Cape Town, 8000')
        row = self.conn.execute('SELECT * FROM clients WHERE id=?', (client_id,)).fetchone()
        self.assertEqual(row['name'], '')
        self.assertEqual(easyadmin.client_display_name(row), 'Acme Services (Pty) Ltd')
        self.assertEqual(row['address'], '12 Main Road, Sea Point, Cape Town, 8005')
        self.assertEqual(row['billing_address'], 'PO Box 10, Cape Town, 8000')

    def test_both_client_profile_screens_render_the_new_fields(self):
        self._create_company_client('PO Box 10, Cape Town, 8000')
        for path in ('/booking', '/invoicing/invoices'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                page = response.get_data(as_text=True)
                self.assertIn('Service Address', page)
                self.assertIn('Billing Address', page)
                self.assertIn('Delete Client', page)
                self.assertIn('Acme Services (Pty) Ltd', page)
                self.assertNotIn('id="cBuildingNumber"', page)

    def test_first_name_or_company_name_is_required(self):
        response = self.client.post('/update_client', json={'name': '', 'company_name': ''})
        self.assertEqual(response.status_code, 400)
        self.assertIn('either a First Name or a Company Name', response.get_json()['message'])

        cleaned, errors = easyadmin.validate_import_row(
            'clients',
            {'name': '', 'company_name': 'CSV Company', 'building_number': '5', 'street_name': 'Import Road', 'suburb': 'Central', 'postal_code': '8001'},
            2,
            self.conn,
            self.company_id,
            {},
        )
        self.assertEqual(errors, [])
        self.assertEqual(cleaned['address'], '5 Import Road, Central, 8001')

    def test_financial_documents_use_billing_address_and_service_fallback(self):
        billed_client_id = self._create_company_client('PO Box 10, Cape Town, 8000')
        fallback_client_id = self._create_company_client('')
        billed = self.conn.execute('SELECT * FROM clients WHERE id=?', (billed_client_id,)).fetchone()
        fallback = self.conn.execute('SELECT * FROM clients WHERE id=?', (fallback_client_id,)).fetchone()
        self.assertEqual(easyadmin.client_billing_address(billed), 'PO Box 10, Cape Town, 8000')
        self.assertEqual(easyadmin.client_billing_address(fallback), '12 Main Road, Sea Point, Cape Town, 8005')

        cursor = self.conn.execute(
            '''INSERT INTO invoices
               (company_id, client_id, client_name, date, due_date, subtotal, vat_amount, total, status,
                discount_percent, discount_amount, amount_due_now, balance_remaining, invoice_type, invoice_number)
               VALUES (?, ?, ?, '2026-10-22', '2026-10-31', 100, 15, 115, 'Unpaid', 0, 0, 115, 0, 'standard', 1)''',
            (self.company_id, billed_client_id, 'Acme Services (Pty) Ltd'),
        )
        invoice_id = cursor.lastrowid
        cursor = self.conn.execute(
            '''INSERT INTO quotes
               (company_id, client_id, client_name, date, valid_until, subtotal, vat_amount, total, status, quote_number)
               VALUES (?, ?, ?, '2026-10-22', '2026-11-22', 100, 15, 115, 'Pending', 1)''',
            (self.company_id, fallback_client_id, 'Acme Services (Pty) Ltd'),
        )
        quote_id = cursor.lastrowid
        self.conn.commit()

        invoice = self.client.get(f'/api/invoice/{invoice_id}')
        quote = self.client.get(f'/api/quote/{quote_id}')
        statement = self.client.post('/api/client_statement', json={
            'client_id': billed_client_id,
            'client_name': 'Acme Services (Pty) Ltd',
            'start_date': '2026-10-01',
            'end_date': '2026-10-31',
        })
        self.assertEqual(invoice.get_json()['client_address'], 'PO Box 10, Cape Town, 8000')
        self.assertEqual(quote.get_json()['client_address'], '12 Main Road, Sea Point, Cape Town, 8005')
        self.assertEqual(statement.get_json()['client_address'], 'PO Box 10, Cape Town, 8000')

    def test_route_planner_uses_service_address(self):
        client_id = self._create_company_client('Different Billing Address')
        self.conn.execute(
            '''INSERT INTO bookings (company_id, client_id, title, start, employee, booking_type, transport)
               VALUES (?, ?, 'Acme Services (Pty) Ltd', '2026-10-22T08:00', 'Test Employee', 'Cleaning', 'Pickup Only')''',
            (self.company_id, client_id),
        )
        self.conn.commit()
        response = self.client.post('/daily_route', json={
            'date': '2026-10-22', 'employee': '', 'start_point': '', 'optimize': False,
        })
        self.assertEqual(response.status_code, 200)
        route = response.get_json()['route']
        self.assertEqual(route[0]['address'], '12 Main Road, Sea Point, Cape Town, 8005')
        self.assertEqual(route[0]['area'], 'Cape Town')

    def test_delete_is_allowed_only_without_linked_history(self):
        deletable_id = self._create_company_client()
        response = self.client.post('/delete_client', json={'id': deletable_id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.conn.execute('SELECT 1 FROM clients WHERE id=?', (deletable_id,)).fetchone())

        protected_id = self._create_company_client()
        self.conn.execute(
            '''INSERT INTO bookings (company_id, client_id, title, start, employee)
               VALUES (?, ?, 'Acme Services (Pty) Ltd', '2026-10-22T09:00', 'Test Employee')''',
            (self.company_id, protected_id),
        )
        self.conn.commit()
        response = self.client.post('/delete_client', json={'id': protected_id})
        self.assertEqual(response.status_code, 409)
        self.assertIn('1 bookings', response.get_json()['message'])
        self.assertTrue(self.conn.execute('SELECT 1 FROM clients WHERE id=?', (protected_id,)).fetchone())

    def test_legacy_components_fallback_without_overriding_service_address(self):
        self.assertEqual(
            easyadmin.compose_client_address('10', 'Old Street', 'Old Suburb', '7000', ''),
            '10 Old Street, Old Suburb, 7000',
        )
        self.assertEqual(
            easyadmin.compose_client_address('10', 'Old Street', 'Old Suburb', '7000', 'New Service Address'),
            'New Service Address',
        )

    def test_startup_backfills_and_retains_legacy_address_components(self):
        self.conn.execute(
            '''INSERT INTO clients
               (id, company_id, name, address, building_number, street_name, suburb, postal_code)
               VALUES (9819, ?, 'Legacy Client', '', '10', 'Old Street', 'Old Suburb', '7000')''',
            (self.company_id,),
        )
        self.conn.commit()
        easyadmin.init_db()
        migrated = self.conn.execute('SELECT * FROM clients WHERE id=9819').fetchone()
        self.assertEqual(migrated['address'], '10 Old Street, Old Suburb, 7000')

        response = self.client.post('/update_client', json={
            'id': 9819,
            'name': 'Legacy Client',
            'company_name': '',
            'service_address': '20 New Service Road, New Suburb, 8000',
            'billing_address': '',
            'client_type': 'Ad hoc',
        })
        self.assertEqual(response.status_code, 200)
        updated = self.conn.execute('SELECT * FROM clients WHERE id=9819').fetchone()
        self.assertEqual(updated['address'], '20 New Service Road, New Suburb, 8000')
        self.assertEqual(updated['building_number'], '10')
        self.assertEqual(updated['street_name'], 'Old Street')
        self.assertEqual(updated['suburb'], 'Old Suburb')
        self.assertEqual(updated['postal_code'], '7000')


if __name__ == '__main__':
    unittest.main()
