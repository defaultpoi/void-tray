"""Package update scans and install-all."""

import collections
import hashlib
import subprocess
import threading
import time as time_module

from ..gi_env import GLib
from ..config import load_orphan_nudge, load_updates_check_interval, load_updates_installer
from ..log import _log
from ..proc import _kill_process_group
from ..state import save_state
from ..updates import (
    TopgradeOutput,
    choose_installer,
    get_installed_version,
    list_all_updates,
    list_orphans,
    topgrade_command,
    topgrade_env,
    version_change,
)


PRIVILEGE_CMD = ['sudo', '-n']  # -n: fail fast rather than hang if a password would be needed;


UPDATE_TIMEOUT_SECONDS = 1800  # 30 minutes
ORPHAN_STATUS_CLEAR_MS = 4000  # how long Done/Failed stays on the orphaned-packages row
SUMMARY_CLEAR_SECONDS = 60  # a successful topgrade summary goes by itself; a failed one stays until clicked


def _run_install_command(cmd, on_status, shown='Installing…', on_line=None, env=None):
    """Runs an install command to completion, killing its whole process group
    after UPDATE_TIMEOUT_SECONDS, and calls on_status('Downloading…' or
    'Installing…') whenever xbps-install's section headers change, and on_line
    (if given) with every non-empty output line. Returns (exit code, last output
    lines); the exit code is -1 if it could not run."""
    returncode = -1
    watchdog = None
    proc = None
    tail = collections.deque(maxlen=5)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True, env=env
        )
        # stdout iteration blocks until EOF, so a wait(timeout=)
        # afterwards can never fire on a hang; the watchdog can.
        watchdog = threading.Timer(
            UPDATE_TIMEOUT_SECONDS, _kill_process_group, args=(proc,)
        )
        watchdog.daemon = True
        watchdog.start()
        # xbps-install prints section headers like "[*] Downloading
        # packages", "[*] Collecting package files", "[*] Unpacking
        # packages", "[*] Configuring unpacked packages" — the actual
        # per-file lines under them don't contain words like
        # "download" at all, so we key off these headers instead.
        # Every line in this stream belongs to the command being run
        # regardless of wording.
        for line in proc.stdout:
            stripped = line.strip()
            if stripped:
                tail.append(stripped)
                if on_line is not None:
                    on_line(stripped)
            if not stripped.startswith('[*]'):
                continue
            status = 'Downloading…' if stripped.startswith('[*] Downloading') else 'Installing…'
            if status != shown:  # each change rebuilds the list; skip repeats
                shown = status
                on_status(status)
        proc.wait()
        returncode = proc.returncode
    except Exception:
        pass
    finally:
        if watchdog is not None:
            watchdog.cancel()
        close = getattr(getattr(proc, 'stdout', None), 'close', None)
        if close is not None:
            close()  # reading to EOF does not close the pipe
    return returncode, list(tail)


