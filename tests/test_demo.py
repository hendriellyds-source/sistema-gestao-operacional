from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.settings import Settings, settings
from app.auth import authenticate

def test_demo_settings_are_safe_and_explicit():
    demo = Settings(_env_file=None, demo_mode=True, integrations_enabled=True,
                    selsyn_api_key="not-used", supabase_url="https://example.invalid",
                    whatsapp_enabled=True, cora_enabled=True)
    assert demo.demo_mode
    assert demo.integrations_enabled
    assert not demo.selsyn_api_key and not demo.supabase_url
    assert not demo.whatsapp_enabled and not demo.cora_enabled
    assert demo.system_state_db_path.startswith("data/demo/")

def test_demo_authentication_flow():
    with TestClient(app) as client:
        assert client.get('/api/auth/me').status_code == 401
        assert client.post('/api/auth/login', json={'email':settings.demo_email,'password':'wrong'}).status_code == 401
        response = client.post('/api/auth/login', json={'email':settings.demo_email,'password':settings.demo_password})
        assert response.status_code == 200
        assert 'HttpOnly' in response.headers['set-cookie']
        assert client.get('/api/auth/me').json()['role_code'] == 'admin'
        assert client.get('/workspace').status_code == 200
        assert client.get('/api/vehicles/live').status_code == 200
        assert client.post('/api/auth/logout').status_code == 200
        assert client.get('/api/auth/me').status_code == 401

def test_public_demo_password_cannot_authenticate_operational_mode():
    with patch.object(settings, 'demo_mode', False), patch('app.auth.create_public_supabase', side_effect=RuntimeError('unconfigured')):
        assert authenticate(settings.demo_email, settings.demo_password) is None
