"""Tests for the email_worker parsers — Timo, VNPay, UOB, and Grab."""

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


# ── VNPayParser ───────────────────────────────────────────────────────────────

_VNPAY_BODY = (
    "BIÊN LAI THANH TOÁN\n"
    "(Payment Receipt)\n"
    "Ngày, giờ giao dịch:\n"
    "Trans. Date, Time\n"
    "03/06/2026 20:53\n"
    "Mã giao dịch:\n"
    "Transaction ID\n"
    "21554029701129216\n"
    "Địa điểm giao dịch:\n"
    "Transaction location\n"
    "Ứng dụng VNPAY\n"
    "Tóm tắt giao dịch:\n"
    "Transaction summary\n"
    "Thanh Toán dịch vụ VNPAY\n"
    "Payment for VNPAY Service Payment\n"
    "- Số tiền giao dịch:\n"
    "Transaction amount\n"
    "183.000 VND\n"
    "- Phí giao dịch:\n"
    "Transaction fee\n"
    "0 VND\n"
    "- Số tiền thanh toán:\n"
    "Payment amount\n"
    "183.000 VND\n"
)


class TestVNPayParser:
    def setup_method(self):
        from email_worker.parsers.vnpay import VNPayParser

        self.parser = VNPayParser()

    def test_can_parse_by_sender(self):
        assert self.parser.can_parse("noreply@vnpayapp.vn", "", "") is True

    def test_can_parse_by_body(self):
        assert self.parser.can_parse("other@gmail.com", "", _VNPAY_BODY) is True

    def test_rejects_unrelated(self):
        assert self.parser.can_parse("noreply@grab.com", "", "hello") is False

    def test_amount(self):
        results = self.parser.parse("noreply@vnpayapp.vn", "Biên lai", _VNPAY_BODY, "")
        assert len(results) == 1
        assert results[0].amount == 183_000

    def test_date(self):
        results = self.parser.parse("noreply@vnpayapp.vn", "Biên lai", _VNPAY_BODY, "")
        assert results[0].date == date(2026, 6, 3)

    def test_description_includes_summary(self):
        results = self.parser.parse("noreply@vnpayapp.vn", "Biên lai", _VNPAY_BODY, "")
        assert results[0].description == "VNPay – Thanh Toán dịch vụ VNPAY"

    def test_type_is_expense(self):
        results = self.parser.parse("noreply@vnpayapp.vn", "Biên lai", _VNPAY_BODY, "")
        assert results[0].tx_type == "expense"

    def test_no_amount_returns_empty(self):
        results = self.parser.parse("noreply@vnpayapp.vn", "", "BIÊN LAI THANH TOÁN\nno amount here", "")
        assert results == []


# ── UOBParser: bill payment ────────────────────────────────────────────────────

_UOB_BILL_BODY = (
    "---------- Forwarded message ---------\n"
    "From: <unialerts@uobgroup.com>\n"
    "Subject: UOB Personal Internet Banking Notification Alerts\n"
    "\n"
    "You have made/scheduled bill payment(s) totaling VND 863784 at 08:44PM,"
    " 03/06/2026, VN Time. For assistance, call 1800 5999 21.\n"
)


class TestUOBBillPayment:
    def setup_method(self):
        from email_worker.parsers.uob import UOBParser

        self.parser = UOBParser()

    def test_can_parse_bill_body(self):
        assert self.parser.can_parse("nguyenmkhanhlinh@gmail.com", "", _UOB_BILL_BODY) is True

    def test_amount(self):
        results = self.parser.parse("unialerts@uobgroup.com", "", _UOB_BILL_BODY, "")
        assert len(results) == 1
        assert results[0].amount == 863_784

    def test_date(self):
        results = self.parser.parse("unialerts@uobgroup.com", "", _UOB_BILL_BODY, "")
        assert results[0].date == date(2026, 6, 3)

    def test_description(self):
        results = self.parser.parse("unialerts@uobgroup.com", "", _UOB_BILL_BODY, "")
        assert results[0].description == "UOB Bill Payment"

    def test_payment_method_is_bank_transfer(self):
        results = self.parser.parse("unialerts@uobgroup.com", "", _UOB_BILL_BODY, "")
        assert results[0].payment_method == "bank_transfer"


# ── GrabParser: food restaurant extraction ────────────────────────────────────

