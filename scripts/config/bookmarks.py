#!/usr/bin/env python3
"""Bookmarks: parse a browser export, keep a portable copy in the data hub.

Why this exists
---------------
A browser bookmark export is a 130 KB HTML file full of ``ICON="data:image/png;
base64,…"`` — it cannot be diffed, cannot be merged across machines and cannot be
searched when you are on the *other* computer.  The data hub
(:mod:`projects.hub`, ``<hub>/kf-personal-service/``) is already the place this
user syncs machine-private, portable data through a private git repo, so
bookmarks go there too — as structure, not as an opaque export:

    <hub>/kf-personal-service/bookmarks/
        README.md
        machines/<HOST>.json     what that browser exported (no icons, no paths)
        bookmarks.json           union of every machine, deduplicated

Two rules keep the merge honest:

- **the URL is the identity** after normalisation (scheme dropped, ``www.``
  dropped, trailing slash dropped, tracking parameters dropped), so the same page
  saved on two machines collapses into one row;
- **``ADD_DATE`` is kept as the earliest sighting**, because "when did I first
  save this" is the only ordering a user actually cares about.  A missing date
  (0) never wins over a real one.

    python scripts/config/bookmarks.py import ~/Desktop/bookmarks.html
    python scripts/config/bookmarks.py status
    python scripts/config/bookmarks.py export --format html --out bookmarks.html
    python scripts/config/bookmarks.py sync --message "bookmarks: KAHOTAT"

Nothing here talks to GitHub directly: ``sync`` shells out to
``scripts/vpn/tasks.py run -- git …`` so the existing VPN task table decides
whether the network step needs the proxy.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402
from projects import hub_publish  # noqa: E402

SCHEMA = 1
NAMESPACE = 'kf-personal-service'
FOLDER = 'bookmarks'
# Chromium/Vivaldi 的 date_added：1601-01-01 起的微秒数
CHROMIUM_EPOCH = _dt.datetime(1601, 1, 1)
UNIX_EPOCH = _dt.datetime(1970, 1, 1)
# 分享/统计参数：同一个页面带上不同参数不该算两条书签
TRACKING = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
            'utm_id', 'spm', 'from', 'share_source', 'share_medium', 'gclid',
            'fbclid', 'ref', 'ref_src'}
ORDERED = r'(?:[-*+]|\d+[.)])'
LINK_LINE = re.compile(rf'^\s*{ORDERED}\s+\[([^\]]*)\]\(([^)\s]+)\)\s*$')
BARE_LINK = re.compile(rf'^\s*{ORDERED}\s+<?(https?://\S+?)>?\s*$')
HEADING = re.compile(r'^\s*(#{1,6})\s+(.*?)\s*$')
FRONTMATTER = re.compile(r'\A---\s*\n.*?\n---\s*\n', re.S)


# ── hub layout ───────────────────────────────────────────────────────────


def hub_root(explicit: str = '') -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = str(cfg.get('data_hub', 'path', '') or '').strip()
    if not configured:
        raise SystemExit('[bookmarks] data_hub.path is not set in local.json '
                         '(see kf-personal-service §8)')
    return Path(configured).expanduser()


def namespace_dir(hub: Path) -> Path:
    return hub / NAMESPACE / FOLDER


def machine_name() -> str:
    return os.environ.get('COMPUTERNAME') or 'machine'


# ── parsing a NETSCAPE bookmark export ───────────────────────────────────


class _BookmarkParser(HTMLParser):
    """Walk Chrome/Edge/Firefox ``NETSCAPE-Bookmark-file-1`` exports.

    The format nests folders as ``<DT><H3>name</H3> <DL> … </DL>`` without
    closing the ``<DT>``, so folder context is rebuilt from an explicit ``<DL>``
    stack: an ``<H3>`` only takes effect when the next ``<DL>`` opens, and each
    ``<DL>`` remembers whether it pushed a folder so ``</DL>`` pops the right one.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._folders: list[str] = []
        self._dl: list[bool] = []
        self._pending = None
        self._in_h3 = False
        self._in_a = False
        self._h3: list[str] = []
        self._a_text: list[str] = []
        self._a_attrs: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'dl':
            pushed = self._pending is not None
            if pushed:
                self._folders.append(self._pending)
                self._pending = None
            self._dl.append(pushed)
        elif tag == 'h3':
            self._in_h3 = True
            self._h3 = []
        elif tag == 'a':
            self._in_a = True
            self._a_attrs = values
            self._a_text = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'h3':
            self._in_h3 = False
            self._pending = ''.join(self._h3).strip()
        elif tag == 'a':
            href = self._a_attrs.get('href', '').strip()
            if self._in_a and href:
                try:
                    added = int(self._a_attrs.get('add_date') or 0)
                except ValueError:
                    added = 0
                self.items.append({
                    'url': href,
                    'title': ''.join(self._a_text).strip(),
                    'folder': '/'.join(self._folders),
                    'added': added,
                })
            self._in_a = False
            self._a_attrs = {}
        elif tag == 'dl':
            if self._dl and self._dl.pop() and self._folders:
                self._folders.pop()

    def handle_data(self, data):
        if self._in_h3:
            self._h3.append(data)
        elif self._in_a:
            self._a_text.append(data)


def parse_export(text: str) -> list[dict]:
    parser = _BookmarkParser()
    parser.feed(text)
    return parser.items


# ── Vivaldi / Chromium 的 Bookmarks JSON ─────────────────────────────────


def chromium_seconds(microseconds: int) -> int:
    """Chromium ``date_added``（1601 起微秒）→ Unix 秒；0 保持 0。"""
    if not microseconds:
        return 0
    try:
        stamp = CHROMIUM_EPOCH + _dt.timedelta(microseconds=int(microseconds))
    except (ValueError, OverflowError):
        return 0
    return int((stamp - UNIX_EPOCH).total_seconds())


