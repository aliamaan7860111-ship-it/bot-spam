"""/print all from Discord: the channel sink, and the signed /print-all endpoint (2026-10-28)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import discord_client as dc  # noqa: E402
import order_bridge as ob  # noqa: E402


class Sink(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.p = mock.patch.object(dc.ChannelSink, "_post", lambda _self, data, files=None: self.posts.append((data, files)) or True)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_short_text_is_one_message_that_cannot_ping(self):
        asyncio.run(dc.ChannelSink().send_message(123, "✅ Placed 3 orders"))
        self.assertEqual(self.posts, [({"content": "✅ Placed 3 orders", "allowed_mentions": {"parse": []}}, None)])

    def test_long_text_goes_as_a_file(self):
        text = "\n".join(f"AM {i} - missing city in address" for i in range(200))
        asyncio.run(dc.ChannelSink().send_message(1, text))
        data, files = self.posts[0]
        self.assertLessEqual(len(data["content"]), 2000)
        self.assertIn("full list attached", data["content"])
        self.assertEqual(files["files[0]"][1].decode(), text)

    def test_a_pdf_with_its_caption(self):
        asyncio.run(dc.ChannelSink().send_document(1, document=b"%PDF", filename="labels.pdf", caption="✅ Placed 2"))
        data, files = self.posts[0]
        self.assertEqual(data["content"], "✅ Placed 2")
        self.assertEqual(files["files[0]"][:2], ("labels.pdf", b"%PDF"))


def request(body: str, secret: str | None):
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest() if secret else ""
    text = f"POST /print-all HTTP/1.1\r\nHost: x\r\nX-GRQ-Signature: {sig}\r\nContent-Length: {len(body)}\r\n\r\n{body}"
    return text.split("\r\n"), text


class PrintEndpoint(unittest.TestCase):
    def run_start(self, body, secret, env_secret="s3cret", locked=False):
        async def go():
            started = []
            with mock.patch.dict(os.environ, {"GRQ_OS_INGEST_SECRET": env_secret}), \
                 mock.patch.object(ob, "discord_print_all", new=mock.AsyncMock(side_effect=lambda by, what="all": started.append(by))):
                if locked:
                    await ob._PRINT_LOCK.acquire()
                try:
                    lines, text = request(body, secret)
                    res = await ob._start_discord_print(lines, text)
                    await asyncio.sleep(0)
                finally:
                    if locked:
                        ob._PRINT_LOCK.release()
            return res, started
        return asyncio.run(go())

    def test_a_signed_request_starts_a_run(self):
        (status, out), started = self.run_start(json.dumps({"by": "Ahmed"}), "s3cret")
        self.assertEqual(status, "202 Accepted")
        self.assertEqual(out, {"ok": True, "started": True})
        self.assertEqual(started, ["Ahmed"])

    def test_a_wrong_or_missing_signature_starts_nothing(self):
        for secret in ("wrong", None):
            (status, _), started = self.run_start(json.dumps({"by": "x"}), secret)
            self.assertEqual(status, "401 Unauthorized")
            self.assertEqual(started, [])

    def test_without_a_secret_on_the_vm_nothing_can_start_it(self):
        (status, _), started = self.run_start("{}", "", env_secret="")
        self.assertEqual(status, "401 Unauthorized")
        self.assertEqual(started, [])

    def test_a_second_run_while_one_is_going_is_refused(self):
        (status, out), started = self.run_start(json.dumps({"by": "x"}), "s3cret", locked=True)
        self.assertEqual(status, "409 Conflict")
        self.assertEqual(out["busy"], True)
        self.assertEqual(started, [])


class TelegramWaitsToo(unittest.TestCase):
    def test_telegram_print_all_refuses_while_a_discord_run_is_going(self):
        sent = []

        async def go():
            bot = mock.Mock()
            bot.send_message = mock.AsyncMock(side_effect=lambda chat, text: sent.append(text))
            update = mock.Mock(effective_chat=mock.Mock(id=ob.FULFILLMENT_GROUP_ID or 1), effective_user=mock.Mock(id=9))
            with mock.patch.object(ob, "run_print_all", new=mock.AsyncMock()) as run:
                await ob._PRINT_LOCK.acquire()
                try:
                    await ob.cmd_print_all(update, mock.Mock(bot=bot))
                finally:
                    ob._PRINT_LOCK.release()
                return run
        run = asyncio.run(go())
        run.assert_not_called()
        self.assertTrue(any("already running" in t for t in sent))


if __name__ == "__main__":
    unittest.main()


class PvtFromDiscord(unittest.TestCase):
    def test_what_pvt_starts_the_pvt_run(self):
        async def go():
            calls = []
            body = json.dumps({"by": "Ahmed", "what": "pvt"})
            with mock.patch.dict(os.environ, {"GRQ_OS_INGEST_SECRET": "s3cret"}), \
                 mock.patch.object(ob, "discord_print_all", new=mock.AsyncMock(side_effect=lambda by, what: calls.append((by, what)))):
                lines, text = request(body, "s3cret")
                res = await ob._start_discord_print(lines, text)
                await asyncio.sleep(0)
            return res, calls
        (status, _), calls = asyncio.run(go())
        self.assertEqual(status, "202 Accepted")
        self.assertEqual(calls, [("Ahmed", "pvt")])

    def test_the_pvt_run_says_when_there_is_nothing_to_label(self):
        sent = []

        async def go():
            sink = mock.Mock()
            sink.send_message = mock.AsyncMock(side_effect=lambda chat, text: sent.append(text))
            with mock.patch.object(ob, "_TJR_AVAILABLE", True), \
                 mock.patch.object(ob, "_run_private_driver_labels", new=mock.AsyncMock(return_value={"pdf": None, "skipped": [], "label_error": None})):
                await ob.run_pvt_all(sink, 0)
        asyncio.run(go())
        self.assertIn("No new private-driver orders to label.", sent)