_GRAB_FOOD_BODY_MULTILINE = (
    "Chúc bạn ngon miệng!\n"
    "Tổng cộng\n\n\n59600₫\n"
    "Đặt từ\n\n\nPhở Bò Gánh Xưa\n"
    "Giao đến\nNhà\n"
    "Hồ sơ\nCá nhân\n"
    "05 May 26 11:57\n"
)

_GRAB_FOOD_BODY_INLINE = (
    "Chúc bạn ngon miệng!\nTổng cộng\n59600₫\nĐặt từ Phở Bò Gánh Xưa\nGiao đến Nhà\nHồ sơ\nCá nhân\n05 May 26 11:57\n"
)


class TestGrabFoodRestaurant:
    def setup_method(self):
        from email_worker.parsers.grab import GrabParser

        self.parser = GrabParser()

    def test_restaurant_extracted_multiline_format(self):
        results = self.parser._parse_food(_GRAB_FOOD_BODY_MULTILINE)
        assert len(results) == 1
        assert results[0].description == "Grab Food – Phở Bò Gánh Xưa"

    def test_restaurant_extracted_inline_format(self):
        results = self.parser._parse_food(_GRAB_FOOD_BODY_INLINE)
        assert len(results) == 1
        assert results[0].description == "Grab Food – Phở Bò Gánh Xưa"

    def test_food_amount_parsed(self):
        results = self.parser._parse_food(_GRAB_FOOD_BODY_MULTILINE)
        assert results[0].amount == 59_600

    def test_food_category_hint(self):
        results = self.parser._parse_food(_GRAB_FOOD_BODY_MULTILINE)
        assert results[0].category_hint == "Ăn uống"


# ── GrabParser: transport route description ───────────────────────────────────

_GRAB_RIDE_HTML = """
<html><body>
<table>
  <tr>
    <td><img alt="pick-up" src="x.png"></td>
    <td valign="top" style="padding-left:4px">
      <div style="line-height:24px;color:#1c1c1c">482/10/26F1 Nơ Trang Long</div>
      <div style="color:#676767;font-size:12px">6:18PM</div>
    </td>
  </tr>
  <tr>
    <td><img alt="drop-off" src="x.png"></td>
    <td valign="top" style="padding-left:4px">
      <div style="line-height:24px;color:#1c1c1c">Ốc Đào - Trường Sa</div>
      <div style="color:#676767;font-size:12px">6:34PM</div>
    </td>
  </tr>
</table>
</body></html>
"""

_GRAB_BIKE_BODY = (
    "Bike Plus\n"
    "Hy vọng bạn đã có một chuyến đi vui vẻ!\n"
    "Ngày đi 22 May 2026\n"
    "Tổng đã thanh toán VND 28.000\n"
    "Profile\nFAMILY\n"
)


class TestGrabTransportRoute:
    def setup_method(self):
        from email_worker.parsers.grab import GrabParser

        self.parser = GrabParser()

    def test_extract_route_returns_pickup_and_dropoff(self):
        pickup, dropoff = self.parser._extract_route(_GRAB_RIDE_HTML)
        assert pickup == "482/10/26F1 Nơ Trang Long"
        assert dropoff == "Ốc Đào - Trường Sa"

    def test_extract_route_empty_html_returns_empty(self):
        pickup, dropoff = self.parser._extract_route("")
        assert pickup == "" and dropoff == ""

    def test_transport_description_includes_route(self):
        results = self.parser._parse_transport(_GRAB_BIKE_BODY, _GRAB_RIDE_HTML)
        assert len(results) == 1
        desc = results[0].description
        assert "482/10/26F1 Nơ Trang Long" in desc
        assert "Ốc Đào - Trường Sa" in desc
        assert " - " in desc

    def test_transport_description_format(self):
        results = self.parser._parse_transport(_GRAB_BIKE_BODY, _GRAB_RIDE_HTML)
        assert results[0].description == ('Grab Bike Plus: "482/10/26F1 Nơ Trang Long" - "Ốc Đào - Trường Sa"')

    def test_transport_description_fallback_without_html(self):
        results = self.parser._parse_transport(_GRAB_BIKE_BODY, "")
        assert len(results) == 1
        assert results[0].description == "Grab Bike Plus"

    def test_amount_still_parsed_correctly(self):
        results = self.parser._parse_transport(_GRAB_BIKE_BODY, _GRAB_RIDE_HTML)
        assert results[0].amount == 28_000
