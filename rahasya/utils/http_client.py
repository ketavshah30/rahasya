import asyncio
import random
import time
from typing import Optional, Dict, Any
from loguru import logger

import httpx

from rahasya.storage.network_audit import record_audit_event

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0"
]

class StealthHTTPClient:
    """Resilient HTTP client with anti-detection features."""
    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        ssl_verify: bool = True,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            proxy=proxy,
            timeout=timeout,
            verify=ssl_verify,
        )

    def _get_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        if custom_headers:
            headers.update(custom_headers)
        return headers

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        delay = 1.0
        last_exception = None
        
        # Random delay between requests to avoid rate limits
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        for attempt in range(self.max_retries):
            request_started = time.monotonic()
            response_recorded = False
            try:
                kwargs["headers"] = self._get_headers(kwargs.get("headers"))
                response = await self._client.request(method, url, **kwargs)
                duration_ms = round((time.monotonic() - request_started) * 1000, 2)
                if response.status_code == 429:
                    outcome = "rate_limited"
                elif response.status_code >= 400:
                    outcome = "http_error"
                else:
                    outcome = "success"
                record_audit_event(
                    "network_request",
                    outcome=outcome,
                    url=str(response.request.url),
                    method=method.upper(),
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                    max_attempts=self.max_retries,
                    via_proxy=bool(self.proxy),
                )
                response_recorded = True
                response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if not response_recorded:
                    record_audit_event(
                        "network_request",
                        outcome="failed",
                        url=url,
                        method=method.upper(),
                        duration_ms=round((time.monotonic() - request_started) * 1000, 2),
                        attempt=attempt + 1,
                        max_attempts=self.max_retries,
                        via_proxy=bool(self.proxy),
                        error_type=type(e).__name__,
                        error=str(e),
                    )
                logger.warning(f"Attempt {attempt+1}/{self.max_retries} failed for {url}: {e}")
                last_exception = e
                if (
                    isinstance(e, httpx.HTTPStatusError)
                    and 400 <= e.response.status_code < 500
                    and e.response.status_code != 429
                ):
                    raise
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2  # Exponential backoff
        
        raise last_exception or Exception("Unknown request failure")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self._request_with_retry("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self._request_with_retry("POST", url, **kwargs)
        
    async def download(self, url: str, **kwargs) -> bytes:
        response = await self.get(url, **kwargs)
        return response.content
        
    async def close(self):
        await self._client.aclose()


class TorHTTPClient(StealthHTTPClient):
    """HTTP Client that routes traffic through Tor SOCKS5 proxy."""
    def __init__(
        self,
        tor_proxy: str = "socks5://127.0.0.1:9050",
        timeout: float = 60.0,
        ssl_verify: bool = True,
    ):
        super().__init__(proxy=tor_proxy, timeout=timeout, ssl_verify=ssl_verify)
        logger.info(f"Initialized TorHTTPClient via {tor_proxy}")


class PlaywrightClient:
    """Client for JS-heavy sites using headless browser."""
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser = None

    async def _init_browser(self):
        if not self._browser:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
            except ImportError:
                raise ImportError("Playwright is required. Install it using 'pip install playwright' and run 'playwright install'")

    async def get(self, url: str) -> str:
        """Returns the rendered HTML content of the page."""
        started = time.monotonic()
        await self._init_browser()
        page = await self._browser.new_page(
            user_agent=random.choice(USER_AGENTS)
        )
        try:
            await page.goto(url, wait_until="networkidle")
            content = await page.content()
            record_audit_event(
                "network_request",
                outcome="success",
                url=url,
                method="BROWSER_GET",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                browser="playwright",
            )
            return content
        except Exception as exc:
            record_audit_event(
                "network_request",
                outcome="failed",
                url=url,
                method="BROWSER_GET",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                browser="playwright",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            await page.close()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
