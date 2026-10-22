# void-tray

A minimal tray-based RSS/Atom reader for Linux, built with Python and GTK3 as a lightweight replacement for QuiteRSS. It also shows weather, Void package updates, Twitch and YouTube live channels, and a countdown timer in the same popup. Written for XFCE on Void Linux; it should work on any desktop with a `Gtk.StatusIcon`-compatible tray. This is the modular layout of the former single-file `rss-tray`: same behaviour, split into small modules (see [Project layout](#project-layout)).

## Features

### Tray icon, popup and feeds

- **Tray icon** with three states:
  - green: nothing unread (shows the current temperature)
  - orange: unread news
  - red: package updates available

  Live Twitch and YouTube channels are deliberately not counted in the badge.
- **Popup list** grouped by feed. Left-click a row to open it and mark it read, right-click a row to mark it read only, click the bookmark icon in front of a row to save it for later (see Read later), click a feed header to mark the whole feed read, right-click it for the feed menu (below). Hovering a news item shows its categories and author. The popup auto-opens on new items and closes on focus-out; if it is already open when something new arrives, the list is updated in place (scroll position and an open Launch list are kept).
- **Breaking news:** a news item whose title, or one of whose categories, contains the word "breaking" (whole word, any case, accents ignored) pulses a light red in the popup until you read or dismiss it; the categories are the ones listed in the item's tooltip. The words are configurable: put them under `[highlight]`, one per line or separated by `|` (for example `breaking|urgent`); with no `[highlight]` lines the default is `breaking`.
- **Feeds:** per-feed check intervals, custom display names, mute phrases (global, in `[mute]`) and per-feed mute rules (see below). Items older than 24 h are silently marked seen the first time they are seen. A feed that fails to load is retried after 2 minutes rather than a full interval, and feeds are not polled while offline. At most 500 unread items are kept; the oldest beyond that are dropped (they stay marked as seen and never come back). The feed sections appear in the order of the `[feeds]` lines in `config.conf`, whichever feed has the newest item, so reading an item never moves a feed; reorder the lines to reorder the sections.
- **Read later:** the bookmark icon in front of a news item moves it from the unread list to a separate **Read later** list under the feeds. It is not counted in the tray badge, never changes the icon, plays no sound and does not open the popup. Left-click a saved item to open it (it leaves the list), right-click it or use its minus icon to remove it without opening. Saved items are kept in `state.json` until you open or remove them. A feed's right-click menu can also make a category or an author **send new items straight to Read later** (see Per-feed rules); an item that matches both a mute rule and a read-later rule is muted.

### Live streams

- **Twitch:** live channels are polled (every 15 minutes by default, `interval=` in `[twitch]`; optionally only during per-channel schedules, see below) through Twitch's unofficial GQL API (no app registration), in batches of 20 channels. Rows read `<name> - <category> - <viewers> - <elapsed>`, where `<name>` is the channel's display name from the config (the channel name when it has none) (e.g. `channel - Chess - 283 - 3h 21min`; the stream title is the tooltip), and the bullet turns into a play triangle on the channel that's playing. Click a row to play it with `streamlink` and `mpv` at the configured quality (default `best`); the popup closes, and a second click on another channel reuses the same maximized mpv window. If either binary isn't installed, or launching fails, it opens the channel in your browser instead.
- **YouTube Live:** live channels are polled (every 15 minutes by default, `interval=` in `[youtube]`; same per-channel schedules) via `streamlink --json <channel>/live` per channel -- no API key, and the same extraction path used for actual playback, so the check can't disagree with what clicking the row does. There's no keyless batch API for YouTube, so this is one streamlink call per channel (heavier than Twitch's single batched request). Shares the same "Live now" list, quality config, and browser fallback as Twitch. Viewers and elapsed time come from one extra request to the live page for each channel that is live (best effort: if YouTube doesn't give them, the row simply omits them).
- **Player window titles:** mpv's title is `<Site> · <name> · <stream title>` for both platforms, with the configured display name when the channel has one and otherwise the name the platform gives.

### Podcasts, series and movies

- **Sources** (`[media]` in `config.conf`, one per line: `url|display name|order`): an [archive.org](https://archive.org) collection (each item an episode) or item (each video or audio file an episode, so a movie is one), or any podcast or video RSS/Atom feed with audio or video enclosures. Everything is DRM-free. Sites that need a login, or that protect their streams (the usual streaming services), are not supported.
- **Checking:** every hour by default (`interval=<minutes>`, minimum 10), and again as soon as the network is back. A source that cannot be read keeps what is known of it until the next check. The first check only sets the baseline: nothing is announced for what already exists.
- **Order:** an archive.org source is read as a series, from its first episode (`oldest`); a feed is read as a podcast, starting at its latest episode (`newest`) and skipping the backlog. Set the third field to the other one to change that. Episodes are ordered by `S01E05` / `1x05` in their titles where there are some, otherwise by date.
- **Downloads:** the next `ahead=<n>` unwatched episodes of every source (2 by default; `0` downloads only when you click) are kept on disk, one download at a time, into `path=<folder>` (default `~/videos`) as `<Source>/<Source> - S01E05 - <Title>.mp4` (the date instead of the number when the title has none, and the series name and number are cut from the title since they are already there). Downloads resume where they stopped after an interruption or a restart, and a failed one is retried at the next check or by clicking its row. For archive.org the h.264 version is preferred over the original and the small derivatives. Nothing is deleted when an episode is watched.
- **Subtitles:** every subtitle file (`.srt`, `.vtt`, `.ass`) an archive.org item has is saved next to the video with its language, `<name>.en.srt`, and mpv loads them on its own. RSS feeds carry none.
- **The popup:** a **Media** section under the live channels with one row per source: `Source - S01E05 Title - ready - 2 new`. The marker is green when the next episode is on disk, amber while it is queued or downloading (with the percent), red when the download failed (the reason is in the tooltip, which also shows the unwatched count and the last episode watched) and grey when it is not downloaded. Click the row to play the next unwatched episode in the shared mpv window, or to download it first if it is not on disk. New episodes open the popup and play the notification sound, like a channel going live; the section never counts in the tray badge and, like the live channels, does not keep the popup open once it is showing. Downloads update the rows in place.
- **Watched:** an episode counts as watched when playback reached 90 % of it, or got to its last 20 seconds after at least half; the next one is then downloaded. Where you stopped inside an episode is not remembered. The state (episode lists, what is watched and downloaded) is in `media.json` next to `state.json`.

### Weather

- **Weather** (Open-Meteo): today's temperature, high/low, wind and rain, with a 5-day forecast behind the `›` button (it turns into `‹` in the same spot, so a second click on it goes back). The popup is as wide as the wider of the two views needs, anchored to the right edge of the screen: two-digit or negative forecast temperatures make it grow to the left, and it does not change size when you switch views. Refreshed every 30 minutes by default (`interval=<minutes>` in `[weather]`, minimum 5).
- **Severe-weather alerts** (optional, `alerts=true` in `[weather]`): polls the MeteoAlarm feed for your country on the same cycle as the weather, filtered to the region matching the configured coordinates (country and region are reverse-geocoded via OpenStreetMap once and then stored in `config.conf`; delete the `country=`/`region=` lines to resolve them again; Europe only, since MeteoAlarm only covers European countries; if your coordinates resolve to a country it doesn't cover, the app writes `alerts=false` into `config.conf` itself). An active alert is color-coded by severity (yellow/orange/red for moderate/severe/extreme) and pulses the matching weather value and the tray badge, faster for more severe alerts. With nothing unread the pulsing badge shows the current temperature instead of a 0.

### Package updates

- **Void package updates:** a system-wide `xbps-install -Mn -u` dry run twice a day by default (`interval=<hours>` in `[updates]`). Click the "Updates available" header to install everything. With [topgrade](https://github.com/topgrade-rs/topgrade) installed that is one run of its `system` step (on Void: `xbps-install -Su xbps`, then `xbps-install -u`) plus any other steps you list under `steps=`; every row shows the same live status and counts as done when its installed version changed. Without topgrade (or with `installer=xbps`) it installs one package at a time, each with its own status. A scan is skipped while an install is running. Scanning always uses the xbps dry run: topgrade can run updates but cannot list what is pending.
- **Topgrade summary:** when Install all ran through topgrade, its closing summary is shown in the popup under the updates: a `Topgrade · 14:32 · OK · 3 packages updated` header, one line per step such as `System update: OK`, and one line per package whose installed version changed, as `libfoo 3.2 -> 3.3` (the build revision `_1` is left off unless it is all that changed), then the same for tools whose own output says what they went from and to (Claude Code, and each rustup toolchain; others, such as Codex or the dependencies inside uv tools, are not listed), with a red `Not updated:` line for any package that did not change (a step reading OK only means it ran without error). A failed step is shown in red with its last output lines, which is usually the reason (a missing sudo rule, for example). A clean summary clears itself after a minute; one with a failure stays, and keeps the popup from auto-closing, until you click its header. Topgrade runs with English output forced (`LC_ALL=C.UTF-8`) so the summary can be recognised whatever the session language.
- **Orphaned packages:** on the same cycle as the update scan, when the popup is opened by hand and after an install finishes, `xbps-query -O` (no root needed) lists packages installed as dependencies that nothing needs any more. When there are some, an "N orphaned packages" row appears below the updates. Click it to get **Remove** (`sudo -n xbps-remove -o -y`), **Remove and clean the package cache** (also `xbps-remove -O -y`) and **Cancel**, with the packages that would go listed underneath; nothing is removed until you pick a Remove row, and the result shows on the row for a few seconds. To keep a package, mark it with `xbps-pkgdb -m manual <name>`. The row never counts in the tray badge, changes the icon colour, plays a sound or opens the popup by itself, and `orphans=false` in `[updates]` hides it.

### Tools

- **Timer:** a slide behind the "Timer" footer button with two modes, switched at its top left:
  - **Countdown:** a slider (0 to 2 h in 1-minute steps by default; the maximum and the step are configurable with `max=<minutes>` and `step=<seconds>` in `[timer]`, see below). The label shows the time left and, in brackets, the clock time it ends at (e.g. `4 Hours, 34 Minutes and 23 Seconds (15:11)`). It counts down against a fixed deadline on the boot clock, so time the computer spends suspended counts, keeps counting with the popup closed, and plays the notification sound twice at zero. If it ran out while the computer was asleep it does not ring late: the slide's label and the corner readout show a pulsing red `missed 3 hours 12 minutes ago` (like a breaking item) until you move the slider or click the readout. The end time is also saved in `state.json`, so restarting the app (or rebooting) does not lose a running countdown: it carries on with the time that is left by the wall clock. One that ran out while the app was not running does not ring late either; it shows the same `missed ... ago` readout (or rings, if it ended only a few seconds before the start).
  - **Schedule:** recurring actions instead of a sound, e.g. suspend the machine every night and lock the screen on weekday mornings (see "Scheduled actions" below). Add as many schedules as you like; each has its own time, days and action, set right in the slide and stored in `config.conf`. The popup is always tall enough for this slide, even with nothing unread. While the Timer slide is closed and something is counting, a small readout in the popup's bottom-right corner shows, one per line and right-aligned, the running countdown as `hh:mm:ss` and each enabled schedule as its action label and the time it next fires (`Suspend 23:59`; a schedule a day or more away also names the weekday, `Power off Mon 07:30`; a snoozed one shows its snoozed time). It updates every second, disappears while the slide is open, and lets clicks through to the list.
- **Launcher:** a "Launch" footer button opens a list of your own commands (the `[launcher]` section, `Label|command` per line); clicking one runs it detached through the shell and closes the popup. The list is read when you open it, so edits apply immediately.

### Connectivity

- **Offline indicator:** a red "Offline" banner appears under the weather bar when the internet stops working, and goes away when it is back.
  - **Detection:** a probe runs every 10 s and when the popup opens. It checks that a public address accepts a connection **and** that a hostname resolves, so a DNS-only outage (the link looks fine, but nothing with a name works) counts too. A failed probe is re-checked after 3 s, and a failing feed, weather or live-channel request triggers a probe at once (at most one every 5 s), so the banner shows up within seconds. It takes two failed probes in a row to go offline, so one dropped probe doesn't flash it.
  - **Cause:** hover the banner for what failed and since when: DNS lookups failing, the router answering but the internet being unreachable (provider or upstream), the router not answering, or no default route (the link is down). The same line goes to the debug log (`VOID_TRAY_DEBUG=1`).
  - **Reset network button:** if `reset=<command>` is set in `[network]` (for example `reset=~/.local/bin/reset-network.sh`), the banner gets a "Reset network" button that runs it. It only runs when you click it; the button is disabled for 20 s while the connection comes back.
  - **While it shows:** left-clicking news items, saved items, feed headers, live channels and the "Updates available" header does nothing (and so do the menu's open buttons); right-clicking a news item still dismisses it (as does "Mark all read"). On the first successful probe the banner goes away and feeds, weather and live channels refresh immediately instead of waiting out their intervals.

## Requirements

- Python 3, PyGObject (GTK3), pycairo, `feedparser`
- `xbps-install` / `xbps-query` for update detection (Void Linux only)
- `topgrade` (optional, `sudo xbps-install topgrade`) to install updates with it; see [Passwordless updates](#passwordless-updates-optional)
- `streamlink` and `mpv` for Twitch and YouTube Live streams; `mpv` also plays the podcasts, series and movies
- A command-line audio player for the notification sound

```bash
sudo xbps-install -S python3-gobject python3-cairo python3-feedparser streamlink mpv
```

## Installation

```bash
git clone https://github.com/defaultpoi/void-tray.git ~/projects/void-tray
ln -sf ~/projects/void-tray/void-tray ~/.local/bin/void-tray
void-tray   # run once in the foreground to see any tracebacks
```

The launcher resolves the symlink, so updating is `git pull` and a restart of the app; there is no copy step.

Coming from the single-file `rss-tray`: on its first start without a config of its own, void-tray copies `config.conf`, `state.json` and `notification.wav` from `~/.config/rss-tray/` (the old directory is left untouched), so feeds, read state and the sound carry over.

Autostart: create `~/.config/autostart/void-tray.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Void Tray
Exec=/home/YOUR_USER/.local/bin/void-tray
Terminal=false
```

## Configuration

Everything lives in `~/.config/void-tray/`:

| File | Purpose |
|------|---------|
| `config.conf` | feeds, mute phrases, Twitch and YouTube channels |
| `state.json` | read/unread state, the Read later list, timestamps, cached updates and live channels, the end of a running countdown (safe to delete to reset, which also forgets saved items) |
| `notification.wav` | sound played on new items and when the timer ends (no sound if the file is absent) |

`config.conf` is INI-like:

```ini
[feeds]
# URL|display name|check interval in minutes|mute rules|read-later rules   (all but the URL optional)
https://example.com/feed.xml|My Blog|5
# Mute rules are field=value pairs separated by ;  -- e.g. hide every item of
# this feed that has the category "sports" or that this author wrote:
https://example.com/feed.xml|My Blog|15|category=sports; author=Some Author
# The fifth field takes the same rules and sends matching items straight to Read later:
https://example.com/feed.xml|My Blog|15||category=long reads

[mute]
# phrases (case and accents ignored), several per line separated by |; titles containing any are dropped
sponsored

[twitch]
# one channel name per line: channel|display name|quality|schedule  (all but the
# channel are optional and a field may be left empty; the display name is what the
# popup row and the player window call the channel, default the channel name;
# quality is a streamlink format such as 720p60, default "best"). Lines written
# before the display name existed (channel|quality|schedule) need an extra | after
# the channel.
# interval=<minutes> sets how often to check for live channels (default 15,
# minimum 1).
interval=15
# quality=<streamlink quality> for channels without their own; a comma-
# separated list is a fallback chain (first one that exists wins), e.g. 720p60
# if the stream has it, else 720p, else the best there is. Default: best.
quality=720p60,720p,best
somechannel
somechannel2|Some Name
weeklychannel|||wed 18:00-23:00
dailychannel|Daily Show|720p60|tue-sun 13:00-; sat 10:00-12:00

[youtube]
# one channel identifier per line -- whatever goes after youtube.com/, so
# either @handle or channel/UCxxxxxxxxxxxxxxxxxxxxxx (case-sensitive,
# unlike Twitch names). Optionally |display name|quality|schedule, same as [twitch].
# interval=<minutes> between live checks (default 15, minimum 5 -- each
# channel costs a full streamlink run).
interval=15
quality=720p,480p,best
@somehandle|Some Name
channel/UCxxxxxxxxxxxxxxxxxxxxxx|Other Name|720p60

[media]
# path=<folder> downloads go to (default ~/videos)
# interval=<minutes> between checks (default 60, minimum 10)
# ahead=<n> unwatched episodes kept downloaded per source (default 2; 0 = only on a click)
# One source per line: url|display name (optional)|order (optional)
#   url: an archive.org collection or item, or any podcast / video RSS or Atom feed
#   order: oldest (a series, from its first episode; the default for archive.org)
#          or newest (a podcast, from the latest; the default for feeds)
path=~/videos
https://archive.org/details/some-collection|Some Series
https://example.com/podcast.xml|Some Podcast|newest

[weather]
# lat|lon for the weather bar (Open-Meteo, no key needed); the weather
# bar stays hidden until this is set
<latitude>|<longitude>
# interval=<minutes> between weather (and alert) refreshes (default 30, minimum 5)
interval=30
alerts=true

[highlight]
# Words that make a news item pulse when they appear in its title or one of its categories (default: breaking)
breaking|urgent

[network]
# Command behind the "Reset network" button on the Offline banner (no button if
# empty). It runs only when you click the button.
reset=~/.local/bin/reset-network.sh

[updates]
# interval=<hours> between package update scans (default 12, minimum 1)
interval=12
# installer= what Install all runs: auto (topgrade if it is installed, else xbps-install;
# the default), topgrade or xbps
# steps= topgrade steps to run, comma-separated (default: system); `all` runs every step
# topgrade finds, and a step that needs a sudo password fails instead of asking. The names
# are the ones `topgrade --help` lists under --only: lowercase with underscores, for example
# system, flatpak, shell (Oh My Zsh and the like), rustup, uv, claude_code, github_cli_extensions
installer=auto
steps=system
# orphans=false hides the row that offers to remove orphaned packages
orphans=true

[actions]
# Label|command lines the timer's Schedule mode can run (the built-in default,
# used when this section is empty, is Suspend and Power off).
Suspend|loginctl suspend
Power off|loginctl poweroff

[schedule]
# Managed by the popup's Timer > Schedule; you can edit it by hand too.
# One schedule per line: on|off, HH:MM, days, action label from [actions]
# (days: daily, weekdays, weekends or e.g. mon,wed,fri).
on|00:30|daily|Suspend
off|07:00|weekdays|Lock screen
# Shared by all schedules: seconds of warning (0 = none), minutes the Snooze
# button postpones, and the seconds after which a missed run is skipped.
warn=60
snooze=30
grace=120

[launcher]
# Label|command per line for the Launch button. Commands run through the
# shell (so ~, quotes and && work), detached, from your home directory.
Files|thunar ~
Update system|xfce4-terminal -e "sudo xbps-install -Su"

[timer]
# max=<minutes> caps the slider (default 120); step=<seconds> sets the
# arrow-key/scroll increment (default 60). Both optional.
max=120
step=60
```

### Per-feed mute rules

**The easy way:** right-click a feed's title. A small menu has a row of three buttons, **Open all N as tabs** (every unread item of that feed becomes a browser tab one after another and counts as read, as opening one does), **Website** and **XML**, and below it two lists built from the feed's items that are **still unread**: Categories and Authors, each name with how many of those items carry it (dismiss or read some and the lists shrink accordingly). Every name has two buttons. **Mute** adds it to that feed's mute rules and hides the unread items it matches right away. **Later** adds it to the feed's read-later rules and moves the unread items it matches to Read later right away; new items that match go there as they arrive. A second category joins the same rule (`category=cat1, cat2`). The menu opens on the side of the title with more room and scrolls when the popup is short. To undo a rule, edit its line in `config.conf`. Items fetched before this version don't carry their categories, so they don't appear in the lists and stay until dismissed. Hover a news item to see which categories and author it has.

**By hand:** the rule syntax is below.

The fourth field of a `[feeds]` line mutes items of that feed by one of their fields, not just by words in the title. The fifth field has exactly the same syntax but sends matching items to Read later instead of hiding them (leave the fourth empty to use only the fifth: `url|name|5||category=long reads`). A rule is `field=value`; a comma lists several values for one field (`category=a,b,c`), and several rules are separated by `;`. An item is muted if **any** value of **any** rule matches. A muted item is marked read and never shown.

```ini
https://example.com/feed.xml|My Blog|15|category=sports,golf*; author=Some Author
```

- **Fields:** `category` (also `tag`/`tags`; a feed item with several `<category>` elements matches if any one equals the value), `author`, `title`, `summary`, `link`, or any other field the feed parser exposes for the item.
- **Commas:** `category=a,b,c` is the same as three separate `category=` rules. If a value itself contains a comma, write `\,` (e.g. `title=*Smith\, John*`).
- **Matching:** the whole value must match, ignoring case and accents (so `cafe` also matches "Café"); `*` and `?` are wildcards, so `category=*sports*` also catches "winter sports", and `title=*sponsor*` is a title substring rule for one feed only.
- **To see what to put:** open the feed address in a browser and look at the `<category>` elements of an item, or `<dc:creator>` for the author.
- **Not retroactive:** a rule written by hand applies to items fetched from now on; anything already in your unread list stays until you dismiss it. (The menu's Mute and Later buttons also act on the unread items at once.)

### Scheduled actions

Timer > Schedule runs one of your `[actions]` at a time of day, on the days you pick, with no cron job, `snooze` loop or other service involved (this replaces a script such as `snooze -H0 -M30 loginctl poweroff` in a `while` loop).

- **Several schedules:** the selector at the top of the slide lists them (`00:30 daily · Suspend`, with "(off)" on disabled ones); `+` adds one (off by default, so it can't surprise you) and `−` removes the selected one. A schedule is one line in `[schedule]`: `on|00:30|daily|Suspend`.
- **Controls** (for the selected schedule): the "On" checkbox, the time (hours and minutes), the action (from `[actions]`), and a Mo..Su button per day (at least one stays selected). Changes are saved to `config.conf` immediately, and edits made to the file by hand are picked up within a second.
- **Warning:** `warn=` seconds before a run (60 by default; `warn=0` for none) the notification sound plays, the popup opens, the tray icon flashes a red "!" and a red bar reads "Suspend in 42 s" with two buttons:
  - **Cancel** drops that run (the schedule's next run still happens).
  - **Snooze N min** postpones that run by `snooze=` minutes (30 by default). Pressing it again postpones further. The Snooze button in the slide does the same for the selected schedule before the warning starts.
  - If two schedules warn at once, the one that is due first is shown; each is handled separately.
- **Late runs are skipped:** the app only acts within `grace=` seconds (120 by default) of the scheduled time. If the machine was asleep or off at that moment, or you enabled or edited the schedule after the time had passed, that run is dropped instead of firing late. A run is never repeated after a wake-up.
- **Time:** the system's local clock, DST included. Every handled run is recorded in `state.json`, so restarting the app doesn't repeat it. Editing a schedule's time, days or action starts it afresh.
- **Actions:** any command works (`Lock screen|loginctl lock-session`, `Hibernate|systemctl hibernate`, ...); a schedule refers to it by label. A label that no longer exists is shown flagged in the slide and nothing runs.
- **From the first single-schedule version:** a `[schedule]` section with `enabled=`, `time=`, `days=` and `action=` lines is still read as one schedule, and is rewritten in the new format the first time you change anything in the slide.

### Channel schedules

A schedule limits live checks to the times a channel is expected to stream, so you aren't polling it around the clock. It is the third `|` field of a channel line (leave the quality empty to skip it: `channel||wed 18:00-23:00`).

- A schedule is one or more windows separated by `;`, each `[days] HH:MM[-HH:MM]` (`:` or `.` between hours and minutes).
- Days: `mon`...`sun` (or full names), ranges such as `tue-sun` (they may wrap, `fri-mon`), lists such as `mon,wed,fri`, `daily`, `weekdays`, `weekends`. Leave the days out for every day; leave the time out for the whole day.
- No end time means until midnight. An end earlier than the start runs past midnight into the next day (`fri 22:00-02:00`).
- Times are the system's local time (DST included). The app reads the system clock itself, so it needs no extra service or dependency.
- Inside a window the channel is checked at the normal `interval=`, and once right when the window opens. A channel that is live when its window ends keeps being checked until it goes offline. An unreadable schedule is ignored (the channel is then checked all the time).

## Passwordless updates (optional)

Nothing in a tray app can type a password, so installs must be allowed in sudoers or they fail immediately.

**With topgrade** (the default once it is installed) the `system` step runs `sudo xbps-install -Su xbps -y` and then `sudo xbps-install -u -y`. One command that writes a rule for exactly those two, checks it with `visudo` and only then installs it:

```bash
t=$(mktemp) && echo "$USER ALL=(root) NOPASSWD: /usr/bin/xbps-install -Su xbps -y, /usr/bin/xbps-install -u -y" > "$t" && sudo visudo -cf "$t" && sudo install -m 440 -o root -g root "$t" /etc/sudoers.d/zz-void-tray; rm -f "$t"
```

This rule only allows upgrading what is already installed; it cannot install arbitrary packages. Other steps (for example a system-wide flatpak update) need their own rule, or they fail instead of asking. If `doas` is installed, topgrade prefers it over `sudo`; set `sudo_command = "sudo"` under `[misc]` in `~/.config/topgrade.toml` to pin it.

**Without topgrade** (`installer=xbps`, or topgrade is not installed) installs run `sudo -n xbps-install -Su -y <pkg>`, which fails immediately if a password would be needed. Allow it with a scoped rule:

```bash
sudo visudo -f /etc/sudoers.d/zz-void-tray
```

```
YOUR_USER ALL=(root) NOPASSWD: /usr/bin/xbps-install -Su -y *
```

The `zz-` prefix matters: sudoers uses the last matching rule, so the file must sort after anything that might override it.

**Security note:** the xbps rule lets any process running as your user run `xbps-install -Su -y` (and so install any package) as root without a password, not just this app. The topgrade rule is much narrower, which is a reason to prefer it.

**Removing orphaned packages** runs `sudo -n xbps-remove -o -y` and, with the cache option, `sudo -n xbps-remove -O -y`. Without a rule they fail at once and the row shows "Failed". One command that writes a rule for exactly those two:

```bash
t=$(mktemp) && echo "$USER ALL=(root) NOPASSWD: /usr/bin/xbps-remove -o -y, /usr/bin/xbps-remove -O -y" > "$t" && sudo visudo -cf "$t" && sudo install -m 440 -o root -g root "$t" /etc/sudoers.d/zz-void-tray-remove; rm -f "$t"
```

To run every topgrade step interactively, put it in the Launch list, for example `Topgrade|xfce4-terminal -e topgrade` under `[launcher]`.

## Design notes

- The popup is a borderless `Gtk.Window` that is destroyed and rebuilt on every manual open (an auto-open while it is already showing just refreshes the list). This avoids stuck-size bugs; do not make it persistent or call `resize()` on it.
- Package updates are system-wide rather than feed-based, because GitHub's atom feed is a sliding window and version bumps get missed.
- Threading: `lock` guards state, `_check_lock` RSS cycles, `_xbps_lock` xbps scans and installs, `_twitch_lock`/`_youtube_lock` their respective live-channel checks. All GTK mutations from threads go through `GLib.idle_add`.
- A failed xbps scan or Twitch request keeps the previous state; failed scans back off exponentially up to the normal update interval.
- The install timeout kills the whole process group (`sudo` and the `xbps-install` it forked).

## Known limitations

- `Gtk.StatusIcon` is deprecated upstream but still works on XFCE and most X11 panels; the app silences its deprecation warnings. If a future GTK drops it, the tray icon will need porting to an AppIndicator (which would replace the popup with a menu).
- Twitch's GQL API is unofficial and undocumented; it can break without notice.
- YouTube Live detection depends on streamlink's own YouTube plugin staying current with YouTube's changes; a channel's liveness check takes a few seconds (full streamlink extraction), unlike Twitch's near-instant batched check.
- Update detection and installs are Void-specific.

## Project layout

```
void-tray              launcher (symlink it into ~/.local/bin)
void_tray/
  app.py               RssTray (assembled from the mixins below), signals, main()
  constants.py         constants shared by several modules
  config.py            config.conf: creation, every section reader, the single writer
  state.py             state.json load/save
  rules.py             item ids and ages, mute rules, highlight words, tooltip details
  windows.py           day/time windows for per-channel live schedules
  schedule.py          recurring timer actions: parsing, saving, occurrence maths
  network.py           connectivity probes and diagnosis
  updates.py           the xbps dry run and its parser
  live.py              Twitch/YouTube live checks and formatting
  playback.py          one persistent mpv fed by streamlink
  weather.py           Open-Meteo, MeteoAlarm, weather glyphs
  desktop.py           sound, fullscreen detection, editor, launcher
  gi_env.py            the GObject-introspection imports, in one place
  ui/                  mixins: feeds, live, weather, updates, timer, icon, connectivity, popup
tests/                 one test file per module, plus facade.py, helpers.py and run_stubbed.py
```

Pure logic (parsers, config readers, schedule maths) lives in the top-level modules and never touches GTK; everything that builds or updates widgets is in `ui/`.

## Tests

```bash
python3 -m unittest discover -s tests -v   # real PyGObject (needs PyGObject and feedparser)
python3 tests/run_stubbed.py               # GTK replaced by mocks; both must pass
```

CI runs both on every push. Tests refer to the code through `tests/facade.py`, one namespace over the whole package: reading `rt.name` returns the value from the module that defines it, and `mock.patch.object(rt, 'name', ...)` rebinds it in every module that imported it, so a patch reaches the code under test wherever it lives.

## License

MIT, see [LICENSE](LICENSE).
