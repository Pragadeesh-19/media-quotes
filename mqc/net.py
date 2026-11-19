import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; mqc/0.1; media quotes research; +https://github.com/Pragadeesh-19/media-quotes)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 120


class HttpFailure(Exception):
    pass


class Http:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 1.0, max_retries: int = 3,
                 base_delay: float = 2.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, jitter: Callable[[], float] = random.random):
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True,
                                             headers={"User-Agent": USER_AGENT})
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._last_request: dict[str, float] = {}

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error = ""
        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit(urlsplit(url).netloc)
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}: {error}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                last_error = f"HTTP {response.status_code}"
                if attempt < self.max_retries:
                    self._sleep(self._retry_after(response) or self._backoff(attempt))
                continue
            if attempt < self.max_retries:
                self._sleep(self._backoff(attempt))
        raise HttpFailure(f"{method} {url} failed after {self.max_retries + 1} attempts: {last_error}")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def _respect_rate_limit(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _backoff(self, attempt: int) -> float:
        return self.base_delay * (2 ** attempt) * (0.5 + self._jitter() / 2)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        if value.isdigit():
            return min(float(value), MAX_RETRY_AFTER_SECONDS)
        try:
            delta = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return None
        return min(max(delta, 0.0), MAX_RETRY_AFTER_SECONDS)
