"""Package updates: the xbps dry run and its parser, and the install commands."""

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
