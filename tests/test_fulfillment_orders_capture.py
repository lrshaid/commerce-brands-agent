import json
import unittest
from unittest.mock import patch

from agent.warehouse.fulfillment_orders_capture import FulfillmentOrdersCapture
from tests.test_inventory_capture import Bucket, connection, response


SOURCE = """query FulfillmentOrdersBulk { fulfillmentOrders { edges { node {
  id order { id } status requestStatus createdAt updatedAt fulfillAt fulfillBy
  assignedLocation { location { id } } destination { city province countryCode zip }
  deliveryMethod { methodType } lineItems(first: 50) { edges { node {
    id inventoryItemId totalQuantity remainingQuantity lineItem { id }
  } } }
} } } }"""


class FulfillmentOrdersCaptureTests(unittest.TestCase):
    def capture(self):
        return FulfillmentOrdersCapture(
            bucket=Bucket(), domain="test.myshopify.com", token="private",
            api_version="2026-04", shop_gid="gid://shopify/Shop/1",
            extraction_id="run", query_source=SOURCE,
            window_start="2026-09-01T00:00:00Z", window_end="2026-09-10T00:00:00Z",
            page_size=2)

    def test_collects_only_window_orders_and_all_line_pages(self):
        capture = self.capture()
        orders = [
            {"id": "gid://shopify/FulfillmentOrder/2", "order": {"id": "gid://shopify/Order/2"},
             "updatedAt": "2026-09-11T00:00:00Z"},
            {"id": "gid://shopify/FulfillmentOrder/1", "order": {"id": "gid://shopify/Order/1"},
             "updatedAt": "2026-09-05T00:00:00Z"},
        ]
        lines = [{"id": "gid://shopify/FulfillmentOrderLineItem/10"}]
        with patch.object(capture, "_http", side_effect=[
                response({"fulfillmentOrders": connection(orders, True, "next")}),
                response({"node": {"id": "gid://shopify/FulfillmentOrder/1",
                                    "lineItems": connection(lines, False, None)}}),
                response({"fulfillmentOrders": connection([
                    {"id": "gid://shopify/FulfillmentOrder/0",
                     "order": {"id": "gid://shopify/Order/0"},
                     "updatedAt": "2026-08-31T00:00:00Z"}], False, None)}),
        ]):
            seal = capture.collect()
        self.assertEqual(seal["counts"], {"fulfillmentOrders": 1, "lineItems": 1})
        self.assertEqual([page["operation"] for page in seal["pages"]],
                         ["fulfillmentOrders", "lineItems", "fulfillmentOrders"])


if __name__ == "__main__":
    unittest.main()
