import copy
import json
import sqlite3
import unittest
from unittest import mock

from app_modules import bank_exports


CAPITEC_TEMPLATE_KEY = "capitec_business_acb_csv"


def _as_dict(row):
    return dict(row) if row is not None else {}


def _definition(template):
    data = _as_dict(template)
    value = data.get("definition", data.get("definition_json"))
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise AssertionError("Template did not contain a definition mapping.")
    return value


def _rendered_bytes(result, definition):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, bytes):
        return result
    return str(result).encode(definition.get("encoding") or "utf-8")


def _messages(result):
    if result is None:
        return []
    if isinstance(result, dict):
        values = result.get("errors", result.get("issues", []))
    else:
        values = result
    if not isinstance(values, (list, tuple)):
        values = [values]
    return [str(item.get("message", item) if isinstance(item, dict) else item) for item in values]


class BankExportTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bank_exports.ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def capitec_template(self):
        template = bank_exports.get_export_template(self.conn, CAPITEC_TEMPLATE_KEY)
        self.assertIsNotNone(template)
        return template

    def capitec_row(self, **changes):
        row = {
            "emp_number": "EMP0001",
            "name": "Vuyokazi Gwaluweyo",
            "account_holder": "Vuyokazi Gwaluweyo",
            "bank_name": "Capitec Bank",
            "branch_code": "470010",
            "account_number": "123456789",
            "account_type": "Savings",
            "net_salary": "4760",
            "payment_reference": "SALARYSEP2026",
            "beneficiary_reference": "EMP0001",
            "surname_initials": "GWALUWEYOV",
        }
        row.update(changes)
        return row


class SchemaAndSeedTests(BankExportTestCase):
    def test_schema_setup_is_idempotent_and_seeds_active_capitec_template(self):
        bank_exports.ensure_schema(self.conn)

        active = [_as_dict(item) for item in bank_exports.get_active_templates(self.conn)]
        capitec = [item for item in active if item.get("template_key") == CAPITEC_TEMPLATE_KEY]

        self.assertEqual(len(capitec), 1)
        self.assertIn("Capitec", capitec[0].get("display_name", capitec[0].get("bank_name", "")))
        self.assertTrue(bool(capitec[0].get("is_active", 1)))

        stored = _as_dict(self.capitec_template())
        self.assertEqual(stored["active_version"]["status"], "active")
        self.assertGreaterEqual(int(stored.get("version_number", 0)), 1)

    def test_seeded_capitec_definition_has_six_columns_and_no_header(self):
        definition = _definition(self.capitec_template())

        self.assertEqual(definition["delimiter"], ",")
        self.assertFalse(definition["include_header"])
        self.assertEqual(definition["file_extension"].lower(), ".csv")
        self.assertEqual(definition["line_ending"], "CRLF")
        self.assertEqual(len(definition["columns"]), 6)
        self.assertEqual(
            [column["source"] for column in definition["columns"]],
            [
                "branch_code",
                "account_number",
                "net_salary",
                "payment_reference",
                "beneficiary_reference",
                "surname_initials",
            ],
        )


class CapitecRenderingTests(BankExportTestCase):
    def test_renders_exact_capitec_csv_and_left_pads_account_number(self):
        definition = _definition(self.capitec_template())

        result = bank_exports.render_export_bytes(definition, [self.capitec_row()])

        self.assertEqual(
            _rendered_bytes(result, definition),
            b"470010,0000000123456789,4760.00,SALARYSEP2026,EMP0001,GWALUWEYOV\r\n",
        )

    def test_preserves_already_padded_account_number_and_two_decimal_amount(self):
        definition = _definition(self.capitec_template())
        row = self.capitec_row(
            account_number="0000123456789012",
            net_salary="999999.99",
            payment_reference="PAYMENT000000001",
            beneficiary_reference="BENEFICIARY00001",
            surname_initials="SURNAMEINITIALS1",
        )

        result = bank_exports.render_export(definition, [row])

        self.assertEqual(
            _rendered_bytes(result, definition),
            b"470010,0000123456789012,999999.99,PAYMENT000000001,BENEFICIARY00001,SURNAMEINITIALS1\r\n",
        )

    def test_valid_rows_have_no_validation_issues(self):
        definition = _definition(self.capitec_template())
        self.assertEqual(_messages(bank_exports.validate_rows(definition, [self.capitec_row()])), [])

    def test_rejects_invalid_capitec_rows_instead_of_generating_partial_batch(self):
        definition = _definition(self.capitec_template())
        invalid_rows = [
            self.capitec_row(branch_code="47001"),
            self.capitec_row(account_number="12A34"),
            self.capitec_row(net_salary="0.00"),
            self.capitec_row(net_salary="1000000.00"),
            self.capitec_row(payment_reference="PAY@REF"),
            self.capitec_row(name="", surname_initials=""),
        ]

        for row in invalid_rows:
            with self.subTest(row=row):
                issues = _messages(bank_exports.validate_rows(definition, [row]))
                self.assertTrue(issues)
                with self.assertRaises(ValueError):
                    bank_exports.render_export(definition, [self.capitec_row(), row])


