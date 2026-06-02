"""Tests for the email_worker parsers — Timo and routing."""

from datetime import date

from email_worker.parsers.timo import TimoParser

DEBIT_BODY = (
    "Hi Vo Tran The Vi,\n\n"
    "Your Spend Account has been debited 37,000 VND on 02/06/2026 08:39.\n"
    "Your Current Balance is: 573,193 VND.\n\n"
    "Transaction Description: 7Eleven MXN 517557.\n\n"
    "Thank you for using Timo.\n"
)

CREDIT_BODY = (
    "Hi Vo Tran The Vi,\n\n"
    "Your Spend Account has been credited 165,000 VND on 31/05/2026 13:25.\n"
    "Your current balance is: 610,000 VND.\n\n"
    "Transaction Description: Sent by Nguyen Mai Khanh Linh from my Timo.\n\n"
    "Thank you for using Timo.\n"
)


class TestTimoParserCanParse:
    def test_matches_timo_sender(self):
        p = TimoParser()
        assert p.can_parse("support@timo.vn", "Other", "") is True

    def test_matches_debit_subject(self):
        p = TimoParser()
        assert p.can_parse("other@bank.com", "Debit Transaction Notice", "") is True

    def test_matches_credit_subject(self):
        p = TimoParser()
        assert p.can_parse("other@bank.com", "Credit Transaction Notice", "") is True

    def test_rejects_unrelated(self):
        p = TimoParser()
        assert p.can_parse("noreply@vcb.com.vn", "Account Statement", "") is False


class TestTimoParserDebit:
    def setup_method(self):
        self.parser = TimoParser()
        self.results = self.parser.parse("support@timo.vn", "Debit Transaction Notice", DEBIT_BODY, "")

    def test_returns_one_transaction(self):
        assert len(self.results) == 1

    def test_type_is_expense(self):
        assert self.results[0].tx_type == "expense"

    def test_amount(self):
        assert self.results[0].amount == 37_000

    def test_date(self):
        assert self.results[0].date == date(2026, 6, 2)

    def test_description(self):
        assert self.results[0].description == "7Eleven MXN 517557"

    def test_confidence(self):
        assert self.results[0].confidence >= 0.90


class TestTimoParserCredit:
    def setup_method(self):
        self.parser = TimoParser()
        self.results = self.parser.parse("support@timo.vn", "Credit Transaction Notice", CREDIT_BODY, "")

    def test_returns_one_transaction(self):
        assert len(self.results) == 1

    def test_type_is_income(self):
        assert self.results[0].tx_type == "income"

    def test_amount(self):
        assert self.results[0].amount == 165_000

    def test_date(self):
        assert self.results[0].date == date(2026, 5, 31)

    def test_description_contains_sender(self):
        assert "Nguyen Mai Khanh Linh" in self.results[0].description


class TestTimoParserEdgeCases:
    def test_no_match_returns_empty(self):
        p = TimoParser()
        result = p.parse("support@timo.vn", "Debit Transaction Notice", "Hello, nothing useful here.", "")
        assert result == []

    def test_amount_without_commas(self):
        body = (
            "Your Spend Account has been debited 80000 VND on 30/05/2026 10:25.\n"
            "Transaction Description: QRVCB payment.\n"
        )
        p = TimoParser()
        results = p.parse("support@timo.vn", "Debit Transaction Notice", body, "")
        assert len(results) == 1
        assert results[0].amount == 80_000
