from scrapy import Item, Field
 
class ArchiBaseItem(Item):
    """Fields shared by every source type."""
    url = Field()          # canonical URL of the page
    content = Field()      # str (HTML/Markdown/text) or bytes (PDF)
    suffix = Field()       # "html" | "pdf" | "md" | ...
    title = Field()        # page title, may be empty

class WebPageItem(ArchiBaseItem):
    """Item produced by the plain-Link spider."""
    content_type = Field()   # value of Content-Type response header
    encoding = Field()       # response encoding (e.g. "utf-8")

class PDFItem(ArchiBaseItem):
    """Binary PDF scraped from a web URL."""
    content_type = Field()

class TWikiPageItem(WebPageItem):
    """Item produced by the trivial Twiki spider."""
    body_length = Field()
    body_preview = Field()
