#!/usr/bin/env python3
"""GitHub CLI (gh) management.

Checks install and login state, prints the login command, lists repos, and reads
the current repo. Commands that hit the network (``status --net``, ``repo``,
``repos``) should run through the VPN task runner::

    python scripts/vpn/tasks.py run -- python scripts/projects/gh.py repos --user <user>

The token itself is never printed. ``gh auth status`` reports a masked token;
this script only parses account, protocol and scopes.

Usage:
    python scripts/projects/gh.py status
    python scripts/projects/gh.py status --net
    python scripts/projects/gh.py login
    python scripts/projects/gh.py repos --user <github-user> --limit 20
    python scripts/projects/gh.py repo [path]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

GH = 'gh'
LOGIN_SCOPES = 'repo,workflow,gist,read:org'
REPO_FIELDS = 'nameWithOwner,description,visibility,isPrivate,defaultBranchRef,url'


def _run(args: list[str], timeout: int = 30, cwd: str | None = None):
    return subprocess.run(
        [GH, *args], capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=timeout, cwd=cwd)


def _json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _git_remote(path: str = '.') -> str:
    try:
        proc = subprocess.run(
            ['git', '-C', path, 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return proc.stdout.strip() if proc.returncode == 0 else ''


def parse_remote(url: str) -> str:
    """Return ``owner/repo`` for a GitHub remote, else an empty string."""
    value = (url or '').strip()
    value = re.sub(r'\.git$', '', value)
    match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)/?$', value)
    return f'{match.group(1)}/{match.group(2)}' if match else ''


def parse_auth(text: str) -> dict:
    data = {'logged_in': False, 'account': '', 'protocol': '',
            'scopes': [], 'active': False}
    match = re.search(r'Logged in to \S+ account (\S+)', text)
    if match:
        data['logged_in'] = True
        data['account'] = match.group(1)
    match = re.search(r'Git operations protocol:\s*(\S+)', text)
    if match:
        data['protocol'] = match.group(1)
    match = re.search(r'Token scopes:\s*(.+)', text)
    if match:
        data['scopes'] = [item.strip().strip("'\"")
                          for item in match.group(1).split(',')
                          if item.strip()]
    data['active'] = 'Active account: true' in text
    return data


def _auth_status() -> tuple[dict, str]:
    try:
        proc = _run(['auth', 'status'])
    except FileNotFoundError:
        return {'logged_in': False, 'account': '', 'protocol': '',
                'scopes': [], 'active': False}, 'gh not installed'
    except subprocess.TimeoutExpired:
        return {'logged_in': False, 'account': '', 'protocol': '',
                'scopes': [], 'active': False}, 'gh auth status timed out'
    text = (proc.stdout or '') + (proc.stderr or '')
    return parse_auth(text), ''


def status(path: str = '.', net: bool = False) -> dict:
    result = {'installed': False, 'version': '', 'auth': {}, 'remote': '',
              'repo': None, 'env_token': '', 'warnings': []}
    try:
        proc = _run(['--version'])
        if proc.returncode == 0:
            result['installed'] = True
            result['version'] = (proc.stdout or '').splitlines()[0].strip()
    except FileNotFoundError:
        result['warnings'].append('gh not installed')
    except subprocess.TimeoutExpired:
        result['warnings'].append('gh --version timed out')

    result['auth'], error = _auth_status()
    if error:
        result['warnings'].append(error)
    elif not result['auth'].get('logged_in'):
        result['warnings'].append('gh is not logged in; run: python scripts/projects/gh.py login')

    for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
        if os.environ.get(name):
            result['env_token'] = name
            result['warnings'].append(
                f'{name} is set; gh prefers it over the keyring, keep it out of '
                'shell profiles and project files')
            break

    remote = _git_remote(path)
    result['remote'] = parse_remote(remote) or remote
    if remote and not parse_remote(remote):
        result['warnings'].append(f'origin is not a GitHub remote: {remote}')

    if net:
        result['repo'], error = repo(path)
        if error:
            result['warnings'].append(f'network check failed: {error}')
    return result


def login_help() -> dict:
    command = (f'gh auth login --hostname github.com --git-protocol https '
               f'--web --scopes {LOGIN_SCOPES}')
    return {
        'command': command,
        'check': 'gh auth status',
        'scopes': LOGIN_SCOPES.split(','),
        'notes': [
            'token 存系统 keyring，不写 local.json、.env 或 shell 配置',
            'gh auth status 只显示掩码；不要运行 gh auth token 或把它贴进对话',
            '换账号：gh auth logout 后再 login',
            '网络命令走 tasks.py run -- ...，任务表已含 gh repo / gh api / gh pr',
        ],
    }


def repos(user: str, limit: int = 20) -> tuple[list, str]:
    try:
        proc = _run(['repo', 'list', user, '--limit', str(limit), '--json',
                     'name,visibility,isPrivate,updatedAt,url'])
    except FileNotFoundError:
        return [], 'gh not installed'
    except subprocess.TimeoutExpired:
        return [], 'gh repo list timed out'
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout or 'gh repo list failed').strip()
    data = _json(proc.stdout or '')
    if data is None:
        return [], 'gh returned invalid JSON'
    return data, ''


def repo(path: str = '.') -> tuple[dict | None, str]:
    try:
        proc = _run(['repo', 'view', '--json', REPO_FIELDS], cwd=path)
    except FileNotFoundError:
        return None, 'gh not installed'
    except subprocess.TimeoutExpired:
        return None, 'gh repo view timed out'
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or 'gh repo view failed').strip()
    data = _json(proc.stdout or '')
    if data is None:
        return None, 'gh returned invalid JSON'
    return data, ''


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    status_p = sub.add_parser('status', help='install, auth, scopes, current remote')
    status_p.add_argument('--net', action='store_true',
                          help='also query the current repo (needs network)')
    status_p.add_argument('--path', default='.', help='repository directory')

    sub.add_parser('login', help='print the login command')

    repos_p = sub.add_parser('repos', help='list repos for a user (needs network)')
    repos_p.add_argument('--user', required=True)
    repos_p.add_argument('--limit', type=int, default=20)

    repo_p = sub.add_parser('repo', help='show the current repo (needs network)')
    repo_p.add_argument('path', nargs='?', default='.')

    args = parser.parse_args(argv)
    if args.command == 'status':
        data = status(args.path, args.net)
        _print(data)
        return 0 if data['installed'] and data['auth'].get('logged_in') else 1
    if args.command == 'login':
        _print(login_help())
        return 0
    if args.command == 'repos':
        data, error = repos(args.user, args.limit)
        if error:
            _print({'error': error})
            return 1
        _print(data)
        return 0
    data, error = repo(args.path)
    if error:
        _print({'error': error})
        return 1
    _print(data)
    return 0


if __name__ == '__main__':
    sys.exit(main())
