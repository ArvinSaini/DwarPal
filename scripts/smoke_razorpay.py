"""One-time check against a real Razorpay TEST account: create one 1-rupee Payment Link, poll it, report
which lookup (order payments vs notes match) surfaces each attempt, cancel it if unpaid.

Usage:  python scripts/smoke_razorpay.py [--poll-timeout 180]
Needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (test keys) in .env or the environment. Uses one of the
account's ~30 test-mode links, so run it once, not in CI.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run from anywhere

from dwarpal.config import Settings  # noqa: E402
from dwarpal.payments import PaymentRequest, PaymentsError
from dwarpal.razorpay_client import RazorpayPayments


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--poll-timeout", type=int, default=180, help="seconds to wait for a payment (default 180)")
    ap.add_argument("--amount", type=int, default=100, help="amount in paise (default 100 = INR 1.00)")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if not settings.razorpay_configured:
        print("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set (test keys only).", file=sys.stderr)
        return 1
    try:
        pay = RazorpayPayments(settings.razorpay_key_id, settings.razorpay_key_secret)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    now = int(time.time())
    req = PaymentRequest("cs_smoke", "agt_smoke", "mnd_smoke", args.amount, "DwarPal smoke test", 1,
                         now + 20 * 60, f"smoke-{now}")
    try:
        link = pay.create_link(req)
    except PaymentsError as exc:
        print(f"create_link failed: {exc}", file=sys.stderr)
        return 1
    print(f"Payment Link {link.link_id}: {link.url}")
    print("Open it, choose Netbanking (or UPI success@razorpay / failure@razorpay if UPI is enabled),")
    print("and click Failure first, then Success, to see both attempts surface. Polling every 3 s...")

    seen: set[str] = set()
    deadline = time.time() + args.poll_timeout
    paid = False
    while time.time() < deadline:
        try:
            res = pay.poll(link.link_id)
        except PaymentsError as exc:
            print(f"  poll error: {exc}")
            time.sleep(3)
            continue
        for a in res.attempts:
            if a.payment_id in seen:
                continue
            seen.add(a.payment_id)
            via = "order payments" if res.order_id else "notes match"
            print(f"  attempt {a.payment_id}: {a.status} {a.amount_paise} paise "
                  f"{a.error_code or ''} {a.error_description or ''} (via {via})")
        if res.link_status == "paid":
            paid = True
            print("  link status: paid")
            break
        time.sleep(3)

    if not paid:
        try:
            pay.cancel_link(link.link_id)
            print("  unpaid after timeout: link cancelled")
        except PaymentsError as exc:
            print(f"  cancel failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
