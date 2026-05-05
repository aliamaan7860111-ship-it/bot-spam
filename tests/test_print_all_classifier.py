import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))


_PHONE_COUNTER = [9000]


def _unique_phone():
    _PHONE_COUNTER[0] += 1
    return f"05012{_PHONE_COUNTER[0]:05d}"


def _order(**overrides):
    """A baseline valid Processed order with a unique phone (no merging)."""
    base = {
        "page_id": "page-" + overrides.get("order_id", "X"),
        "order_id": "AM3030",
        "customer_name": "Test Customer",
        "phone": _unique_phone(),
        "full_address": "Apt 1 Some Tower Dubai",
        "total": 100.00,
        "total_aed": None,
        "item_qty": "Item 1",
        "internal_note": "",
        "order_status": "Processed",
        "filex_status": None,
        "tracking_number": None,
        "filex_submitted": False,
    }
    base.update(overrides)
    return base


class TestPrintAllClassifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from order_bridge import FULFILLMENT_GROUP_ID
        # Use the env's group id so the chat-restriction gate passes.
        self.chat_id = FULFILLMENT_GROUP_ID or 0

    async def test_classifier_separates_already_labeled_validation_and_placeable(self):
        from order_bridge import cmd_print_all

        eligible = [
            _order(order_id="AM3001"),                                    # placeable
            _order(order_id="AM3002", filex_status="Label Created",
                   tracking_number="4866850001"),                          # already labeled
            _order(order_id="AM3003", full_address="Random no city here"), # validation skip: city
            _order(order_id="AM3004", total="AED"),                        # validation skip: total
        ]

        with patch("order_bridge.nc.query_filex_processed", return_value=eligible), \
             patch("order_bridge._lock_pages"), \
             patch("order_bridge._unlock_pages"), \
             patch("order_bridge._write_tracking_to_notion", return_value=["TRK001"]), \
             patch("order_bridge.get_filex_client") as mock_get_client, \
             patch("order_bridge._safe_send_message", new=AsyncMock()), \
             patch("order_bridge._safe_send_document", new=AsyncMock()) as mock_doc, \
             patch("order_bridge._send_long_message", new=AsyncMock()) as mock_long:

            mock_client = MagicMock()
            mock_client.place_orders.return_value = {"data": "success", "trackingnos": [
                {"barcode": "AM3001", "tracking_no": "TRK001"},
            ]}
            mock_client.get_label_pdf.return_value = b"%PDF-1.4 fake"
            mock_get_client.return_value = mock_client

            update = MagicMock()
            update.effective_chat.id = self.chat_id
            update.effective_user.id = 1
            context = MagicMock()
            context.args = ["all"]
            await cmd_print_all(update, context)

        # Validation skip body must mention BOTH categories.
        skip_calls = mock_long.await_args_list
        self.assertTrue(any("Missing city" in str(c) for c in skip_calls))
        self.assertTrue(any("Invalid total" in str(c) for c in skip_calls))
        # Already-labeled message must include AM3002 + tracking.
        self.assertTrue(any("AM3002" in str(c) and "4866850001" in str(c) for c in skip_calls))
        # Placement happened for AM3001.
        mock_client.place_orders.assert_called_once()
        called_payloads = mock_client.place_orders.call_args.args[0]
        self.assertEqual(len(called_payloads), 1)
        self.assertEqual(called_payloads[0]["ShipperRef"], "AM3001")
        # Final PDF was sent.
        mock_doc.assert_awaited()


if __name__ == "__main__":
    unittest.main()
