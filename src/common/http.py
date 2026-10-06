"""HTTP GET with retry on network errors, 429 and 5xx (shared by API ingesters)."""
import time

import requests


def get_json(url, params=None, max_retries=5, timeout=60, session=None, sleep=time.sleep):
    http = session or requests
    for attempt in range(1, max_retries + 1):
        try:
            resp = http.get(url, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            wait = min(2 ** attempt * 5, 120)
            print(f"Network error ({exc}), retry {attempt}/{max_retries} in {wait}s")
            sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"Rate limited (429), waiting {wait}s")
            sleep(wait)
            continue

        if resp.status_code >= 500:
            wait = min(2 ** attempt * 5, 120)
            print(f"Server error {resp.status_code}, retry {attempt}/{max_retries} in {wait}s")
            sleep(wait)
            continue

        # Other 4xx: bad parameters or credentials, retrying will not help
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    raise RuntimeError("Max retries exceeded")
