import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import historical_vision as vision
from app.historical_pricing import refresh_historical_prices


class HistoricalVisionTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        self.role = "financial"
        @app.middleware("http")
        async def user(request: Request, call_next):
            request.state.system_user = {"id": 9, "role_code": self.role}
            return await call_next(request)
        app.include_router(vision.router)
        self.client = TestClient(app)
        self.image = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfftest").decode()
        self.url = "/api/schedules/historical/photo-preview"
        vision._recent.clear()
        vision._running.clear()

    def test_access_and_configuration(self):
        self.role = "aline"
        self.assertEqual(self.client.post(self.url, json={}).status_code, 403)
        self.role = "financial"
        with patch.object(vision.settings, "openai_api_key", ""):
            self.assertEqual(self.client.post(self.url, json={}).status_code, 503)

    def test_draft_only_and_no_provider_secrets(self):
        result = {"rows": [{"team": "Emanuel", "company": "MDM", "work": "Brilhante", "dailyType": "Inteira", "originalText": "MDM Brilhante", "observation": "", "uncertain": False}], "pageNotes": "Rodapé: integrantes"}
        response = httpx.Response(200, request=httpx.Request("POST", "https://api.openai.com/v1/responses"), json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}]})
        with patch.object(vision.settings, "openai_api_key", "private-test-key"), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)) as post:
            answer = self.client.post(self.url, json={"image": self.image})
            self.assertEqual(answer.status_code, 200, answer.text)
            self.assertEqual(answer.json(), result)
            sent = post.call_args.kwargs["json"]
            self.assertFalse(sent["store"])
            self.assertTrue(sent["text"]["format"]["strict"])
            self.assertNotIn("private-test-key", answer.text)

    def test_invalid_images_and_provider_failure(self):
        with patch.object(vision.settings, "openai_api_key", "test"):
            for image in ["https://example.com/private", "data:image/jpeg;base64,broken", "data:image/png;base64,YWJj"]:
                self.assertEqual(self.client.post(self.url, json={"image": image}).status_code, 400)
            with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("secret"))):
                response = self.client.post(self.url, json={"image": self.image})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("secret", response.text)
                self.assertFalse(vision._running)


class HistoricalPricingTests(unittest.TestCase):
    def test_missing_price_follows_work_and_requires_review(self):
        row = {"id": 1, "historical": True, "workId": 7, "companyId": 2, "dailyType": "Inteira", "value": 0, "reviewed": True}
        state = {"works": [{"id": 7, "companyId": 2, "dailyRate": 0}], "workScheduleEntries": [row]}
        refresh_historical_prices(state)
        self.assertTrue(row["historicalPending"])
        state["works"][0]["dailyRate"] = 1500
        refresh_historical_prices(state)
        self.assertEqual(row["value"], 1500)
        self.assertFalse(row["reviewed"])
        row.update(reviewed=True, historicalPending=False)
        refresh_historical_prices(state)
        self.assertFalse(row["historicalPending"])
        state["invoices"] = [{"scheduleEntryIds": [1]}]
        state["works"][0]["dailyRate"] = 1800
        refresh_historical_prices(state)
        self.assertEqual(row["value"], 1500)

    def test_legacy_amounts_and_half_rate(self):
        rows = [{"id": 1, "historical": True, "workId": 7, "companyId": 2, "dailyType": "Meia", "value": 0},
                {"id": 2, "historical": True, "workId": 7, "companyId": 2, "dailyType": "Inteira", "value": 123},
                {"id": 3, "workId": 7, "value": 234}]
        state = {"works": [{"id": 7, "companyId": 2, "dailyRate": 1500}], "workScheduleEntries": rows}
        refresh_historical_prices(state)
        self.assertEqual([r["value"] for r in rows], [750, 123, 234])
