"""Package updates: the xbps dry run and its parser, and the install commands."""

import collections
import os
import re
import shutil
import subprocess

from .proc import _kill_process_group


def get_installed_version(pkgname):
    try:
        result = subprocess.run(
            ['xbps-query', '-p', 'pkgver', pkgname],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def choose_installer(setting):
    """'topgrade' or 'xbps' for the [updates] installer= setting; auto means
    topgrade when it is on the PATH."""
    if setting == 'auto':
        return 'topgrade' if shutil.which('topgrade') else 'xbps'
    return setting


def topgrade_command(steps):
    """The unattended topgrade command line for `steps` (None = every step):
    --yes answers the package manager's prompts, --no-retry stops it asking
    whether to retry a failed step, --no-self-update leaves topgrade itself to
    xbps, --skip-notify skips its desktop notification."""
    command = ['topgrade']
    if steps:
        command += ['--only', *steps]
    return command + ['--yes', '--no-retry', '--no-self-update', '--skip-notify']


_PKGVER_RE = re.compile(r'^(?P<name>\S+)-[^\s-]+_\d+$')


def parse_orphans(output):
    """Sorted package names from `xbps-query -O` output, which prints one pkgver
    (name-version_revision) per line. Anything that is not a pkgver is ignored."""
    names = set()
    for line in output.splitlines():
        parts = line.split()
        match = _PKGVER_RE.match(parts[0]) if parts else None
        if match:
            names.add(match.group('name'))
    return sorted(names)


def list_orphans():
    """Packages installed as dependencies that nothing requires any more, from
    `xbps-query -O`: read-only, no root needed. Returns the sorted names ([] when
    there are none), or None if the query failed."""
    try:
        proc = subprocess.Popen(
            ['xbps-query', '-O'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True
        )
        try:
            out, _err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            return None
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return parse_orphans(out or '')


def topgrade_env():
    """The environment for a topgrade run: English output whatever the session
    language, since topgrade translates its own headings and the summary below is
    found by its heading. C.UTF-8 keeps UTF-8, which the headings use."""
    return {**os.environ, 'LC_ALL': 'C.UTF-8'}


_TOPGRADE_HEADER_RE = re.compile(r'^\u2015+ \d{2}:\d{2}:\d{2} - (?P<name>.+?) \u2015+$')
_TOPGRADE_RESULT_RE = re.compile(r'^(?P<step>.+): (?P<status>OK|FAILED|SKIPPED)$')


class TopgradeOutput:
    """Reads topgrade's piped output line by line. Each step prints a heading
    like `―― 10:46:16 - System update ――` followed by its own output, and the run
    ends with a `Summary` heading and one `<step>: OK|FAILED` line per step. The
    last few lines of every step are kept so a failed one can be explained."""

    def __init__(self, detail_lines=3):
        self._detail_lines = detail_lines
        self._section = None
        self._in_summary = False
        self._sections = {}
        self.summary_lines = []

    def feed(self, line):
        match = _TOPGRADE_HEADER_RE.match(line)
        if match:
            if match.group('name') == 'Summary':
                self._in_summary = True
                self._section = None
            else:
                self._section = match.group('name')
                self._sections.setdefault(self._section, collections.deque(maxlen=self._detail_lines))
        elif self._in_summary:
            self.summary_lines.append(line)
        elif self._section is not None:
            self._sections[self._section].append(line)

    def results(self):
        """[(step, 'OK' | 'FAILED' | 'SKIPPED')] from the summary."""
        found = []
        for line in self.summary_lines:
            match = _TOPGRADE_RESULT_RE.match(line)
            if match:
                found.append((match.group('step'), match.group('status')))
        return found

    def summary(self, returncode, tail=()):
        """{'ok', 'lines', 'details'} for the popup: the summary lines as topgrade
        printed them, and for every failed step its last output lines. Without a
        summary (topgrade died early, or is not installed) the last output lines
        stand in for it."""
        failed = [step for step, status in self.results() if status == 'FAILED']
        details = [f"{step}: {line}" for step in failed for line in self._sections.get(step, ())]
        if not self.summary_lines and returncode != 0:
            details = list(tail)
        return {'ok': returncode == 0 and not failed, 'lines': list(self.summary_lines), 'details': details}


def parse_xbps_updates(output):
    """Pure parser for `xbps-install -Mn -u` output: returns the pkgnames whose
    action is 'update'. Lines look like
        cryptsetup-2.8.8_1 update x86_64 https://... 3203607 568523"""
    updates = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != 'update':
            continue
        match = re.match(r'^(.+)-[0-9][^-]*$', parts[0])
        if match:
            updates.append(match.group(1))
    return updates


def list_all_updates():
    """Read-only, in-memory, system-wide dry run — no root needed, nothing written
    to disk. Returns a list of pkgnames that have a real newer build published.

    Real xbps-install -Mn -u output (per package) looks like:
        cryptsetup-2.8.8_1 update x86_64 https://repo-default.voidlinux.org/current 3203607 568523
    i.e. "<pkgver> <action> <arch> <repo> <dlsize> <instsize>" — no '->' arrow."""
    try:
        proc = subprocess.Popen(
            ['xbps-install', '-Mn', '-u'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True
        )
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            return None  # None = scan failed (distinct from [] = no updates)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    output = (out or '') + (err or '')
    return parse_xbps_updates(output)
