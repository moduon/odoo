# Part of Odoo. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from werkzeug.exceptions import Forbidden

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_adyen import utils as adyen_utils
from odoo.addons.payment_adyen.controllers.main import AdyenController
from odoo.addons.payment_adyen.tests.common import AdyenCommon


@tagged('post_install', '-at_install')
class AdyenTest(AdyenCommon, PaymentHttpCommon):

    def test_processing_values(self):
        tx = self._create_transaction(flow='direct')
        with mute_logger('odoo.addons.payment.models.payment_transaction'), \
            patch(
                'odoo.addons.payment.utils.generate_access_token',
                new=self._generate_test_access_token
            ):
            processing_values = tx._get_processing_values()

        converted_amount = 111111
        self.assertEqual(
            payment_utils.to_minor_currency_units(self.amount, self.currency),
            converted_amount,
        )
        self.assertEqual(processing_values['converted_amount'], converted_amount)
        with patch(
            'odoo.addons.payment.utils.generate_access_token', new=self._generate_test_access_token
        ):
            self.assertTrue(payment_utils.check_access_token(
                processing_values['access_token'], self.reference, converted_amount, self.currency.id, self.partner.id
            ))

    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def test_send_refund_request(self):
        self.provider.support_refund = 'full_only'  # Should simply not be False
        tx = self._create_transaction(
            'redirect', state='done', provider_reference='source_reference'
        )
        tx._post_process()  # Create the payment

        # Send the refund request
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            new=lambda *args, **kwargs: {'pspReference': "refund_reference", 'status': "received"}
        ):
            tx._refund()

        refund_tx = self.env['payment.transaction'].search([('source_transaction_id', '=', tx.id)])
        self.assertTrue(
            refund_tx,
            msg="Refunding an Adyen transaction should always create a refund transaction."
        )
        self.assertTrue(
            refund_tx.state == 'draft',
            msg="A refund request as been made, but the state of the refund tx stays as 'draft' "
                "until a success notification is sent"
        )
        self.assertNotEqual(
            refund_tx.provider_reference,
            tx.provider_reference,
            msg="The provider reference of the refund transaction should be different from that of "
                "the source transaction."
        )

    def test_search_by_reference_returns_refund_tx(self):
        source_tx = self._create_transaction(
            'direct', state='done', provider_reference=self.original_reference
        )
        refund_tx = self._create_transaction(
            'direct',
            reference='RefundTx',
            provider_reference=self.psp_reference,
            amount=-source_tx.amount,
            operation='refund',
            source_transaction_id=source_tx.id
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(
                    source_tx.amount, refund_tx.currency_id
                ),
            },
            eventCode='REFUND',
        )
        returned_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertEqual(returned_tx, refund_tx, msg="The existing refund tx is the one returned")

    def test_search_by_reference_creates_refund_tx_when_missing(self):
        source_tx = self._create_transaction(
            'direct', state='done', provider_reference=self.original_reference
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(self.amount, source_tx.currency_id),
            },
            eventCode='REFUND',
        )
        refund_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertTrue(
            refund_tx,
            msg="If no refund tx is found with received refund data, a refund tx should be created"
        )
        self.assertNotEqual(refund_tx, source_tx)
        self.assertEqual(refund_tx.source_transaction_id, source_tx)

    def test_search_by_reference_returns_partial_capture_child_tx(self):
        self.provider.capture_manually = True
        source_tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference
        )
        capture_tx = self._create_transaction(
            'direct',
            reference='CaptureTx',
            provider_reference=self.psp_reference,
            amount=source_tx.amount-10,
            operation=source_tx.operation,
            source_transaction_id=source_tx.id,
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(
                    source_tx.amount-10, capture_tx.currency_id
                ),
            },
            eventCode='CAPTURE',
        )
        returned_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertEqual(returned_tx, capture_tx, msg="The existing capture tx is the one returned")

    def test_search_by_reference_creates_capture_tx_when_missing(self):
        self.provider.capture_manually = True
        source_tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(
                    self.amount - 10, source_tx.currency_id
                ),
            },
            eventCode='CAPTURE',
        )
        capture_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertTrue(
            capture_tx,
            msg="If no child tx is found with received capture data, a child tx should be created.",
        )
        self.assertNotEqual(capture_tx, source_tx)
        self.assertEqual(capture_tx.source_transaction_id, source_tx)

    def test_search_by_reference_returns_void_tx(self):
        self.provider.capture_manually = True
        source_tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference
        )
        cancel_tx = self._create_transaction(
            'direct',
            reference='CancelTx',
            provider_reference=self.psp_reference,
            amount=source_tx.amount - 10,
            operation=source_tx.operation,
            source_transaction_id=source_tx.id,
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(
                    source_tx.amount - 10, cancel_tx.currency_id
                ),
            },
            eventCode='CANCELLATION',
        )
        returned_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertEqual(returned_tx, cancel_tx, msg="The existing void tx is the one returned")

    def test_search_by_reference_creates_void_tx_when_missing(self):
        self.provider.capture_manually = True
        source_tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference
        )
        data = dict(
            self.webhook_notification_payload,
            amount={
                'currency': self.currency.name,
                'value': payment_utils.to_minor_currency_units(
                    self.amount - 10, source_tx.currency_id
                ),
            },
            eventCode='CANCELLATION',
        )
        void_tx = self.env['payment.transaction']._search_by_reference('adyen', data)
        self.assertTrue(
            void_tx,
            msg="If no child tx is found with received void data, a child tx should be created."
        )
        self.assertNotEqual(void_tx, source_tx)
        self.assertEqual(void_tx.source_transaction_id, source_tx)

    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def test_tx_state_after_send_full_capture_request(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('direct', state='authorized')

        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={'status': 'received'},
        ):
            tx._capture()
        self.assertEqual(
            tx.state,
            'authorized',
            msg="A capture request as been made, but the state of the transaction stays as "
                "'authorized' until a success notification is sent",
        )

    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def test_tx_state_after_partial_capture_request(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('direct', state='authorized')

        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={'status': 'received'},
        ):
            tx._capture(amount_to_capture=10)
        self.assertEqual(
            tx.state,
            'authorized',
            msg="A partial capture request as been made, but the state of the source transaction "
                "stays as 'authorized' until the full amount is either done or canceled.",
        )
        self.assertEqual(
            tx.child_transaction_ids[0].state,
            'draft',
            msg="A partial capture request as been made, but the state of the child transaction "
                "stays as 'draft' until a success notification is sent.",
        )

    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def test_tx_state_after_send_void_request(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('direct', state='authorized')

        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={'status': 'received'},
        ):
            tx._void()
        self.assertEqual(
            tx.state,
            'authorized',
            msg="A void request as been made, but the state of the transaction stays as"
                " 'authorized' until a success notification is sent",
        )

    def test_extract_token_values_maps_fields_correctly(self):
        tx = self._create_transaction('direct')
        payment_data = {
            'additionalData': {
                'recurring.recurringDetailReference': 'token_reference',
                'cardSummary': '4242',
                'recurring.shopperReference': 'partner_reference',
            },
        }
        token_values = tx._extract_token_values(payment_data)
        self.assertDictEqual(token_values, {
            'provider_ref': 'token_reference',
            'payment_details': '4242',
            'adyen_shopper_reference': 'partner_reference',
        })

    def test_webhook_notification_confirms_transaction(self):
        tx = self._create_transaction('direct')
        self._webhook_notification_flow(self.webhook_notification_batch_data)
        self.assertEqual(tx.state, 'done')

    def test_webhook_notification_authorizes_transaction(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('direct')
        self._webhook_notification_flow(self.webhook_notification_batch_data)
        self.assertEqual(
            tx.state,
            'authorized',
            msg="The authorization succeeded, the manual capture is enabled, the tx state should be"
                " 'authorized'.",
        )

    def test_webhook_notification_captures_transaction(self):
        self.provider.capture_manually = True
        tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference, amount=9.99
        )
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload,
                amount={
                    'currency': self.currency.name,
                    'value': payment_utils.to_minor_currency_units(9.99, tx.currency_id),
                },
                eventCode='CAPTURE',
            )
        }])
        self._webhook_notification_flow(payload)
        self.assertEqual(
            tx.state, 'done',
            msg="The capture succeeded, the tx state should be 'done'.",
        )

    def test_webhook_notification_cancels_transaction(self):
        tx = self._create_transaction(
            'direct', state='pending', provider_reference=self.original_reference, amount=9.99
        )
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload,
                amount={
                    'currency': self.currency.name,
                    'value': payment_utils.to_minor_currency_units(9.99, tx.currency_id),
                },
                eventCode='CANCELLATION',
            )
        }])
        self._webhook_notification_flow(payload)
        self.assertEqual(
            tx.state,
            'cancel',
            msg="The cancellation succeeded, the tx state should be 'cancel'.",
        )

    def test_webhook_notification_refunds_transaction(self):
        source_tx = self._create_transaction(
            'direct', state='done', provider_reference=self.original_reference
        )
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload,
                amount={
                    'currency': self.currency.name,
                    'value': payment_utils.to_minor_currency_units(
                        self.amount, source_tx.currency_id
                    ),
                },
                eventCode='REFUND',
            )
        }])
        self._webhook_notification_flow(payload)
        refund_tx = self.env['payment.transaction'].search(
            [('source_transaction_id', '=', source_tx.id)]
        )
        self.assertEqual(
            refund_tx.state,
            'done',
            msg="After a successful refund notification, the refund state should be in 'done'.",
        )

    def test_failed_webhook_authorization_notification_leaves_transaction_in_draft(self):
        tx = self._create_transaction('direct')
        payload = dict(self.webhook_notification_batch_data, notificationItems=[
            {'NotificationRequestItem': dict(self.webhook_notification_payload, success='false')}
        ])
        self._webhook_notification_flow(payload)
        self.assertEqual(
            tx.state, 'draft',
            msg="The authorization failed, as we don't support failed authorization, the tx state "
                "should still be 'draft'.",
        )

    def test_failed_webhook_capture_notification_leaves_transaction_authorized(self):
        tx = self._create_transaction(
            'direct', state='authorized', provider_reference=self.original_reference
        )
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload, eventCode='CAPTURE', success='false'
            )
        }])
        self._webhook_notification_flow(payload)
        self.assertEqual(
            tx.state, 'authorized',
            msg="The capture failed, the tx state should still be 'authorized'.",
        )

    def test_failed_webhook_cancellation_notification_leaves_transaction_authorized(self):
        tx = self._create_transaction('direct', state='authorized')
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload, eventCode='CANCELLATION', success='false'
            )
        }])
        self._webhook_notification_flow(payload)
        self.assertEqual(
            tx.state, 'authorized',
            msg="The cancellation failed, the tx state should still be 'authorized'.",
        )

    def test_failed_webhook_refund_notification_sets_refund_transaction_in_error(self):
        source_tx = self._create_transaction(
            'direct', state='done', provider_reference=self.original_reference
        )
        payload = dict(self.webhook_notification_batch_data, notificationItems=[{
            'NotificationRequestItem': dict(
                self.webhook_notification_payload,
                amount={
                    'currency': self.currency.name,
                    'value': payment_utils.to_minor_currency_units(
                        self.amount, source_tx.currency_id
                    ),
                },
                eventCode='REFUND',
                success='false',
            )
        }])
        self._webhook_notification_flow(payload)
        refund_tx = self.env['payment.transaction'].search([
            ('source_transaction_id', '=', source_tx.id)]
        )
        self.assertEqual(
            refund_tx.state,
            'error',
            msg="After a failed refund notification, the refund state should be in 'error'.",
        )

    @mute_logger('odoo.addons.payment_adyen.controllers.main')
    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def _webhook_notification_flow(self, payload):
        """ Send a notification to the webhook, ignore the signature, and check the response. """
        url = self._build_url(AdyenController._webhook_url)
        with patch('odoo.addons.payment_adyen.controllers.main.AdyenController._verify_signature'):
            response = self._make_json_request(url, data=payload).json()
        self.assertEqual(
            response, '[accepted]', msg="The webhook should always respond '[accepted]'",
        )

    @mute_logger('odoo.addons.payment_adyen.controllers.main')
    def test_webhook_notification_triggers_signature_check(self):
        """ Test that receiving a webhook notification triggers a signature check. """
        self._create_transaction('direct')
        url = self._build_url(AdyenController._webhook_url)
        with patch(
            'odoo.addons.payment_adyen.controllers.main.AdyenController._verify_signature'
        ) as signature_check_mock, patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._process'
        ):
            self._make_json_request(url, data=self.webhook_notification_batch_data)
            self.assertEqual(signature_check_mock.call_count, 1)

    def test_accept_webhook_notification_with_valid_signature(self):
        """ Test the verification of a webhook notification with a valid signature. """
        tx = self._create_transaction('direct')
        self._assert_does_not_raise(
            Forbidden, AdyenController._verify_signature, self.webhook_notification_payload, tx
        )

    @mute_logger('odoo.addons.payment_adyen.controllers.main')
    def test_reject_webhook_notification_with_missing_signature(self):
        """ Test the verification of a webhook notification with a missing signature. """
        payload = dict(self.webhook_notification_payload, additionalData={'hmacSignature': None})
        tx = self._create_transaction('direct')
        self.assertRaises(Forbidden, AdyenController._verify_signature, payload, tx)

    @mute_logger('odoo.addons.payment_adyen.controllers.main')
    def test_reject_webhook_notification_with_invalid_signature(self):
        """ Test the verification of a webhook notification with an invalid signature. """
        payload = dict(self.webhook_notification_payload, additionalData={'hmacSignature': 'dummy'})
        tx = self._create_transaction('direct')
        self.assertRaises(Forbidden, AdyenController._verify_signature, payload, tx)

    @mute_logger('odoo.addons.payment_adyen.models.payment_transaction')
    def test_no_information_missing_from_partner_address(self):
        test_partner = self.env['res.partner'].create({
            'name': 'Dummy Partner',
            'email': 'norbert.buyer@example.com',
            'phone': '0032 12 34 56 78',
        })
        test_address = adyen_utils.format_partner_address(test_partner)
        for key in ('city', 'country', 'stateOrProvince', 'street',):
            self.assertTrue(test_address.get(key))