class DefinitionSafetyTests(BankExportTestCase):
    def test_rejects_unknown_source_and_executable_expression(self):
        definition = _definition(self.capitec_template())
        for source in ("unknown_database_column", "__import__('os').system('whoami')"):
            candidate = copy.deepcopy(definition)
            candidate["columns"][0]["source"] = source
            with self.subTest(source=source):
                issues = _messages(bank_exports.validate_definition(candidate))
                self.assertTrue(issues)

    def test_rejects_unknown_transform_and_unsafe_delimiter(self):
        definition = _definition(self.capitec_template())

        unknown_format = copy.deepcopy(definition)
        unknown_format["columns"][0]["format"] = "execute_python"
        self.assertTrue(_messages(bank_exports.validate_definition(unknown_format)))

        unsafe_delimiter = copy.deepcopy(definition)
        unsafe_delimiter["delimiter"] = "||"
        self.assertTrue(_messages(bank_exports.validate_definition(unsafe_delimiter)))

    def test_accepts_the_structured_editor_boolean_padding_payload(self):
        definition = copy.deepcopy(_definition(self.capitec_template()))
        for column in definition["columns"]:
            column.setdefault("literal", "")
            column.setdefault("default", "")
            column.setdefault("exact_length", None)
            column.setdefault("max_length", None)
            column.setdefault("pad_left", False)
            column.setdefault("pad_character", "0")
            column.setdefault("truncate", False)
            column.setdefault("min_value", None)
            column.setdefault("max_value", None)

        self.assertEqual(_messages(bank_exports.validate_definition(definition)), [])

    def test_every_editor_source_field_is_available_from_a_production_shaped_row(self):
        definition = bank_exports.blank_definition()
        definition["columns"] = []
        for source in sorted(bank_exports.ALLOWED_SOURCE_FIELDS):
            column = {"header": source, "source": source, "format": "trim", "required": True}
            if source == "beneficiary_reference":
                column["default"] = "{company_name_compact}"
            definition["columns"].append(column)
        row = {
            "payslip_id": 1001,
            "date": "2026-10-31",
            "net_salary": "4760.00",
            "emp_number": "EMP0001",
            "name": "Vuyokazi Gwaluweyo",
            "id_passport": "9001015009087",
            "bank_name": "Capitec Bank",
            "account_holder": "Vuyokazi Gwaluweyo",
            "account_number": "123456789",
            "branch_code": "470010",
            "account_type": "Savings",
            "payment_reference": "SALARYOCT2026",
        }
        context = {
            "company_id": 14,
            "company_name": "AFY Enterprises",
            "period": "2026-10",
            "current_date": "2026-10-31",
        }

        self.assertEqual(bank_exports.validate_rows(definition, [row], context=context), [])

    def test_seeded_company_reference_defaults_are_not_bypassed(self):
        template = bank_exports.get_export_template(self.conn, "fnb_enterprise_csv")
        row = {
            "name": "Jane Example",
            "bank_name": "Example Bank",
            "account_holder": "Jane Example",
            "account_number": "1234567890",
            "branch_code": "250655",
            "account_type": "Current",
            "net_salary": "1234.56",
            "payment_reference": "Salary202610",
        }

        content = bank_exports.render_export(
            template["definition"],
            [row],
            context={"company_name": "AFY Enterprises", "period": "2026-10"},
        )

        self.assertIn(",AFY Enterprises Payroll\r\n", content)

    def test_beneficiary_reference_without_a_live_default_is_rejected(self):
        definition = copy.deepcopy(_definition(self.capitec_template()))
        beneficiary_column = next(
            column for column in definition["columns"]
            if column.get("source") == "beneficiary_reference"
        )
        beneficiary_column.pop("default", None)

        errors = _messages(bank_exports.validate_definition(definition))

        self.assertTrue(any("requires a default" in message for message in errors))

    def test_synthetic_preview_adapts_to_template_length_rules(self):
        definition = copy.deepcopy(_definition(self.capitec_template()))
        definition["columns"][0]["exact_length"] = 8
        definition["columns"][1]["exact_length"] = 12

        rows = bank_exports.synthetic_rows(definition)

        self.assertEqual(bank_exports.validate_rows(definition, rows), [])


class FilenameTests(BankExportTestCase):
    def test_build_filename_supports_company_month_and_date_aliases(self):
        definition = bank_exports.blank_definition()
        definition["filename_pattern"] = "payroll_{company}_{month}_{date}"

        filename = bank_exports.build_filename(
            definition,
            {
                "company_name": "AFY Enterprises",
                "period": "2026-10",
                "current_date": "2026-10-08",
            },
        )

        self.assertEqual(filename, "payroll_AFY_Enterprises_2026-10_2026-10-08.csv")


