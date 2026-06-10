"""JSON-backed configuration + persistent state.

Stored under %APPDATA%\\ClassAvailability\\config.json so the file survives
moving the app folder and isn't accidentally committed.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path


# --- Secret-at-rest encryption (Windows DPAPI) ---
#
# The Gmail App Password is the only secret we persist. We encrypt it with
# Windows DPAPI (user scope) so it isn't readable as plaintext in config.json.
# DPAPI ties the ciphertext to the current Windows user account — no key to
# manage, and a copied config can't be decrypted by another user/machine.
# In memory the password stays plaintext (SMTP login needs it); encryption
# only happens at the JSON boundary.

_DPAPI_PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_call(func, data: bytes) -> bytes:
    blob_in = _DataBlob(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                    ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = _DataBlob()
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _protect_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns 'dpapi:<base64>' on success.
    On non-Windows or if DPAPI fails, returns plaintext unchanged — better to
    keep working than to silently drop the password."""
    if not plaintext or os.name != "nt":
        return plaintext
    try:
        enc = _dpapi_call(ctypes.windll.crypt32.CryptProtectData, plaintext.encode("utf-8"))
        return _DPAPI_PREFIX + base64.b64encode(enc).decode("ascii")
    except Exception:
        return plaintext


def _unprotect_secret(stored: str) -> str:
    """Inverse of _protect_secret. Values without the prefix are treated as
    legacy plaintext and returned as-is (they get encrypted on next save).
    If decryption fails (e.g. config from another user), returns '' so the app
    prompts for re-entry instead of crashing."""
    if not stored or not stored.startswith(_DPAPI_PREFIX):
        return stored
    if os.name != "nt":
        return ""
    try:
        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        return _dpapi_call(ctypes.windll.crypt32.CryptUnprotectData, raw).decode("utf-8")
    except Exception:
        return ""


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        # Fallback for non-Windows or stripped envs
        base = str(Path.home() / ".config")
    p = Path(base) / "ClassAvailability"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULT_PROFILE_NAME = "Default"


@dataclass
class Profile:
    """A named recipient. Each tracked section is bound to one profile so
    different courses can notify different addresses (e.g. school vs personal
    email, or routing certain courses to a partner/advisor)."""
    name: str
    recipient_email: str


@dataclass
class TrackedSection:
    course_code: str         # normalized, e.g. "COMP-521"
    course_title: str        # cached for display, e.g. "Modern Computer Games"
    term: str                # e.g. "202609"
    section_no: str          # e.g. "001"
    block_type: str          # e.g. "Lec"
    # Edge-trigger state: last seen open_seats value. None = never checked yet.
    last_open_seats: int | None = None
    last_is_open: bool | None = None
    last_checked_iso: str | None = None
    last_status: str = "Not yet checked"
    # When did we last fire an email for this section opening? ISO string.
    last_notified_iso: str | None = None
    # Edge-trigger state for waitlist notifications: was a waitlist spot
    # available on the last poll? None = never checked yet.
    last_waitlist_available: bool | None = None
    # Which profiles (recipients) get notified for this section. Multiple
    # profiles means the opening fires one email per profile, so a single
    # course can ping e.g. a student and an advisor independently. An empty
    # list falls back to settings.recipient_email.
    profile_names: list[str] = field(default_factory=lambda: [DEFAULT_PROFILE_NAME])

    @property
    def key(self) -> str:
        return f"{self.term}|{self.course_code}|{self.block_type}|{self.section_no}"


@dataclass
class Settings:
    poll_interval_seconds: int = 30
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str = ""
    sender_app_password: str = ""
    recipient_email: str = ""
    # Only re-notify when a section closes and reopens (edge-trigger).
    # If False, we'd spam every poll while it's open; default True.
    edge_trigger_notifications: bool = True
    # Also notify when a full section has a spot open on its waitlist.
    notify_waitlist: bool = False
    minimize_to_tray_on_close: bool = True
    start_polling_on_launch: bool = True
    theme: str = "dark"  # "dark" or "light"


