"""Background polling loop.

A single Tracker instance runs one daemon thread that loops over all tracked
sections, fetches their current state from VSB, persists results, and fires an
email when a section transitions from closed -> open.

The tracker is decoupled from the GUI by an `on_update` callback: every change
emits a callback so the UI can schedule a repaint via root.after().
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone

import config
import notifier
import vsb_client


def _now_iso() -> str:
    # Local time, no microseconds — friendly for display.
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Tracker:
    def __init__(self, cfg: config.AppConfig,
                 on_update: Callable[[], None],
                 log: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.on_update = on_update
        self.log = log or (lambda _msg: None)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        # Aggregate status of the most recent poll cycle, shown in the status bar.
        self.last_cycle_started_iso: str | None = None
        self.last_cycle_finished_iso: str | None = None
        self.last_cycle_status: str = "Idle"
        # Heartbeat timer
        self._last_heartbeat_time: float | None = None

    # ---- thread lifecycle ----

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._wakeup.clear()
            self._last_heartbeat_time = time.time()
            self._thread = threading.Thread(
                target=self._run, name="vsb-tracker", daemon=True,
            )
            self._running = True
            self._thread.start()
            self.log("Polling started.")
            self.on_update()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop.set()
            self._wakeup.set()
            self._running = False
            self.log("Polling stopped.")
            self.on_update()

    @property
    def is_running(self) -> bool:
        return self._running

    def poke(self) -> None:
        """Wake the loop now (e.g. after the user adds a course or changes the
        interval). Safe to call from any thread."""
        self._wakeup.set()

    # ---- polling ----

    def _run(self) -> None:
        # First cycle fires immediately on start.
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                # Defensive: never let the loop die.
                self.last_cycle_status = "Error: " + traceback.format_exc(limit=1).strip().splitlines()[-1]
                self.log(f"Unhandled error in poll cycle: {traceback.format_exc()}")
                self.on_update()

            try:
                self._check_heartbeat()
            except Exception as exc:
                self.log(f"Error checking heartbeat: {exc}")

            interval = max(5, int(self.cfg.settings.poll_interval_seconds))
            self._wakeup.clear()
            # Wait up to `interval` seconds, but wake up early on poke()/stop().
            self._wakeup.wait(timeout=interval)

    def _poll_once(self) -> None:
        self.last_cycle_started_iso = _now_iso()
        self.on_update()

        # Group tracked sections by (term, course) so we only hit the API once
        # per course, even if the user is tracking multiple sections of it.
        with self._lock:
            tracked = list(self.cfg.tracked)
        if not tracked:
            self.last_cycle_finished_iso = _now_iso()
            self.last_cycle_status = "OK (no tracked courses)"
            self.on_update()
            return

        groups: dict[tuple[str, str], list[config.TrackedSection]] = {}
        for ts in tracked:
            groups.setdefault((ts.term, ts.course_code), []).append(ts)

        successes = 0
        failures = 0
        first_err: str | None = None
        notify_failures = 0

        for (term, code), members in groups.items():
            try:
                sections = vsb_client.fetch_course(term, code)
            except vsb_client.VSBError as exc:
                failures += 1
                if first_err is None:
                    first_err = f"{code}: {exc}"
                stamp = _now_iso()
                for ts in members:
                    ts.last_checked_iso = stamp
                    ts.last_status = f"Error: {exc}"
                continue

            stamp = _now_iso()
            by_key = {(s.block_type, s.section_no): s for s in sections}
            for ts in members:
                live = by_key.get((ts.block_type, ts.section_no))
                ts.last_checked_iso = stamp
                if live is None:
                    ts.last_status = "Section not found in current data"
                    continue
                successes += 1
                # Keep the cached title fresh in case VSB changes it.
                if live.course_title and live.course_title != ts.course_title:
                    ts.course_title = live.course_title

                was_open = ts.last_is_open
                is_open = live.is_open
                ts.last_open_seats = live.open_seats
                ts.last_is_open = is_open
                ts.last_status = (
                    f"OPEN — {live.open_seats} seat(s) available"
                    if is_open
                    else f"Full (waitlist {live.waitlist_seats}/{live.waitlist_capacity})"
                )

                # Edge-trigger: notify on transition from not-open -> open.
                # If we've never checked this section before (was_open is None)
                # and it's already open, fire once so the user knows immediately.
                should_notify = is_open and (was_open is None or was_open is False)
                if should_notify:
                    recipients = self.cfg.resolve_recipients(ts)
                    if not recipients:
                        notify_failures += 1
                        ts.last_status += " (no recipients configured)"
                        self.log(f"No recipients for {ts.course_code} {live.section_no}")
                    else:
                        subject, body = notifier.format_opening(
                            section_label=f"{live.block_type} {live.section_no}",
                            course_code=ts.course_code,
                            course_title=ts.course_title,
                            term=ts.term,
                            open_seats=live.open_seats,
                            note=live.note,
                        )
                        # Send one email per recipient so addresses aren't cross-leaked,
                        # and so one bad address doesn't fail the whole notification.
                        sent: list[str] = []
                        errs: list[str] = []
                        for addr in recipients:
                            try:
                                notifier.send_email(self.cfg.settings, subject, body, recipient=addr)
                                sent.append(addr)
                            except notifier.EmailError as exc:
                                errs.append(f"{addr}: {exc}")
                        if sent:
                            ts.last_notified_iso = stamp
                            self.log(
                                f"Notified {', '.join(sent)} about {ts.course_code} "
                                f"{live.block_type} {live.section_no} — {live.open_seats} open"
                            )
                        if errs:
                            notify_failures += len(errs)
                            ts.last_status += f" (email failed: {'; '.join(errs)})"
                            for e in errs:
                                self.log(f"Email failed for {ts.course_code} {live.section_no} → {e}")

                # Waitlist edge-trigger (opt-in): notify when a full section has
                # a spot open on its waitlist. Only meaningful while the section
                # is full — an open section is better news and handled above.
                if self.cfg.settings.notify_waitlist:
                    was_wl_avail = ts.last_waitlist_available
                    wl_avail = (not is_open) and live.waitlist_available
                    ts.last_waitlist_available = wl_avail
                    should_notify_wl = wl_avail and (was_wl_avail is None or was_wl_avail is False)
                    if should_notify_wl:
                        recipients = self.cfg.resolve_recipients(ts)
                        if not recipients:
                            notify_failures += 1
                            ts.last_status += " (no recipients configured)"
                            self.log(f"No recipients for {ts.course_code} {live.section_no}")
                        else:
                            subject, body = notifier.format_waitlist_opening(
                                section_label=f"{live.block_type} {live.section_no}",
                                course_code=ts.course_code,
                                course_title=ts.course_title,
                                term=ts.term,
                                waitlist_seats=live.waitlist_seats,
                                waitlist_capacity=live.waitlist_capacity,
                                note=live.note,
                            )
                            sent = []
                            errs = []
                            for addr in recipients:
                                try:
                                    notifier.send_email(self.cfg.settings, subject, body, recipient=addr)
                                    sent.append(addr)
                                except notifier.EmailError as exc:
                                    errs.append(f"{addr}: {exc}")
                            if sent:
                                ts.last_notified_iso = stamp
                                self.log(
                                    f"Notified {', '.join(sent)} about {ts.course_code} "
                                    f"{live.block_type} {live.section_no} — waitlist spot "
                                    f"({live.waitlist_seats} avail)"
                                )
                            if errs:
                                notify_failures += len(errs)
                                ts.last_status += f" (waitlist email failed: {'; '.join(errs)})"
                                for e in errs:
                                    self.log(f"Email failed for {ts.course_code} {live.section_no} → {e}")

        # Persist updated state so a restart doesn't re-fire emails.
        try:
            config.save(self.cfg)
        except OSError as exc:
            self.log(f"Could not save config: {exc}")

        self.last_cycle_finished_iso = _now_iso()
        if failures == 0 and notify_failures == 0:
            self.last_cycle_status = f"OK — checked {successes} section(s)"
        elif failures > 0:
            self.last_cycle_status = f"Errors fetching: {first_err}"
        else:
            self.last_cycle_status = f"OK ({successes}) but {notify_failures} email(s) failed"
        self.on_update()

    def _check_heartbeat(self) -> None:
        if not self.cfg.settings.heartbeat_enabled:
            self._last_heartbeat_time = None
            return

        now = time.time()
        if self._last_heartbeat_time is None:
            self._last_heartbeat_time = now
            return

        interval_seconds = max(1, self.cfg.settings.heartbeat_interval_hours) * 3600
        if now - self._last_heartbeat_time >= interval_seconds:
            self._last_heartbeat_time = now
            try:
                subject = "[ClassAvailability] Status Update: Still Polling"
                body = (
                    "This is an automated update to let you know that ClassAvailability is "
                    "still active and polling VSB.\n\n"
                    f"Current tracked courses: {len(self.cfg.tracked)} section(s)\n"
                    f"Check interval: {self.cfg.settings.poll_interval_seconds} seconds\n"
                    f"Status updates: Every {self.cfg.settings.heartbeat_interval_hours} hour(s)."
                )
                self.log("Sending heartbeat status email...")
                notifier.send_email(self.cfg.settings, subject, body)
                self.log("Heartbeat status email sent successfully.")
            except Exception as exc:
                self.log(f"Failed to send heartbeat status email: {exc}")
