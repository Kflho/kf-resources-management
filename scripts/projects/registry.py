#!/usr/bin/env python3
"""Local development project registry.

The registry lives in the skill's gitignored ``local.json``:

    "projects": {
      "roots": ["D:/.../programming"],
      "items": [{"name": "sk_01", "path": "D:/.../sk_01",
                 "remote": "https://github.com/<owner>/sk_01.git",
                 "role": "user-facing", "tags": ["skill"], "added_at": "..."}]
    }

Commands:
    python scripts/projects/registry.py list
    python scripts/projects/registry.py add <path> [--name N] [--tags a,b] \
        [--test "pytest -q"] [--note "postgraduate/pg 01 note.md"] \
        [--test-repo "kf-development-project/<目录名>"]
    python scripts/projects/registry.py set <name-or-path> \
        [--test "pytest -q"] [--note "postgraduate/pg 01 note.md"] \
        [--test-repo "kf-development-project/<目录名>"]
    python scripts/projects/registry.py remove <name-or-path>
    python scripts/projects/registry.py roots
    python scripts/projects/registry.py roots add <dir>
    python scripts/projects/registry.py scan [--root DIR ...] [--depth 3] [--add]
    python scripts/projects/registry.py status [--require-note] [--check-test-repo]

``test_repo`` 记的是仓库内的相对位置：``<测试项目仓在总账里的名字>/<目录名>``。
测试项目仓本身也按普通条目登记（``role: test``），所以换机器只要总账里两台机器
各有那条记录，路径就能重新拼出来。

``scan`` treats a directory as a project when it contains ``.git`` or one of
``pyproject.toml`` / ``package.json`` / ``Cargo.toml`` / ``go.mod``, then does
not descend into it.  Machine paths stay in local.json and never reach the
release repo.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402

MARKERS = ('pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod',
           'pom.xml', 'build.gradle')
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'models',
             'temp', 'dist', 'build', '.pytest_cache'}


def _skip_dirs() -> set[str]:
    """Skip the hub checkout too, whatever the user named it."""
    dirs = set(SKIP_DIRS)
    hub = str(cfg.get('data_hub', 'path', '')).strip()
    if hub:
        dirs.add(Path(hub).name)
    return dirs
ENTRY_KEYS = ('name', 'path', 'remote', 'role', 'tags', 'test', 'note',
              'test_repo', 'added_at', 'release_path')
CONTENT_TAGS = {'skill', 'script', 'koishi', 'plugin', 'research'}


def _load() -> dict:
    return cfg.load()


def registry() -> dict:
    data = _load().get('projects') or {}
    roots = [str(item) for item in (data.get('roots') or []) if str(item)]
    items = [item for item in (data.get('items') or [])
             if isinstance(item, dict) and item.get('path')]
    return {'roots': roots, 'items': items}


def _save(data: dict) -> None:
    full = _load()
    full['projects'] = data
    path = cfg.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def _norm(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def remote_url(path: Path) -> str:
    """Return a git remote URL; empty means local-only."""
    return _git(path, 'remote', 'get-url', 'origin') or _git(path, 'remote')


def has_remote(path: Path) -> bool:
    return path.is_dir() and bool(remote_url(path))


def _ordered(entry: dict) -> dict:
    """Put known keys in the documented order, keep extras after them."""
    ordered = {key: entry[key] for key in ENTRY_KEYS if key in entry}
    ordered.update((key, value) for key, value in entry.items()
                   if key not in ordered)
    return ordered


def _set_optional(entry: dict, key: str, value: str | None) -> None:
    """Set a free-form field; an empty string clears it."""
    if value is None:
        return
    value = value.strip()
    if value:
        entry[key] = value
    else:
        entry.pop(key, None)


def add(path: str, name: str = '', tags: str = '',
        test: str | None = None, note: str | None = None,
        test_repo: str | None = None) -> dict:
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f'not a directory: {target}')
    remote = remote_url(target)
    if not remote:
        raise ValueError(f'{target} has no git remote; local-only repos are '
                         'not managed')
    data = registry()
    key = _norm(target)
    entry = next((item for item in data['items']
                  if _norm(item['path']) == key), None)
    tag_list = [tag.strip() for tag in tags.replace(',', ' ').split()
                if tag.strip()]
    if entry is None:
        entry = {'name': name or target.name, 'path': str(target),
                 'remote': remote, 'role': 'unknown', 'tags': tag_list,
                 'added_at': _dt.datetime.now().isoformat(timespec='seconds')}
        _set_optional(entry, 'test', test)
        _set_optional(entry, 'note', note)
        _set_optional(entry, 'test_repo', test_repo)
        data['items'].append(entry)
    else:
        if name:
            entry['name'] = name
        # A renamed repo keeps working through GitHub redirects but stops
        # matching `github --user`, so re-running `add` refreshes the URL.
        entry['remote'] = remote
        entry['tags'] = sorted(set(entry.get('tags') or []) | set(tag_list))
        _set_optional(entry, 'test', test)
        _set_optional(entry, 'note', note)
        _set_optional(entry, 'test_repo', test_repo)
        index = data['items'].index(entry)
        data['items'][index] = _ordered(entry)
        entry = data['items'][index]
    _save(data)
    return entry


def _find(data: dict, key: str) -> dict | None:
    target = Path(key).expanduser()
    norm = _norm(target) if target.exists() else key
    return next((item for item in data['items']
                 if item.get('name') == key or _norm(item['path']) == norm), None)


def set_fields(key: str, test: str | None = None,
               note: str | None = None,
               test_repo: str | None = None) -> dict:
    """Update test/note/test_repo for an existing entry; no live remote required."""
    data = registry()
    entry = _find(data, key)
    if entry is None:
        raise ValueError(f'not in registry: {key}')
    _set_optional(entry, 'test', test)
    _set_optional(entry, 'note', note)
    _set_optional(entry, 'test_repo', test_repo)
    index = data['items'].index(entry)
    data['items'][index] = _ordered(entry)
    _save(data)
    return data['items'][index]


def remove(key: str) -> dict:
    data = registry()
    key_path = Path(key).expanduser()
    norm = _norm(key_path) if key_path.exists() else key
    kept, removed = [], []
    for item in data['items']:
        if item.get('name') == key or _norm(item['path']) == norm:
            removed.append(item)
        else:
            kept.append(item)
    data['items'] = kept
    _save(data)
    return {'removed': removed, 'remaining': len(kept)}


def set_role(key: str, role: str) -> dict:
    roles = ('user-facing', 'development', 'test', 'archive')
    if role not in roles:
        raise ValueError(f'role must be one of {roles}')
    data = registry()
    for item in data['items']:
        if item.get('name') == key or _norm(item['path']) == _norm(key):
            item['role'] = role
            _save(data)
            return item
    raise ValueError(f'project not found: {key}')


def roots(action: str = '', directory: str = '') -> dict:
    data = registry()
    if action == 'add' and directory:
        target = Path(directory).expanduser().resolve()
        if not target.is_dir():
            raise ValueError(f'not a directory: {target}')
        if _norm(target) not in {_norm(item) for item in data['roots']}:
            data['roots'].append(str(target))
            _save(data)
    return {'roots': data['roots']}


def scan(roots_to_scan: list[str], depth: int = 5) -> list[str]:
    found: list[str] = []

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        names = {entry.name for entry in entries}
        is_project = '.git' in names or any(marker in names for marker in MARKERS)
        if is_project and (directory / '.git').is_dir() and has_remote(directory):
            found.append(str(directory))
            return
        for entry in entries:
            if entry.is_dir() and entry.name not in _skip_dirs():
                walk(entry, level + 1)

    for raw in roots_to_scan:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            continue
        walk(root, 0)
    return sorted(set(found))


def _git(path: Path, *args: str) -> str:
    try:
        out = subprocess.run(['git', '-C', str(path), *args],
                             capture_output=True, text=True, timeout=5,
                             encoding='utf-8', errors='replace')
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout.strip() if out.returncode == 0 else ''


def item_git_path(item: dict) -> Path:
    path = Path(item['path'])
    release = item.get('release_path')
    return path / release if release else path


def item_remote(item: dict) -> str:
    if item.get('remote'):
        return str(item['remote'])
    return remote_url(item_git_path(item))


def vault_root() -> Path | None:
    """Obsidian vault root: ``obsidian.vault``，没有就用 ``bookmarks.vault``。"""
    for section in ('obsidian', 'bookmarks'):
        raw = str(cfg.get(section, 'vault', '') or '').strip()
        if raw:
            return Path(raw)
    return None


def note_state(note: str | None) -> tuple[bool, bool | None]:
    """返回 (有没有填 note, note 文件在不在)。vault 没配时第二个是 None。"""
    if not note:
        return False, None
    root = vault_root()
    if root is None:
        return True, None
    return True, (root / note).is_file()


def resolve_test_repo(entry: dict, data: dict | None = None) -> tuple[str, Path | None]:
    """把 ``test_repo`` 解析成本机路径。

    ``test_repo`` 写成 ``<测试项目仓在总账里的名字>/<仓里的目录名>``，
    例如 ``kf-development-project/obsidian-note-tidy-test``。记的是仓库内的相对位置：
    换机器后按总账里那条测试项目仓的本地路径重新拼接，机器绝对路径不会进总账。
    解析不出来（名字不在总账里）时返回 (原值, None)。
    """
    value = (entry.get('test_repo') or '').strip().strip('/')
    if not value:
        return '', None
    repo_name, _, directory = value.partition('/')
    source = data if data is not None else registry()
    for item in source['items']:
        if item.get('name') == repo_name:
            base = Path(item['path'])
            return value, (base / directory if directory else base)
    return value, None


def status(require_note: bool = False, check_test_repo: bool = False) -> list[dict]:
    data = registry()
    rows = []
    for item in data['items']:
        path = Path(item['path'])
        git_path = item_git_path(item)
        row = {'name': item.get('name'), 'path': str(path),
               'role': item.get('role') or 'unknown',
               'tags': item.get('tags') or [], 'exists': path.is_dir()}
        for key in ('test', 'note', 'release_path'):
            if item.get(key):
                row[key] = item[key]
        if item.get('test_repo'):
            value, resolved = resolve_test_repo(item, data)
            row['test_repo'] = value
            if resolved is not None:
                row['test_repo_path'] = str(resolved)
                row['test_repo_exists'] = resolved.is_dir()
        has_note, exists = note_state(item.get('note'))
        row['no_note'] = not has_note
        if not exists is None:
            row['note_exists'] = exists
        if item.get('release_path'):
            row['release_path'] = item['release_path']
            row['git_path'] = str(git_path)
        if row['exists']:
            row['branch'] = _git(git_path, 'rev-parse', '--abbrev-ref', 'HEAD')
            row['head'] = _git(git_path, 'rev-parse', '--short', 'HEAD')
            row['dirty'] = bool(_git(git_path, 'status', '--porcelain'))
            row['remote'] = item_remote(item)
            row['managed'] = bool(row['remote'])
        rows.append(row)
    if require_note:
        missing = [r['name'] for r in rows
                   if r.get('no_note') and (set(r.get('tags') or []) & CONTENT_TAGS)]
        broken = [r['name'] for r in rows if r.get('note_exists') is False]
        if missing or broken:
            raise SystemExit(f"note 缺失：{missing}；指向不存在：{broken}")
    if check_test_repo:
        unknown = [f"{r['name']} -> {r['test_repo']}" for r in rows
                   if r.get('test_repo') and 'test_repo_path' not in r]
        missing = [f"{r['name']} -> {r['test_repo']}" for r in rows
                   if r.get('test_repo_exists') is False]
        if unknown or missing:
            raise SystemExit(f"测试仓登记指向不存在：{unknown + missing}")
    return rows


def prune() -> dict:
    """Drop missing paths and repos that no longer have a remote."""
    data = registry()
    kept, removed = [], []
    for item in data['items']:
        if Path(item['path']).is_dir() and item_remote(item):
            kept.append(item)
        else:
            removed.append(item)
    data['items'] = kept
    _save(data)
    return {'removed': removed, 'remaining': len(kept)}


def canonical_project_name(repo_name: str) -> str:
    """Project name = repo name; skill repos drop the '-skill' suffix."""
    name = str(repo_name or '').strip()
    return name[:-6] if name.lower().endswith('-skill') else name


def parse_github_remote(url: str) -> str:
    """Return 'owner/repo' (lowercase) for a GitHub remote URL."""
    match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$', url)
    if not match:
        return ''
    return f'{match.group(1)}/{match.group(2)}'.lower()


def _fetch_github(user: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 6):
        url = (f'https://api.github.com/users/{user}/repos'
               f'?per_page=100&type=owner&page={page}')
        request = urllib.request.Request(url, headers={
            'User-Agent': 'kf-personal-service',
            'Accept': 'application/vnd.github+json',
        })
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ValueError(f'GitHub API failed for {user}: {exc}') from exc
        if not isinstance(payload, list):
            raise ValueError(f'GitHub API returned {type(payload).__name__}')
        repos.extend(payload)
        if len(payload) < 100:
            break
    return repos


def local_git_repos(roots_to_scan: list[str], depth: int = 5) -> list[dict]:
    rows: list[dict] = []

    def walk(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        names = {entry.name for entry in entries}
        if '.git' in names or any(marker in names for marker in MARKERS):
            remote = remote_url(directory) if (directory / '.git').is_dir() else ''
            if remote:
                row = {'path': str(directory), 'remote': remote,
                       'full': parse_github_remote(remote)}
                if directory.name.lower() == 'release':
                    row['project_root'] = str(directory.parent)
                    row['release_path'] = 'release'
                elif directory.parent.name.lower() == 'dev':
                    row['project_root'] = str(directory.parent.parent)
                    row['release_path'] = 'dev/release'
                rows.append(row)
                return
        for entry in entries:
            if entry.is_dir() and entry.name not in _skip_dirs():
                walk(entry, level + 1)

    for raw in roots_to_scan:
        root = Path(raw).expanduser().resolve()
        if root.is_dir():
            walk(root, 0)
    return sorted(rows, key=lambda row: row['path'])


def github_sync(user: str, roots_to_scan: list[str], depth: int = 5,
                add: bool = False, replace: bool = False) -> dict:
    repos = _fetch_github(user)
    by_full = {str(item.get('full_name', '')).lower(): item for item in repos}
    local = local_git_repos(roots_to_scan, depth)
    local_full = {row['full'] for row in local}
    for item in registry()['items']:
        remote = str(item.get('remote') or '')
        full = parse_github_remote(remote)
        if remote and full and full not in local_full:
            local.append({'path': item['path'], 'remote': remote,
                          'full': full, 'project_root': item['path'],
                          'existing': item})
            local_full.add(full)
    matched, local_other = [], []
    for row in local:
        repo = by_full.get(row['full'])
        if repo is None:
            local_other.append(row)
            continue
        matched.append({**row, 'name': canonical_project_name(repo.get('name')),
                        'private': bool(repo.get('private')),
                        'role': (row.get('existing') or {}).get('role', 'unknown')})
    github_missing = [
        {'full_name': item.get('full_name'), 'private': bool(item.get('private')),
         'clone_url': item.get('clone_url')}
        for item in repos if str(item.get('full_name', '')).lower()
        not in {row['full'] for row in local}
    ]
    if add or replace:
        data = registry()
        if replace:
            data['items'] = []
        existing = {_norm(item['path']) for item in data['items']}
        for row in matched:
            project_path = row.get('project_root') or row['path']
            if _norm(project_path) in existing:
                continue
            item = {'name': row['name'], 'path': project_path,
                    'remote': row['remote'], 'role': row.get('role', 'unknown'),
                    'tags': ['github'],
                    'added_at': _dt.datetime.now().isoformat(timespec='seconds')}
            if row.get('release_path'):
                item['release_path'] = row['release_path']
            data['items'].append(item)
        _save(data)
    return {'user': user, 'github_total': len(repos),
            'local_total': len(local), 'matched': matched,
            'github_not_local': github_missing,
            'local_not_on_github': local_other}


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')

    add_p = sub.add_parser('add')
    add_p.add_argument('path')
    add_p.add_argument('--name', default='')
    add_p.add_argument('--tags', default='')
    add_p.add_argument('--test', default=None, help='test command for this project')
    add_p.add_argument('--note', default=None, help='Obsidian note path')
    add_p.add_argument('--test-repo', default=None,
                       help='test repo as <test-repo name>/<directory>, '
                            'e.g. kf-development-project/obsidian-note-tidy-test')

    set_p = sub.add_parser('set')
    set_p.add_argument('key')
    set_p.add_argument('--test', default=None, help='test command for this project')
    set_p.add_argument('--note', default=None, help='Obsidian note path')
    set_p.add_argument('--test-repo', default=None,
                       help='test repo as <test-repo name>/<directory>')

    remove_p = sub.add_parser('remove')
    remove_p.add_argument('key')

    role_p = sub.add_parser('role')
    role_p.add_argument('key')
    role_p.add_argument('role', choices=('user-facing', 'development', 'test', 'archive'))

    roots_p = sub.add_parser('roots')
    roots_p.add_argument('action', nargs='?', choices=('list', 'add'),
                         default='list')
    roots_p.add_argument('directory', nargs='?', default='')

    scan_p = sub.add_parser('scan')
    scan_p.add_argument('--root', action='append', default=[])
    scan_p.add_argument('--depth', type=int, default=5)
    scan_p.add_argument('--add', action='store_true')

    status_p = sub.add_parser('status')
    status_p.add_argument('--require-note', action='store_true',
                          help='每个项目都要有 note 且文件存在，否则退出 1')
    status_p.add_argument('--check-test-repo', action='store_true',
                          help='登记的 test_repo 都要能解析到本机目录，否则退出 1')
    sub.add_parser('prune')

    gh = sub.add_parser('github')
    gh.add_argument('--user', default=str(cfg.get('github', 'user', '')))
    gh.add_argument('--root', action='append', default=[])
    gh.add_argument('--depth', type=int, default=5)
    gh.add_argument('--add', action='store_true')
    gh.add_argument('--replace', action='store_true')

    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            _print(registry())
        elif args.command == 'add':
            _print(add(args.path, args.name, args.tags, args.test, args.note,
                       args.test_repo))
        elif args.command == 'set':
            _print(set_fields(args.key, args.test, args.note, args.test_repo))
        elif args.command == 'remove':
            _print(remove(args.key))
        elif args.command == 'role':
            _print(set_role(args.key, args.role))
        elif args.command == 'roots':
            _print(roots(args.action, args.directory))
        elif args.command == 'scan':
            data = registry()
            scanned_roots = args.root or data['roots']
            if not scanned_roots:
                raise ValueError('no roots configured; pass --root or run roots add')
            found = scan(scanned_roots, args.depth)
            added = [add(path, tags='scanned') for path in found] if args.add else []
            _print({'roots': scanned_roots, 'found': found,
                    'added': added, 'count': len(found)})
        elif args.command == 'prune':
            _print(prune())
        elif args.command == 'github':
            if not args.user:
                raise ValueError('pass --user or set github.user in local.json')
            configured = registry()['roots']
            scanned_roots = args.root or configured
            if not scanned_roots:
                raise ValueError('no roots configured; pass --root')
            _print(github_sync(args.user, scanned_roots, args.depth,
                               args.add, args.replace))
        elif args.command == 'status':
            _print(status(require_note=getattr(args, 'require_note', False),
                          check_test_repo=getattr(args, 'check_test_repo', False)))
        else:
            _print(status())
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
