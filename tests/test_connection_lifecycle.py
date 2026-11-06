from __future__ import annotations

import gc
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from fastapi.testclient import TestClient

from app import projects_projects, geocoding, history, system_state, tracker_health
from app import operations, employee_portal, document_storage
from app.settings import settings
from app.sqlite_connection import ClosingConnection


class ConnectionLifecycleTests(unittest.TestCase):
    def test_projects_http_flow_preserves_saved_data_after_reopening(self):
        from app.main import app
        from tests.test_access_and_routes import user
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for setting, name in [('projects_db_path', 'projects.db'),
                                  ('projects_documents_root_path', 'documents'),
                                  ('projects_imports_root_path', 'imports')]:
                stack.enter_context(patch.object(settings, setting, str(Path(directory) / name)))
            stack.enter_context(patch('app.main.user_from_session', return_value=user('projects')))
            projects_projects.init_projects_projects()
            client = TestClient(app)
            stack.callback(client.close)
            response = client.post('/api/projects/clients', json={'data': {'name': 'Availability test', 'person_type': 'PF'}})
            self.assertEqual(response.status_code, 200, response.text)
            client_id = response.json()['id']
            data = {'client_id': client_id, 'title': 'Test process', 'process_type': 'Prefeitura',
                    'service_type': 'Retificação de área', 'city': 'Joinville', 'status_manual': 'OK'}
            response = client.post('/api/projects/processes', json={'data': data})
            self.assertEqual(response.status_code, 200, response.text)
            process_id = response.json()['id']
            data['title'] = 'Updated process'
            response = client.put(f'/api/projects/processes/{process_id}', json={'data': data})
            self.assertEqual(response.status_code, 200, response.text)
            content = b'Preserve this attachment'
            response = client.post('/api/projects/documents', json={
                'owner_type': 'process', 'owner_id': process_id, 'category': 'Outros',
                'filename': 'verification.txt', 'data_url': 'data:text/plain;base64,' + base64.b64encode(content).decode()})
            self.assertEqual(response.status_code, 200, response.text)
            document_id = response.json()['id']
            projects_projects.init_projects_projects()
            self.assertEqual(client.get(f'/api/projects/processes/{process_id}').json()['title'], 'Updated process')
            self.assertEqual(client.get(f'/api/projects/documents/{document_id}/download').content, content)
            for endpoint in ['summary', 'clients', 'processes', 'works',
                             'calendar?date_from=2026-10-01&date_to=2026-10-31', 'notifications']:
                response = client.get('/api/projects/' + endpoint)
                self.assertEqual(response.status_code, 200, (endpoint, response.text))

    def test_commit_and_rollback_both_close_without_garbage_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            with sqlite3.connect(path, factory=ClosingConnection) as db:
                db.execute('CREATE TABLE entries (value TEXT)')
                db.execute("INSERT INTO entries VALUES ('keep')")
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with self.assertRaisesRegex(ValueError, 'rollback'):
                with sqlite3.connect(path, factory=ClosingConnection) as failed:
                    failed.execute("INSERT INTO entries VALUES ('discard')")
                    raise ValueError('rollback')
            with self.assertRaises(sqlite3.ProgrammingError):
                failed.execute('SELECT 1')
            with sqlite3.connect(path, factory=ClosingConnection) as reopened:
                self.assertEqual(reopened.execute('SELECT value FROM entries').fetchall(), [('keep',)])

    def test_polling_factories_release_every_connection_even_when_gc_is_delayed(self):
        factories = [(projects_projects, 'projects_db_path'), (history, 'history_db_path'),
                     (tracker_health, 'tracker_health_db_path'), (geocoding, 'geocoding_db_path'),
                     (system_state, 'system_state_db_path'),
                     (operations, 'history_db_path'), (employee_portal, 'employee_portal_db_path'),
                     (document_storage, 'documents_db_path')]
        enabled = gc.isenabled()
        gc.disable()
        try:
            with tempfile.TemporaryDirectory() as directory:
                for module, setting in factories:
                    with self.subTest(module=module.__name__), patch.object(settings, setting, str(Path(directory) / (setting + '.db'))):
                        for _ in range(100):
                            with (module.connect() if module is operations else module._connect()) as db:
                                self.assertEqual(db.execute('SELECT 1').fetchone()[0], 1)
                            with self.assertRaises(sqlite3.ProgrammingError):
                                db.execute('SELECT 1')
        finally:
            if enabled:
                gc.enable()

    def test_health_probe_does_not_require_login_or_query_business_databases(self):
        from app.main import app
        with patch('app.main.user_from_session', side_effect=AssertionError('login on probe')), \
             patch('app.main.projects_health', side_effect=AssertionError('database on probe')):
            client = TestClient(app)
            try:
                response = client.get('/healthz')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'ok': True})
            finally:
                client.close()
