# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
from base64 import b64encode
from unittest.mock import patch

from werkzeug.exceptions import Forbidden

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_worldline.controllers.main import WorldlineController
from odoo.addons.payment_worldline.tests.common import WorldlineCommon


@tagged('post_install', '-at_install')
class WorldlineTest(WorldlineCommon, PaymentHttpCommon):

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def _webhook_notification_flow(self, payload):
        """ Send a notification to the webhook, ignore the signature, and check the response. """
        url = self._build_url(WorldlineController._webhook_url)
        with patch(
            'odoo.addons.payment_worldline.controllers.main.WorldlineController._verify_signature'
        ):
            response = self._make_json_request(url, data=payload)
        self.assertEqual(
            response.json(), '', msg="The webhook should always respond ''.",
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_webhook_notification_confirms_transaction(self):
        """ Test the processing of a webhook notification. """
        tx = self._create_transaction('redirect')
        self.assertFalse(tx.tokenize, "No token should be asked.")
        self._webhook_notification_flow(self.payment_data)
        self.assertFalse(tx.token_id, "No token should be created.")
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, '1234567890')

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_webhook_notification_creates_token(self):
        """ Test the processing of a webhook notification when creating a token. """
        tx = self._create_transaction('redirect', tokenize=True)
        self.assertTrue(tx.tokenize, "A token should be asked.")
        self._webhook_notification_flow(self.payment_data)
        self.assertEqual(tx.state, 'done')
        self.assertFalse(tx.tokenize, "No token should be asked any more.")
        self.assertTrue(tx.token_id, "A token should have been created and linked to the tx.")
        self.assertEqual(tx.token_id.provider_ref, 'whateverToken')
        self.assertEqual(tx.token_id.payment_details, '4242')

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_failed_webhook_notification_set_tx_as_error_1(self):
        """ Test the processing of a webhook notification for a failed transaction. """
        tx = self._create_transaction('redirect')
        test = self.payment_data_insufficient_funds
        self._webhook_notification_flow(test)
        self.assertEqual(tx.state, 'error')
        self.assertEqual(
            tx.state_message,
            "Transaction declined with error code 30511001.",
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_failed_webhook_notification_set_tx_as_error_2(self):
        """ Test the processing of a webhook notification for a failed transaction. """
        tx = self._create_transaction('redirect')
        test = self.payment_data_expired_card
        self._webhook_notification_flow(test)
        self.assertEqual(tx.state, 'error')
        self.assertEqual(
            tx.state_message,
            "Transaction declined with error code 30331001.",
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_failed_webhook_notification_set_tx_as_cancel(self):
        """Test the processing of a webhook notification for a cancelled transaction."""
        tx = self._create_transaction('redirect')
        test = {
            'payment': {
                'paymentOutput': self.payment_data['payment']['paymentOutput'],
                'hostedCheckoutSpecificOutput': {
                    'hostedCheckoutId': '123456789',
                },
                'status': 'CANCELLED',
                'statusOutput': {
                    'errors': [{
                        'errorCode': '30171001',
                    }],
                },
            },
        }
        self._webhook_notification_flow(test)
        self.assertEqual(tx.state, 'cancel')
        self.assertEqual(
            tx.state_message,
            "Transaction cancelled with error code 30171001.",
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_webhook_notification_triggers_signature_check(self):
        """ Test that receiving a webhook notification triggers a signature check. """
        self._create_transaction('redirect')
        url = self._build_url(WorldlineController._webhook_url)
        with patch(
            'odoo.addons.payment_worldline.controllers.main.WorldlineController._verify_signature'
        ) as signature_check_mock, patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._process'
        ):
            self._make_json_request(url, data=self.payment_data)
            self.assertEqual(signature_check_mock.call_count, 1)

    def test_accept_notification_with_valid_signature(self):
        """ Test the verification of a notification with a valid signature. """
        tx = self._create_transaction('redirect')
        unencoded_result = hmac.new(
            self.worldline.worldline_webhook_secret.encode(),
            json.dumps(self.payment_data).encode(),
            hashlib.sha256,
        ).digest()
        expected_signature = b64encode(unencoded_result)
        self._assert_does_not_raise(
            Forbidden,
            WorldlineController._verify_signature,
            json.dumps(self.payment_data).encode(),
            expected_signature,
            tx,
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_reject_notification_with_missing_signature(self):
        """ Test the verification of a notification with a missing signature. """
        tx = self._create_transaction('redirect')
        self.assertRaises(
            Forbidden,
            WorldlineController._verify_signature,
            json.dumps(self.payment_data).encode(),
            None,
            tx,
        )

    @mute_logger('odoo.addons.payment_worldline.controllers.main')
    def test_reject_notification_with_invalid_signature(self):
        """ Test the verification of a notification with an invalid signature. """
        tx = self._create_transaction('redirect')
        self.assertRaises(
            Forbidden,
            WorldlineController._verify_signature,
            json.dumps(self.payment_data).encode(),
            'dummy',
            tx,
        )
