"""A reaction on our message is not the customer's turn: it must not stamp a lead."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "execution"))

from execution import rpgrq_webhook_server as srv  # noqa: E402


class IsReaction(unittest.TestCase):
    def test_a_thumbs_up_is_a_reaction(self):
        self.assertTrue(srv.is_reaction({"message": "#ATTACHMENT:reaction#👍"}))

    def test_any_text_key_is_read(self):
        self.assertTrue(srv.is_reaction({"text": "#attachment:reaction#❤️"}))

    def test_words_are_not_a_reaction(self):
        self.assertFalse(srv.is_reaction({"message": "Oh ok thank you"}))

    def test_other_attachments_are_messages(self):
        self.assertFalse(srv.is_reaction({"message": "#ATTACHMENT:location# |caption:"}))

    def test_a_button_tap_is_a_message(self):
        self.assertFalse(srv.is_reaction({"message": "#Button Reply#Chat with Human"}))

    def test_no_text_is_not_a_reaction(self):
        self.assertFalse(srv.is_reaction({}))


class HandleIncoming(unittest.TestCase):
    def run_incoming(self, text):
        payload = {"chat_id": "971500000001", "whatsapp_bot_id": "460585",
                   "whatsapp_bot_name": "Amara's Room", "message": text,
                   "wa_message_id": f"wamid.{text!r}"}
        srv.message_id_seen = srv.BoundedSet()
        with mock.patch.object(srv.notion, "find_ticket", new=mock.AsyncMock(return_value=None)) as find, \
             mock.patch.object(srv.grq_leads, "inbound", new=mock.AsyncMock(return_value=True)), \
             mock.patch.object(srv.notion, "create_ticket", new=mock.AsyncMock(return_value=None)):
            rr = mock.Mock(next_agent=mock.AsyncMock(return_value=None))
            asyncio.run(srv.handle_incoming(mock.Mock(), rr, payload))
        return find

    def test_a_reaction_never_reaches_the_ticket(self):
        self.assertFalse(self.run_incoming("#ATTACHMENT:reaction#👍").called)

    def test_a_message_still_does(self):
        self.assertTrue(self.run_incoming("Is it available?").called)


if __name__ == "__main__":
    unittest.main()
