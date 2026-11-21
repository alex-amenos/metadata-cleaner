"""Keep/remove comments option. Run: pytest tests/test_comments.py"""
import io
import json
import shutil
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main

BASE = "http://127.0.0.1:8000"
OK = {"Origin": BASE, "X-MDC": "1"}
need = lambda tool: pytest.mark.skipif(shutil.which(tool) is None, reason=f"{tool} not installed")  # noqa: E731

PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Annots[4 0 R 5 0 R]>>endobj
4 0 obj<</Type/Annot/Subtype/Text/Rect[10 10 30 30]/Contents(secret comment)/T(Alice)>>endobj
5 0 obj<</Type/Annot/Subtype/Link/Rect[50 50 80 80]/Border[0 0 0]/A<</S/URI/URI(http://x.org)>>>>endobj
trailer<</Root 1 0 R/Size 6>>
%%EOF
"""


def docx() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("_rels/.rels", '<Relationships><Relationship Id="1" Type="x/thumbnail" Target="docProps/thumbnail.jpeg"/>'
                   '<Relationship Id="2" Type="x/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", "<w:document>Hello body</w:document>")
        z.writestr("word/comments.xml", "<w:comments>Bob says hi</w:comments>")
        z.writestr("docProps/core.xml", "<cp:coreProperties><dc:creator>Alice</dc:creator></cp:coreProperties>")
        z.writestr("docProps/thumbnail.jpeg", b"jpg")
    return b.getvalue()


def fetch(name, data, keep):
    """Clean one file and return the downloaded bytes (a single file is not zipped)."""
    c = TestClient(main.app, base_url=BASE)
    r = c.post("/clean", files={"files": (name, data)}, data={"keep_comments": str(keep).lower()}, headers=OK)
    assert r.status_code == 200, r.text
    d = c.get(f"/download/{r.json()['token']}", headers=OK)
    assert d.status_code == 200
    return d.content


def test_docx_keep_comments_keeps_comments_but_drops_properties():
    inner = zipfile.ZipFile(io.BytesIO(fetch("a.docx", docx(), True)))
    assert b"Bob says hi" in inner.read("word/comments.xml")
    assert b"Hello body" in inner.read("word/document.xml")
    assert b"Alice" not in inner.read("docProps/core.xml")
    assert "docProps/thumbnail.jpeg" not in inner.namelist()
    assert b"thumbnail" not in inner.read("_rels/.rels")


def valid_pdf(tmp_path) -> bytes:
    """qpdf rebuilds the missing xref table of the hand-written PDF above."""
    src, out = tmp_path / "in.pdf", tmp_path / "valid.pdf"
    src.write_bytes(PDF)
    subprocess.run(["qpdf", str(src), str(out)], capture_output=True)
    return out.read_bytes()


def annots(pdf: bytes, tmp_path):
    p = tmp_path / "o.pdf"
    p.write_bytes(pdf)
    d = json.loads(subprocess.run(["qpdf", "--json=2", "--json-key=qpdf", str(p)], capture_output=True, text=True).stdout)
    return [o["value"].get("/Subtype") for o in d["qpdf"][1].values()
            if isinstance(o.get("value"), dict) and o["value"].get("/Type") == "/Annot"]


@need("qpdf")
@need("exiftool")
@pytest.mark.parametrize("keep,expected", [(False, ["/Link"]), (True, ["/Link", "/Text"])])
def test_pdf_comments_option(tmp_path, keep, expected):
    pdf = fetch("a.pdf", valid_pdf(tmp_path), keep)
    assert sorted(annots(pdf, tmp_path)) == expected
