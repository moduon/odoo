# Part of Odoo. See LICENSE file for full copyright and licensing details.

import unittest
from unittest.mock import patch

from werkzeug.urls import url_encode, url_join

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_stripe import const
from odoo.addons.payment_stripe.controllers.main import StripeController
from odoo.addons.payment_stripe.tests.common import StripeCommon


@tagged('post_install', '-at_install')
class StripeTest(StripeCommon, PaymentHttpCommon):

    def test_processing_values(self):
        dummy_client_secret = 'pi_123456789_secret_dummy_123456789'
        tx = self._create_transaction(flow='direct')  # We don't really care what the flow is here.

        # Ensure no external API call is done, we only want to check the processing values logic
        def mock_stripe_stripe_create_intent(self):
            return {'client_secret': dummy_client_secret}

        with patch.object(
            type(self.env['payment.transaction']), '_stripe_create_intent',
            mock_stripe_stripe_create_intent,
        ), mute_logger('odoo.addons.payment.models.payment_transaction'):
            processing_values = tx._get_processing_values()

        self.assertEqual(processing_values['client_secret'], dummy_client_secret)

        base_url = self.provider.get_base_url()
        return_url = url_join(
            base_url, f'{StripeController._return_url}?{url_encode({"reference": tx.reference})}'
        )
        self.assertEqual(processing_values['return_url'], return_url)

    @mute_logger('odoo.addons.payment_stripe.models.payment_transaction')
    def test_tx_state_after_send_capture_request(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('direct', state='authorized')

        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={
                'id': 'pi_3KTk9zAlCFm536g81Wy7RCPH',
                'status': 'succeeded',
                **self.notification_amount_and_currency,
            },
        ):
            tx._capture()
        self.assertEqual(
            tx.state, 'done', msg="The state should be 'done' after a successful capture."
        )

    @mute_logger('odoo.addons.payment_stripe.models.payment_transaction')
    def test_tx_state_after_send_void_request(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect', state='authorized')

        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={
                'id': 'pi_3KTk9zAlCFm536g81Wy7RCPH',
                'status': 'canceled',
                **self.notification_amount_and_currency,
            },
        ):
            child_tx = tx._void()
        self.assertEqual(
            child_tx.state, 'cancel', msg="The state should be 'cancel' after voiding the transaction."
        )

    @mute_logger('odoo.addons.payment_stripe.controllers.main')
    def test_webhook_notification_confirms_transaction(self):
        """ Test the processing of a webhook notification. """
        tx = self._create_transaction('redirect')
        url = self._build_url(StripeController._webhook_url)
        with patch(
            'odoo.addons.payment_stripe.controllers.main.StripeController._verify_signature'
        ):
            self._make_json_request(url, data=self.payment_data)
        self.assertEqual(tx.state, 'done')

    def test_extract_token_values_maps_fields_correctly(self):
        tx = self._create_transaction('direct')
        payment_data = {
           'payment_intent': {
              'charges': {'data': [{}]},
              'customer': 'test_customer',
           },
           'payment_method': {
              'card': {
                 'brand': 'visa',
                 'last4': '1111'
              },
              'id': 'pm_test',
              'object': 'payment_method',
              'type': 'card',
           }
        }
        token_values = tx._extract_token_values(payment_data)
        self.assertDictEqual(token_values, {
            'payment_details': '1111',
            'provider_ref': 'test_customer',
            'stripe_mandate': None,
            'stripe_payment_method': 'pm_test'
        })

    @mute_logger('odoo.addons.payment_stripe.controllers.main')
    def test_webhook_notification_tokenizes_payment_method(self):
        """ Test the processing of a webhook notification. """
        self._create_transaction('dummy', operation='validation', tokenize=True)
        url = self._build_url(StripeController._webhook_url)
        payment_method_response = {
            'card': {'last4': '4242'},
            'id': 'pm_1KVZSNAlCFm536g8sYB92I1G',
            'type': 'card'
        }
        with patch(
            'odoo.addons.payment_stripe.controllers.main.StripeController._verify_signature'
        ), patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=payment_method_response,
        ), patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._tokenize'
        ) as tokenize_check_mock:
            self._make_json_request(
                url, data=dict(self.payment_data, type="setup_intent.succeeded")
            )
        self.assertEqual(tokenize_check_mock.call_count, 1)

    @mute_logger('odoo.addons.payment_stripe.controllers.main')
    def test_webhook_notification_triggers_signature_check(self):
        """ Test that receiving a webhook notification triggers a signature check. """
        self._create_transaction('redirect')
        url = self._build_url(StripeController._webhook_url)
        with patch(
            'odoo.addons.payment_stripe.controllers.main.StripeController._verify_signature'
        ) as signature_check_mock, patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._process'
        ):
            self._make_json_request(url, data=self.payment_data)
            self.assertEqual(signature_check_mock.call_count, 1)

    def test_onboarding_action_redirect_to_url(self):
        """ Test that the action generate and return an URL when the provider is disabled. """
        if country := self.env['res.country'].search([('code', 'in', list(const.SUPPORTED_COUNTRIES))], limit=1):
            self.env.company.country_id = country
        else:
            raise unittest.SkipTest("Unable to find a country supported by both odoo and stripe")

        with patch.object(
            type(self.env['payment.provider']), '_stripe_fetch_or_create_connected_account',
            return_value={'id': 'dummy'},
        ), patch.object(
            type(self.env['payment.provider']), '_stripe_create_account_link',
            return_value='https://dummy.url',
        ):
            onboarding_url = self.stripe.action_start_onboarding()
        self.assertEqual(onboarding_url['url'], 'https://dummy.url')

    def test_only_create_webhook_if_not_already_done(self):
        """ Test that a webhook is created only if the webhook secret is not already set. """
        self.stripe.stripe_webhook_secret = False
        with patch.object(self.env.registry['payment.provider'], '_send_api_request') as mock:
            self.stripe.action_stripe_create_webhook()
            self.assertEqual(mock.call_count, 1)

    def test_do_not_create_webhook_if_already_done(self):
        """ Test that no webhook is created if the webhook secret is already set. """
        self.stripe.stripe_webhook_secret = 'dummy'
        with patch.object(self.env.registry['payment.provider'], '_send_api_request') as mock:
            self.stripe.action_stripe_create_webhook()
            self.assertEqual(mock.call_count, 0)

    def test_create_account_link_pass_required_parameters(self):
        """ Test that the generation of an account link includes all the required parameters. """
        with patch.object(
            self.env.registry['payment.provider'], '_send_api_request',
            return_value={'url': 'https://dummy.url'},
        ) as mock:
            self.stripe._stripe_create_account_link('dummy', 'dummy')
            mock.assert_called_once()
            call_args = mock.call_args.kwargs['json']['params']['payload'].keys()
            for payload_param in ('account', 'return_url', 'refresh_url', 'type'):
                self.assertIn(payload_param, call_args)
