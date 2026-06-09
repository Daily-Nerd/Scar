import time

import requests

VENDOR_URL = "https://api.vendorpay.example.com/v1/charge"


def charge(payload, max_attempts=4):
    last_error = None
    for attempt in range(max_attempts):
        try:
            resp = requests.post(VENDOR_URL, json=payload, timeout=10)
            if resp.status_code == 429:
                time.sleep(7)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(7)
    raise RuntimeError(f"charge failed after {max_attempts} attempts") from last_error
