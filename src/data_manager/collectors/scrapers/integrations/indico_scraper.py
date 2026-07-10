"""Scraper integration for CERN Indico events and materials."""

import re
import time
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource
from src.data_manager.collectors.utils.slide_converter import SlideConverter
from src.utils.config_access import get_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager


class IndicoScraper:
    """Scraper integration for CERN Indico events and materials.

    Uses Indico REST API to fetch event metadata and CERNSSOScraper
    for authentication. Downloads attachments and converts slides to
    markdown using MarkItDown (no original file storage).
    """

    def __init__(
        self, manager: "ScraperManager", indico_config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Initialize the Indico scraper.

        Args:
            manager: Parent ScraperManager instance
            indico_config: Configuration dictionary for Indico scraping
        """
        self.manager = manager
        self.config = indico_config or {}

        global_config = get_global_config()
        self.data_path = global_config["DATA_PATH"]

        # Configuration
        self.base_url = self.config.get("base_url", "https://indico.cern.ch")
        self.use_sso = self.config.get("use_sso", True)

        # Slide conversion config
        slide_config = self.config.get("slide_conversion", {})
        self.conversion_enabled = slide_config.get("enabled", True)
        self.supported_formats = set(
            slide_config.get("formats", ["pdf", "pptx", "ppt", "odp"])
        )

        # Initialize slide converter
        llm_client = slide_config.get("llm_client")
        llm_model = slide_config.get("llm_model")
        self.slide_converter = SlideConverter(
            llm_client=llm_client, llm_model=llm_model
        )

        # SSO scraper for authentication
        self.sso_scraper = None
        if self.use_sso:
            try:
                from src.data_manager.collectors.scrapers.integrations.sso_scraper import (
                    CERNSSOScraper,
                )

                sso_kwargs = self.config.get("sso_kwargs", {"headless": True})
                self.sso_scraper = CERNSSOScraper(**sso_kwargs)
                logger.info("Initialized CERNSSOScraper for Indico authentication")
            except Exception as e:
                logger.warning(f"Failed to initialize SSO scraper: {e}")
                self.sso_scraper = None

    def collect(self, event_urls: List[str]) -> List[ScrapedResource]:
        """Collect events, contributions, and materials from Indico URLs.

        Args:
            event_urls: List of Indico event or category URLs

        Returns:
            List of ScrapedResource objects with converted markdown content
        """
        if not event_urls:
            return []

        resources: List[ScrapedResource] = []
        authenticated_session = None

        try:
            for url in event_urls:
                try:
                    if "/event/" in url:
                        event_id = self._extract_event_id(url)
                        if event_id:
                            # Auto-detect if authentication is needed
                            needs_auth = self._check_event_requires_auth(event_id)

                            if needs_auth and self.use_sso:
                                # Set up authenticated session (reuse if already created)
                                if authenticated_session is None:
                                    authenticated_session = (
                                        self._setup_authenticated_session(url)
                                    )
                                session = authenticated_session
                            elif needs_auth and not self.use_sso:
                                logger.warning(
                                    f"Event {event_id} requires authentication but use_sso=False; skipping"
                                )
                                continue
                            else:
                                # Public event - use unauthenticated session
                                session = requests.Session()

                            resources.extend(self._collect_event(event_id, session))
                    elif "/category/" in url:
                        category_id = self._extract_category_id(url)
                        if category_id:
                            # Categories often require auth, use authenticated session if available
                            if self.use_sso and authenticated_session is None:
                                authenticated_session = (
                                    self._setup_authenticated_session(url)
                                )
                            session = (
                                authenticated_session
                                if authenticated_session
                                else requests.Session()
                            )
                            resources.extend(
                                self._collect_category(category_id, session)
                            )
                    else:
                        logger.warning(f"Unrecognized Indico URL format: {url}")
                except Exception as e:
                    logger.error(f"Error collecting from {url}: {e}")

        finally:
            # Clean up SSO scraper
            if self.sso_scraper:
                self.sso_scraper.close()

        logger.info(f"Indico scraping completed. Collected {len(resources)} resources.")
        return resources

    def _compute_allowed_contribution_dates(
        self, event_data: Dict[str, Any]
    ) -> Optional[set[str]]:
        """
        Compute allowed contribution dates (YYYY-MM-DD) based on configuration.

        Supported config keys under `data_manager.sources.indico`:
          - days: [YYYY-MM-DD, ...] (explicit allow-list)
          - date_range: {from: YYYY-MM-DD, to: YYYY-MM-DD} (inclusive)
          - only_first_day: true (alias: first_day_only)
          - day_limit: N (alias: max_days) -> allow first N days starting at event startDate
        """
        # Highest precedence: explicit allow-list
        days = self.config.get("days")
        if isinstance(days, list) and days:
            return {str(d).strip() for d in days if str(d).strip()}

        # Next: inclusive date range
        date_range = self.config.get("date_range")
        if isinstance(date_range, dict):
            start_s = str(date_range.get("from", "")).strip()
            end_s = str(date_range.get("to", "")).strip()
            if start_s and end_s:
                try:
                    start_d = date_cls.fromisoformat(start_s)
                    end_d = date_cls.fromisoformat(end_s)
                    if end_d < start_d:
                        start_d, end_d = end_d, start_d
                    out: set[str] = set()
                    cur = start_d
                    while cur <= end_d:
                        out.add(cur.isoformat())
                        cur += timedelta(days=1)
                    return out
                except Exception as e:
                    logger.warning(
                        f"Invalid indico.date_range; expected YYYY-MM-DD: {e}"
                    )

        # Next: first day only
        only_first_day = bool(
            self.config.get("only_first_day") or self.config.get("first_day_only")
        )
        start_date_s = str((event_data.get("startDate") or {}).get("date", "")).strip()
        if only_first_day and start_date_s:
            return {start_date_s}

        # Next: first N days
        day_limit = self.config.get("day_limit", self.config.get("max_days"))
        if isinstance(day_limit, int) and day_limit > 0 and start_date_s:
            try:
                start_d = date_cls.fromisoformat(start_date_s)
                return {
                    (start_d + timedelta(days=i)).isoformat() for i in range(day_limit)
                }
            except Exception as e:
                logger.warning(
                    f"Invalid indico day_limit/max_days start date '{start_date_s}': {e}"
                )

        return None

    def _check_event_requires_auth(self, event_id: str) -> bool:
        """Check if an event requires authentication by trying an unauthenticated request.

        Indico returns empty results for protected events rather than 401/403,
        so we check for actual content in the response.

        Args:
            event_id: Event ID to check

        Returns:
            True if authentication is required, False if public
        """
        test_url = f"{self.base_url}/export/event/{event_id}.json"
        try:
            response = requests.get(test_url, timeout=10, allow_redirects=False)

            # Public events return 200 with JSON data containing results
            if response.status_code == 200:
                try:
                    data = response.json()
                    results = data.get("results", [])

                    # Protected events return {"results": []} - empty array
                    # Public events return {"results": [{event_data}]}
                    if isinstance(results, list) and len(results) > 0:
                        # Check the first result has actual content
                        first_result = results[0]
                        if first_result.get("title") or first_result.get("id"):
                            logger.info(
                                f"Event {event_id} is public (no auth required)"
                            )
                            return False

                    # Empty results = protected event
                    if data.get("count", 1) == 0 or not results:
                        logger.info(
                            f"Event {event_id} requires authentication (empty results)"
                        )
                        return True

                except ValueError:
                    pass  # Not JSON, probably needs auth

            # Check for auth redirects or forbidden responses
            if response.status_code in [401, 403]:
                logger.info(
                    f"Event {event_id} requires authentication (got {response.status_code})"
                )
                return True

            # Redirect to login page indicates auth required
            if response.status_code in [301, 302, 303, 307, 308]:
                location = response.headers.get("Location", "")
                if (
                    "login" in location.lower()
                    or "sso" in location.lower()
                    or "auth" in location.lower()
                ):
                    logger.info(
                        f"Event {event_id} requires authentication (redirect to login)"
                    )
                    return True

            # If we get here with non-200, assume auth might be needed
            if response.status_code != 200:
                logger.info(
                    f"Event {event_id} status {response.status_code}, assuming auth required"
                )
                return True

            # Default: assume auth required for safety
            logger.info(f"Event {event_id} - unclear status, assuming auth required")
            return True

        except Exception as e:
            logger.warning(
                f"Error checking auth for event {event_id}: {e}, assuming auth required"
            )
            return True

    def _setup_authenticated_session(self, initial_url: str) -> requests.Session:
        """Set up an authenticated session using CERNSSOScraper.

        Args:
            initial_url: URL to trigger authentication

        Returns:
            Authenticated requests.Session
        """
        session = requests.Session()

        if self.sso_scraper:
            try:
                logger.info("Setting up authenticated session with CERN SSO")
                self.sso_scraper.setup_driver()

                # Authenticate and get cookies
                cookies = self.sso_scraper.authenticate(initial_url)

                if cookies:
                    for cookie in cookies:
                        # Transfer all cookie attributes including domain
                        # Strip leading dot from domain if present (requests handles this)
                        domain = cookie.get("domain", "")
                        if domain.startswith("."):
                            domain = domain[1:]  # Remove leading dot

                        session.cookies.set(
                            cookie["name"],
                            cookie["value"],
                            domain=domain,
                            path=cookie.get("path", "/"),
                            secure=cookie.get("secure", False),
                        )
                        logger.debug(
                            f"Set cookie: {cookie['name']} for domain {domain}"
                        )
                    logger.info(
                        f"Successfully authenticated with CERN SSO ({len(cookies)} cookies)"
                    )
                else:
                    logger.warning("Authentication did not return cookies")

            except Exception as e:
                logger.error(f"Error during SSO authentication: {e}")

        return session

    def _collect_event(
        self, event_id: str, session: requests.Session
    ) -> List[ScrapedResource]:
        """Collect a single event with its contributions and materials.

        Args:
            event_id: Indico event ID
            session: Authenticated session

        Returns:
            List of ScrapedResource objects
        """
        resources: List[ScrapedResource] = []

        try:
            # Fetch event metadata
            event_data = self._fetch_event_metadata(event_id, session)
            if not event_data:
                return resources

            # Create resource for event metadata
            event_resource = self._create_event_resource(event_id, event_data)
            if event_resource:
                resources.append(event_resource)

            # Fetch contributions (talks)
            contributions = self._fetch_contributions(event_id, session)

            allowed_dates = self._compute_allowed_contribution_dates(event_data)
            if allowed_dates:
                before = len(contributions)
                contributions = [
                    c
                    for c in contributions
                    if str((c.get("startDate") or {}).get("date", "")).strip()
                    in allowed_dates
                ]
                logger.info(
                    "Filtering contributions by date for event %s: %s (kept %s/%s)",
                    event_id,
                    sorted(allowed_dates),
                    len(contributions),
                    before,
                )

            event_title = (event_data or {}).get("title", "")
            event_start = (event_data or {}).get("startDate") or {}
            event_date = (
                event_start.get("date", "") if isinstance(event_start, dict) else ""
            )

            for contribution in contributions:
                # Download and convert materials first
                material_resources = self._collect_materials(
                    event_id,
                    contribution,
                    session,
                    event_title=event_title,
                    event_date=event_date,
                )
                # Only create standalone contribution metadata when there are no materials:
                # otherwise we duplicate and show a wrong URL (API id vs URL contribution id)
                if not material_resources:
                    contrib_resource = self._create_contribution_resource(
                        event_id,
                        contribution,
                        event_title=event_title,
                        event_date=event_date,
                    )
                    if contrib_resource:
                        resources.append(contrib_resource)
                resources.extend(material_resources)

        except Exception as e:
            logger.error(f"Error collecting event {event_id}: {e}")

        return resources

    def _collect_category(
        self, category_id: str, session: requests.Session
    ) -> List[ScrapedResource]:
        """Collect events from a category.

        For now, only collects events directly in the category (no recursion).

        Args:
            category_id: Indico category ID
            session: Authenticated session

        Returns:
            List of ScrapedResource objects
        """
        resources: List[ScrapedResource] = []

        try:
            # Fetch category metadata to get list of events
            category_url = f"{self.base_url}/export/category/{category_id}.json"
            response = session.get(category_url, timeout=30)
            response.raise_for_status()
            category_data = response.json()

            events = category_data.get("results", [])
            logger.info(f"Found {len(events)} events in category {category_id}")

            for event in events:
                event_id = str(event.get("id", ""))
                if event_id:
                    resources.extend(self._collect_event(event_id, session))

        except Exception as e:
            logger.error(f"Error collecting category {category_id}: {e}")

        return resources

    def _fetch_event_metadata(
        self, event_id: str, session: requests.Session
    ) -> Optional[Dict]:
        """Fetch event metadata from Indico API.

        Args:
            event_id: Event ID
            session: Authenticated session

        Returns:
            Event data dictionary or None
        """
        try:
            url = f"{self.base_url}/export/event/{event_id}.json"
            logger.info(f"Fetching event metadata: {url}")

            response = session.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            # Indico wraps the event in a 'results' list
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
                if results and len(results) > 0:
                    return results[0]

            return data

        except Exception as e:
            logger.error(f"Error fetching event {event_id}: {e}")
            return None

    def _fetch_contributions(
        self, event_id: str, session: requests.Session
    ) -> List[Dict]:
        """Fetch all contributions (talks) for an event.

        First tries the JSON API. If that returns 0 contributions,
        falls back to scraping the timetable HTML view.

        Args:
            event_id: Event ID
            session: Authenticated session

        Returns:
            List of contribution dictionaries
        """
        # Try the standard API first
        contributions = self._fetch_contributions_from_api(event_id, session)

        # Fallback to timetable scraping if API returns nothing
        if not contributions:
            logger.info(
                f"No contributions from API for event {event_id}, trying timetable view..."
            )
            contributions = self._fetch_contributions_from_timetable(event_id, session)

        return contributions

    def _fetch_contributions_from_api(
        self, event_id: str, session: requests.Session
    ) -> List[Dict]:
        """Fetch contributions from the JSON API endpoint."""
        try:
            url = f"{self.base_url}/export/event/{event_id}.json?detail=contributions"
            logger.info(f"Fetching contributions: {url}")

            response = session.get(url, timeout=30)
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            if results and isinstance(results, list) and len(results) > 0:
                contributions = results[0].get("contributions", [])
            else:
                contributions = []
            logger.info(
                f"Found {len(contributions)} contributions from API for event {event_id}"
            )

            return contributions

        except Exception as e:
            logger.error(
                f"Error fetching contributions from API for event {event_id}: {e}"
            )
            return []

    def _fetch_contributions_from_timetable(
        self, event_id: str, session: requests.Session
    ) -> List[Dict]:
        """Fallback: scrape contributions from the timetable HTML view.

        Some Indico events structure content in timetable sessions rather than
        direct contributions. This scrapes the timetable page to extract them.
        Uses Selenium if available for authenticated access.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("BeautifulSoup not available; cannot scrape timetable HTML")
            return []

        contributions = []
        html_content = None

        # Try Selenium first if SSO scraper is available (better for authenticated pages)
        if self.sso_scraper and self.sso_scraper.driver:
            timetable_url = f"{self.base_url}/event/{event_id}/timetable/#all.detailed"
            logger.info(f"Fetching timetable via Selenium: {timetable_url}")
            try:
                self.sso_scraper.driver.get(timetable_url)
                import time

                time.sleep(2)  # Wait for JavaScript to render
                html_content = self.sso_scraper.driver.page_source
            except Exception as e:
                logger.warning(f"Selenium timetable fetch failed: {e}")

        # Fallback to requests if Selenium didn't work
        if not html_content:
            timetable_urls = [
                f"{self.base_url}/event/{event_id}/timetable/",
                f"{self.base_url}/event/{event_id}/",
            ]

            for timetable_url in timetable_urls:
                logger.info(f"Fetching timetable via requests: {timetable_url}")
                try:
                    response = session.get(timetable_url, timeout=30)
                    response.raise_for_status()
                    html_content = response.text
                    break
                except Exception as e:
                    logger.warning(
                        f"Error fetching timetable from {timetable_url}: {e}"
                    )
                    continue

        if not html_content:
            logger.warning(f"Could not fetch timetable for event {event_id}")
            return []

        # Parse the HTML
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            # Look for contribution links (most reliable pattern)
            contrib_links = soup.select('a[href*="/contribution/"]')
            for link in contrib_links:
                href = link.get("href", "")
                if "/contribution/" in href:
                    parts = href.split("/contribution/")
                    if len(parts) > 1:
                        contrib_id = parts[1].split("/")[0].split("?")[0]
                        if contrib_id and contrib_id.isdigit():
                            title = (
                                link.get_text(strip=True)
                                or f"Contribution {contrib_id}"
                            )
                            contributions.append(
                                {
                                    "id": contrib_id,
                                    "title": title,
                                    "_from_timetable": True,
                                    "folders": [],
                                }
                            )

            # Look for direct material links (PDF, PPTX, etc.)
            file_extensions = [".pdf", ".pptx", ".ppt", ".odp", ".doc", ".docx"]
            all_links = soup.select("a[href]")
            for link in all_links:
                href = link.get("href", "")
                filename = link.get_text(strip=True)

                # Check if it's a material link
                is_material = (
                    any(href.lower().endswith(ext) for ext in file_extensions)
                    or "/attachments/" in href
                    or "/material/" in href
                )

                if is_material and filename:
                    full_url = (
                        href if href.startswith("http") else f"{self.base_url}{href}"
                    )
                    contributions.append(
                        {
                            "id": f"material_{hash(full_url) % 100000}",
                            "title": filename,
                            "_from_timetable": True,
                            "_direct_material_url": full_url,
                            "folders": [],
                        }
                    )

        except Exception as e:
            logger.error(f"Error parsing timetable HTML for event {event_id}: {e}")

        # Deduplicate by ID and URL
        seen = set()
        unique_contributions = []
        for c in contributions:
            key = c.get("_direct_material_url") or c.get("id")
            if key and key not in seen:
                seen.add(key)
                unique_contributions.append(c)

        logger.info(
            f"Found {len(unique_contributions)} contributions from timetable for event {event_id}"
        )
        return unique_contributions

    def _collect_materials(
        self,
        event_id: str,
        contribution: Dict,
        session: requests.Session,
        event_title: str = "",
        event_date: str = "",
    ) -> List[ScrapedResource]:
        """Download and convert materials for a contribution.

        Args:
            event_id: Event ID
            contribution: Contribution data dictionary
            session: Authenticated session
            event_title: Event title (for searchable metadata)
            event_date: Event start date (for searchable metadata)

        Returns:
            List of ScrapedResource objects with markdown content
        """
        resources: List[ScrapedResource] = []

        contribution_id = str(contribution.get("id", ""))

        # Handle direct material URLs from timetable scraping
        direct_url = contribution.get("_direct_material_url")
        if direct_url:
            try:
                # Create a synthetic attachment for the direct URL
                filename = direct_url.split("/")[-1].split("?")[0]
                attachment = {
                    "download_url": direct_url,
                    "filename": filename,
                    "title": contribution.get("title", filename),
                }
                resource = self._download_and_convert_material(
                    event_id,
                    contribution_id,
                    contribution,
                    attachment,
                    session,
                    event_title=event_title,
                    event_date=event_date,
                )
                if resource:
                    resources.append(resource)
            except Exception as e:
                logger.error(f"Error processing direct material URL: {e}")
            return resources

        # Standard API flow: process folders and attachments.
        # Deduplicate by stem: when the same slides are uploaded in multiple
        # formats (e.g. PDF + PPTX), keep only the first match in the
        # configured format-priority order to avoid duplicate chunks.
        folders = contribution.get("folders", [])
        format_priority = list(
            self.supported_formats
        )  # e.g. ["pdf", "pptx", "ppt", "odp"]

        all_attachments: List[Dict] = []
        for folder in folders:
            all_attachments.extend(folder.get("attachments", []))

        seen_stems: dict = {}  # stem -> format priority index
        deduplicated: List[Dict] = []
        for attachment in all_attachments:
            fname = attachment.get("filename", "")
            stem = Path(fname).stem
            ext = Path(fname).suffix.lstrip(".").lower()
            priority = (
                format_priority.index(ext)
                if ext in format_priority
                else len(format_priority)
            )
            prev = seen_stems.get(stem)
            if prev is not None and priority >= prev:
                logger.debug(
                    f"Skipping duplicate attachment {fname} (already have {stem} in higher-priority format)"
                )
                continue
            if prev is not None:
                # Replace: this format has higher priority
                deduplicated = [
                    a for a in deduplicated if Path(a.get("filename", "")).stem != stem
                ]
            seen_stems[stem] = priority
            deduplicated.append(attachment)

        for attachment in deduplicated:
            try:
                resource = self._download_and_convert_material(
                    event_id,
                    contribution_id,
                    contribution,
                    attachment,
                    session,
                    event_title=event_title,
                    event_date=event_date,
                )
                if resource:
                    resources.append(resource)
            except Exception as e:
                logger.error(f"Error processing attachment: {e}")

        return resources

    def _download_and_convert_material(
        self,
        event_id: str,
        contribution_id: str,
        contribution: Dict,
        attachment: Dict,
        session: requests.Session,
        event_title: str = "",
        event_date: str = "",
    ) -> Optional[ScrapedResource]:
        """Download an attachment and convert to markdown.

        Only stores the markdown conversion, not the original file.

        Args:
            event_id: Event ID
            contribution_id: Contribution ID
            contribution: Full contribution data
            attachment: Attachment data dictionary
            session: Authenticated session

        Returns:
            ScrapedResource with markdown content, or None if conversion fails
        """
        if not self.conversion_enabled:
            logger.debug("Slide conversion disabled, skipping")
            return None

        filename = attachment.get("filename", "")
        download_url = attachment.get("download_url", "")
        content_type = attachment.get("content_type", "")

        # Check if format is supported
        file_ext = Path(filename).suffix.lstrip(".").lower()
        if file_ext not in self.supported_formats:
            logger.debug(f"Skipping unsupported format: {file_ext}")
            return None

        try:
            # Download file
            logger.info(f"Downloading material: {filename}")

            # Handle relative URLs
            if not download_url.startswith("http"):
                download_url = urljoin(self.base_url, download_url)

            response = session.get(download_url, timeout=60)
            response.raise_for_status()

            file_bytes = response.content
            logger.info(f"Downloaded {len(file_bytes)} bytes")

            # Convert to markdown
            conversion_result = self.slide_converter.convert_bytes(
                file_bytes,
                content_type,
                filename,
            )

            if conversion_result.error:
                logger.error(f"Conversion failed: {conversion_result.error}")
                return None

            if not conversion_result.markdown:
                logger.warning(f"Conversion produced empty markdown for {filename}")
                return None

            # Extract contribution metadata for richer context
            contrib_title = contribution.get("title", "")
            speaker = self._extract_speaker_name(contribution)
            speaker_affiliation = self._extract_speaker_affiliation(contribution)
            contrib_code = contribution.get("code", "")
            contrib_type = contribution.get(
                "contribution_type", contribution.get("type", "")
            )
            keywords = contribution.get("keywords", [])
            start_date = contribution.get("startDate", {})
            duration = contribution.get("duration", "")
            session = contribution.get("session", "")

            # Contribution page URL (for citing the talk, not the PDF)
            url_contrib_id = (
                self._get_contribution_url_id(contribution) or contribution_id
            )
            contribution_url = (
                f"{self.base_url}/event/{event_id}/contributions/{url_contrib_id}/"
            )

            # Primary authors for metadata block
            primary_authors = contribution.get("primaryauthors", [])
            primary_author_names = [
                (a.get("fullName") or a.get("name") or "").strip()
                for a in primary_authors
                if isinstance(a, dict)
            ]
            primary_author_names = [n for n in primary_author_names if n]

            # Human-readable display name for UI and catalog (contribution title + speaker)
            display_name = contrib_title or Path(filename).stem
            if speaker:
                display_name = f"{display_name} ({speaker})"

            # Full metadata block at start so the chatbot can find and cite this talk
            date_str = start_date.get("date", "") if start_date else ""
            time_str = start_date.get("time", "") if start_date else ""
            header_lines = []
            if event_title:
                header_lines.append(f"Event: {event_title}.")
            if event_date:
                header_lines.append(f"Event date: {event_date}.")
            header_lines.extend(
                [
                    f"Contribution: {contrib_title}.",
                    f"Speaker: {speaker}.",
                ]
            )
            if primary_author_names:
                header_lines.append(
                    f"Primary authors: {', '.join(primary_author_names)}."
                )
            if date_str or time_str:
                header_lines.append(f"Date and time: {date_str} {time_str}.")
            if duration:
                header_lines.append(f"Duration: {duration} minutes.")
            if session:
                header_lines.append(f"Session: {session}.")
            header_lines.append("Has slides: yes.")
            header_lines.append(f"Contribution URL: {contribution_url}")
            header_lines.append("")
            header = "\n".join(header_lines) + "\n"
            content_with_header = header + conversion_result.markdown

            # Create resource with markdown content
            resource = ScrapedResource(
                url=download_url,
                content=content_with_header,
                suffix="md",
                source_type="web",
                metadata={
                    "scraper": "indico",
                    "display_name": display_name,
                    "event_id": event_id,
                    "event_title": event_title,
                    "event_date": event_date,
                    "contribution_id": contribution_id,
                    "contribution_url": contribution_url,
                    "contribution_code": contrib_code,
                    "contribution_title": contrib_title,
                    "contribution_type": contrib_type if contrib_type else "",
                    "speaker": speaker,
                    "speaker_affiliation": speaker_affiliation,
                    "primary_authors": (
                        ", ".join(primary_author_names) if primary_author_names else ""
                    ),
                    "start_date": date_str,
                    "start_time": time_str,
                    "duration": str(duration) if duration else "",
                    "session": session if session else "",
                    "has_slides": "yes",
                    "keywords": ", ".join(keywords) if keywords else "",
                    "resource_type": "material",
                    "original_filename": filename,
                    "original_format": file_ext,
                    "original_size_bytes": str(len(file_bytes)),
                    "content_type": content_type,
                    "converted_to_markdown": "true",
                    "material_title": attachment.get("title", ""),
                },
                file_name=f"{Path(filename).stem}.md",
            )

            logger.info(f"Successfully converted {filename} to markdown")
            return resource

        except Exception as e:
            logger.error(f"Error downloading/converting {filename}: {e}")
            return None

    def _create_event_resource(
        self, event_id: str, event_data: Dict
    ) -> Optional[ScrapedResource]:
        """Create a resource representing event metadata.

        Args:
            event_id: Event ID
            event_data: Event data dictionary

        Returns:
            ScrapedResource with event metadata as markdown
        """
        try:
            title = event_data.get("title", "Unknown Event")
            description = event_data.get("description", "")
            location = event_data.get("location", "")
            room = event_data.get("room", "")
            roomFullname = event_data.get("roomFullname", "")
            start_date = event_data.get("startDate", {})
            end_date = event_data.get("endDate", {})
            url = event_data.get("url", f"{self.base_url}/event/{event_id}/")
            event_type = event_data.get("type", "")
            category = event_data.get("category", "")
            category_id = event_data.get("categoryId", "")
            keywords = event_data.get("keywords", [])
            organizer = event_data.get("organizer", "")
            timezone = event_data.get("timezone", "")
            chairs = event_data.get("chairs", [])

            # Format metadata as markdown
            markdown_content = f"# {title}\n\n"

            if description:
                markdown_content += f"{description}\n\n"

            markdown_content += "## Event Details\n\n"

            if event_type:
                markdown_content += f"- **Type**: {event_type}\n"
            if category:
                markdown_content += f"- **Category**: {category}\n"
            if start_date:
                markdown_content += f"- **Start**: {start_date.get('date', '')} {start_date.get('time', '')}"
                if timezone:
                    markdown_content += f" ({timezone})"
                markdown_content += "\n"
            if end_date:
                markdown_content += (
                    f"- **End**: {end_date.get('date', '')} {end_date.get('time', '')}"
                )
                if timezone:
                    markdown_content += f" ({timezone})"
                markdown_content += "\n"
            if location:
                markdown_content += f"- **Location**: {location}"
                if room:
                    markdown_content += f", {room}"
                elif roomFullname:
                    markdown_content += f", {roomFullname}"
                markdown_content += "\n"
            if organizer:
                markdown_content += f"- **Organizer**: {organizer}\n"

            # Add chairs/organizers
            if chairs:
                chair_names = []
                for chair in chairs:
                    if isinstance(chair, dict):
                        name = chair.get("fullName", chair.get("name", ""))
                        if name:
                            chair_names.append(name)
                if chair_names:
                    markdown_content += f"- **Chairs**: {', '.join(chair_names)}\n"

            # Add keywords
            if keywords:
                markdown_content += f"- **Keywords**: {', '.join(keywords)}\n"

            markdown_content += f"- **URL**: {url}\n"

            resource = ScrapedResource(
                url=url,
                content=markdown_content,
                suffix="md",
                source_type="web",
                metadata={
                    "scraper": "indico",
                    "event_id": event_id,
                    "resource_type": "event",
                    "title": title,
                    "event_type": event_type,
                    "category": category,
                    "category_id": str(category_id) if category_id else "",
                    "location": location,
                    "room": roomFullname or room,
                    "start_date": start_date.get("date", ""),
                    "end_date": end_date.get("date", ""),
                    "timezone": timezone,
                    "organizer": organizer,
                    "keywords": ", ".join(keywords) if keywords else "",
                    "chairs": ", ".join(
                        [c.get("fullName", "") for c in chairs if isinstance(c, dict)]
                    ),
                },
                file_name=f"event_{event_id}.md",
            )

            return resource

        except Exception as e:
            logger.error(f"Error creating event resource: {e}")
            return None

    def _get_contribution_url_id(self, contribution: Dict) -> Optional[str]:
        """
        Get the contribution ID used in Indico URLs (from first attachment URL if present).
        The API often returns a short 'id' (e.g. 81) but URLs use a longer id (e.g. 6491865).
        """
        for folder in contribution.get("folders", []) or []:
            for att in folder.get("attachments", []) or []:
                url = att.get("download_url") or att.get("url") or ""
                match = re.search(r"/contributions/(\d+)/", url)
                if match:
                    return match.group(1)
        return None

    def _create_contribution_resource(
        self,
        event_id: str,
        contribution: Dict,
        event_title: str = "",
        event_date: str = "",
    ) -> Optional[ScrapedResource]:
        """Create a resource representing contribution metadata.

        Args:
            event_id: Event ID
            contribution: Contribution data dictionary
            event_title: Event title (for searchable metadata)
            event_date: Event start date (for searchable metadata)

        Returns:
            ScrapedResource with contribution metadata as markdown
        """
        try:
            # Use URL contribution id from first attachment when available (correct /contributions/6491865/ link)
            contrib_id = self._get_contribution_url_id(contribution) or str(
                contribution.get("id", "")
            )
            title = contribution.get("title", "Unknown Contribution")
            description = contribution.get("description", "")
            duration = contribution.get("duration", "")
            start_date = contribution.get("startDate", {})
            speaker = self._extract_speaker_name(contribution)
            speaker_affiliation = self._extract_speaker_affiliation(contribution)
            url = f"{self.base_url}/event/{event_id}/contributions/{contrib_id}/"

            # Extract additional metadata
            code = contribution.get("code", "")
            contrib_type = contribution.get("type", "")
            location = contribution.get("location", "")
            room = contribution.get("room", "")
            roomFullname = contribution.get("roomFullname", "")
            session = contribution.get("session", "")
            track = contribution.get("track", "")
            keywords = contribution.get("keywords", [])

            # Extract authors
            primary_authors = contribution.get("primaryauthors", [])
            coauthors = contribution.get("coauthors", [])

            def extract_author_names(author_list):
                names = []
                for author in author_list:
                    if isinstance(author, dict):
                        name = author.get("fullName", author.get("name", ""))
                        if name:
                            names.append(name)
                return names

            primary_author_names = extract_author_names(primary_authors)
            coauthor_names = extract_author_names(coauthors)

            # Format as markdown (event/date at top for searchability, same as materials)
            markdown_content = ""
            if event_title:
                markdown_content += f"Event: {event_title}.\n"
            if event_date:
                markdown_content += f"Event date: {event_date}.\n"
            if markdown_content:
                markdown_content += "\n"
            markdown_content += f"# {title}\n\n"

            if code:
                markdown_content += f"**Contribution Code**: {code}\n\n"

            if speaker:
                markdown_content += f"**Speaker**: {speaker}\n\n"

            # Add authors
            if primary_author_names:
                markdown_content += (
                    f"**Primary Authors**: {', '.join(primary_author_names)}\n\n"
                )
            if coauthor_names:
                markdown_content += f"**Co-authors**: {', '.join(coauthor_names)}\n\n"

            if description:
                markdown_content += f"{description}\n\n"

            markdown_content += "**This contribution has no slides or materials.**\n\n"
            markdown_content += "## Details\n\n"

            if contrib_type:
                markdown_content += f"- **Type**: {contrib_type}\n"
            if start_date:
                markdown_content += f"- **Time**: {start_date.get('date', '')} {start_date.get('time', '')}\n"
            if duration:
                markdown_content += f"- **Duration**: {duration} minutes\n"
            if location:
                markdown_content += f"- **Location**: {location}"
                if room:
                    markdown_content += f", {room}"
                elif roomFullname:
                    markdown_content += f", {roomFullname}"
                markdown_content += "\n"
            if session:
                markdown_content += f"- **Session**: {session}\n"
            if track:
                markdown_content += f"- **Track**: {track}\n"
            if keywords:
                markdown_content += f"- **Keywords**: {', '.join(keywords)}\n"

            markdown_content += f"- **URL**: {url}\n"

            # Human-readable display name and mark as no slides
            display_name = f"{title} ({speaker})" if speaker else title
            resource = ScrapedResource(
                url=url,
                content=markdown_content,
                suffix="md",
                source_type="web",
                metadata={
                    "scraper": "indico",
                    "display_name": display_name,
                    "event_id": event_id,
                    "event_title": event_title,
                    "event_date": event_date,
                    "contribution_id": contrib_id,
                    "contribution_url": url,
                    "resource_type": "contribution",
                    "has_slides": "no",
                    "title": title,
                    "code": code,
                    "contribution_title": title,
                    "contribution_type": contrib_type if contrib_type else "",
                    "speaker": speaker,
                    "speaker_affiliation": speaker_affiliation,
                    "primary_authors": ", ".join(primary_author_names),
                    "coauthors": ", ".join(coauthor_names),
                    "start_date": start_date.get("date", "") if start_date else "",
                    "start_time": start_date.get("time", "") if start_date else "",
                    "duration": str(duration) if duration else "",
                    "location": location,
                    "room": roomFullname or room,
                    "session": session if session else "",
                    "track": track if track else "",
                    "keywords": ", ".join(keywords) if keywords else "",
                },
                file_name=f"contribution_{contrib_id}.md",
            )

            return resource

        except Exception as e:
            logger.error(f"Error creating contribution resource: {e}")
            return None

    def _extract_speaker_name(self, contribution: Dict) -> str:
        """Extract speaker name from contribution data.

        Args:
            contribution: Contribution data dictionary

        Returns:
            Speaker name or empty string
        """
        speakers = contribution.get("speakers", [])
        if speakers and len(speakers) > 0:
            first_speaker = speakers[0]
            full_name = first_speaker.get("fullName", "")
            if full_name:
                return full_name
            # Fallback to first + last name
            first = first_speaker.get("first_name", "")
            last = first_speaker.get("last_name", "")
            return f"{first} {last}".strip()
        return ""

    @staticmethod
    def _extract_speaker_affiliation(contribution: Dict) -> str:
        """Extract the first speaker's affiliation from contribution data."""
        speakers = contribution.get("speakers", [])
        if speakers and isinstance(speakers[0], dict):
            return (speakers[0].get("affiliation") or "").strip()
        return ""

    def _extract_event_id(self, url: str) -> Optional[str]:
        """Extract event ID from Indico URL.

        Args:
            url: Indico event URL

        Returns:
            Event ID or None
        """
        # Match /event/123456/ or /event/123456
        match = re.search(r"/event/(\d+)", url)
        if match:
            return match.group(1)
        return None

    def _extract_category_id(self, url: str) -> Optional[str]:
        """Extract category ID from Indico URL.

        Args:
            url: Indico category URL

        Returns:
            Category ID or None
        """
        # Match /category/123/ or /category/123
        match = re.search(r"/category/(\d+)", url)
        if match:
            return match.group(1)
        return None
