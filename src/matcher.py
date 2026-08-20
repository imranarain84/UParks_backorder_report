"""Finds backordered line items whose SKU has NO open replenishment PO at
all — these are stockouts we haven't even ordered more inventory for.

SKUs affecting `max_orders_per_sku` or more distinct orders are excluded on
purpose: at that volume it's presumably already a known, actively-managed
issue. This report exists to surface the smaller, easy-to-miss ones.
"""


def find_no_po_matches(
    backordered_orders: list[dict],
    open_po_skus: dict[str, list[dict]],
    preorder_tag: str,
    max_orders_per_sku: int = 5,
) -> tuple[list[dict], dict]:
    """Returns (rows, breakdown).

    rows = one row per backordered line item whose SKU has no inbound PO,
    for SKUs affecting fewer than `max_orders_per_sku` distinct orders, on
    orders not already tagged `preorder_tag`.

    breakdown = counts at each filtering stage, so the email summary can
    show exactly where line items got excluded instead of just a start and
    end number with a big invisible gap in between.
    """
    total_line_items = 0
    skipped_already_tagged = 0
    skipped_has_open_po = 0
    candidates_by_sku: dict[str, list[dict]] = {}

    for order in backordered_orders:
        already_tagged = preorder_tag in (order.get("tags") or [])
        for line_item in order["backordered_line_items"]:
            total_line_items += 1
            if already_tagged:
                skipped_already_tagged += 1
                continue
            sku = line_item["sku"]
            if sku in open_po_skus:
                skipped_has_open_po += 1
                continue  # has replenishment inbound — not what we want here
            candidates_by_sku.setdefault(sku, []).append(
                {
                    "order_id": order["id"],
                    "order_number": order["order_number"],
                    "order_date": order.get("order_date"),
                    "customer_email": order.get("email"),
                    "sku": sku,
                    "product_name": line_item.get("product_name"),
                    "qty_backordered": line_item.get("backorder_quantity"),
                }
            )

    rows: list[dict] = []
    skus_excluded_over_threshold = 0
    line_items_excluded_over_threshold = 0
    for sku, items in candidates_by_sku.items():
        distinct_orders = {item["order_id"] for item in items}
        if len(distinct_orders) >= max_orders_per_sku:
            skus_excluded_over_threshold += 1
            line_items_excluded_over_threshold += len(items)
            continue  # too many affected orders — treat as already known/handled
        for item in items:
            item["orders_affected_for_sku"] = len(distinct_orders)
            rows.append(item)

    breakdown = {
        "total_line_items": total_line_items,
        "skipped_already_tagged": skipped_already_tagged,
        "skipped_has_open_po": skipped_has_open_po,
        "skus_excluded_over_threshold": skus_excluded_over_threshold,
        "line_items_excluded_over_threshold": line_items_excluded_over_threshold,
        "line_items_in_report": len(rows),
    }
    return rows, breakdown
