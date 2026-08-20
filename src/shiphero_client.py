"""Thin GraphQL client for the ShipHero public API.

Field names below are taken from ShipHero's published docs/examples
(developer.shiphero.com). If ShipHero changes their schema, GraphQL
Playground against your own account (Docs/Schema tab) is the source of
truth — this file is the one place to update field names if so.

Carried over unchanged from the Snow Commerce build: these fixes address
real ShipHero API quirks (not just Snow Commerce's account), so they apply
to Universal Parks' account as-is.
"""
import re
import time
from typing import Any, Optional

import requests


class ShipHeroClient:
    def __init__(self, access_token: str, graphql_url: str):
        self.graphql_url = graphql_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )

    def _post(self, query: str, variables: Optional[dict] = None, max_retries: int = 30) -> dict:
        """POST a GraphQL query, retrying with backoff if ShipHero throttles us
        for not having enough credits yet (code 30). ShipHero tells us exactly
        how long to wait, so we trust that instead of guessing.

        max_retries is generous (30) because on accounts with real production
        traffic, the credit pool is shared with everything else hitting the
        API — it can take a while for a large enough window to open up.
        """
        for attempt in range(max_retries + 1):
            resp = self.session.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            body = resp.json()

            errors = body.get("errors")
            if errors:
                throttle_error = next(
                    (e for e in errors if e.get("code") == 30), None
                )
                if throttle_error and attempt < max_retries:
                    wait_seconds = self._parse_wait_seconds(
                        throttle_error.get("time_remaining", "5 seconds")
                    )
                    if attempt % 5 == 0:  # don't spam the log every single retry
                        print(
                            f"  ShipHero credit limit hit (need {throttle_error.get('required_credits')}, "
                            f"have {throttle_error.get('remaining_credits')}) — "
                            f"waiting {wait_seconds}s before retrying (attempt {attempt + 1}/{max_retries})..."
                        )
                    time.sleep(wait_seconds + 2)
                    continue
                raise RuntimeError(f"ShipHero GraphQL error: {errors}")

            return body["data"]

        raise RuntimeError("ShipHero GraphQL request failed after retries")

    @staticmethod
    def _parse_wait_seconds(time_remaining: str) -> int:
        match = re.search(r"(\d+)", time_remaining)
        return int(match.group(1)) if match else 5

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_backordered_orders(
        self, customer_account_id: Optional[str], warehouse_id: Optional[str], order_date_from: str
    ) -> tuple[list[dict], int]:
        """Returns (orders, total_orders_scanned).

        orders = every order with at least one line item that has
        backorder_quantity > 0. Filters client-side since ShipHero doesn't
        expose a single 'backorder' fulfillment_status to filter on.
        total_orders_scanned = every pending order examined, regardless of
        whether it had a backordered item, so the report can show scale.

        customer_account_id / warehouse_id are left out of the query
        entirely when not provided — ShipHero's API rejects an explicit
        null for these rather than treating it as "don't filter."
        """
        var_decls = ["$order_date_from: ISODateTime", "$after: String"]
        args = ['order_date_from: $order_date_from', 'fulfillment_status: "pending"']
        variables: dict = {"order_date_from": order_date_from}

        if customer_account_id:
            var_decls.append("$customer_account_id: String")
            args.append("customer_account_id: $customer_account_id")
            variables["customer_account_id"] = customer_account_id
        if warehouse_id:
            var_decls.append("$warehouse_id: String")
            args.append("warehouse_id: $warehouse_id")
            variables["warehouse_id"] = warehouse_id

        query = f"""
        query BackorderedOrders({", ".join(var_decls)}) {{
          orders({", ".join(args)}) {{
            request_id
            complexity
            data(first: 5, after: $after) {{
              pageInfo {{ hasNextPage endCursor }}
              edges {{
                node {{
                  id
                  legacy_id
                  order_number
                  fulfillment_status
                  order_date
                  account_id
                  email
                  tags
                  line_items(first: 10) {{
                    edges {{
                      node {{
                        id
                        sku
                        product_name
                        quantity
                        quantity_allocated
                        backorder_quantity
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        orders = []
        total_scanned = 0
        after = None
        while True:
            variables["after"] = after
            data = self._post(query, variables)
            page = data["orders"]["data"]
            for edge in page["edges"]:
                node = edge["node"]
                total_scanned += 1
                backordered_items = [
                    li["node"]
                    for li in node["line_items"]["edges"]
                    if li["node"].get("backorder_quantity", 0) > 0
                ]
                if backordered_items:
                    node["backordered_line_items"] = backordered_items
                    orders.append(node)
            if page["pageInfo"]["hasNextPage"]:
                after = page["pageInfo"]["endCursor"]
            else:
                break
        return orders, total_scanned

    # ------------------------------------------------------------------
    # Purchase Orders
    # ------------------------------------------------------------------

    def get_open_purchase_order_skus(
        self, customer_account_id: Optional[str], warehouse_id: Optional[str]
    ) -> dict[str, list[dict]]:
        """Returns a dict of sku -> list of {po_number, po_id, qty_inbound,
        expected_date} for every open PO line item still awaiting receipt
        (quantity > quantity_received).

        customer_account_id / warehouse_id are left out of the query
        entirely when not provided — same reasoning as get_backordered_orders.
        """
        var_decls = ["$after: String"]
        args = []
        variables: dict = {}

        if customer_account_id:
            var_decls.append("$customer_account_id: String")
            args.append("customer_account_id: $customer_account_id")
            variables["customer_account_id"] = customer_account_id
        if warehouse_id:
            var_decls.append("$warehouse_id: String")
            args.append("warehouse_id: $warehouse_id")
            variables["warehouse_id"] = warehouse_id

        args_clause = f"({', '.join(args)})" if args else ""

        query = f"""
        query OpenPOs({", ".join(var_decls)}) {{
          purchase_orders{args_clause} {{
            request_id
            complexity
            data(first: 5, after: $after) {{
              pageInfo {{ hasNextPage endCursor }}
              edges {{
                node {{
                  id
                  po_number
                  fulfillment_status
                  po_date
                  line_items(first: 15) {{
                    edges {{
                      node {{
                        sku
                        quantity
                        quantity_received
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        sku_map: dict[str, list[dict]] = {}
        after = None
        while True:
            variables["after"] = after
            data = self._post(query, variables)
            page = data["purchase_orders"]["data"]
            for edge in page["edges"]:
                po = edge["node"]
                if po.get("fulfillment_status") == "closed":
                    continue
                for li_edge in po["line_items"]["edges"]:
                    li = li_edge["node"]
                    qty_inbound = (li.get("quantity") or 0) - (
                        li.get("quantity_received") or 0
                    )
                    if qty_inbound > 0:
                        sku_map.setdefault(li["sku"], []).append(
                            {
                                "po_id": po["id"],
                                "po_number": po["po_number"],
                                "po_date": po.get("po_date"),
                                "qty_inbound": qty_inbound,
                            }
                        )
            if page["pageInfo"]["hasNextPage"]:
                after = page["pageInfo"]["endCursor"]
            else:
                break
        return sku_map

    # ------------------------------------------------------------------
    # On-order lookup (per SKU, for the small final report list only)
    # ------------------------------------------------------------------

    def get_on_order_quantity(self, sku: str, warehouse_id: Optional[str]) -> int:
        """Returns the same "On Order" number ShipHero shows on the product
        page: the sum of (quantity - quantity_received - quantity_rejected)
        across all inbound PO line items for this SKU, floored at 0 per
        inbound. If warehouse_id is set, only that warehouse's inbounds are
        counted; otherwise all warehouses on the account are summed.

        Called once per SKU in the final report (a short list), not for
        every SKU in the account — so this stays cheap even without a
        warehouse filter.

        Fetches only the first 50 inbound line items per warehouse_product.
        That's enough headroom for this use case (a handful of open PO
        line items per SKU); if a SKU ever has more than that on one
        warehouse, a warning prints rather than silently under-counting.
        """
        query = """
        query ProductOnOrder($sku: String!) {
          product(sku: $sku) {
            request_id
            complexity
            data {
              sku
              warehouse_products {
                warehouse_id
                inbounds(first: 50) {
                  pageInfo { hasNextPage }
                  edges {
                    node {
                      quantity
                      quantity_received
                      quantity_rejected
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = self._post(query, {"sku": sku})
        product_data = data.get("product", {}).get("data")
        if not product_data:
            return 0  # SKU not found — shouldn't normally happen

        total_on_order = 0
        for wp in product_data.get("warehouse_products") or []:
            if warehouse_id and wp.get("warehouse_id") != warehouse_id:
                continue
            inbounds = wp.get("inbounds") or {}
            for edge in inbounds.get("edges", []):
                node = edge["node"]
                outstanding = (
                    (node.get("quantity") or 0)
                    - (node.get("quantity_received") or 0)
                    - (node.get("quantity_rejected") or 0)
                )
                if outstanding > 0:
                    total_on_order += outstanding
            if inbounds.get("pageInfo", {}).get("hasNextPage"):
                print(
                    f"  WARNING: SKU {sku} has more than 50 open inbound "
                    f"line items on one warehouse — on_order count may be "
                    f"understated."
                )

        return total_on_order
