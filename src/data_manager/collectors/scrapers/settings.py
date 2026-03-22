BOT_NAME = "archi_scrapers"

SPIDER_MODULES = ["src.data_manager.collectors.scrapers.spiders"]

NEWSPIDER_MODULE = "src.data_manager.collectors.scrapers.spiders"

# Browser-like UA to avoid bot-blocking (e.g. Twiki ConnectionLost issue)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36; archi_scrapers"
)

# Default RETRY_TIMES is 2. We bump to 3 for transient failures.
# ConnectionLost is in RETRY_HTTP_CODES by default as a non-HTTP failure;
# Scrapy retries it automatically via RetryMiddleware.
RETRY_ENABLED = True
RETRY_TIMES = 3  # total attempts = 1 original + 3 retries
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# Per-request timeout — prevents indefinite hangs
DOWNLOAD_TIMEOUT = 30  # seconds

# ---------------------------------------------------------------------------
# Safety: fail loudly on spider import errors
# ---------------------------------------------------------------------------
SPIDER_LOADER_WARN_ONLY = False
