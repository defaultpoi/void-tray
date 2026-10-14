"""Package update scans and install-all."""

import hashlib
import subprocess
import threading
import time as time_module

from ..gi_env import GLib
from ..config import load_updates_check_interval
from ..proc import _kill_process_group
from ..state import save_state
from ..updates import get_installed_version, list_all_updates


PRIVILEGE_CMD = ['sudo', '-n']  # -n: fail fast rather than hang if a password would be needed;


UPDATE_TIMEOUT_SECONDS = 1800  # 30 minutes


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

    def install_all_updates(self):
        if self.active_installs > 0:
            return  # already running
        with self.lock:
            pkgnames = [e['pkgname'] for e in self.state.get('available_updates', [])]
        if not pkgnames:
            return
        self.install_status = {pkg: 'Waiting…' for pkg in pkgnames}
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

    def _run_update_all(self, pkgnames):
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
                returncode = -1
                watchdog = None
                try:
                    proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, start_new_session=True
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
                    # Since we install one package at a time, every line in this
                    # stream belongs to the current package regardless of wording.
                    shown = 'Installing…'
                    for line in proc.stdout:
                        stripped = line.strip()
                        if not stripped.startswith('[*]'):
                            continue
                        status = 'Downloading…' if stripped.startswith('[*] Downloading') else 'Installing…'
                        if status != shown:  # each change rebuilds the list; skip repeats
                            shown = status
                            self._set_status(pkgname, status)
                    proc.wait()
                    returncode = proc.returncode
                except Exception:
                    pass
                finally:
                    if watchdog is not None:
                        watchdog.cancel()
                after = get_installed_version(pkgname)
                success = returncode == 0 and before != after
                if success:
                    self._set_status(pkgname, 'Done')
                    GLib.timeout_add(1200, self._finalize_package_removal, pkgname)
                else:
                    self._set_status(pkgname, 'Failed')
        GLib.idle_add(self._end_install)
