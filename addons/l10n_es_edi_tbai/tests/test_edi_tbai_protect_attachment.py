from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TestEsEdiTbaiCommonGipuzkoa


@tagged('post_install', '-at_install', 'post_install_l10n')
class TestTbaiProtectAttachment(TestEsEdiTbaiCommonGipuzkoa):

    def _send_invoice(self, invoice, response):
        invoice_send_wizard = self._get_invoice_send_wizard(invoice)
        with patch(
            'odoo.addons.l10n_es_edi_tbai.models.l10n_es_edi_tbai_document.requests.Session.request',
            return_value=response,
        ):
            invoice_send_wizard.action_send_and_print()

    def _send_invoice_expecting_error(self, invoice, response):
        # A bare try/except is used instead of assertRaises: the latter rolls back
        # the savepoint and the created document/attachment would be lost.
        try:
            self._send_invoice(invoice, response)
            raise AssertionError("A UserError should have been raised.")
        except UserError:
            pass

    def test_cannot_unlink_sent_document_xml(self):
        """The XML of a document sent to the government cannot be deleted."""
        invoice = self._create_posted_invoice()
        self._send_invoice(invoice, self.mock_response_post_invoice_success)

        attachment = invoice.l10n_es_tbai_post_document_id.xml_attachment_id
        self.assertTrue(attachment)

        with self.assertRaises(UserError):
            attachment.unlink()

        self.assertTrue(attachment.exists())

    def test_cannot_unlink_rejected_document_xml(self):
        """The XML is protected as soon as it is linked to a TicketBAI document."""
        invoice = self._create_posted_invoice()
        self._send_invoice_expecting_error(invoice, self.mock_response_failure)

        attachment = invoice.l10n_es_tbai_post_document_id.xml_attachment_id
        self.assertTrue(attachment)

        with self.assertRaises(UserError):
            attachment.unlink()

        self.assertTrue(attachment.exists())

    def test_cannot_unlink_cancel_document_xml(self):
        """The XML of a cancellation sent to the government cannot be deleted either."""
        invoice = self._create_posted_invoice()
        self._send_invoice(invoice, self.mock_response_post_invoice_success)

        with patch(
            'odoo.addons.l10n_es_edi_tbai.models.l10n_es_edi_tbai_document.requests.Session.request',
            return_value=self.mock_response_cancel_invoice_success,
        ):
            invoice.l10n_es_tbai_cancel()

        attachment = invoice.l10n_es_tbai_cancel_document_id.xml_attachment_id
        self.assertTrue(attachment)

        with self.assertRaises(UserError):
            attachment.unlink()

        self.assertTrue(attachment.exists())

    def test_unrelated_attachment_can_be_unlinked(self):
        """The constraint only applies to attachments linked to a TicketBAI document."""
        attachment = self.env['ir.attachment'].create({
            'name': 'unrelated.xml',
            'raw': b'<xml/>',
        })
        attachment.unlink()
        self.assertFalse(attachment.exists())

    def test_resending_rejected_invoice_still_works(self):
        """Resending a rejected invoice must not be blocked by the constraint."""
        invoice = self._create_posted_invoice()
        self._send_invoice_expecting_error(invoice, self.mock_response_failure)
        self.assertEqual(invoice.l10n_es_tbai_post_document_id.state, 'rejected')

        self._send_invoice(invoice, self.mock_response_post_invoice_success)

        self.assertEqual(invoice.l10n_es_tbai_state, 'sent')
        self.assertEqual(invoice.l10n_es_tbai_post_document_id.state, 'accepted')
        self.assertTrue(invoice.l10n_es_tbai_post_document_id.xml_attachment_id)
