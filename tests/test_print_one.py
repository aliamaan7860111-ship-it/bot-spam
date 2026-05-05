import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))


def _order(**overrides):
    base = {
        "page_id": "page-X",
        "order_id": "AM3030",
        "customer_name": "Test Customer",
        "phone": "0501234567",
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


class TestPrintOne(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from order_bridge import FULFILLMENT_GROUP_ID
        self.chat_id = FULFILLMENT_GROUP_ID or 0

    async def _run(self, order_lookup_result, fixture_order_id="AM3030"):
        from order_bridge import cmd_print_one
        with patch("order_bridge._resolve_order_id", return_value=order_lookup_result), \
             patch("order_bridge._lock_pages"), \
             patch("order_bridge._unlock_pages"), \
             patch("order_bridge._write_tracking_to_notion", return_value=["TRK001"]), \
             patch("order_bridge.get_filex_client") as mock_get_client, \
             patch("order_bridge._safe_send_message", new=AsyncMock()) as mock_msg, \
             patch("order_bridge._safe_send_document", new=AsyncMock()) as mock_doc:
            mock_client = MagicMock()
            mock_client.place_orders.return_value = {
                "data": "success",
                "trackingnos": [{"barcode": fixture_order_id, "tracking_no": "TRK001"}],
            }
            mock_client.get_label_pdf.return_value = b"%PDF-1.4 fake"
            mock_get_client.return_value = mock_client

            update = MagicMock()
            update.effective_chat.id = self.chat_id
            update.effective_user.id = 1
            context = MagicMock()
            await cmd_print_one(update, context, fixture_order_id)
            return mock_msg, mock_doc, mock_client

    async def test_not_found(self):
        mock_msg, mock_doc, mock_client = await self._run(None)
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("not found" in str(c).lower() for c in mock_msg.await_args_list))

    async def test_not_processed(self):
        mock_msg, mock_doc, mock_client = await self._run(_order(order_status="Confirmed"))
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("only \"Processed\"" in str(c) for c in mock_msg.await_args_list))

    async def test_already_labeled_refetch(self):
        mock_msg, mock_doc, mock_client = await self._run(
            _order(filex_status="Label Created", tracking_number="4866850001")
        )
        mock_client.place_orders.assert_not_called()
        mock_client.get_label_pdf.assert_called_once_with(["4866850001"])
        mock_doc.assert_awaited()

    async def test_fresh_placement(self):
        mock_msg, mock_doc, mock_client = await self._run(_order())
        mock_client.place_orders.assert_called_once()
        mock_client.get_label_pdf.assert_called_once_with(["TRK001"])
        mock_doc.assert_awaited()

    async def test_validation_failure_refuses(self):
        mock_msg, mock_doc, mock_client = await self._run(
            _order(full_address="Random no city here")
        )
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("missing city" in str(c).lower() for c in mock_msg.await_args_list))


if __name__ == "__main__":
    unittest.main()
