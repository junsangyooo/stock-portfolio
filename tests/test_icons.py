"""Product icons (ui-open-decisions B8): served from memory on fixed routes,
immutable-cached, transparent favicon and opaque touch icon."""
import io

from PIL import Image
from fastapi.testclient import TestClient

from src.dashboard_web import server

client = TestClient(server.app)

ROUTES = {
    "/favicon.ico": "image/x-icon",
    "/favicon-32.png": "image/png",
    "/favicon-64.png": "image/png",
    "/apple-touch-icon.png": "image/png",
}


def test_icon_routes():
    for path, ctype in ROUTES.items():
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"] == ctype, path
        assert len(r.content) > 100, path
        cc = r.headers["cache-control"]
        assert "immutable" in cc and "no-cache" not in cc, (path, cc)


def test_favicon_is_transparent_outside_the_artwork():
    for path, size in (("/favicon-32.png", 32), ("/favicon-64.png", 64)):
        im = Image.open(io.BytesIO(client.get(path).content))
        assert im.size == (size, size) and im.mode == "RGBA", path
        a = im.getchannel("A")
        assert all(a.getpixel(c) == 0 for c in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)))
        assert a.getextrema()[1] == 255, "some fully opaque artwork"
    ico = Image.open(io.BytesIO(client.get("/favicon.ico").content))
    assert ico.format == "ICO" and (16, 16) in ico.info["sizes"] and (32, 32) in ico.info["sizes"]


def test_apple_touch_icon_is_opaque_180():
    im = Image.open(io.BytesIO(client.get("/apple-touch-icon.png").content))
    assert im.size == (180, 180)
    assert "A" not in im.getbands() or im.getchannel("A").getextrema() == (255, 255)
    assert im.convert("RGB").getpixel((0, 0)) == (110, 127, 147)   # slate, no white corner
