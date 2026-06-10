"""McGill Visual Schedule Builder (VSB) read-only API client.

The class-data endpoint requires two anti-bot params, derived from the current
wall-clock minute. The algorithm is lifted verbatim from VSB's own common.js
(function nWindow): t = floor(now_ms/60000) % 1000, e = t%3 + t%39 + t%42.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

BASE_URL = "https://vsb.mcgill.ca/api/class-data"
REFERER = "https://vsb.mcgill.ca/criteria.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@dataclass
class Section:
    course_key: str       # e.g. "COMP-521"
    course_title: str     # e.g. "Modern Computer Games"
    section_no: str       # e.g. "001"
    block_type: str       # e.g. "Lec", "Tut", "Lab"
    open_seats: int       # current open seats (os attr)
    is_full: bool         # isFull attr ("1"/"0")
    status: str           # status attr — "A" usually
    waitlist_seats: int   # ws attr
    waitlist_capacity: int  # wc attr
    note: str             # n attr — e.g. "Section reserved for graduate students."
    teacher: str
    campus: str

    @property
    def label(self) -> str:
        return f"{self.block_type} {self.section_no}"

    @property
    def is_open(self) -> bool:
        # A section is "open" if it has at least one real seat and isn't marked full.
        return self.open_seats > 0 and not self.is_full


def _anti_bot_params() -> tuple[int, int]:
    t = (int(time.time() * 1000) // 60000) % 1000
    e = t % 3 + t % 39 + t % 42
    return t, e


def _normalize_course_code(code: str) -> str:
    """Accept 'COMP 521', 'comp521', 'COMP-521' and return 'COMP-521'."""
    s = code.strip().upper().replace(" ", "-")
    if "-" not in s:
        # Split letters from digits if user typed e.g. COMP521
        for i, ch in enumerate(s):
            if ch.isdigit():
                s = s[:i] + "-" + s[i:]
                break
    return s


class VSBError(Exception):
    pass


def fetch_course(term: str, course_code: str, timeout: float = 15.0) -> list[Section]:
    """Fetch all sections (Lec/Tut/Lab) for one course in one term.

    Raises VSBError on network errors or if VSB returned an <error> response.
    """
    course_key = _normalize_course_code(course_code)
    t, e = _anti_bot_params()
    qs = urllib.parse.urlencode({
        "term": term,
        "course_0_0": course_key,
        "va_0_0": "",
        "rq_0_0": "",
        "t": t,
        "e": e,
        "nouser": "1",
    })
    url = f"{BASE_URL}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": REFERER},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as exc:
        raise VSBError(f"Network error fetching {course_key}: {exc}") from exc

    # VSB sits behind an F5 firewall that returns an HTML "Request Rejected"
    # page when something about the request looks wrong (bad term, header that
    # trips a rule, etc.). Detect that before handing the body to the XML parser.
    head = raw[:200].lstrip().lower()
    if head.startswith(b"<html") or b"<title>request rejected" in head:
        raise VSBError(
            "Request was rejected by VSB's firewall. The term code or course "
            "code may be invalid, or VSB may be temporarily blocking us."
        )

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise VSBError(f"Could not parse VSB response: {exc}") from exc

    errors = [el.text for el in root.findall(".//errors/error") if el.text]
    if errors:
        raise VSBError("VSB returned: " + "; ".join(errors))

    sections: list[Section] = []
    for course_el in root.findall(".//course"):
        if course_el.get("key", "").upper() != course_key.upper():
            continue
        offering = course_el.find(".//offering")
        title = (offering.get("title", "") if offering is not None else "") or course_key
        for block in course_el.findall(".//block"):
            try:
                open_seats = int(block.get("os", "0") or "0")
            except ValueError:
                open_seats = 0
            try:
                ws = int(block.get("ws", "0") or "0")
            except ValueError:
                ws = 0
            try:
                wc = int(block.get("wc", "0") or "0")
            except ValueError:
                wc = 0
            sections.append(Section(
                course_key=course_key,
                course_title=title,
                section_no=block.get("secNo", ""),
                block_type=block.get("type", ""),
                open_seats=open_seats,
                is_full=(block.get("isFull", "0") == "1"),
                status=block.get("status", ""),
                waitlist_seats=ws,
                waitlist_capacity=wc,
                note=block.get("n", "") or "",
                teacher=block.get("teacher", "") or "",
                campus=block.get("campus", "") or "",
            ))
    return sections


if __name__ == "__main__":
    # Quick manual smoke test.
    import sys
    term = sys.argv[1] if len(sys.argv) > 1 else "202609"
    code = sys.argv[2] if len(sys.argv) > 2 else "COMP-521"
    for s in fetch_course(term, code):
        print(f"{s.course_key} {s.label}: open_seats={s.open_seats} "
              f"isFull={s.is_full} status={s.status} title={s.course_title!r} note={s.note!r}")
