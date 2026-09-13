"""Validate list-valued Order.transactions exports without flattening the raw file."""
from .refund_capture import CaptureError, decode, gid
from .raw_records import iter_raw_records


def validate_order_transactions_file(source, identity, export):
    roots, transactions = set(), set()
    source.seek(0)
    for row in iter_raw_records(source, identity):
        order = decode(row["record_text"].encode())
        owner = gid(order.get("id"), "Order")
        if row["parent_gid"] is not None or owner in roots or not isinstance(order.get("transactions"), list):
            raise CaptureError("Invalid order transaction root")
        roots.add(owner)
        for transaction in order["transactions"]:
            identifier = gid(transaction.get("id"), "OrderTransaction")
            if identifier in transactions:
                raise CaptureError("Duplicate order transaction")
            transactions.add(identifier)
    if len(roots) != export.root_count or len(roots) != export.object_count:
        raise CaptureError("Order transaction export counts differ from provider")
    source.seek(0)
    return dict(record_count=len(roots), root_count=len(roots), transaction_count=len(transactions))
