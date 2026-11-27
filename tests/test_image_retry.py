"""A photo that fails for a moment is retried, not dropped from the album (JW1045, 2026-11-27)."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import telegram_client as tg  # noqa: E402

URL = "https://cdn.shopify.com/s/files/1/1/files/abc_800x.jpg?v=1"


def _client(monkeypatch, answers):
    """Patch httpx.Client so each GET pops the next (status, url-check) answer."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        status = answers.pop(0)
        return httpx.Response(status, content=b"img" if status == 200 else b"",
                              headers={"content-type": "image/jpeg"})

    real = httpx.Client
    monkeypatch.setattr(tg.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_a_502_then_success_is_retried(monkeypatch):
    seen = _client(monkeypatch, [502, 502, 200])
    assert tg.download_image(URL, waits=(0, 0)) == b"img"
    assert len(seen) == 3


def test_falls_back_to_the_full_size_original(monkeypatch):
    seen = _client(monkeypatch, [502, 502, 502, 200])
    assert tg.download_image(URL, waits=(0, 0)) == b"img"
    assert seen[-1] == "https://cdn.shopify.com/s/files/1/1/files/abc.jpg?v=1"


def test_a_404_is_not_retried(monkeypatch):
    seen = _client(monkeypatch, [404, 404])
    assert tg.download_image(URL, waits=(0, 0)) is None
    assert len(seen) == 2  # one try, then the original once


def test_original_url_only_for_resized_pictures():
    assert tg._original_image_url(URL) == "https://cdn.shopify.com/s/files/1/1/files/abc.jpg?v=1"
    assert tg._original_image_url("https://x/files/abc.png") is None
