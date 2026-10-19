import os
import shutil
import tempfile
import time
import unittest


TEST_ROOT = tempfile.mkdtemp(prefix='easyadmin-booking-transport-', dir=os.path.dirname(__file__))
os.environ['DATABASE_PATH'] = os.path.join(TEST_ROOT, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(TEST_ROOT, 'uploads')
os.environ['SECRET_KEY'] = 'test-only-booking-transport-secret-key'
os.environ['EASYADMIN_CSRF_ENABLED'] = 'false'
os.environ['EASYADMIN_RATE_LIMIT_ENABLED'] = 'false'
for database_url_name in ('DATABASE_URL', 'SUPABASE_DATABASE_URL', 'POSTGRES_URL'):
    os.environ.pop(database_url_name, None)

import app as easyadmin


class BookingDailyTransportTests(unittest.TestCase):
    company_id = 901
    other_company_id = 902
    reference_date = '2026-10-19'

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    def setUp(self):
        self.conn = easyadmin.get_db_connection()
        for table in ('staff_notifications', 'custom_field_values', 'attachments', 'bookings', 'leave_records', 'clients', 'employees'):
            self.conn.execute(f'DELETE FROM {table} WHERE company_id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('DELETE FROM companies WHERE id IN (?, ?)', (self.company_id, self.other_company_id))
        self.conn.execute('INSERT INTO companies (id, name, can_booking) VALUES (?, ?, 1)', (self.company_id, 'Booking Test Company'))
        self.conn.execute('INSERT INTO companies (id, name, can_booking) VALUES (?, ?, 1)', (self.other_company_id, 'Other Test Company'))

        employees = [
            (self.company_id, 'Alice', 'Contract', 'Active'),
            (self.company_id, 'Bob', 'Contract', 'Active'),
            (self.company_id, 'Cara', 'Ad Hoc', 'Active'),
            (self.company_id, 'Inactive Employee', 'Contract', 'Inactive'),
            (self.company_id, 'Supplier Employee', 'Supplier', 'Active'),
            (self.other_company_id, 'Other Tenant Employee', 'Contract', 'Active'),
        ]
        self.conn.executemany(
            'INSERT INTO employees (company_id, name, emp_type, status) VALUES (?, ?, ?, ?)',
            employees,
        )
        self.conn.execute(
            "INSERT INTO clients (id, company_id, name, surname, client_type, address, suburb) VALUES (9011, ?, 'Contract', 'Client', 'Contract', '1 Main Road', 'Central')",
            (self.company_id,),
        )
        self.conn.execute(
            "INSERT INTO clients (id, company_id, name, surname, client_type, address, suburb) VALUES (9012, ?, 'Adhoc', 'Client', 'Ad Hoc', '2 Side Road', 'North')",
            (self.company_id,),
        )
        self.conn.execute(
            "INSERT INTO clients (id, company_id, name, surname, client_type, address, suburb) VALUES (9021, ?, 'Other', 'Tenant', 'Contract', '3 Remote Road', 'Elsewhere')",
            (self.other_company_id,),
        )

        bookings = [
            (9101, self.company_id, 9011, 'Contract Client', '2026-10-19T08:00', 'Alice, Bob', 'Cleaning', 'Pickup'),
            (9102, self.company_id, 9011, 'Contract Client', '2026-10-19T09:00', 'Alice', 'Cleaning', ''),
            (9103, self.company_id, 9012, 'Adhoc Client', '2026-10-20T10:00', '', 'Cleaning', 'Drop Off'),
            (9104, self.company_id, 9012, 'Adhoc Client', '2026-10-19T10:00', 'Cara', 'Cleaning', 'Drop Off'),
            (9105, self.company_id, 9012, 'Adhoc Client', '2026-10-19T11:00', 'Cara', 'Cleaning', 'Pickup & Drop Off'),
            (9106, self.company_id, 9012, 'Adhoc Client', '2026-10-19T12:00', 'Cara', 'Cleaning', 'Drop Off, Pickup'),
            (9107, self.company_id, 9012, 'Adhoc Client', '2026-10-19T13:00', 'Cara', 'Cleaning', 'No Transport Required'),
            (9108, self.company_id, 9012, 'Adhoc Client', '2026-10-19T14:00', 'Cara', 'Cleaning', 'Own Transport'),
            (9201, self.other_company_id, 9021, 'Other Tenant', '2026-10-19T08:00', 'Other Tenant Employee', 'Cleaning', 'Pickup'),
        ]
        self.conn.executemany(
            '''INSERT INTO bookings
               (id, company_id, client_id, title, start, employee, booking_type, transport)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            bookings,
        )
        self.conn.commit()
        self.client = easyadmin.app.test_client()
        with self.client.session_transaction() as flask_session:
            flask_session['logged_in'] = True
            flask_session['company_id'] = self.company_id
            flask_session['company_name'] = 'Booking Test Company'
            flask_session['username'] = 'booking.test'
            flask_session['can_booking'] = 1
            flask_session['last_activity_at'] = time.time()

    def tearDown(self):
        self.conn.close()

    def test_transport_classifier_supports_current_and_legacy_values(self):
        included = [
            'Pickup', 'Pickup Only', 'pick-up', 'Drop Off', 'Drop off Only',
            'drop-off', 'Pickup & Drop Off', 'Drop Off, Pickup', 'Pickup, Drop Off',
        ]
        excluded = [None, '', '  ', 'No Transport Required', 'no transport', 'None', 'Own Transport']
        for value in included:
            with self.subTest(value=value):
                self.assertTrue(easyadmin._booking_uses_company_transport(value))
        for value in excluded:
            with self.subTest(value=value):
                self.assertFalse(easyadmin._booking_uses_company_transport(value))

    def test_daily_summary_is_added_without_changing_weekly_root_contract(self):
        response = self.client.get(f'/api/booking-dashboard-summary?reference_date={self.reference_date}')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(payload['daily']['date'], self.reference_date)
        self.assertEqual(payload['daily']['label'], 'Monday, 19 October 2026')
        self.assertEqual(payload['daily']['employee_metrics']['active_employees'], 3)
        self.assertEqual(payload['daily']['employee_metrics']['employees_booked'], 3)
        self.assertEqual(payload['daily']['employee_metrics']['total_assignments'], 8)
        self.assertEqual(payload['daily']['customer_metrics']['customers_booked'], 2)
        self.assertEqual(payload['daily']['customer_metrics']['total_bookings'], 7)

        self.assertIn('employee_metrics', payload)
        self.assertIn('customer_metrics', payload)
        self.assertIn('employee_summary', payload)
        self.assertIn('customer_summary', payload)
        self.assertEqual(payload['customer_metrics']['total_bookings'], 8)
        self.assertEqual(payload['customer_metrics']['unassigned_bookings'], 1)
        self.assertEqual(payload['week']['start'], '2026-10-19')
        self.assertEqual(payload['week']['end'], '2026-10-25')

    def test_calendar_colours_bookings_by_transport_and_keeps_white_text(self):
        response = self.client.get('/bookings?start=2026-10-19&end=2026-10-21')
        self.assertEqual(response.status_code, 200)
        events = {
            int(event['id']): event
            for event in response.get_json()
            if event.get('extendedProps', {}).get('event_type') == 'booking'
        }

        for booking_id in (9101, 9103, 9104, 9105, 9106):
            self.assertEqual(events[booking_id]['color'], '#0d6efd')
            self.assertEqual(events[booking_id]['textColor'], '#ffffff')
        for booking_id in (9102, 9107, 9108):
            self.assertEqual(events[booking_id]['color'], '#198754')
            self.assertEqual(events[booking_id]['textColor'], '#ffffff')
        self.assertNotIn(9201, events)

    def test_booking_page_renders_daily_section_before_weekly_and_includes_legend(self):
        response = self.client.get('/booking')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)

        self.assertLess(page.index('Daily Booking Overview'), page.index('Weekly Booking Overview'))
        self.assertIn('Daily Employee Summary', page)
        self.assertIn('Daily Client Booking Summary', page)
        self.assertIn('Company transport', page)
        self.assertIn('No transport required', page)
        self.assertIn('All company transport bookings', page)

    def test_daily_route_returns_only_company_transport_bookings(self):
        response = self.client.post('/daily_route', json={
            'date': self.reference_date,
            'employee': '',
            'start_point': '',
            'optimize': False,
        })
        self.assertEqual(response.status_code, 200)
        route_ids = {int(item['id']) for item in response.get_json()['route']}

        self.assertEqual(route_ids, {9101, 9104, 9105, 9106})
        self.assertNotIn(9102, route_ids)
        self.assertNotIn(9107, route_ids)
        self.assertNotIn(9108, route_ids)
        self.assertNotIn(9201, route_ids)

    def test_daily_route_employee_filter_is_applied_after_transport_filter(self):
        response = self.client.post('/daily_route', json={
            'date': self.reference_date,
            'employee': 'Bob',
            'start_point': '',
            'optimize': False,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([int(item['id']) for item in response.get_json()['route']], [9101])


if __name__ == '__main__':
    unittest.main()

