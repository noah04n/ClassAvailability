# ClassAvailability — McGill VSB Course Availability Tracker

ClassAvailability watches McGill's Visual Schedule Builder (VSB) for the
courses you care about and emails you the moment a full section opens up. It
runs quietly in the system tray and polls VSB on an interval you choose.

- Track any number of sections (Lec / Tut / Lab) across terms.
- Get an email the moment a section transitions from full to open.
- Optionally get notified when a full section has a spot open on its waitlist.
- Route different courses to different recipients using profiles.
- Light/dark themes, system-tray operation, and a single-instance guard so you
  never accidentally run two pollers at once.

Known Limitations:
- Does not work for classes that are reserved closed.
---

## Table of Contents

- [Requirements](#requirements)
- [Quickstart](#quickstart)
  - [1. Install](#1-install)
  - [2. Set up the sending email](#2-set-up-the-sending-email)
  - [3. Launch and enter your settings](#3-launch-and-enter-your-settings)
  - [4. Add a course and send a test](#4-add-a-course-and-send-a-test)
- [Profiles](#profiles)
- [Add courses to track](#add-courses-to-track)
- [How notifications work](#how-notifications-work)
- [Send a test email](#send-a-test-email)
- [System tray](#system-tray)
- [Where settings are stored](#where-settings-are-stored)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- Windows with Python 3.10 or newer (the launchers use `python` / `pythonw`).
- A Gmail account to send notifications from (set up in the [Quickstart](#quickstart)).

The email and GUI code use only the Python standard library. The optional
`pystray` and `Pillow` packages add the system-tray icon; without them the app
still works but the close button just minimizes the window instead of hiding to
the tray.

---

## Quickstart

Install dependencies, set up a Gmail sender, launch the app, and start tracking
a course — a few minutes end to end.

**Install Python 3.10+**:
Download Python 3.10 or newer from the [Microsoft Store](https://apps.microsoft.com/detail/9PNRBTZXMB4Z?hl=en-us&gl=CA&ocid=pdpshare).

**Download:** Grab the ClassAvailability.zip latest release from
**[Here](https://github.com/Kenships/ClassAvailability/releases/)**,
Then unzip it to a folder of your choice.

### 1. Install

1. Open the folder you unzipped.
2. Double-click **`install.bat`**. This upgrades `pip` and installs the packages
   in `requirements.txt` (`pystray`, `Pillow`). Wait for it to print `Done.`

Manual alternative:

```
python -m pip install -r requirements.txt
```

### 2. Set up the sending email

Notifications go out over Gmail's SMTP server, which requires a **16-character
App Password** — not your normal account password.

1. Use (or create) a Gmail account to send from.
2. Turn on **2-Step Verification** for that Google account — App Passwords are
   only available once 2FA is enabled.
3. Go to **myaccount.google.com/apppasswords** and generate a new App Password.
   Google shows it as four groups of four characters (e.g. `abcd efgh ijkl mnop`).

> **⚠️ Don't use the same address to send and receive.**
> If the **Send from** address and the **Send notifications to** address are the
> same, you may never see the alert. When Gmail receives a message it sent to
> itself, it often files it under **Sent** / **All Mail** and skips the inbox,
> or collapses it into an existing thread — so the notification can silently go
> missing. Use a **different** address for the recipient (for example, send from
> a dedicated Gmail account and receive on your school or personal email). If you
> must use the same address, check **All Mail** and **Sent**, not just the inbox.

### 3. Launch and enter your settings

Launch the app:

- **`run.bat`** — silent launch with `pythonw` (no console window). Use this for
  everyday use.
- **`run-debug.bat`** — launches with a console window so you can see log output.
  Use this if something isn't working.

Only one copy runs at a time: if ClassAvailability is already running and you
launch it again, the second copy detects the first, shows a brief "already
running" message, and exits. If a launch seems to do nothing, check the **system
tray** (near the clock) and choose **Show ClassAvailability**.

The window has four tabs — **Tracked Courses**, **Add Course**, **Profiles**,
and **Settings**. Open **Settings** and fill in **Email notifications**:

- **Send notifications to:** the address that should receive alerts.
- **Send from (Gmail address):** the Gmail account from step 2.
- **Gmail App Password:** paste the 16-character code (tick **Show** to confirm
  you typed it correctly). Spaces are fine.
- **SMTP host / Port:** leave as `smtp.gmail.com` and `587` unless you know you
  need different values.

While you're here, set the rest:

- **Polling → Check interval (seconds):** how often to re-check VSB. Minimum 5s;
  **30s is recommended**. Very short intervals hit VSB harder and risk being
  temporarily blocked.
- **Behavior:** toggle *only-on-full→open* alerts, *waitlist-spot* alerts, *close
  to tray*, and *start polling on launch* (see [How notifications
  work](#how-notifications-work)).
- **Appearance → Theme:** `dark` or `light`, applied immediately on Save.

Click **Save settings** when done.

### 4. Add a course and send a test

1. Open the **Add Course** tab, pick the **Term**, and type the **course code**
   (`COMP 521`, `comp521`, or `COMP-521` all work). Click **Look up sections**.
2. Tick the sections you want and click **Add selected to tracker**.
3. Back in **Settings**, click **Send test email** to confirm delivery (see
   [Send a test email](#send-a-test-email)).

That's it — leave the app running (or minimized to the tray) and you'll be
emailed when seats open.

---

## Profiles

Profiles let you send notifications for different courses to different email
addresses — for example, route your own courses to your personal email and a
course you're watching for a friend to theirs.

- The **Default** profile always exists. It has no address of its own; it falls
  back to whatever you set as **Send notifications to** in Settings. So the
  Settings recipient is the single source of truth for the default.
- To add a recipient, go to the **Profiles** tab → **Add profile…**, give it a
  name and an email address. Use **Edit selected…** (or double-click a row) to
  change one, and **Remove selected** to delete it.

A single section can be assigned to **multiple profiles** — when it opens, the
app sends one email per recipient. This lets one course notify, say, both you
and an advisor independently.

You choose which profiles a course uses when you add it, and you can change it
later from **Tracked Courses → Change profile…**.

---

## Add courses to track

1. Open the **Add Course** tab.
2. Pick the **Term** from the dropdown.
3. Type the **course code**. Any of these formats work: `COMP 521`, `comp521`,
   or `COMP-521`.
4. Tick the **Notify profiles** you want this course to alert (manage the list
   in the Profiles tab).
5. Click **Look up sections**. The matching Lec/Tut/Lab sections appear with
   their current open-seat counts.
6. Tick the sections you want and click **Add selected to tracker**.

They now appear under **Tracked Courses**, showing status, open seats, the
assigned profile, and when each was last checked / last notified.

---

## How notifications work

While polling is running, the app re-checks every tracked section on your
chosen interval. A section counts as **open** when it has at least one real
open seat and isn't flagged full.

With *edge-trigger* on (the recommended default), you get an email only when a
section flips from **full → open**. You won't be re-spammed while it stays
open, and if it fills again and later reopens, you'll be notified again.

Each alert email includes the course code and title, the section, the term, the
number of open seats, any section note, and a link to McGill's registration
page so you can act quickly.

If *Also email when a full section has a spot open on its waitlist* is enabled,
you'll additionally get a one-time email when a **full** section has waitlist
room available (it won't fire for sections that are already open, since an open
seat is the better outcome and is reported separately). Like open-seat alerts,
this is edge-triggered: you're notified once when waitlist room appears, not on
every poll.

Start and stop polling from the **Tracked Courses** tab (**Start polling** /
**Stop polling**) or from the tray icon menu.

---

## Send a test email

Before relying on alerts, confirm your email setup works:

1. Make sure **Send from**, **Gmail App Password**, and **Send notifications
   to** are filled in under **Settings**, and that you clicked **Save settings**.
2. Click **Send test email**.
3. Check the recipient inbox for a message titled *"ClassAvailability test
   email"*. (Remember the same-address warning in the [Quickstart](#2-set-up-the-sending-email) —
   if you don't see it, check All Mail / Sent.)

If sending fails, the app shows the reason. The most common one is a login
error: double-check that **Send from** is correct and that you used the
16-character **App Password**, not your normal Google password.

---

## System tray

When *Close button minimizes to system tray* is enabled, closing the window
hides it to the tray instead of quitting, so polling keeps running. Right-click
(or click) the tray icon for:

- **Show ClassAvailability** — bring the window back.
- **Start polling / Stop polling**.
- **Quit** — fully exit the app.

If `pystray`/`Pillow` aren't installed, closing the window just minimizes it
instead of hiding to the tray.

---

## Where settings are stored

Your configuration (settings, tracked sections, and profiles) is saved to:

```
%APPDATA%\ClassAvailability\config.json
```

This lives outside the app folder, so you can move or update the app without
losing your setup. If the file ever becomes corrupted, the app backs it up as
`config.json.broken` and starts fresh rather than failing to launch.

Your **Gmail App Password is encrypted at rest** using Windows DPAPI (user
scope) — it is not stored as readable text in `config.json`. Because DPAPI ties
the encryption to your Windows user account, a `config.json` copied to a
different account or computer can't decrypt the password, so you'll simply be
asked to re-enter it there. (Email addresses and other settings are stored in
the clear, as they aren't secrets.)

---

## Troubleshooting

- **Launching does nothing / a second window won't open.** The app is already
  running (single-instance guard). Open it from the system tray.
- **No notification email arrives.** Check that polling is running, send a test
  email, and review the same-address warning in the Quickstart. Also check spam
  / All Mail.
- **"Gmail rejected the login."** Use a 16-character App Password (not your
  account password) and confirm the **Send from** address is correct and has
  2-Step Verification enabled.
- **"Request was rejected by VSB's firewall."** The term or course code may be
  invalid, or VSB is temporarily blocking requests. Verify the code and try a
  longer polling interval.
- **Window or text looks wrong / blurry.** Run via `run-debug.bat` to see log
  output, and make sure you're on Python 3.10+.
