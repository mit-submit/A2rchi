BOT_NAME = "archi_scrapers"

SPIDER_MODULES = ["src.data_manager.collectors.scrapers.spiders"]

NEWSPIDER_MODULE = "src.data_manager.collectors.scrapers.spiders"

# Browser-like UA to avoid bot-blocking (e.g. Twiki ConnectionLost issue)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
    "archi_scrapers/1.0 (+https://github.com/archi-physics/archi)"
)

# Default RETRY_TIMES is 2. We bump to 3 for transient failures.
# ConnectionLost is in RETRY_HTTP_CODES by default as a non-HTTP failure;
# Scrapy retries it automatically via RetryMiddleware.
RETRY_ENABLED = True
RETRY_TIMES = 3   # max retries per request (transport + server errors only)
RETRY_HTTP_CODES = [
    500,  # Internal Server Error — transient server fault
    502,  # Bad Gateway — upstream not reachable
    503,  # Service Unavailable — server overloaded
    504,  # Gateway Timeout
    408,  # Request Timeout — network-level timeout
    # 429 (Too Many Requests) omitted: AutoThrottle should prevent hitting it;
]

# Conservative floor delay for all sources.
# AutoThrottle will increase this dynamically but never go below it.
# Indico's robots.txt mandates Crawl-delay: 10 — Indico spiders must override
# this to 10 via custom_settings = {"DOWNLOAD_DELAY": 10}.
DOWNLOAD_DELAY = 2  # seconds
# Per-request timeout — prevents indefinite hangs
DOWNLOAD_TIMEOUT = 30  # seconds
 
# Keep a single concurrent request per domain.
# AutoThrottle adjusts throughput dynamically; starting at 1 is safe.
CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1

# Robots.txt: obey by default.
# override this per-spider:  custom_settings = {"ROBOTSTXT_OBEY": False}
# Never disable globally — it would affect all spiders.
ROBOTSTXT_OBEY = True

# AutoThrottle
# Enabled as a second politeness layer on top of DOWNLOAD_DELAY.
# AutoThrottle treats DOWNLOAD_DELAY as a minimum — it will never go lower.
# Target concurrency of 1.0 keeps us single-threaded per domain by default.
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = DOWNLOAD_DELAY   # initial delay before AT calibrates
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
AUTOTHROTTLE_MAX_DELAY = 60                 # cap: never wait more than 60s
# Log every AutoThrottle adjustment — useful during development, can be
# set False in production if log volume is too high.
AUTOTHROTTLE_DEBUG = False

# ------------------------------------------------------------------ #
# Depth limiting — safety cap; spiders can narrow via custom_settings.
# ------------------------------------------------------------------ #
DEPTH_LIMIT = 2   # hard cap so a misconfigured crawl can't run forever

# ---------------------------------------------------------------------------
# Safety: fail loudly on spider import errors
# ---------------------------------------------------------------------------
SPIDER_LOADER_WARN_ONLY = False

# Maximum error count before the spider is closed automatically.
# 25 gives enough room to diagnose intermittent failures without letting
# a completely broken crawl run for hours.
CLOSESPIDER_ERRORCOUNT = 25

LOG_LEVEL = "INFO"

# The class used to detect and filter duplicate requests
DUPEFILTER_CLASS = "scrapy.dupefilters.RFPDupeFilter"

# ---------------------------------------------------------------------------
# Middlewares, Pipelines and Extensions Priorities
# ---------------------------------------------------------------------------
DOWNLOADER_MIDDLEWARES = { }

ITEM_PIPELINES = { }

EXTENSIONS = { 
    "scrapy.extensions.closespider.CloseSpider": 500,
}
