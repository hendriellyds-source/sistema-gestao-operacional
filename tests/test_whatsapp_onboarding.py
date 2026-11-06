from __future__ import annotations

import unittest
from unittest.mock import call, patch

from app.whatsapp_onboarding import (
    WhatsAppOnboardingError,
    inspect_authorized_business,
    subscribe_app_to_waba,
)


class WhatsAppOnboardingTests(unittest.TestCase):
    def test_inspection_subscribes_app_before_returning_connection(self) -> None:
        with (
            patch(
                "app.whatsapp_onboarding.exchange_embedded_signup_code",
                return_value="temporary-token",
            ),
            patch(
                "app.whatsapp_onboarding._graph_get",
                side_effect=[
                    {"id": "waba-1", "name": "Empresa Cliente"},
                    {
                        "id": "phone-1",
                        "display_phone_number": "+55 48 99999-0000",
                        "verified_name": "Empresa Cliente",
                    },
                ],
            ) as graph_get,
            patch(
                "app.whatsapp_onboarding.subscribe_app_to_waba"
            ) as subscribe,
        ):
            connection = inspect_authorized_business(
                code="authorization-code",
                waba_id="waba-1",
                phone_number_id="phone-1",
                business_id="business-1",
            )

        self.assertEqual(
            graph_get.call_args_list,
            [
                call("waba-1", access_token="temporary-token", fields="id,name"),
                call(
                    "phone-1",
                    access_token="temporary-token",
                    fields=(
                        "id,display_phone_number,verified_name,quality_rating"
                    ),
                ),
            ],
        )
        subscribe.assert_called_once_with(
            waba_id="waba-1",
            access_token="temporary-token",
        )
        self.assertTrue(connection.webhook_subscribed)
        self.assertEqual(connection.access_token, "temporary-token")

    def test_subscription_requires_explicit_meta_success(self) -> None:
        with patch(
            "app.whatsapp_onboarding._graph_post",
            return_value={"success": False},
        ):
            with self.assertRaisesRegex(
                WhatsAppOnboardingError,
                "não confirmou o recebimento",
            ):
                subscribe_app_to_waba(
                    waba_id="waba-1",
                    access_token="temporary-token",
                )


if __name__ == "__main__":
    unittest.main()
