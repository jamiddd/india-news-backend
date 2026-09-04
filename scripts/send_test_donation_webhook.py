"""
Fires a correctly-signed fake `payment.captured` at the donation webhook, so
the endpoint can be exercised without waiting on a real payment.

Signs the body with RAZORPAY_WEBHOOK_SECRET exactly as Razorpay does — HMAC
SHA256, hex, over the raw bytes — so a pass here means signature verification,
parsing and the upsert all work end to end.

Run it twice with the same --payment-id to prove idempotency: the second call
must still return 200 and must NOT create a second donations row.

Usage:
    python3 scripts/send_test_donation_webhook.py                       # local
    python3 scripts/send_test_donation_webhook.py --url https://openindiannews.com
    python3 scripts/send_test_donation_webhook.py --amount 4900 --user-id usr_abc123
    python3 scripts/send_test_donation_webhook.py --tamper   # must be rejected
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx

from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--amount", type=int, default=9900, help="Amount in paise")
    parser.add_argument("--user-id", default=None, help="Backend user id (usr_xxx) to attribute to")
    parser.add_argument("--payment-id", default=None, help="Reuse to test idempotency")
    parser.add_argument("--tamper", action="store_true",
                        help="Send a body that does not match the signature; must be rejected")
    args = parser.parse_args()

    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret:
        sys.exit("RAZORPAY_WEBHOOK_SECRET is not set — the endpoint would 503 anyway.")

    entity = {
        "id": args.payment_id or f"pay_test_{uuid.uuid4().hex[:12]}",
        "amount": args.amount,
        "currency": "INR",
    }
    if args.user_id:
        entity["notes"] = {"user_id": args.user_id}

    raw = json.dumps({"event": "payment.captured",
                      "payload": {"payment": {"entity": entity}}}).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    if args.tamper:
        # Signed one body, send another — exactly what an attacker replaying a
        # captured webhook with a bigger number would be doing.
        raw = raw.replace(b'"amount": %d' % args.amount, b'"amount": 1')

    response = httpx.post(
        f"{args.url.rstrip('/')}/payments/razorpay/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
        timeout=15.0,
    )
    print(f"payment_id : {entity['id']}")
    print(f"status     : {response.status_code}")
    print(f"body       : {response.text}")

    if args.tamper and response.status_code == 200:
        sys.exit("FAIL: a tampered body was accepted.")
    if not args.tamper and response.status_code != 200:
        sys.exit("FAIL: a correctly signed webhook was rejected.")
    print("OK")


if __name__ == "__main__":
    main()
