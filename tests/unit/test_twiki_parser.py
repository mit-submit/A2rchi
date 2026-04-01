# tests/unit/test_twiki_parser.py
from pathlib import Path

from scrapy.http import HtmlResponse, Request, Response

from src.data_manager.collectors.scrapers.parsers.twiki import parse_twiki_page

FIXTURES = Path(__file__).parent / "fixtures"

def fake_html_response(url: str, fixture_name: str, charset: str) -> HtmlResponse:
    body = (FIXTURES / fixture_name).read_bytes()
    headers = {}
    if charset:
        headers[b"Content-Type"] = [f"text/html; charset={charset}".encode("ascii")]
    # No `encoding=`: let Scrapy infer from headers + HTML meta (like a real download).
    return HtmlResponse(
        url=url,
        status=200,
        body=body,
        headers=headers,
        request=Request(url=url),
    )

class TestParseTwikiPage:

    def test_conventional_twiki_page(self):
        response = fake_html_response(
            "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            "twiki_twiki_bin_view_cmspublic_crab3_configuration_file.html",
            "iso-8859-1",
        )
        item = next(parse_twiki_page(response))
        assert item['title'] == "CRAB3ConfigurationFile"
        assert item['suffix'] == "html"
        assert item['source_type'] == "web"
        assert item['content_type'] == "text/html; charset=iso-8859-1"
        assert item['encoding'] == "cp1252"
        # HTML fragment (outer tag + children), not flattened text — for MarkItDown etc.
        assert "<" in item["content"] and ">" in item["content"]
        assert "patternMainContents" in item["content"]
        assert "href=" in item["content"]

    def test_pdf_yields_bytes_like_link_parser(self):
        url = "https://twiki.cern.ch/twiki/pub/CMSPublic/Topic/file.pdf"
        response = Response(
            url=url,
            body=b"%PDF-1.4 minimal",
            headers={b"Content-Type": [b"application/pdf"]},
            request=Request(url=url),
        )
        item = next(parse_twiki_page(response))
        assert item["suffix"] == "pdf"
        assert item["content"] == b"%PDF-1.4 minimal"
        assert item["title"] == "file"