def setting(key: str, default: str = '') -> str:
    """本机书签相关设置，读 local.json 的 ``bookmarks`` 节。"""
    value = cfg.get('bookmarks', key, '')
    return str(value).strip() if value is not None and str(value).strip() else default


def vivaldi_profile(explicit: str = '') -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = setting('vivaldi_profile')
    if configured:
        return Path(configured).expanduser()
    local = os.environ.get('LOCALAPPDATA', '')
    return Path(local) / 'Vivaldi' / 'User Data' / 'Default'


def vivaldi_bookmarks_file(profile: Path) -> Path:
    return profile / 'Bookmarks'


VIVALDI_ROOTS = {'bookmark_bar': '', 'other': '其他书签', 'synced': '已同步'}


def parse_vivaldi(path: Path) -> list[dict]:
    """读 Vivaldi/Chromium 当前的 ``Bookmarks`` JSON（不用先导出 HTML）。"""
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    roots = payload.get('roots') or {}
    items: list[dict] = []

    def walk(node: dict, folder: str) -> None:
        for child in node.get('children') or []:
            kind = child.get('type')
            name = (child.get('name') or '').strip()
            if kind == 'folder':
                nested = f'{folder}/{name}' if folder else name
                walk(child, nested)
            elif kind == 'url':
                url = (child.get('url') or '').strip()
                if not url:
                    continue
                items.append({'url': url,
                              'title': name,
                              'folder': folder,
                              'added': chromium_seconds(child.get('date_added') or 0)})

    for key, label in VIVALDI_ROOTS.items():
        node = roots.get(key)
        if isinstance(node, dict):
            walk(node, label)
    return items


# ── Markdown：Obsidian 里就是这份列表 ────────────────────────────────────


def parse_markdown(text: str) -> list[dict]:
    """解析 Obsidian 笔记里的 ``## 文件夹`` + ``- [标题](链接)`` 列表。

    嵌套标题按层级还原成 ``父/子`` 路径（``#`` 视为笔记标题，不进路径）；
    也接受裸链接行和无标题链接；frontmatter 与代码块被忽略。
    """
    body = FRONTMATTER.sub('', text)
    items: list[dict] = []
    levels: dict[int, str] = {}
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = HEADING.match(line)
        if heading:
            hashes, title = heading.groups()
            depth = len(hashes)
            levels[depth] = title.strip()
            for deeper in [d for d in levels if d > depth]:
                levels.pop(deeper, None)
            continue
        match = LINK_LINE.match(line)
        if match:
            title, url = match.group(1).strip(), match.group(2).strip()
        else:
            match = BARE_LINK.match(line)
            if not match:
                continue
            title, url = '', match.group(1).strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        folder = '/'.join(levels[d] for d in sorted(levels) if d >= 2 and levels[d])
        items.append({'url': url, 'title': title, 'folder': folder, 'added': 0})
    return items


def _folder_of(item: dict) -> str:
    return (item.get('folder') or '').strip()


def _folder_parts(folder: str) -> list[str]:
    return [part for part in (folder or '').split('/') if part.strip()]


HOSTLIKE = re.compile(r'^(?:https?://)?(?:www\.)?[\w.-]+\.[A-Za-z]{2,}(?:[/?#].*)?$')


def display_title(item: dict) -> str:
    """真标题，或空串 —— 名字本身就是网址时按「没标题」处理。

    浏览器在页面没给 title 时会把地址当标题存下来（``pixiv.net/``、
    ``ipinfo.io/what-is-my-ip``）。这类在笔记里应该直接写链接，而不是
    ``- [pixiv.net/](…)``。
    """
    title = (item.get('title') or '').strip()
    if not title:
        return ''
    lowered = title.lower()
    if lowered.startswith(('http://', 'https://')):
        return ''
    if HOSTLIKE.match(title):
        return ''
    host = urlsplit(item.get('url') or '').netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    candidate = lowered[4:] if lowered.startswith('www.') else lowered
    path = (urlsplit(item.get('url') or '').path or '').strip('/')
    if candidate in (host, f'{host}/{path}'):
        return ''
    return title


def _group_tree(items: list[dict]) -> dict:
    """按 ``a/b/c`` 目录路径分组，保持条目在浏览器里的先后顺序。"""
    root: dict = {'children': OrderedDict(), 'items': []}
    for item in items:
        node = root
        for part in _folder_parts(_folder_of(item)):
            node = node['children'].setdefault(part, {'children': OrderedDict(),
                                                      'items': []})
        node['items'].append(item)
    return root


def render_markdown(items: list[dict], title: str = '',
                    frontmatter: bool = False, instructions: bool = False) -> str:
    """按书签目录树渲染成 Markdown 文本（不落盘，便于直接贴给用户）。"""
    lines: list[str] = []
    if frontmatter:
        stamp = _dt.datetime.now().isoformat(timespec='seconds')
        lines += ['---', f'title: {title or "Web Bookmarks"}', f'updated: {stamp}',
                  f'count: {len(items)}', '---', '']
    if title:
        lines += [f'# {title}', '']
    if instructions:
        lines += ['> 直接改下面的列表：`## 文件夹` 下写 `- [标题](链接)` 或直接写链接。',
                  '> 改完 `bookmarks.py note import` 收回，再 `bookmarks.py sync` 同步。', '']

    def emit(node: dict, depth: int) -> None:
        for item in node['items']:
            label = display_title(item)
            lines.append(f'- [{label}]({item["url"]})' if label
                         else f'- {item["url"]}')
        for name, child in node['children'].items():
            if lines and lines[-1] != '':
                lines.append('')
            lines.append('#' * (depth + 2) + ' ' + name)
            lines.append('')
            emit(child, depth + 1)

    emit(_group_tree(items), 0)
    return '\n'.join(lines).rstrip() + '\n'


