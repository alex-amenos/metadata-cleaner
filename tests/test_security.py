"""Security regression tests. Run: pip install pytest httpx2 pillow && pytest"""
import io
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import main

BASE = "http://127.0.0.1:8000"
OK = {"Origin": BASE, "X-MDC": "1"}


@pytest.fixture()
def client():
    return TestClient(main.app, base_url=BASE)


def jpeg() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(b, "JPEG")
    return b.getvalue()


need = lambda tool: pytest.mark.skipif(shutil.which(tool) is None, reason=f"{tool} not installed")  # noqa: E731


def test_dns_rebinding_host_is_rejected(client):
    assert client.get("/tools", headers={"Host": "evil.example:8000"}).status_code == 403
    assert client.get("/tools").status_code == 200


def test_cross_site_post_is_rejected(client):
    f = {"files": ("a.jpg", b"x")}
    assert client.post("/inspect", files=f, headers={"Origin": "http://evil.example", "X-MDC": "1"}).status_code == 403
    assert client.post("/inspect", files=f, headers={"Origin": BASE}).status_code == 403  # no custom header
    assert client.post("/clean", files=f).status_code == 403  # no Origin
    assert client.post("/inspect", files=f, headers=OK).status_code == 200


def test_too_many_files_rejected(client):
    files = [("files", (f"{i}.jpg", b"x")) for i in range(main.MAX_FILES + 1)]
    assert client.post("/inspect", files=files, headers=OK).status_code == 413


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert client.get("/docs").status_code == 404


@need("exiftool")
def test_errors_do_not_leak_paths_or_content(client):
    r = client.post("/inspect", files={"files": ("secret-name.png", b"not an image SECRETCONTENT")}, headers=OK)
    text = r.text
    assert "/tmp" not in text and "SECRETCONTENT" not in text and "input." not in text


@need("exiftool")
def test_clean_and_download_with_awkward_filename(client):
    name = 'we"ird;ñame.jpg'
    r = client.post("/clean", files={"files": (name, jpeg())}, headers=OK)
    assert r.status_code == 200
    d = client.get("/download/" + r.json()["token"])
    assert d.status_code == 200
    assert "filename*=UTF-8''" in d.headers["content-disposition"]
    assert client.get("/download/" + r.json()["token"]).status_code == 404  # single use


@need("ffmpeg")
@need("exiftool")
def test_ffmpeg_playlist_disguised_as_video_is_not_followed(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    playlist = f"ffconcat version 1.0\nfile '{secret}'\n".encode()
    r = client.post("/clean", files={"files": ("x.mp4", playlist)}, headers=OK)
    assert r.status_code == 422
    assert "TOPSECRET" not in r.text


@need("ffmpeg")
@need("exiftool")
def test_real_mp4_still_cleans(client, tmp_path):
    mp4 = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                    "-metadata", "title=Secret", str(mp4)], check=True)
    r = client.post("/clean", files={"files": ("in.mp4", mp4.read_bytes())}, headers=OK)
    assert r.status_code == 200, r.text
    after = r.json()["files"]["in.mp4"]["after"]
    assert not any("Title" in k for k in after)


def test_expired_results_are_swept(tmp_path):
    d = tmp_path / "mdclean-x"
    d.mkdir()
    main.RESULTS["t"] = {"dir": d, "files": [], "errors": {}, "created": time.time() - main.RESULT_TTL - 1}
    main.sweep()
    assert "t" not in main.RESULTS and not d.exists()
