"""Daily backorder-without-PO report: finds backordered orders where the
SKU has NO open replenishment PO at all, limited to SKUs affecting fewer
than a threshold number of orders (bigger, known stockouts are excluded).
Also looks up the live "On Order" quantity per SKU as an informational
column. Writes a CSV and emails it out.

Usage:
    python src/main.py             # full run
    python src/main.py --dry-run   # writes CSV, prints what it WOULD email
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
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
