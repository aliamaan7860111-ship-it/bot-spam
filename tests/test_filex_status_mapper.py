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

if __name__ == "__main__":
    unittest.main()
