"""CIK-bound retrieval receipts for explicitly reviewed SEC identities."""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests


def https_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def evidence_available(member):
    cik = member.get("edgar_identity_cik")
    urls = member.get("edgar_identity_evidence")
    checks = member.get("edgar_identity_evidence_checks")
    if not isinstance(urls, list) or not urls or not all(https_url(u) for u in urls):
        return False
    if not isinstance(checks, list) or len(checks) != len(urls):
        return False
    return all(
        isinstance(check, dict)
        and check.get("url") == url
        and check.get("cik") == cik
        and check.get("status") == "available"
        and https_url(check.get("resolved_url"))
        and isinstance(check.get("checked_at"), str) and bool(check["checked_at"])
        and isinstance(check.get("sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", check["sha256"])
        for url, check in zip(urls, checks)
    )


def retrieve_identity_evidence(cik, urls, headers):
    """Fetch declared evidence once; failures remain explicit, never bypassed.

    A successful retrieval is not identity proof. A separate explicit manual
    verification and matching CIK are still mandatory.
    """
    checks = []
    for url in urls:
        check = {"url": url, "cik": cik, "checked_at": datetime.now(timezone.utc).isoformat(),
                 "status": "unavailable"}
        try:
            current = url
            for redirect in range(4):
                if not https_url(current):
                    raise ValueError("evidence requires HTTPS")
                with requests.get(current, headers=headers, timeout=30,
                                  stream=True, allow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        current = urljoin(current, response.headers.get("Location", ""))
                        continue
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("evidence did not return HTTP 200")
                    digest, size = hashlib.sha256(), 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 8 * 1024 * 1024:
                            raise ValueError("evidence exceeds 8 MiB review limit")
                        digest.update(chunk)
                    if not size:
                        raise ValueError("empty evidence")
                    check.update(status="available", resolved_url=current,
                                 sha256=digest.hexdigest(), bytes=size)
                    break
            else:
                raise ValueError("too many evidence redirects")
        except (requests.RequestException, ValueError) as exc:
            check["error"] = str(exc)
        checks.append(check)
    return checks
