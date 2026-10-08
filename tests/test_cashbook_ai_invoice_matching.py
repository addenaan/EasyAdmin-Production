import json
import unittest
from unittest.mock import patch

from app_modules import cashbook_ai


class InvoiceReferenceTests(unittest.TestCase):
    def test_normalises_case_and_separators_without_dropping_leading_zeroes(self):
        self.assertEqual(cashbook_ai.normalise_invoice_reference(" inv-00 42 "), "INV0042")
        self.assertNotEqual(
            cashbook_ai.normalise_invoice_reference("INV-0042"),
            cashbook_ai.normalise_invoice_reference("INV-42"),
        )


class InvoicePaymentSuggestionTests(unittest.TestCase):
    def _invoice(self, invoice_id=7, number="INV-0042", outstanding="100.00", client="Test Client"):
        return {
            "invoice_id": invoice_id,
            "invoice_number": number,
            "outstanding_amount": outstanding,
            "client_name": client,
        }

    def _completed_response(self, matches):
        return {
            "id": "resp_test_123",
            "status": "completed",
            "model": "gpt-test",
            "output_text": json.dumps({"matches": matches}),
            "usage": {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
        }

    @patch("app_modules.cashbook_ai._request_json")
    def test_exact_reference_and_amount_match_without_calling_ai(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[
                {
                    "line_id": 11,
                    "transaction_date": "2026-10-01",
                    "description": "EFT PAYMENT INV 0042",
                    "credit": "100.00",
                    "debit": 0,
                },
                {
                    "line_id": 12,
                    "description": "DEBIT INV-0042",
                    "credit": 0,
                    "debit": "100.00",
                },
            ],
            outstanding_invoices=[self._invoice()],
        )

        request_json.assert_not_called()
        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(result["suggestions"][0]["invoice_id"], 7)
        self.assertEqual(result["suggestions"][0]["match_method"], "exact_reference")
        self.assertEqual(result["suggestions"][0]["confidence"], 1.0)
        self.assertEqual(result["usage"]["total_tokens"], 0)

    @patch("app_modules.cashbook_ai._request_json")
    def test_never_uses_amount_alone_or_combines_invoices(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[
                {"line_id": 21, "description": "CUSTOMER EFT", "credit": "100.00"},
                {"line_id": 22, "description": "INV-0100 AND INV-0200", "credit": "300.00"},
            ],
            outstanding_invoices=[
                self._invoice(invoice_id=1, number="INV-0001", outstanding="100.00"),
                self._invoice(invoice_id=2, number="INV-0100", outstanding="100.00"),
                self._invoice(invoice_id=3, number="INV-0200", outstanding="200.00"),
            ],
        )

        request_json.assert_not_called()
        self.assertEqual([item["invoice_id"] for item in result["suggestions"]], [None, None])
        self.assertTrue(all(item["match_method"] == "none" for item in result["suggestions"]))

    @patch("app_modules.cashbook_ai._request_json")
    def test_rejects_multiple_references_even_when_one_invoice_exactly_matches_amount(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{
                "line_id": 26,
                "description": "PAYMENT INV-0042 AND INV-0088",
                "credit": "100.00",
            }],
            outstanding_invoices=[
                self._invoice(invoice_id=7, number="INV-0042", outstanding="100.00"),
                self._invoice(invoice_id=8, number="INV-0088", outstanding="250.00"),
            ],
        )

        request_json.assert_not_called()
        self.assertIsNone(result["suggestions"][0]["invoice_id"])
        self.assertIn("more than one outstanding invoice", result["suggestions"][0]["reason"].lower())

    @patch("app_modules.cashbook_ai._request_json")
    def test_amount_must_equal_live_outstanding_balance_to_the_cent(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 31, "description": "INV-0042", "credit": "99.99"}],
            outstanding_invoices=[self._invoice(outstanding="100.00")],
        )

        request_json.assert_not_called()
        self.assertIsNone(result["suggestions"][0]["invoice_id"])

    @patch("app_modules.cashbook_ai._request_json")
    def test_noisy_reference_uses_structured_ai_and_revalidates_result(self, request_json):
        request_json.return_value = self._completed_response([
            {
                "line_id": 41,
                "invoice_id": 7,
                "confidence": 0.93,
                "reason": "Invoice number is present in a noisy reference.",
            }
        ])

        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{
                "line_id": 41,
                "transaction_date": "2026-10-01",
                "description": "CLIENT PAID INVOICE NO 0042",
                "credit": "100.00",
            }],
            outstanding_invoices=[self._invoice()],
        )

        request_json.assert_called_once()
        path, = request_json.call_args.args
        payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(path, "/responses")
        self.assertEqual(request_json.call_args.kwargs["method"], "POST")
        self.assertTrue(payload["text"]["format"]["strict"])
        sent = json.loads(payload["input"][0]["content"][0]["text"])
        self.assertEqual(sent["deposits"][0]["candidate_invoices"][0]["invoice_id"], 7)
        self.assertEqual(sent["deposits"][0]["candidate_invoices"][0]["outstanding_amount"], "100.00")
        self.assertEqual(result["suggestions"][0]["invoice_id"], 7)
        self.assertEqual(result["suggestions"][0]["match_method"], "ai_reference")
        self.assertEqual(result["response_id"], "resp_test_123")
        self.assertEqual(result["usage"]["total_tokens"], 42)

    @patch("app_modules.cashbook_ai._request_json")
    def test_ai_cannot_invent_an_invoice_id(self, request_json):
        request_json.return_value = self._completed_response([
            {"line_id": 51, "invoice_id": 999, "confidence": 0.99, "reason": "Invented"}
        ])

        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 51, "description": "INVOICE NO 0042", "credit": "100.00"}],
            outstanding_invoices=[self._invoice()],
        )

        self.assertIsNone(result["suggestions"][0]["invoice_id"])
        self.assertEqual(result["suggestions"][0]["match_method"], "none")

    @patch("app_modules.cashbook_ai._request_json")
    def test_low_confidence_ai_match_is_rejected(self, request_json):
        request_json.return_value = self._completed_response([
            {"line_id": 61, "invoice_id": 7, "confidence": 0.84, "reason": "Uncertain"}
        ])

        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 61, "description": "INVOICE NO 0042", "credit": "100.00"}],
            outstanding_invoices=[self._invoice()],
        )

        self.assertIsNone(result["suggestions"][0]["invoice_id"])

    @patch("app_modules.cashbook_ai._request_json")
    def test_same_invoice_is_not_suggested_for_two_deposits(self, request_json):
        request_json.return_value = self._completed_response([
            {"line_id": 71, "invoice_id": 7, "confidence": 0.96, "reason": "Reference found"},
            {"line_id": 72, "invoice_id": 7, "confidence": 0.95, "reason": "Reference found"},
        ])

        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[
                {"line_id": 71, "description": "INVOICE NO 0042 FIRST", "credit": "100.00"},
                {"line_id": 72, "description": "INVOICE NO 0042 SECOND", "credit": "100.00"},
            ],
            outstanding_invoices=[self._invoice()],
        )

        self.assertEqual([item["invoice_id"] for item in result["suggestions"]], [None, None])
        self.assertTrue(all("more than one deposit" in item["reason"].lower() for item in result["suggestions"]))

    @patch("app_modules.cashbook_ai._request_json")
    def test_duplicate_exact_deposits_fail_closed_without_ai(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[
                {"line_id": 81, "description": "INV-0042", "credit": "100.00"},
                {"line_id": 82, "description": "INV 0042", "credit": "100.00"},
            ],
            outstanding_invoices=[self._invoice()],
        )

        request_json.assert_not_called()
        self.assertEqual([item["invoice_id"] for item in result["suggestions"]], [None, None])

    @patch("app_modules.cashbook_ai._request_json")
    def test_ambiguous_duplicate_invoice_reference_fails_closed(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 86, "description": "PAYMENT INV-0042", "credit": "100.00"}],
            outstanding_invoices=[
                self._invoice(invoice_id=7),
                self._invoice(invoice_id=8),
            ],
        )

        request_json.assert_not_called()
        self.assertIsNone(result["suggestions"][0]["invoice_id"])
        self.assertIn("more than one outstanding invoice", result["suggestions"][0]["reason"].lower())

    @patch("app_modules.cashbook_ai._request_json")
    def test_duplicate_invoice_number_is_rejected_before_amount_filtering(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 87, "description": "PAYMENT INV-0042", "credit": "100.00"}],
            outstanding_invoices=[
                self._invoice(invoice_id=7, number="INV-0042", outstanding="100.00"),
                self._invoice(invoice_id=8, number="INV 0042", outstanding="250.00"),
            ],
        )

        request_json.assert_not_called()
        self.assertIsNone(result["suggestions"][0]["invoice_id"])
        self.assertIn("not unique", result["suggestions"][0]["reason"].lower())

    @patch("app_modules.cashbook_ai._request_json")
    def test_duplicate_model_rows_for_a_line_are_rejected(self, request_json):
        request_json.return_value = self._completed_response([
            {"line_id": 91, "invoice_id": 7, "confidence": 0.99, "reason": "First"},
            {"line_id": 91, "invoice_id": 7, "confidence": 0.99, "reason": "Second"},
        ])

        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[{"line_id": 91, "description": "INVOICE NO 0042", "credit": "100.00"}],
            outstanding_invoices=[self._invoice()],
        )

        self.assertIsNone(result["suggestions"][0]["invoice_id"])
        self.assertIn("duplicate", result["suggestions"][0]["reason"].lower())

    @patch("app_modules.cashbook_ai._request_json")
    def test_explicit_deposit_direction_is_supported_but_debit_conflicts_are_rejected(self, request_json):
        result = cashbook_ai.suggest_invoice_payments(
            model="gpt-test",
            deposits=[
                {"line_id": 101, "description": "INV-0042", "direction": "deposit", "amount": "100.00"},
                {
                    "line_id": 102,
                    "description": "INV-0042",
                    "direction": "deposit",
                    "amount": "100.00",
                    "debit": "100.00",
                },
            ],
            outstanding_invoices=[self._invoice()],
        )

        request_json.assert_not_called()
        self.assertEqual([item["line_id"] for item in result["suggestions"]], [101])
        self.assertEqual(result["suggestions"][0]["invoice_id"], 7)


if __name__ == "__main__":
    unittest.main()