def export_markdown(items: list[dict], out: Path, title: str = '',
                    frontmatter: bool = False, instructions: bool = False) -> Path:
    """写一份 Obsidian 里可以直接改、再 ``note import`` 回来的笔记。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(items, title, frontmatter, instructions),
                   encoding='utf-8')
    return out


def note_path(explicit: str = '') -> Path:
    """Obsidian 笔记位置：``--note`` > local.json ``bookmarks.note``。"""
    raw = explicit or setting('note')
    if not raw:
        raise SystemExit('[bookmarks] no note configured; pass --note or set '
                         'bookmarks.note in local.json')
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    vault = setting('vault')
    if not vault:
        raise SystemExit('[bookmarks] relative note needs bookmarks.vault in local.json')
    return Path(vault).expanduser() / path


def find_vivaldi(explicit: str = '') -> str:
    candidates = [explicit, setting('vivaldi_exe')]
    if sys.platform == 'win32':
        candidates += [r'C:\Program Files\Vivaldi\Application\vivaldi.exe',
                       os.path.join(os.environ.get('PROGRAMFILES', ''), 'Vivaldi',
                                    'Application', 'vivaldi.exe'),
                       os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Vivaldi',
                                    'Application', 'vivaldi.exe')]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ''


# ── identity & merge ─────────────────────────────────────────────────────


def url_key(url: str) -> str:
    """Normalised identity: no scheme, no ``www.``, no slash, no tracking params."""
    raw = (url or '').strip()
    if not raw:
        return ''
    parts = urlsplit(raw)
    host = parts.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING]
    pairs.sort()
    path = parts.path.rstrip('/')
    query = f'?{urlencode(pairs)}' if pairs else ''
    return f'{host}{path}{query}'


def _earliest(*values: int) -> int:
    present = [v for v in values if v]
    return min(present) if present else 0


def merge(snapshots: list[tuple[str, dict]]) -> dict:
    """Union of machine snapshots, collapsing rows with the same ``url_key``."""
    rows: OrderedDict[str, dict] = OrderedDict()
    for host, payload in snapshots:
        for item in payload.get('bookmarks') or []:
            key = url_key(item.get('url') or '')
            if not key:
                continue
            current = rows.get(key)
            if current is None:
                rows[key] = {
                    'url': item.get('url') or '',
                    'title': (item.get('title') or '').strip(),
                    'folder': item.get('folder') or '',
                    'added': int(item.get('added') or 0),
                    'machines': [host],
                }
                continue
            current['added'] = _earliest(current['added'], int(item.get('added') or 0))
            if not current['title'] and item.get('title'):
                current['title'] = item['title'].strip()
            if not current['folder'] and item.get('folder'):
                current['folder'] = item['folder']
            if host not in current['machines']:
                current['machines'].append(host)
    ordered = sorted(rows.values(), key=lambda r: (r['folder'], r['title'], r['url']))
    for row in ordered:
        row['machines'] = sorted(row['machines'])
    return {'schema_version': SCHEMA,
            'updated': _dt.datetime.now().isoformat(timespec='seconds'),
            'count': len(ordered),
            'bookmarks': ordered}


# ── snapshots on disk ────────────────────────────────────────────────────


def snapshot_path(hub: Path, host: str) -> Path:
    return namespace_dir(hub) / 'machines' / f'{host}.json'


def write_snapshot(hub: Path, host: str, items: list[dict], source: str) -> Path:
    path = snapshot_path(hub, host)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'schema_version': SCHEMA,
               'machine': host,
               'source': Path(source).name if source else '',
               'imported': _dt.datetime.now().isoformat(timespec='seconds'),
               'count': len(items),
               'bookmarks': items}
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return path


def load_snapshots(hub: Path) -> list[tuple[str, dict]]:
    directory = namespace_dir(hub) / 'machines'
    snapshots = []
    if not directory.is_dir():
        return snapshots
    for path in sorted(directory.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        snapshots.append((payload.get('machine') or path.stem, payload))
    return snapshots


def _same_ignoring_timestamp(left: dict, right: dict) -> bool:
    def strip(payload):
        return {k: v for k, v in payload.items() if k != 'updated'}
    return strip(left) == strip(right)


def rebuild(hub: Path) -> dict:
    merged = merge(load_snapshots(hub))
    directory = namespace_dir(hub)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'bookmarks.json'
    # 内容没变就别动文件：否则每次 sync 都会因为 updated 时间戳产生一个空提交
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and _same_ignoring_timestamp(existing, merged):
            return existing
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return merged


def load_merged(hub: Path) -> dict:
    path = namespace_dir(hub) / 'bookmarks.json'
    if not path.is_file():
        return rebuild(hub)
    return json.loads(path.read_text(encoding='utf-8'))


# ── exports ──────────────────────────────────────────────────────────────


def _tree(items: list[dict]) -> dict:
    root = {'children': OrderedDict(), 'items': []}
    for item in items:
        node = root
        for part in [p for p in (item.get('folder') or '').split('/') if p]:
            node = node['children'].setdefault(part, {'children': OrderedDict(),
                                                      'items': []})
        node['items'].append(item)
    return root


def export_html(items: list[dict], out: Path, title: str = 'Bookmarks') -> Path:
    lines = ['<!DOCTYPE NETSCAPE-Bookmark-file-1>',
             '<!-- This is an automatically generated file. -->',
             '<!-- It will be read and overwritten. -->',
             '<!-- Do NOT EDIT! -->',
             '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
             f'<TITLE>{escape(title)}</TITLE>',
             f'<H1>{escape(title)}</H1>',
             '<DL><p>']

    def emit(node: dict, indent: str) -> None:
        for name, child in node['children'].items():
            lines.append(f'{indent}<DT><H3>{escape(name)}</H3>')
            lines.append(f'{indent}<DL><p>')
            emit(child, indent + '    ')
            lines.append(f'{indent}</DL><p>')
        for item in node['items']:
            added = item.get('added') or 0
            stamp = f' ADD_DATE="{added}"' if added else ''
            label = escape(item.get('title') or item.get('url') or '')
            lines.append(f'{indent}<DT><A HREF="{escape(item["url"], quote=True)}"'
                         f'{stamp}>{label}</A>')

    emit(_tree(items), '    ')
    lines.append('</DL><p>')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out


# ── Vivaldi 写回：生成合法的 Bookmarks JSON ─────────────────────────────


def chromium_now() -> int:
    delta = _dt.datetime.now() - CHROMIUM_EPOCH
    return int(delta.total_seconds() * 1_000_000)


def new_guid() -> str:
    import uuid
    return str(uuid.uuid4())


def bookmarks_checksum(roots: dict) -> str:
    """Vivaldi/Chromium 的 ``checksum``：前序遍历 id+标题(UTF-16LE)+类型(+url) 取 MD5。

    实测差异：Vivaldi 把 ``trash`` 也一起算（上游 Chromium 只算三个永久根），
    用现有 ``Bookmarks`` 验证过——这条不能用猜的，算错会被当损坏文件回退。
    """
    hasher = hashlib.md5()

    def walk(node: dict) -> None:
        hasher.update(str(node.get('id', '')).encode('ascii'))
        hasher.update((node.get('name') or '').encode('utf-16-le'))
        if node.get('type') == 'url':
            hasher.update(b'url')
            hasher.update((node.get('url') or '').encode('utf-8'))
        else:
            hasher.update(b'folder')
            for child in node.get('children') or []:
                walk(child)

    for key in ('bookmark_bar', 'other', 'synced', 'trash'):
        node = roots.get(key)
        if isinstance(node, dict):
            walk(node)
    return hasher.hexdigest()


def _folder_node(name: str, node_id: int, stamp: int, meta: dict | None = None) -> dict:
    node = {'children': [], 'date_added': str(stamp), 'date_last_used': '0',
            'date_modified': str(stamp), 'guid': new_guid(), 'id': str(node_id),
            'name': name, 'type': 'folder'}
    if meta:
        node['meta_info'] = meta
    return node


def _url_node(item: dict, node_id: int, stamp: int) -> dict:
    return {'date_added': str(stamp), 'date_last_used': '0', 'guid': new_guid(),
            'id': str(node_id), 'name': display_title(item) or item['url'],
            'type': 'url', 'url': item['url']}


def _max_node_id(roots: dict) -> int:
    highest = 100

    def scan(node: dict) -> None:
        nonlocal highest
        try:
            highest = max(highest, int(node.get('id') or 0))
        except (TypeError, ValueError):
            pass
        for child in node.get('children') or []:
            scan(child)

    for node in roots.values():
        if isinstance(node, dict):
            scan(node)
    return highest


def build_vivaldi_payload(items: list[dict], base: dict | None = None,
                          folder_name: str = '') -> dict:
    """把条目建成「书签栏里只有一个文件夹」的 Bookmarks 结构。

    这个文件夹带 ``meta_info.Speeddial = "true"``（Vivaldi 的「设为快速拨号」），
    书签栏不留其他节点；``other`` / ``synced`` / ``trash`` 全部清空。
    """
    base = base or {}
    existing = base.get('roots') or {}
    stamp = chromium_now()
    counter = _max_node_id(existing)

    def take_id() -> int:
        nonlocal counter
        counter += 1
        return counter

    tree = _group_tree(items)

    def convert(group: dict, name: str, meta: dict | None = None) -> dict:
        node = _folder_node(name, take_id(), stamp, meta)
        for item in group['items']:
            node['children'].append(_url_node(item, take_id(), stamp))
        for child_name, child in group['children'].items():
            node['children'].append(convert(child, child_name))
        return node

    top = list(tree['children'])
    bar_children: list[dict] = []
    if len(top) == 1 and not tree['items']:
        bar_children.append(convert(tree['children'][top[0]], top[0],
                                    {'Speeddial': 'true'}))
    elif tree['items'] or tree['children']:
        wrapper = {'items': tree['items'], 'children': tree['children']}
        bar_children.append(convert(wrapper, folder_name or '书签',
                                    {'Speeddial': 'true'}))

    roots: dict = {}
    defaults = (('bookmark_bar', '书签'), ('other', '其他书签'),
                ('synced', '移动设备书签'), ('trash', '回收站'))
    for key, default_name in defaults:
        old = existing.get(key) if isinstance(existing.get(key), dict) else {}
        node = {k: v for k, v in old.items() if k != 'children'}
        node.setdefault('name', default_name)
        node.setdefault('type', 'folder')
        node.setdefault('id', str(len(roots)))
        node.setdefault('guid', new_guid())
        node.setdefault('date_added', str(stamp))
        node.setdefault('date_last_used', '0')
        node.setdefault('date_modified', str(stamp))
        node['children'] = []
        roots[key] = node
    roots['bookmark_bar']['children'] = bar_children
    roots['bookmark_bar']['date_modified'] = str(stamp)
    roots['other']['date_modified'] = '0'
    roots['synced']['date_modified'] = '0'
    roots['trash']['date_modified'] = str(stamp)

    payload = {'checksum': bookmarks_checksum(roots),
               'roots': roots,
               # 旧 sync_metadata 指向已删除的节点，原样保留会让 Vivaldi 同步把
               # 旧书签再“恢复”回来（实测），所以清空。
               'sync_metadata': '',
               'version': 1}
    return payload


def summarize_bookmarks(roots: dict) -> dict:
    folders = urls = 0

    def walk(node: dict) -> None:
        nonlocal folders, urls
        for child in node.get('children') or []:
            if child.get('type') == 'url':
                urls += 1
            else:
                folders += 1
                walk(child)

    for node in roots.values():
        if isinstance(node, dict):
            walk(node)
    return {'folders': folders, 'urls': urls,
            'speeddial': [n.get('name') for n in (roots.get('bookmark_bar') or {}).get('children') or []
                          if (n.get('meta_info') or {}).get('Speeddial')]}


def verify_bookmarks_file(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    roots = data.get('roots') or {}
    stored = data.get('checksum', '')
    computed = bookmarks_checksum(roots)
    return {'path': str(path), 'stored': stored, 'computed': computed,
            'ok': stored == computed, 'version': data.get('version'),
            **summarize_bookmarks(roots)}



def _load_items(path: Path, fmt: str) -> list[dict]:
    if fmt == 'vivaldi':
        return parse_vivaldi(path)
    text = path.read_text(encoding='utf-8', errors='replace')
    return parse_markdown(text) if fmt == 'md' else parse_export(text)


def _detect_format(path: Path, explicit: str) -> str:
    if explicit and explicit != 'auto':
        return explicit
    suffix = path.suffix.lower()
    if suffix == '.json':
        return 'vivaldi'
    if suffix in ('.md', '.markdown'):
        return 'md'
    return 'html'


def _filter(items: list[dict], folder: str = '', pattern: str = '') -> list[dict]:
    if folder:
        items = [i for i in items
                 if folder.lower() in _folder_of(i).lower()]
    if pattern:
        regex = re.compile(pattern, re.I)
        items = [i for i in items
                 if regex.search(i.get('url') or '') or regex.search(i.get('title') or '')]
    return items


# ── commands ─────────────────────────────────────────────────────────────


def cmd_import(args) -> int:
    if args.file:
        path = Path(args.file).expanduser()
        fmt = _detect_format(path, args.format)
        source = path
    else:
        profile = vivaldi_profile(args.profile)
        source = vivaldi_bookmarks_file(profile)
        fmt = 'vivaldi'
    if not source.is_file():
        print(f'[bookmarks] no such file: {source}', file=sys.stderr)
        return 2
    items = _load_items(source, fmt)
    if not items:
        hint = 'is this a browser export?'
        if fmt == 'vivaldi':
            hint = 'Vivaldi 的书签在 <profile>/Bookmarks，检查 --profile'
        print(f'[bookmarks] parsed 0 bookmarks — {hint}', file=sys.stderr)
        return 2
    hub = hub_root(args.hub)
    host = args.host or machine_name()
    written = write_snapshot(hub, host, items, str(source))
    merged = rebuild(hub)
    unique_mine = len({url_key(i['url']) for i in items})
    print(f'[bookmarks] {host} ({fmt}): {len(items)} bookmark(s), '
          f'{unique_mine} unique -> {written}')
    print(f'[bookmarks] merged: {merged["count"]} bookmark(s) across '
          f'{len(load_snapshots(hub))} machine(s)')
    return 0


def cmd_merge(args) -> int:
    hub = hub_root(args.hub)
    merged = rebuild(hub)
    print(f'[bookmarks] merged {merged["count"]} bookmark(s) from '
          f'{len(load_snapshots(hub))} machine(s)')
    return 0


def cmd_status(args) -> int:
    hub = hub_root(args.hub)
    snapshots = load_snapshots(hub)
    merged = load_merged(hub)
    report = {
        'hub': str(namespace_dir(hub)),
        'machines': [{'machine': host, 'count': payload.get('count'),
                      'imported': payload.get('imported')}
                     for host, payload in snapshots],
        'total': merged.get('count', 0),
        'shared': sum(1 for row in merged.get('bookmarks', [])
                      if len(row.get('machines') or []) > 1),
        'untitled': sum(1 for row in merged.get('bookmarks', []) if not row.get('title')),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"hub: {report['hub']}")
    for machine in report['machines']:
        print(f"  {machine['machine']:20} {machine['count']:>5}  "
              f"imported {machine['imported']}")
    print(f"total: {report['total']}  shared across machines: {report['shared']}  "
          f"untitled: {report['untitled']}")
    return 0


def _items_for_export(hub: Path, machine: str = '') -> list[dict]:
    """合并表，或某台机器的原始快照（保留浏览器里的目录顺序）。"""
    if machine:
        for host, payload in load_snapshots(hub):
            if host == machine:
                return payload.get('bookmarks') or []
        raise SystemExit(f'[bookmarks] no snapshot for machine {machine!r}')
    return load_merged(hub).get('bookmarks', [])


def cmd_export(args) -> int:
    hub = hub_root(args.hub)
    if getattr(args, 'from_note', False):
        path = note_path(args.note)
        items = parse_markdown(path.read_text(encoding='utf-8', errors='replace'))
        source = f'note {path.name}'
    else:
        items = _items_for_export(hub, getattr(args, 'machine', ''))
        source = 'hub'
    items = _filter(items, args.folder, args.filter)
    if not items:
        print('[bookmarks] nothing matched', file=sys.stderr)
        return 4
    if args.stdout:
        if args.format != 'md':
            print('[bookmarks] --stdout only supports --format md', file=sys.stderr)
            return 2
        sys.stdout.write(render_markdown(items, title=args.title))
        return 0
    out = Path(args.out).expanduser()
    if args.format == 'html':
        export_html(items, out)
    elif args.format == 'md':
        export_markdown(items, out, title=args.title)
    elif args.format == 'urls':
        out.write_text('\n'.join(i['url'] for i in items) + '\n', encoding='utf-8')
    else:
        out.write_text(json.dumps({'count': len(items), 'bookmarks': items},
                                  ensure_ascii=False, indent=2) + '\n',
                       encoding='utf-8')
    print(f'[bookmarks] {len(items)} bookmark(s) -> {out}')
    return 0


def cmd_note(args) -> int:
    """Obsidian 侧：输出笔记文本 / 写笔记 / 收回笔记改动 / 打开笔记。

    本机对 vault 可能没有写权限（vault 是另一台机器同步下来的），所以
    ``export --stdout`` 是默认安全姿势：把文本交给用户，由权威副本那边落盘。
    """
    path = note_path(args.note) if setting('note') or args.note else Path('')
    hub = hub_root(args.hub)
    if args.action == 'export':
        items = _filter(_items_for_export(hub, args.machine), args.folder, args.filter)
        if not items:
            print('[bookmarks] nothing matched', file=sys.stderr)
            return 4
        text = render_markdown(items, title=args.title)
        readonly = setting('readonly_vault').lower() in ('1', 'true', 'yes', 'on')
        if args.stdout or not path or readonly:
            sys.stdout.write(text)
            if not args.stdout and readonly:
                print('[bookmarks] readonly_vault=true — printed instead of writing',
                      file=sys.stderr)
            elif not args.stdout:
                print('[bookmarks] note path not configured — printed to stdout',
                      file=sys.stderr)
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        print(f'[bookmarks] {len(items)} bookmark(s) -> note {path}')
        print('[bookmarks] 在 Obsidian 里改完，跑 `bookmarks.py note import` 收回')
        return 0
    if args.action == 'import':
        if not path or not path.is_file():
            print(f'[bookmarks] no such note: {path or "(unset)"}', file=sys.stderr)
            return 2
        items = parse_markdown(path.read_text(encoding='utf-8', errors='replace'))
        if not items:
            print('[bookmarks] note has no `- [标题](链接)` entries', file=sys.stderr)
            return 2
        host = args.host or 'OBSIDIAN'
        written = write_snapshot(hub, host, items, str(path))
        merged = rebuild(hub)
        print(f'[bookmarks] {host}: {len(items)} bookmark(s) from note -> {written}')
        print(f'[bookmarks] merged: {merged["count"]} bookmark(s)')
        return 0
    # open：交给 Obsidian 客户端
    vault = Path(setting('vault')).expanduser()
    if not vault.is_dir():
        print('[bookmarks] bookmarks.vault not configured in local.json', file=sys.stderr)
        return 2
    relative = path.relative_to(vault).with_suffix('') if path.is_relative_to(vault) else path.stem
    url = f'obsidian://open?vault={vault.name}&file={relative.as_posix()}'
    os.startfile(url) if sys.platform == 'win32' else subprocess.call(['xdg-open', url])
    print(f'[bookmarks] opened in Obsidian: {url}')
    return 0


def disable_bookmark_sync(profile: Path) -> dict:
    """在 Preferences 里关掉书签同步（只关 bookmarks，其他同步不动）。"""
    path = profile / 'Preferences'
    data = json.loads(path.read_text(encoding='utf-8'))
    sync = data.setdefault('sync', {})
    before = sync.get('bookmarks')
    sync['bookmarks'] = False
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')),
                         encoding='utf-8')
    os.replace(temporary, path)
    return {'before': before, 'after': sync['bookmarks']}


def vivaldi_sync_state(profile: Path) -> dict:
    """Vivaldi 的书签同步状态：开着的时候改本地文件会被云端合并回来（实测）。"""
    try:
        prefs = json.loads((profile / 'Preferences').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'bookmarks_sync': False, 'account': ''}
    sync = prefs.get('sync') or {}
    account = (prefs.get('vivaldi') or {}).get('vivaldi_account') or {}
    return {'bookmarks_sync': bool(sync.get('bookmarks')),
            'account': account.get('id', '')}


def vivaldi_dir(explicit: str = '') -> Path:
    return Path(explicit).expanduser() if explicit else vivaldi_profile()


def vivaldi_running() -> bool:
    if sys.platform != 'win32':
        return subprocess.run(['pgrep', '-f', 'vivaldi'], capture_output=True).returncode == 0
    try:
        output = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq vivaldi.exe'],
                                capture_output=True, text=True, timeout=20).stdout
        return 'vivaldi.exe' in output.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def close_vivaldi(wait: float = 40.0) -> bool:
    """优雅关闭 Vivaldi（只发 CloseMainWindow，不强杀）。"""
    if not vivaldi_running():
        return True
    if sys.platform == 'win32':
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        '(Get-Process vivaldi -ErrorAction SilentlyContinue).CloseMainWindow() '
                        '| Out-Null'], capture_output=True, timeout=60)
    else:
        subprocess.run(['pkill', '-f', 'vivaldi'], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not vivaldi_running():
            return True
        time.sleep(1)
    return not vivaldi_running()


def vivaldi_backup_dir() -> Path:
    stamp = _dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    return Path(__file__).resolve().parents[2] / 'dev' / 'backups' / 'vivaldi' / stamp


def cmd_vivaldi(args) -> int:
    profile = vivaldi_dir(args.profile)
    target = profile / 'Bookmarks'
    if args.action == 'verify':
        path = Path(args.file).expanduser() if args.file else target
        info = verify_bookmarks_file(path)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info['ok'] else 1

    note = note_path(args.note)
    if not note.is_file():
        print(f'[bookmarks] note not found: {note}', file=sys.stderr)
        return 2
    items = parse_markdown(note.read_text(encoding='utf-8', errors='replace'))
    if not items:
        print(f'[bookmarks] note has no links: {note}', file=sys.stderr)
        return 2
    base = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
    payload = build_vivaldi_payload(items, base, args.folder)
    summary = summarize_bookmarks(payload['roots'])

    if args.action == 'build':
        out = Path(args.out).expanduser() if args.out else Path('vivaldi_Bookmarks.json')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=3), encoding='utf-8')
        print(f"[bookmarks] built {summary['urls']} url(s) in {summary['folders']} folder(s)"
              f" -> {out}")
        print(f"[bookmarks] speed dial folder(s): {summary['speeddial']}")
        print(f"[bookmarks] checksum: {payload['checksum']}")
        return 0

    print(f"[bookmarks] {summary['urls']} url(s), {summary['folders']} folder(s), "
          f"speed dial: {summary['speeddial']}")
    state = vivaldi_sync_state(profile)
    if state['bookmarks_sync'] and not getattr(args, 'ignore_sync', False) \
            and not getattr(args, 'disable_sync', False):
        print(f"[bookmarks] Vivaldi 书签同步开着（账号 {state['account'] or '?'}）："
              '直接改本地 Bookmarks 会被云端合并/回滚。先在 Vivaldi 关闭书签同步，'
              '或加 --disable-sync 由脚本关掉，确认要硬写就加 --ignore-sync',
              file=sys.stderr)
        return 3
    if args.dry_run:
        print('[bookmarks] dry-run: nothing written')
        return 0
    if vivaldi_running():
        if not args.close:
            print('[bookmarks] Vivaldi is running — close it, or pass --close',
                  file=sys.stderr)
            return 3
        if not close_vivaldi():
            print('[bookmarks] Vivaldi did not exit; aborting', file=sys.stderr)
            return 3
    if not target.is_file():
        print(f'[bookmarks] no Bookmarks file at {target}', file=sys.stderr)
        return 2
    backup = vivaldi_backup_dir()
    backup.mkdir(parents=True, exist_ok=True)
    for name in ('Bookmarks', 'Bookmarks.bak', 'Preferences'):
        source = profile / name
        if source.is_file():
            shutil.copy2(source, backup / name)
    if getattr(args, 'disable_sync', False):
        result = disable_bookmark_sync(profile)
        print(f"[bookmarks] 书签同步: {result['before']} -> {result['after']}")
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=3),
                         encoding='utf-8')
    os.replace(temporary, target)
    info = verify_bookmarks_file(target)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f'[bookmarks] backup: {backup}')
    return 0 if info['ok'] else 1


def cmd_open(args) -> int:
    """把一组书签当标签页丢给浏览器（Vivaldi --new-window url…）。"""
    hub = hub_root(args.hub)
    merged = load_merged(hub)
    items = _filter(merged.get('bookmarks', []), args.folder, args.filter)
    if not items:
        print('[bookmarks] nothing matched', file=sys.stderr)
        return 4
    if args.limit and len(items) > args.limit:
        items = items[:args.limit]
    urls = [i['url'] for i in items]
    exe = find_vivaldi(args.vivaldi_exe)
    if not exe:
        print('[bookmarks] Vivaldi not found; pass --vivaldi-exe', file=sys.stderr)
        return 2
    command = [exe, '--new-window', *urls]
    if args.dry_run:
        print(f'[bookmarks] would open {len(urls)} tab(s): {exe} --new-window <urls>')
        for url in urls[:5]:
            print(f'    {url}')
        return 0
    subprocess.Popen(command)
    print(f'[bookmarks] opened {len(urls)} tab(s) in Vivaldi')
    return 0


def cmd_diff(args) -> int:
    hub = hub_root(args.hub)
    merged = {url_key(r['url']): r for r in load_merged(hub).get('bookmarks', [])}
    path = Path(args.file).expanduser()
    incoming = {url_key(i['url']): i for i in _load_items(path, _detect_format(path, args.format))}
    added = [incoming[k] for k in incoming.keys() - merged.keys()]
    removed = [merged[k] for k in merged.keys() - incoming.keys()]
    print(f'[bookmarks] +{len(added)} new, -{len(removed)} only in the hub')
    for item in added[:args.limit]:
        print(f'  + {item.get("title") or item["url"]}  [{item.get("folder") or "-"}]')
    for item in removed[:args.limit]:
        print(f'  - {item.get("title") or item["url"]}  [{item.get("folder") or "-"}]')
    return 0


def _tasks_py() -> Path:
    return Path(__file__).resolve().parents[1] / 'vpn' / 'tasks.py'


def _git(hub: Path, *args: str, network: bool = False) -> int:
    """Run git in the hub; network steps go through the VPN task table."""
    command = ['git', '-C', str(hub), *args]
    if network:
        command = [sys.executable, str(_tasks_py()), 'run', '--', *command]
    return subprocess.call(command)


def _git_capture(hub: Path, *args: str) -> str:
    """Run git and return stdout (local commands only)."""
    proc = subprocess.run(
        ['git', '-C', str(hub), *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc.stdout.strip() if proc.returncode == 0 else ''


def _branch(hub: Path) -> str:
    return _git_capture(hub, 'rev-parse', '--abbrev-ref', 'HEAD') or 'main'


def _publish_single(hub: Path, message: str, no_push: bool) -> int:
    """把 hub 压成**一个无父提交**再推（数据仓不需要历史，2026-11-23 用户拍板）。

    具体步骤在 `scripts/projects/hub_publish.py`：暂存 → 无父提交 → 强推 →
    远程被抢先时取回、重建、再压一次。所有写数据仓的入口都走那一份实现。
    """

    def rebuild_after_conflict() -> int:
        rebuild(hub)
        return 0

    return hub_publish.publish(
        hub,
        message,
        paths=[f'{NAMESPACE}/{FOLDER}'],
        no_push=no_push,
        run=lambda args, network=False: _git(hub, *args, network=network),
        on_conflict=rebuild_after_conflict,
    )


def cmd_sync(args) -> int:
    hub = hub_root(args.hub)
    if not (hub / '.git').is_dir():
        print(f'[bookmarks] {hub} is not a git repository', file=sys.stderr)
        return 2
    branch = _branch(hub)
    # 采用远端：单提交模式下本地与远端没有共同历史，pull / rebase 用不了
    if _git(hub, 'stash', 'push', '--include-untracked', '-m', 'bookmarks-sync') not in (0, 1):
        print('[bookmarks] stash failed', file=sys.stderr)
        return 3
    if _git(hub, 'fetch', 'origin', branch, network=True) != 0:
        print('[bookmarks] fetch failed (VPN?) — nothing pushed', file=sys.stderr)
        return 3
    if _git(hub, 'reset', '--hard', 'FETCH_HEAD') != 0:
        return 3
    _git(hub, 'stash', 'pop')
    merged = rebuild(hub)
    relative = f'{NAMESPACE}/{FOLDER}'
    if _git(hub, 'add', relative) != 0:
        return 3
    dirty = subprocess.run(['git', '-C', str(hub), 'diff', '--cached', '--quiet']).returncode
    if dirty == 0:
        print(f'[bookmarks] nothing to commit ({merged["count"]} bookmark(s))')
        return 0
    message = args.message or f'bookmarks: {machine_name()} ({merged["count"]})'
    if _publish_single(hub, message, no_push=args.no_push) != 0:
        return 3
    if args.no_push:
        return 0
    print(f'[bookmarks] synced {merged["count"]} bookmark(s): {message}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Keep browser bookmarks as mergeable data in the private hub repo.')
    parser.add_argument('--hub', default='', help='override data_hub.path')
    sub = parser.add_subparsers(dest='command', required=True)

    imp = sub.add_parser('import', help='import Vivaldi / HTML export / Obsidian note')
    imp.add_argument('file', nargs='?', help='omit to read the live Vivaldi Bookmarks')
    imp.add_argument('--format', default='auto', choices=['auto', 'html', 'vivaldi', 'md'])
    imp.add_argument('--profile', default='', help='Vivaldi profile dir')
    imp.add_argument('--host', default='')

    sub.add_parser('merge', help='rebuild bookmarks.json from machine snapshots')
    status = sub.add_parser('status', help='counts per machine and overall')
    status.add_argument('--json', action='store_true')

    export = sub.add_parser('export', help='write html / md / json / urls')
    export.add_argument('--format', default='html',
                        choices=['html', 'md', 'json', 'urls'])
    export.add_argument('--out', default='', help='output file (omit with --stdout)')
    export.add_argument('--stdout', action='store_true',
                        help='print the markdown instead of writing a file')
    export.add_argument('--folder', default='', help='only folders containing this')
    export.add_argument('--filter', default='', help='regex over url/title')
    export.add_argument('--title', default='', help='md note title')
    export.add_argument('--machine', default='', help='export one snapshot, keeping browser order')
    export.add_argument('--from-note', action='store_true',
                        help='export the Obsidian note instead of the hub merge')
    export.add_argument('--note', default='', help='note override for --from-note')

    note = sub.add_parser('note', help='Obsidian note: export / import / open')
    note.add_argument('action', choices=['export', 'import', 'open'])
    note.add_argument('--note', default='', help='override bookmarks.note')
    note.add_argument('--stdout', action='store_true',
                      help='print the note instead of touching the vault')
    note.add_argument('--host', default='', help='snapshot name for import (default OBSIDIAN)')
    note.add_argument('--folder', default='')
    note.add_argument('--filter', default='')
    note.add_argument('--title', default='')
    note.add_argument('--machine', default='', help='export one snapshot instead of the merge')

    open_p = sub.add_parser('open', help='open a bookmark set as Vivaldi tabs')
    open_p.add_argument('--folder', default='')
    open_p.add_argument('--filter', default='')
    open_p.add_argument('--limit', type=int, default=20)
    open_p.add_argument('--vivaldi-exe', default='')
    open_p.add_argument('--dry-run', action='store_true')

    diff = sub.add_parser('diff', help='compare a fresh export with the merged table')
    diff.add_argument('file')
    diff.add_argument('--format', default='auto', choices=['auto', 'html', 'vivaldi', 'md'])
    diff.add_argument('--limit', type=int, default=20)

    sync = sub.add_parser('sync', help='pull, merge, commit, push through the VPN table')
    sync.add_argument('--message', default='')
    sync.add_argument('--no-push', action='store_true')

    vivaldi = sub.add_parser('vivaldi', help='read or rewrite Vivaldi Bookmarks')
    vivaldi.add_argument('action', choices=['verify', 'build', 'apply'])
    vivaldi.add_argument('--profile', default='', help='Vivaldi profile dir')
    vivaldi.add_argument('--file', default='', help='Bookmarks file for verify')
    vivaldi.add_argument('--note', default='', help='override bookmarks.note')
    vivaldi.add_argument('--folder', default='', help='top folder name fallback')
    vivaldi.add_argument('--out', default='', help='build output path')
    vivaldi.add_argument('--close', action='store_true', help='close Vivaldi before writing')
    vivaldi.add_argument('--ignore-sync', action='store_true',
                         help='write even when Vivaldi bookmark sync is enabled')
    vivaldi.add_argument('--disable-sync', action='store_true',
                         help='turn off Vivaldi bookmark sync in Preferences before writing')
    vivaldi.add_argument('--dry-run', action='store_true')

    args = parser.parse_args(argv)
    handlers = {'import': cmd_import, 'merge': cmd_merge, 'status': cmd_status,
                'export': cmd_export, 'diff': cmd_diff, 'sync': cmd_sync,
                'note': cmd_note, 'open': cmd_open, 'vivaldi': cmd_vivaldi}
    return handlers[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
