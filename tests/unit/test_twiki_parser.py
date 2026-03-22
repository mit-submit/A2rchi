# tests/unit/test_twiki_parser.py
from pathlib import Path
from scrapy.http import HtmlResponse, Request
from src.data_manager.collectors.scrapers.spiders.twiki import parse_twiki_page

FIXTURES = Path(__file__).parent / "fixtures"

def fake_html_response(url: str, fixture_name: str) -> HtmlResponse:
    body = (FIXTURES / fixture_name).read_bytes()
    return HtmlResponse(url=url, body=body, encoding="utf-8", request=Request(url=url))

class TestParseTwikiPage:
    def test_prefers_topic_title(self):
        response = fake_html_response(
            "https://twiki.cern.ch/twiki/bin/view/CMSPublic/CRAB3ConfigurationFile",
            "twiki_twiki_bin_view_cmspublic_crab3_configuration_file.html",
        )
        item = next(parse_twiki_page(response))
        assert item["title"] == "CRAB3ConfigurationFile"
