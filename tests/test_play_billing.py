"""What the verify-purchase endpoint tells the client, and when.

The client treats a definite {"valid": false} as "reject" and an error/absent
answer as "could not check". These tests pin which Google outcomes land in
which bucket: only failures that say nothing about the token (429, 5xx,
timeouts) may become "could not check"; a permission or bad-token failure must
stay a definite rejection.
"""
import pytest
from fastapi import HTTPException

from app.services import play_billing
from app.services.play_billing import verify_purchase


class _Resp:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def _patch_google(monkeypatch, resp=None, exc=None):
    monkeypatch.setattr(play_billing, "_get_session", lambda: object())

    def fake_get(session, url):
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(play_billing, "_get_with_retry", fake_get)


@pytest.mark.parametrize("product_type", ["subs", "inapp"])
class TestVerifyPurchase:
    async def test_active_purchase_is_valid(self, monkeypatch, product_type):
        body = ({"subscriptionState": "SUBSCRIPTION_STATE_ACTIVE"} if product_type == "subs"
                else {"purchaseState": 0})
        _patch_google(monkeypatch, _Resp(200, body))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert result.valid and not result.transient

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_google_rate_limit_or_5xx_is_transient(self, monkeypatch, product_type, status):
        _patch_google(monkeypatch, _Resp(status, text="boom"))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert not result.valid and result.transient

    @pytest.mark.parametrize("status", [401, 403])
    async def test_our_permission_errors_are_transient(self, monkeypatch, product_type, status):
        _patch_google(monkeypatch, _Resp(status, text="insufficient permissions"))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert not result.valid and result.transient

    @pytest.mark.parametrize("status", [400, 404])
    async def test_bad_token_errors_stay_a_definite_rejection(self, monkeypatch, product_type, status):
        _patch_google(monkeypatch, _Resp(status, text="nope"))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert not result.valid and not result.transient

    async def test_timeout_is_transient(self, monkeypatch, product_type):
        _patch_google(monkeypatch, exc=TimeoutError("read timed out"))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert not result.valid and result.transient

    async def test_inactive_state_is_a_definite_rejection(self, monkeypatch, product_type):
        body = ({"subscriptionState": "SUBSCRIPTION_STATE_EXPIRED"} if product_type == "subs"
                else {"purchaseState": 1})
        _patch_google(monkeypatch, _Resp(200, body))
        result = await verify_purchase("premium_x", "tok", product_type)
        assert not result.valid and not result.transient


async def test_unknown_product_type_is_a_definite_rejection(monkeypatch):
    _patch_google(monkeypatch, _Resp(200))
    result = await verify_purchase("premium_x", "tok", "other")
    assert not result.valid and not result.transient


class TestEndpoint:
    async def _call(self, monkeypatch, verification):
        from app import main

        async def fake_verify(*args, **kwargs):
            return verification

        monkeypatch.setattr(main, "verify_purchase", fake_verify)
        payload = main.VerifyPurchaseRequest(
            product_id="premium_x", purchase_token="tok", product_type="subs")
        # limiter.limit wraps the route; call the undecorated function.
        fn = getattr(main.verify_play_purchase, "__wrapped__", main.verify_play_purchase)
        return await fn(request=None, payload=payload)

    async def test_transient_failure_answers_503_not_false(self, monkeypatch):
        v = play_billing.PurchaseVerification(False, "lookup failed: 503", transient=True)
        with pytest.raises(HTTPException) as e:
            await self._call(monkeypatch, v)
        assert e.value.status_code == 503

    async def test_definite_rejection_answers_valid_false(self, monkeypatch):
        v = play_billing.PurchaseVerification(False, "lookup failed: 401")
        assert (await self._call(monkeypatch, v)).valid is False

    async def test_valid_purchase_answers_valid_true(self, monkeypatch):
        v = play_billing.PurchaseVerification(True, "purchased")
        assert (await self._call(monkeypatch, v)).valid is True
