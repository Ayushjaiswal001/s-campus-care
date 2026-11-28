import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as campus_app


class CampusCareTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / 'test.db')
        campus_app.app.config.update(TESTING=True, DATABASE=self.database)
        campus_app.init_db()
        self.client = campus_app.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, role='student'):
        credentials = {'student': ('student', 'stud123'), 'admin': ('admin', 'admin123')}
        username, password = credentials[role]
        return self.client.post('/', data={
            'role': role, 'username': username, 'password': password,
        }, follow_redirects=True)

    def test_login_and_student_dashboard(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Student Dashboard', response.data)

    def test_legacy_plaintext_password_is_upgraded_after_login(self):
        conn = sqlite3.connect(self.database)
        conn.execute("UPDATE users SET password='stud123' WHERE username='student'")
        conn.commit()
        conn.close()
        self.assertEqual(self.login().status_code, 200)
        conn = sqlite3.connect(self.database)
        stored = conn.execute("SELECT password FROM users WHERE username='student'").fetchone()[0]
        conn.close()
        self.assertTrue(stored.startswith(('scrypt:', 'pbkdf2:')))

    def test_student_submission_persists_and_renders_named_fields_and_date(self):
        self.login()
        response = self.client.post('/student', data={
            'description': 'WiFi is not working in the computer lab',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'WiFi is not working in the computer lab', response.data)
        self.assertIn(b'IT &amp; Lab Infrastructure', response.data)
        self.assertIn(b'Open', response.data)
        conn = sqlite3.connect(self.database)
        row = conn.execute('SELECT description, status, created_at FROM issues').fetchone()
        conn.close()
        self.assertEqual(row[0], 'WiFi is not working in the computer lab')
        self.assertEqual(row[1], 'Open')
        self.assertTrue(row[2])

    def test_invalid_submission_is_rejected_without_losing_dashboard(self):
        self.login()
        response = self.client.post('/student', data={'description': 'bad'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Description must be between 5 and 2000 characters', response.data)
        conn = sqlite3.connect(self.database)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM issues').fetchone()[0], 0)
        conn.close()

    def test_named_equipment_category_overrides_generic_working_language(self):
        category, _ = campus_app.analyze_issue_ai(
            'Ceiling fan in the computer lab is not working'
        )
        self.assertEqual(category, 'Electrical & Maintenance')

    def test_description_without_an_equipment_hint_does_not_default_to_it(self):
        category, _ = campus_app.analyze_issue_ai('I am not working')
        self.assertEqual(category, 'General Campus Facility')

    def test_positive_status_is_saved_as_feedback_not_an_issue(self):
        self.login()
        response = self.client.post('/student', data={
            'description': 'Everything is fine and the fan is working',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'saved separately from reported issues', response.data)
        conn = sqlite3.connect(self.database)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM issues').fetchone()[0], 0)
        feedback = conn.execute('SELECT description FROM feedback').fetchone()
        conn.close()
        self.assertEqual(feedback[0], 'Everything is fine and the fan is working')

    def test_negated_working_status_stays_an_issue(self):
        self.login()
        self.client.post('/student', data={
            'description': 'The fan is not working',
        })
        conn = sqlite3.connect(self.database)
        row = conn.execute('SELECT category FROM issues').fetchone()
        feedback_count = conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0]
        conn.close()
        self.assertEqual(row[0], 'Electrical & Maintenance')
        self.assertEqual(feedback_count, 0)

    def test_admin_updates_issue_and_rejects_unknown_status(self):
        self.login()
        self.client.post('/student', data={'description': 'Broken window in the corridor'})
        self.client.get('/logout')
        self.login('admin')
        response = self.client.post('/update_issue/1', data={'status': 'Resolved'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        conn = sqlite3.connect(self.database)
        self.assertEqual(conn.execute('SELECT status FROM issues WHERE id=1').fetchone()[0], 'Resolved')
        conn.close()
        self.client.post('/update_issue/1', data={'status': 'Injected value'})
        conn = sqlite3.connect(self.database)
        self.assertEqual(conn.execute('SELECT status FROM issues WHERE id=1').fetchone()[0], 'Resolved')
        conn.close()

    def test_legacy_issue_table_is_migrated_in_place(self):
        legacy_db = str(Path(self.temp_dir.name) / 'legacy.db')
        conn = sqlite3.connect(legacy_db)
        conn.execute('CREATE TABLE issues (id INTEGER PRIMARY KEY, student TEXT, description TEXT, category TEXT, priority TEXT, status TEXT)')
        conn.execute("INSERT INTO issues VALUES (1, 'student', 'Existing report', 'General Campus Facility', 'Low', 'Open')")
        conn.commit()
        conn.close()
        campus_app.app.config['DATABASE'] = legacy_db
        campus_app.init_db()
        conn = sqlite3.connect(legacy_db)
        row = conn.execute('SELECT description, created_at FROM issues WHERE id=1').fetchone()
        conn.close()
        self.assertEqual(row[0], 'Existing report')
        self.assertTrue(row[1])


if __name__ == '__main__':
    unittest.main()