class TemplateVersioningTests(BankExportTestCase):
    def _save_draft(self, definition, note="Capitec format update", display_name="Capitec Business CSV"):
        return bank_exports.save_draft(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            definition,
            display_name=display_name,
            bank_name="Capitec Bank",
            change_note=note,
            actor="superadmin@example.test",
        )

    def test_draft_does_not_replace_active_version_until_tested_and_activated(self):
        original = _as_dict(self.capitec_template())
        definition = _definition(original)
        definition["filename_pattern"] = "Capitec_Payments_{month}.csv"

        draft = _as_dict(self._save_draft(definition))

        self.assertEqual(draft["status"], "draft")
        self.assertGreater(int(draft["version_number"]), int(original["version_number"]))
        self.assertEqual(
            int(_as_dict(self.capitec_template())["version_number"]),
            int(original["version_number"]),
        )

        tested = bank_exports.test_version(
            self.conn,
            int(draft["id"]),
            rows=[self.capitec_row()],
        )
        self.assertTrue(bool(_as_dict(tested).get("success", _as_dict(tested).get("valid"))))

        bank_exports.activate_version(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            int(draft["id"]),
            actor="superadmin@example.test",
        )
        active = _as_dict(self.capitec_template())
        self.assertEqual(int(active["active_version_id"]), int(draft["id"]))
        self.assertEqual(active["active_version"]["status"], "active")

    def test_rollback_restores_previous_active_version(self):
        original = _as_dict(self.capitec_template())
        definition = _definition(original)
        definition["filename_pattern"] = "Capitec_Updated_{month}.csv"
        draft = _as_dict(self._save_draft(definition, note="Bank specification revision"))
        bank_exports.test_version(self.conn, int(draft["id"]), rows=[self.capitec_row()])
        bank_exports.activate_version(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            int(draft["id"]),
            actor="superadmin@example.test",
        )

        bank_exports.rollback_template(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            actor="superadmin@example.test",
            change_note="Restore the previous approved bank format",
        )

        restored = _as_dict(self.capitec_template())
        self.assertEqual(int(restored["active_version_id"]), int(original["active_version_id"]))
        self.assertEqual(restored["active_version"]["status"], "active")

    def test_draft_metadata_is_not_published_until_activation_and_rolls_back(self):
        original = _as_dict(self.capitec_template())
        original_name = original["display_name"]
        definition = _definition(original)
        draft = _as_dict(
            self._save_draft(
                definition,
                note="Rename the bank template",
                display_name="Capitec New Import Name",
            )
        )

        self.assertEqual(_as_dict(self.capitec_template())["display_name"], original_name)
        self.assertEqual(draft["display_name"], "Capitec New Import Name")

        result = bank_exports.test_version(self.conn, int(draft["id"]))
        self.assertTrue(result["valid"])
        bank_exports.activate_version(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            int(draft["id"]),
            actor="superadmin@example.test",
        )
        self.assertEqual(_as_dict(self.capitec_template())["display_name"], "Capitec New Import Name")

        bank_exports.rollback_template(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            actor="superadmin@example.test",
            change_note="Restore the approved display name",
        )
        self.assertEqual(_as_dict(self.capitec_template())["display_name"], original_name)

    def test_failed_lifecycle_write_rolls_back_the_draft_and_event_together(self):
        definition = _definition(self.capitec_template())
        before_versions = len(bank_exports.get_versions(self.conn, CAPITEC_TEMPLATE_KEY))

        with mock.patch.object(bank_exports, "_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self._save_draft(definition, note="This write must roll back")

        self.assertEqual(len(bank_exports.get_versions(self.conn, CAPITEC_TEMPLATE_KEY)), before_versions)

    def test_disabled_template_is_hidden_but_history_is_retained(self):
        original = _as_dict(self.capitec_template())

        bank_exports.set_template_active(
            self.conn,
            CAPITEC_TEMPLATE_KEY,
            False,
            actor="superadmin@example.test",
        )

        active_keys = {
            _as_dict(item).get("template_key")
            for item in bank_exports.get_active_templates(self.conn)
        }
        self.assertNotIn(CAPITEC_TEMPLATE_KEY, active_keys)
        self.assertIsNone(bank_exports.get_export_template(self.conn, CAPITEC_TEMPLATE_KEY))
        stored = _as_dict(bank_exports.get_version(self.conn, int(original["active_version_id"])))
        self.assertEqual(int(stored["id"]), int(original["active_version_id"]))

    def test_draft_requires_an_update_note(self):
        definition = _definition(self.capitec_template())
        with self.assertRaises(ValueError):
            bank_exports.save_draft(
                self.conn,
                CAPITEC_TEMPLATE_KEY,
                definition,
                display_name="Capitec Business CSV",
                bank_name="Capitec Bank",
                change_note="",
                actor="superadmin@example.test",
            )


if __name__ == "__main__":
    unittest.main()
