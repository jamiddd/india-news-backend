"""
Webhook verification/parsing for donations — app/services/donations.py.

The signature check is the only thing standing between an anonymous POST and
a row in the donations table, so it gets the most attention here. No DB
needed: the endpoint's persistence is a plain upsert on
uq_donations_provider_payment_id (see POST /payments/razorpay/webhook).
"""
import hashlib
import hmac
import json

import pytest

from app.config import settings
from app.services.donations import (
    CapturedPayment,
    MalformedWebhook,
    create_payment_link,
    parse_captured_payment,
    signature_matches,
)

SECRET = "whsec_test"


def signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    return raw, hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def capture(**entity) -> dict:
    return {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_1", "amount": 9900, "currency": "INR", **entity}}},
    }


class TestSignature:
    def test_accepts_a_correctly_signed_body(self):
        raw, signature = signed(capture())
        assert signature_matches(raw, signature, SECRET)

    def test_rejects_a_tampered_body(self):
        raw, signature = signed(capture())
        tampered = raw.replace(b'"amount": 9900', b'"amount": 1')
        assert not signature_matches(tampered, signature, SECRET)

    def test_rejects_wrong_secret(self):
        raw, signature = signed(capture())
        assert not signature_matches(raw, signature, "whsec_other")

    @pytest.mark.parametrize("signature", ["", None, "not-hex", "0" * 64])
    def test_rejects_missing_or_junk_signatures(self, signature):
        raw, _ = signed(capture())
        assert not signature_matches(raw, signature, SECRET)

    def test_is_byte_exact_not_json_equivalent(self):
        # Why the endpoint verifies request.body() rather than re-serializing
        # the parsed dict: the same JSON with different spacing is a different
        # digest, so a re-serialized body would fail its own signature.
        body = capture()
        raw, signature = signed(body)
        assert not signature_matches(json.dumps(body, indent=2).encode(), signature, SECRET)


class TestParsing:
    def test_extracts_a_captured_payment(self):
        assert parse_captured_payment(capture(notes={"user_id": "usr_abc"})) == CapturedPayment(
            provider_payment_id="pay_1", amount_paise=9900, currency="INR", user_id="usr_abc"
        )

    def test_anonymous_donation_has_no_user(self):
        assert parse_captured_payment(capture()).user_id is None
        assert parse_captured_payment(capture(notes={})).user_id is None

    def test_non_string_user_id_is_dropped_not_stored(self):
        assert parse_captured_payment(capture(notes={"user_id": 12})).user_id is None

    @pytest.mark.parametrize("event", ["payment.authorized", "payment.failed", "refund.created"])
    def test_non_capture_events_are_ignored_not_errors(self, event):
        # Ignored rather than rejected: a non-2xx would make Razorpay retry an
        # event that will never be stored.
        body = capture()
        body["event"] = event
        assert parse_captured_payment(body) is None

    @pytest.mark.parametrize("entity", [
        {"id": None},
        {"amount": None},
        {"amount": "9900"},
        {"amount": 0},
        {"amount": -100},
        {"amount": True},  # bool is an int subclass; must not become ₹0.01
    ])
    def test_unusable_capture_raises(self, entity):
        with pytest.raises(MalformedWebhook):
            parse_captured_payment(capture(**entity))

    def test_missing_payload_raises(self):
        with pytest.raises(MalformedWebhook):
            parse_captured_payment({"event": "payment.captured"})

    def test_currency_defaults_to_inr(self):
        body = capture()
        del body["payload"]["payment"]["entity"]["currency"]
        assert parse_captured_payment(body).currency == "INR"

    def test_oversized_fields_are_truncated_to_column_widths(self):
        parsed = parse_captured_payment(capture(id="p" * 200, notes={"user_id": "u" * 200}))
        assert len(parsed.provider_payment_id) == 128
        assert len(parsed.user_id) == 64


class TestPaymentLink:
    """Payment Links, not a hosted Payment Page: a Payment Page accepts no
    `notes`, so donations made through one arrive unattributable."""

    @pytest.fixture
    def keys(self, monkeypatch):
        monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_key")
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "secret")

    @pytest.fixture
    def capture_request(self, monkeypatch):
        """Records the outgoing call and returns a canned Razorpay response."""
        sent = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "plink_1", "short_url": "https://rzp.io/i/abc"}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None, auth=None):
                sent.update(url=url, body=json, auth=auth)
                return FakeResponse()

        monkeypatch.setattr("app.services.donations.httpx.AsyncClient", FakeClient)
        return sent

    @pytest.mark.asyncio
    async def test_returns_the_payable_short_url(self, keys, capture_request):
        assert await create_payment_link(9900, "usr_abc") == "https://rzp.io/i/abc"

    @pytest.mark.asyncio
    async def test_sends_amount_in_paise_with_the_user_id_in_notes(self, keys, capture_request):
        await create_payment_link(9900, "usr_abc")
        assert capture_request["body"]["amount"] == 9900
        assert capture_request["body"]["currency"] == "INR"
        assert capture_request["body"]["notes"] == {"user_id": "usr_abc"}
        assert capture_request["auth"] == ("rzp_test_key", "secret")

    @pytest.mark.asyncio
    async def test_anonymous_donation_sends_no_notes(self, keys, capture_request):
        await create_payment_link(4900, None)
        assert "notes" not in capture_request["body"]

    @pytest.mark.asyncio
    async def test_never_asks_razorpay_to_chase_the_donor(self, keys, capture_request):
        # We hold no verified contact details and have no business messaging
        # anyone who donates.
        await create_payment_link(4900, None)
        assert capture_request["body"]["notify"] == {"sms": False, "email": False}
        assert capture_request["body"]["reminder_enable"] is False
        assert capture_request["body"]["accept_partial"] is False

    @pytest.mark.asyncio
    async def test_returns_none_when_keys_are_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", None)
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", None)
        assert await create_payment_link(9900, None) is None

    @pytest.mark.asyncio
    async def test_returns_none_rather_than_raising_when_razorpay_fails(self, keys, monkeypatch):
        class ExplodingClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                raise RuntimeError("connection reset")

        monkeypatch.setattr("app.services.donations.httpx.AsyncClient", ExplodingClient)
        assert await create_payment_link(9900, "usr_abc") is None
