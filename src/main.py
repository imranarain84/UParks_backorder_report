"""Daily backorder-without-PO report: finds backordered orders where the
SKU has NO open replenishment PO at all, limited to SKUs affecting fewer
than a threshold number of orders (bigger, known stockouts are excluded).
Also looks up the live "On Order" quantity per SKU as an informational
column. Writes a CSV and emails it out.

Usage:
    python src/main.py               # full run
    python src/main.py --dry-run     # writes CSV, prints what it WOULD email
    python src/main.py --test-email  # sends a real, obviously-labeled test
                                      # email with a tiny sample CSV attached,
                                      # regardless of whether there are any
                                      # real backorders today. Use this to
                                      # confirm Gmail auth/delivery works
                                      # end-to-end before relying on the
                                      # daily cron.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Config
from shiphero_auth import get_access_token
from shiphero_client import ShipHeroClient
from matcher import find_no_po_matches
from csv_export import write_csv
from gmail_client import send_report_email


def send_test_email(cfg: "Config") -> None:
    """Sends a real email through the same Gmail send path the daily report
    uses, but with clearly-labeled fake/sample content. Doesn't touch
    ShipHero at all — this only exists to confirm Gmail OAuth + delivery
    work before the first real scheduled run.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    sample_rows = [
        {
            "order_number": "TEST-0001",
            "order_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_email": "test-customer@example.com",
            "sku": "TEST-SKU-001",
            "product_name": "Sample Product (test row — not a real order)",
            "qty_backordered": 1,
            "orders_affected_for_sku": 1,
            "on_order": 0,
            "order_id": "TEST-ID-0001",
        }
    ]

    csv_path = Path(f"output/TEST_universal_parks_backordered_report_{today}.csv")
    write_csv(sample_rows, csv_path)
    print(f"Wrote sample test CSV to {csv_path}")

    body = (
        f"=== THIS IS A TEST EMAIL — not a real backorder report ===\n"
        f"Sent to confirm Gmail auth/delivery works for the Universal Parks\n"
        f"backorder report before relying on the daily schedule.\n\n"
        f"Sent at:  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"The attached CSV contains one fake sample row, not real data.\n"
        f"If you received this, Gmail sending is working correctly."
    )

    print(f"Sending TEST email to {cfg.recipients}...")
    send_report_email(
        client_id=cfg.gmail_client_id,
        client_secret=cfg.gmail_client_secret,
        refresh_token=cfg.gmail_refresh_token,
        sender=cfg.gmail_sender_email,
        recipients=cfg.recipients,
        subject=f"[TEST] Vertical Passage x Universal Parks: Backorder Report - {today}",
        body_text=body,
        csv_path=csv_path,
    )
    print("Test email sent. Check the inbox for the recipients above.")


def run(dry_run: bool = False) -> None:
    cfg = Config()
    run_started = datetime.now(timezone.utc)

    print("Authenticating with ShipHero...")
    access_token = get_access_token(cfg.shiphero_refresh_token, cfg.shiphero_auth_url)
    client = ShipHeroClient(access_token, cfg.shiphero_graphql_url)

    order_date_from = (run_started - timedelta(days=cfg.lookback_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    print(f"Fetching backordered orders since {order_date_from}...")
    backordered_orders, total_orders_checked = client.get_backordered_orders(
        cfg.customer_account_id, cfg.warehouse_id, order_date_from
    )
    print(f"  checked {total_orders_checked} orders, "
          f"{len(backordered_orders)} have backordered line items")

    print("Fetching open purchase orders...")
    open_po_skus = client.get_open_purchase_order_skus(
        cfg.customer_account_id, cfg.warehouse_id
    )
    print(f"  found {len(open_po_skus)} SKUs with inbound replenishment")

    rows, breakdown = find_no_po_matches(
        backordered_orders, open_po_skus, cfg.preorder_tag, cfg.max_orders_per_sku
    )
    print(
        f"Line items: {breakdown['total_line_items']} total backordered -> "
        f"{breakdown['skipped_has_open_po']} have an open PO (skipped) -> "
        f"{breakdown['skipped_already_tagged']} already tagged '{cfg.preorder_tag}' (skipped) -> "
        f"{breakdown['line_items_excluded_over_threshold']} on SKUs with "
        f"{cfg.max_orders_per_sku}+ affected orders across "
        f"{breakdown['skus_excluded_over_threshold']} SKU(s) (skipped) -> "
        f"{breakdown['line_items_in_report']} remain in report"
    )

    run_finished = datetime.now(timezone.utc)
    today = run_started.strftime("%Y-%m-%d")

    if not rows:
        print("No matches today — nothing to email.")
        return

    skus_included = sorted({row["sku"] for row in rows})
    print(f"Looking up live 'On Order' quantity for {len(skus_included)} SKU(s)...")
    on_order_by_sku = {
        sku: client.get_on_order_quantity(sku, cfg.warehouse_id)
        for sku in skus_included
    }
    for row in rows:
        row["on_order"] = on_order_by_sku[row["sku"]]

    csv_path = Path(f"output/universal_parks_backordered_report_{today}.csv")
    write_csv(rows, csv_path)
    print(f"Wrote CSV to {csv_path}")

    order_numbers = sorted({row["order_number"] for row in rows})

    if dry_run:
        print(f"[dry-run] Would email CSV to: {cfg.recipients}")
        print(f"[dry-run] SKUs in report: {skus_included}")
        return

    print(f"Emailing report to {cfg.recipients}...")
    body = (
        f"=== Universal Parks — ShipHero Daily Backorder Summary ===\n"
        f"Date processed:          {today}\n"
        f"Run started:             {run_started.strftime('%H:%M:%S UTC')}\n"
        f"Run finished:            {run_finished.strftime('%H:%M:%S UTC')}\n"
        f"Total orders checked:    {total_orders_checked}\n"
        f"Total backorders found:  {len(rows)}\n"
        f"\n"
        f"Full detail attached as CSV."
    )
    send_report_email(
        client_id=cfg.gmail_client_id,
        client_secret=cfg.gmail_client_secret,
        refresh_token=cfg.gmail_refresh_token,
        sender=cfg.gmail_sender_email,
        recipients=cfg.recipients,
        subject=f"Vertical Passage x Universal Parks: Backorder Report - {today}",
        body_text=body,
        csv_path=csv_path,
    )
    print("Email sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a real test email with sample data, regardless of "
        "whether there are any actual backorders today. Does not query "
        "ShipHero at all.",
    )
    args = parser.parse_args()
    try:
        if args.test_email:
            send_test_email(Config())
        else:
            run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