class UpdatesMixin:
    def check_updates_if_due(self, force=False):
        now = time_module.time()
        with self.lock:
            last = self.state.get('updates_last_checked', 0)
            last_attempt = self.state.get('updates_last_attempt', 0)
            fails = self.state.get('updates_fail_count', 0)
        if not force:
            interval = load_updates_check_interval()
            if (now - last) < interval:
                return
            if fails and (now - last_attempt) < min(300 * 2 ** (fails - 1), interval):
                return  # backing off after failed scans
        if not self._xbps_lock.acquire(blocking=False):
            return  # an install is running: scanning mid-transaction would give a wrong list
        try:
            pkgnames = list_all_updates()
        finally:
            self._xbps_lock.release()
        if pkgnames is None:
            with self.lock:
                self.state['updates_last_attempt'] = now
                self.state['updates_fail_count'] = min(fails + 1, 10)
                save_state(self.state)
            return  # keep the previous available_updates untouched
        with self.lock:
            existing = {e['pkgname']: e for e in self.state.get('available_updates', [])}
            promoted_new = []
            for pkgname in pkgnames:
                if pkgname not in existing:
                    entry = {
                        'id': hashlib.sha1(f"update:{pkgname}".encode()).hexdigest(),
                        'title': f"Update available for {pkgname}",
                        'link': '',
                        'pkgname': pkgname,
                    }
                    existing[pkgname] = entry
                    promoted_new.append(entry)
            self.state['available_updates'] = [existing[p] for p in pkgnames if p in existing]
            self.state['updates_last_checked'] = now
            self.state['updates_fail_count'] = 0
            save_state(self.state)
        if promoted_new:
            GLib.idle_add(self.on_new_items)  # reuse: sound + auto-popup + icon refresh

    def _begin_install(self):
        self.active_installs += 1
        self.update_icon()
        self.refresh_list()  # show per-row status under "Updates available"

    def _end_install(self):
        self.active_installs = max(0, self.active_installs - 1)
        self.update_icon()
        # an upgrade can leave old dependencies behind as orphans: look again
        threading.Thread(target=self.check_orphans_if_due, kwargs={'force': True}, daemon=True).start()

    def install_all_updates(self):
        if self.active_installs > 0:
            return  # already running
        with self.lock:
            pkgnames = [e['pkgname'] for e in self.state.get('available_updates', [])]
        if not pkgnames:
            return
        self.install_status = {pkg: 'Waiting…' for pkg in pkgnames}
        self.update_summary = None  # the last run's summary is stale now
        self._begin_install()
        threading.Thread(target=self._run_update_all, args=(pkgnames,), daemon=True).start()

    def _set_status(self, pkgname, status):
        GLib.idle_add(self._apply_status, pkgname, status)

    def _apply_status(self, pkgname, status):
        self.install_status[pkgname] = status
        self.refresh_list()
        return False

    def _finalize_package_removal(self, pkgname):
        with self.lock:
            self.state['available_updates'] = [
                e for e in self.state.get('available_updates', []) if e['pkgname'] != pkgname
            ]
            save_state(self.state)
        self.install_status.pop(pkgname, None)
        self.update_icon()
        self.refresh_list()
        return False

    def check_orphans_if_due(self, force=False):
        """Looks for orphaned packages (`xbps-query -O`, no root) on the update
        scan's schedule, and keeps the previous list when the query fails."""
        if not load_orphan_nudge():
            return
        now = time_module.time()
        with self.lock:
            last = self.state.get('orphans_last_checked', 0)
        if not force and (now - last) < load_updates_check_interval():
            return
        if not self._xbps_lock.acquire(blocking=False):
            return  # an install or removal is running: the list would be wrong mid-transaction
        try:
            names = list_orphans()
        finally:
            self._xbps_lock.release()
        with self.lock:
            changed = names is not None and names != self.state.get('orphans', [])
            if names is not None:
                self.state['orphans'] = names
            self.state['orphans_last_checked'] = now  # a failed query also waits a full interval
            save_state(self.state)
        if changed:
            GLib.idle_add(self._orphans_changed)

    def _orphans_changed(self):
        if self.popup is not None and self.popup.get_visible():
            self.refresh_list(keep_open=True)  # a scan finishing must not close a popup someone is reading
        return False

    def remove_orphans(self, clean_cache=False):
        if self.active_installs > 0:
            return  # an install or removal is already running
        self.orphan_status = 'Removing…'
        self._begin_install()
        threading.Thread(target=self._run_orphan_removal, args=(clean_cache,), daemon=True).start()

    def _run_orphan_removal(self, clean_cache):
        """`sudo -n xbps-remove -o -y`, then `-O -y` for the cache when asked.
        Exactly these argument lists, so a sudoers rule can allow them and nothing
        broader. Afterwards the list is read again: removing orphans can orphan
        their dependencies, and those are shown for the next round."""
        commands = [PRIVILEGE_CMD + ['xbps-remove', '-o', '-y']]
        if clean_cache:
            commands.append(PRIVILEGE_CMD + ['xbps-remove', '-O', '-y'])
        status = 'Done'
        with self._xbps_lock:
            for cmd in commands:
                returncode, tail = _run_install_command(cmd, lambda _status: None)
                if returncode != 0:
                    _log(f"{' '.join(cmd)} exited with {returncode}: " + ' | '.join(tail))
                    status = 'Failed'
                    break
            remaining = list_orphans()
        with self.lock:
            if remaining is not None:
                self.state['orphans'] = remaining
            self.state['orphans_last_checked'] = time_module.time()
            save_state(self.state)
        GLib.idle_add(self._finish_orphan_removal, status)

    def _finish_orphan_removal(self, status):
        self.active_installs = max(0, self.active_installs - 1)
        self.orphan_status = status
        self.update_icon()
        self.refresh_list()
        GLib.timeout_add(ORPHAN_STATUS_CLEAR_MS, self._clear_orphan_status)
        return False

    def _clear_orphan_status(self):
        self.orphan_status = None
        if self.popup is not None:
            self.refresh_list()
        return False

    def _show_update_summary(self, summary):
        """Shows topgrade's closing summary in the popup. A clean one clears itself after a
        minute; one with a failed step stays until it is clicked, so it is not missed."""
        self.update_summary = summary
        if self.popup is not None:
            self.refresh_list(keep_open=True)
        if summary['ok']:
            GLib.timeout_add_seconds(SUMMARY_CLEAR_SECONDS, self._clear_update_summary, summary)
        return False

    def _clear_update_summary(self, summary=None):
        if summary is not None and self.update_summary is not summary:
            return False  # a newer run has replaced it
        self.update_summary = None
        if self.popup is not None:
            self.refresh_list()
        return False

    def _set_all_status(self, pkgnames, status):
        GLib.idle_add(self._apply_all_status, list(pkgnames), status)

    def _apply_all_status(self, pkgnames, status):
        for pkgname in pkgnames:
            self.install_status[pkgname] = status
        self.refresh_list()
        return False

    def _run_update_all(self, pkgnames):
        installer, steps = load_updates_installer()
        if choose_installer(installer) == 'topgrade':
            self._run_topgrade(pkgnames, steps)
            return
        with self._xbps_lock:
            versions_at_start = {pkg: get_installed_version(pkg) for pkg in pkgnames}
            for pkgname in pkgnames:
                before = get_installed_version(pkgname)
                if before != versions_at_start[pkgname]:
                    # `xbps-install -u <pkg>` also updates <pkg>'s outdated
                    # dependencies in the same transaction, so a package listed
                    # later may already have been updated as someone's
                    # dependency; running xbps again would change nothing and
                    # wrongly look like a failure.
                    self._set_status(pkgname, 'Done')
                    GLib.timeout_add(1200, self._finalize_package_removal, pkgname)
                    continue
                self._set_status(pkgname, 'Installing…')
                cmd = PRIVILEGE_CMD + ['xbps-install', '-Su', '-y', pkgname]
                # since we install one package at a time, every status change
                # in the stream belongs to this package
                returncode, _tail = _run_install_command(
                    cmd, lambda status, pkg=pkgname: self._set_status(pkg, status))
                after = get_installed_version(pkgname)
                success = returncode == 0 and before != after
                if success:
                    self._set_status(pkgname, 'Done')
                    GLib.timeout_add(1200, self._finalize_package_removal, pkgname)
                else:
                    self._set_status(pkgname, 'Failed')
        GLib.idle_add(self._end_install)

    def _run_topgrade(self, pkgnames, steps):
        """One topgrade run for everything pending instead of one xbps-install
        per package. Its system step on Void is `sudo xbps-install -Su xbps`
        then `sudo xbps-install -u`, and it also runs any other steps listed
        under steps= in [updates]. It cannot report per package, so every row
        shares one status, and afterwards each package counts as done when its
        installed version changed, however topgrade itself exited; the summary
        shown afterwards names which did and which did not."""
        with self._xbps_lock:
            before = {pkg: get_installed_version(pkg) for pkg in pkgnames}
            self._set_all_status(pkgnames, 'Updating…')
            output = TopgradeOutput()
            returncode, tail = _run_install_command(
                topgrade_command(steps), lambda status: self._set_all_status(pkgnames, status),
                shown='Updating…', on_line=output.feed, env=topgrade_env())
            if returncode != 0:
                _log(f'topgrade exited with {returncode}: ' + ' | '.join(tail))
            updated, not_updated, changes = [], [], []
            for pkgname in pkgnames:
                now = get_installed_version(pkgname)
                if now != before[pkgname]:
                    updated.append(pkgname)
                    changes.append(version_change(pkgname, before[pkgname], now))
                    self._set_status(pkgname, 'Done')
                    GLib.timeout_add(1200, self._finalize_package_removal, pkgname)
                else:
                    not_updated.append(pkgname)
                    self._set_status(pkgname, 'Failed')
            summary = output.summary(returncode, tail)
            summary.update(when=time_module.strftime('%H:%M'), updated=updated, not_updated=not_updated,
                           changes=changes + output.changes())
            GLib.idle_add(self._show_update_summary, summary)
        GLib.idle_add(self._end_install)
