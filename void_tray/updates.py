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


USER_BIN_DIRS = ('~/.local/bin', '~/.cargo/bin')  # where per-user tools (claude, uv, rustup...) install themselves


def topgrade_env():
    """The environment for a topgrade run: English output whatever the session
    language, since topgrade translates its own headings and the summary below is
    found by its heading. C.UTF-8 keeps UTF-8, which the headings use. The
    per-user bin directories that exist are added to the end of PATH when the
    desktop session left them out (they are usually added by the shell's rc file,
    which a tray app started by the session never reads), or topgrade would
    report those tools as not found."""
    env = {**os.environ, 'LC_ALL': 'C.UTF-8'}
    if env.get('PATH'):
        parts = env['PATH'].split(os.pathsep)
        for directory in USER_BIN_DIRS:
            path = os.path.expanduser(directory)
            if os.path.isdir(path) and path not in parts:
                parts.append(path)
        env['PATH'] = os.pathsep.join(parts)
    return env


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
        self._read_lines = {}
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
            if self._section in _CHANGE_READERS:
                kept = self._read_lines.setdefault(self._section, [])
                if len(kept) < _CHANGE_LINES_MAX:
                    kept.append(line)

    def results(self):
        """[(step, 'OK' | 'FAILED' | 'SKIPPED')] from the summary."""
        found = []
        for line in self.summary_lines:
            match = _TOPGRADE_RESULT_RE.match(line)
            if match:
                found.append((match.group('step'), match.group('status')))
        return found

    def changes(self):
        """[(name, old, new)] for the software the steps with a reader moved to a
        new version, in the order the steps ran."""
        found = []
        for step, reader in _CHANGE_READERS.items():
            found += reader(self._read_lines.get(step, ()))
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


_REVISION_RE = re.compile(r'_\d+$')


def _version_of(pkgname, pkgver):
    """The version part of a pkgver (`libfoo-3.2_1` -> `3.2_1`), None if unknown."""
    if not pkgver:
        return None
    prefix = pkgname + '-'
    if pkgver.startswith(prefix):
        return pkgver[len(prefix):]
    return pkgver.rpartition('-')[2]


def version_change(pkgname, before, after):
    """(name, old, new) for a package whose installed pkgver went from `before`
    to `after` (None where one is unknown). The build revision (`_1`) is left
    off, since it is the same on nearly every line, unless it is all that
    changed (`3.2_1` -> `3.2_2`)."""
    old, new = _version_of(pkgname, before), _version_of(pkgname, after)
    if old and new:
        bare_old, bare_new = _REVISION_RE.sub('', old), _REVISION_RE.sub('', new)
        if bare_old != bare_new:
            old, new = bare_old, bare_new
    return (pkgname, old, new)


def format_change(change):
    """`name old -> new` for a (name, old, new) change; just `name -> new` or
    `name` when a version is not known."""
    name, old, new = change
    if old and new:
        return f'{name} {old} -> {new}'
    return f'{name} -> {new}' if new else name


_CLAUDE_UPDATED_RE = re.compile(r'Successfully updated from (\S+) to version (\S+)')
_RUSTUP_UPDATED_RE = re.compile(r'^(?P<toolchain>\S+) updated - rustc (?P<new>\S+) .*\(from rustc (?P<old>\S+) ')


def read_claude_code_changes(lines):
    """[('Claude Code', old, new)] from `claude update`: `Successfully updated
    from 2.1.304 to version 2.1.305`. Nothing when it was already current."""
    for line in lines:
        match = _CLAUDE_UPDATED_RE.search(line)
        if match:
            return [('Claude Code', match.group(1), match.group(2))]
    return []


def read_rustup_changes(lines):
    """One (toolchain, old, new) per toolchain `rustup update` moved to a new
    rustc: `stable-x86_64-unknown-linux-gnu updated - rustc 1.75.0 (...) (from
    rustc 1.74.1 (...))`. Unchanged toolchains print `unchanged` and are skipped."""
    found = []
    for line in lines:
        match = _RUSTUP_UPDATED_RE.match(line.strip())
        if match:
            found.append((match.group('toolchain'), match.group('old'), match.group('new')))
    return found


# Steps whose own output says what version they went from and to, by heading.
# Other steps (uv tool upgrades list only dependency bumps, Codex prints only
# the new version) are not read.
_CHANGE_READERS = {'Claude Code': read_claude_code_changes, 'rustup': read_rustup_changes}
_CHANGE_LINES_MAX = 500  # lines kept per step that has a reader


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
