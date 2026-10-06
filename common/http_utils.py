"""Shared HTTP helper with retry/backoff for the public APIs (ISRIC, NASA POWER, OpenWeatherMap)."""
import time
 
import requests
 
 
def get_json(url, params=None, headers=None, max_retries=5, timeout=60):
    """
    GET a URL and return parsed JSON.
    Retries on network errors, 429 (honours Retry-After) and 5xx with exponential backoff.
    Other 4xx errors are raised immediately because retrying will not fix bad parameters.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            wait = min(2 ** attempt * 5, 120)
            print(f"Network error ({exc}), retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue
 
        if resp.status_code == 200:
            return resp.json()
 
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"Rate limited (429), waiting {wait}s")
            time.sleep(wait)
            continue
 
        if resp.status_code >= 500:
            wait = min(2 ** attempt * 5, 120)
            print(f"Server error {resp.status_code}, retry {attempt}/{max_retries} in {wait}s")
            time.sleep(wait)
            continue
 
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
 
    raise RuntimeError("Max retries exceeded")
 