import unittest
from fastapi.testclient import TestClient
from app.main import app

class PublicSiteTests(unittest.TestCase):
    def test_public_pages_and_brand_asset(self):
        with TestClient(app) as client:
            for path, text in [("/", "Sistema de Gestão Operacional — Demo"), ("/privacy", "Política de Privacidade"), ("/terms", "Termos de Uso"), ("/data-deletion", "Exclusão de Dados"), ("/login", "Entrar no sistema")]:
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(text, response.text)
            self.assertEqual(client.get("/assets/brand.svg").status_code, 200)
