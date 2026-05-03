import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_status_mapper import map_status

class TestStatusMapper(unittest.TestCase):
    def test_delivered_passthrough(self):
        self.assertEqual(map_status("Delivered", None), "Delivered")

    def test_to_be_picked_up_becomes_label_created(self):
        self.assertEqual(map_status("To be Picked Up", None), "Label Created")

    def test_received_at_hubs_becomes_handed_off(self):
        self.assertEqual(map_status("Received at Hubs", None), "Handed Off")

    def test_assigned_to_driver_becomes_shipped(self):
        self.assertEqual(map_status("Assigned to Driver", None), "Shipped")

    def test_in_ops_after_mna(self):
        self.assertEqual(map_status("In OPS", "MNA"), "MNA-OPS")

    def test_in_ops_after_sft(self):
        self.assertEqual(map_status("In OPS", "SFT"), "SFT-OPS")

    def test_in_ops_no_prior_status_falls_back(self):
        self.assertEqual(map_status("In OPS", "Shipped"), "In OPS")

    def test_unknown_status_passthrough(self):
        self.assertEqual(map_status("Some New Status", None), "Some New Status")

    def test_whitespace_in_input_handled(self):
        self.assertEqual(map_status(" In OPS ", "MNA"), "MNA-OPS")

    def test_receiver_cancelled_with_money(self):
        self.assertEqual(
            map_status("Receiver Cancelled With Money", None),
            "Receiver Cancelled With Money",
        )

    def test_in_ops_after_lc(self):
        self.assertEqual(map_status("In OPS", "LC"), "LC-OPS")

    def test_in_ops_after_fd(self):
        self.assertEqual(map_status("In OPS", "FD"), "FD-OPS")

    def test_in_ops_with_none_current_status(self):
        self.assertEqual(map_status("In OPS", None), "In OPS")

    def test_empty_string_input_returns_empty(self):
        self.assertEqual(map_status("", None), "")

    def test_none_input_returns_empty(self):
        self.assertEqual(map_status(None, None), "")

    def test_trailing_whitespace_on_direct_map_key(self):
        self.assertEqual(map_status("Delivered ", None), "Delivered")

    def test_case_sensitive_unknown_passes_through(self):
        # Documents current behavior: lowercase vendor drift will pass
        # through unchanged rather than match.
        self.assertEqual(map_status("delivered", None), "delivered")

if __name__ == "__main__":
    unittest.main()
