from scrapy import Item, Field
 
class ArchiBaseItem(Item):
    """Fields shared by every source type."""
    url = Field()          # canonical URL of the page
    content = Field()      # str (HTML/Markdown/text) or bytes (PDF)
    suffix = Field()       # "html" | "pdf" | "md" | ...
    source_type = Field()  # "web" | "sso" | "git" | ...
    title = Field()        # page title, may be empty

class WebPageItem(ArchiBaseItem):
    """Item produced by the plain-Link spider."""
    content_type = Field()   # value of Content-Type response header
    encoding = Field()       # response encoding (e.g. "utf-8")

class TestTWikiItem(WebPageItem):
    """Item produced by the trivial Twiki spider."""
    body_length = Field()
    body_preview = Field()
    same_host_links_count = Field()
    same_host_links_sample = Field()