@dataclass
class AppConfig:
    settings: Settings = field(default_factory=Settings)
    tracked: list[TrackedSection] = field(default_factory=list)
    profiles: list[Profile] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ensure_default_profile()

    def to_json(self) -> str:
        settings_d = asdict(self.settings)
        # Encrypt the only secret before it touches disk.
        settings_d["sender_app_password"] = _protect_secret(self.settings.sender_app_password)
        return json.dumps(
            {"settings": settings_d,
             "tracked": [asdict(t) for t in self.tracked],
             "profiles": [asdict(p) for p in self.profiles]},
            indent=2,
        )

    @classmethod
    def from_json(cls, data: str) -> "AppConfig":
        obj = json.loads(data)
        settings_d = obj.get("settings", {}) or {}
        tracked_d = obj.get("tracked", []) or []
        profiles_d = obj.get("profiles", []) or []
        # Defensive: drop unknown keys so renaming a field doesn't break load.
        settings_fields = {f for f in Settings.__dataclass_fields__}
        tracked_fields = {f for f in TrackedSection.__dataclass_fields__}
        profile_fields = {f for f in Profile.__dataclass_fields__}
        s = Settings(**{k: v for k, v in settings_d.items() if k in settings_fields})
        # Decrypt the stored secret back to plaintext for in-memory use.
        s.sender_app_password = _unprotect_secret(s.sender_app_password)
        ts = []
        for t in tracked_d:
            kwargs = {k: v for k, v in t.items() if k in tracked_fields}
            # Migrate legacy single-profile field if present in older configs.
            if "profile_names" not in kwargs and "profile_name" in t:
                legacy = t.get("profile_name") or DEFAULT_PROFILE_NAME
                kwargs["profile_names"] = [legacy]
            ts.append(TrackedSection(**kwargs))
        ps = [
            Profile(**{k: v for k, v in p.items() if k in profile_fields})
            for p in profiles_d
        ]
        # __post_init__ handles ensure_default_profile().
        return cls(settings=s, tracked=ts, profiles=ps)

    # --- profile helpers ---

    def ensure_default_profile(self) -> None:
        """Guarantee a 'Default' profile exists. Its recipient_email is left
        empty so it always falls back to settings.recipient_email — that way
        the Settings tab is the single source of truth for the default
        recipient and the Profiles tab doesn't fight it."""
        if not any(p.name == DEFAULT_PROFILE_NAME for p in self.profiles):
            self.profiles.insert(
                0, Profile(name=DEFAULT_PROFILE_NAME, recipient_email=""),
            )

    def get_profile(self, name: str) -> Profile | None:
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def resolve_recipients(self, section: "TrackedSection") -> list[str]:
        """All email addresses that should be notified for this section.

        Each assigned profile contributes its recipient_email if non-empty;
        otherwise it falls back to settings.recipient_email. Result is
        deduplicated (case-insensitive) while preserving the order profiles
        were bound to the section. Returns [] only if nothing is configured."""
        out: list[str] = []
        seen: set[str] = set()
        fallback = self.settings.recipient_email.strip()
        # If no profiles are assigned at all, use the global fallback.
        names = section.profile_names or [DEFAULT_PROFILE_NAME]
        for name in names:
            p = self.get_profile(name)
            addr = (p.recipient_email if p and p.recipient_email else fallback).strip()
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                out.append(addr)
        return out


def load() -> AppConfig:
    p = config_path()
    if not p.exists():
        return AppConfig()
    try:
        return AppConfig.from_json(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, ValueError):
        # Corrupt config — back it up and start fresh rather than crashing.
        backup = p.with_suffix(".json.broken")
        try:
            p.replace(backup)
        except OSError:
            pass
        return AppConfig()


def save(cfg: AppConfig) -> None:
    """Atomic write — write to a temp file then rename, so a crash mid-write
    can't corrupt the live config."""
    p = config_path()
    d = p.parent
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cfg.to_json())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    cfg = load()
    print("Config path:", config_path())
    print(cfg.to_json())
