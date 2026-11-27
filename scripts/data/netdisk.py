#!/usr/bin/env python3
"""Baidu netdisk via the open platform (xpan) API.

The desktop client has no CLI and no usable API; the open platform does.  A
personal access token (scope ``basic,netdisk``, 30 days) is stored in the
gitignored ``local.json`` and never in this repo:

    "netdisk": {"access_token": "<token>", "expires_in": 2592000}

What works with that token: ``listall`` (full tree with size/md5), ``list``,
``filemanager`` (delete / move / rename / copy), search, quota, recycle bin.
Upload of a local file needs ``precreate`` + ``superfile2`` + ``create``; large
bodies get dropped by the server, so big packages still go through the desktop
client.  The md5 field is masked for some files (non-hex characters); masked
values are still stable per content, so duplicates *inside* the disk can be
found, but they cannot be compared with locally computed md5.

Deleting is the one irreversible-ish thing here, so the rules are strict:

* a duplicate is only ``完整 32 位 md5 + 同大小 + 同一目录``; masked md5, size
  mismatch and cross-folder copies are reported as skipped, never deleted;
* ``dups`` only writes a plan (markdown + JSON) and says so; it never deletes;
* ``rm`` prints the whole list first and needs ``--yes`` to act, then reports
  every path as ok/failed, and appends one JSONL line per path under
  ``temp/netdisk-audit/`` (gitignored) so any deletion can be traced later.

Commands:
    python scripts/data/netdisk.py quota
    python scripts/data/netdisk.py ls /data
    python scripts/data/netdisk.py tree /data -o temp/nd_tree.json
    python scripts/data/netdisk.py dups temp/nd_tree.json -o temp/nd_dups.md \
        --plan temp/nd_plan.json
    python scripts/data/netdisk.py rm --plan temp/nd_plan.json --yes
    python scripts/data/netdisk.py rm /data/a/x.txt --yes
    python scripts/data/netdisk.py trash --limit 50
    python scripts/data/netdisk.py mv /data/a/x.zip /data/b
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402

API = 'https://pan.baidu.com/rest/2.0/xpan/'
QUOTA = 'https://pan.baidu.com/api/quota'
RECYCLE = API + 'recycle'

VOLUME = re.compile(r'\.(z\d{2}|r\d{2}|part\d+\.rar|part\d+|7z\.\d{3}|zip\.\d{3})$', re.I)
ARCHIVE = re.compile(r'\.(zip|rar|7z|tar|gz|bz2|iso|dmg|exe|msi|z\d{2}|r\d{2})$', re.I)
COPY = re.compile(r'[(（]\d+[)）]')
# 服务端对部分文件返回打码 md5（含非十六进制字符）；只有完整的 32 位 hex 才算可信
MD5_FULL = re.compile(r'^[0-9a-f]{32}$', re.I)

DEFAULT_TREE = 'temp/nd_tree.json'
DEFAULT_MARK = 'temp/nd_dups.md'
DEFAULT_PLAN = 'temp/nd_plan.json'
DEFAULT_LOG_DIR = 'temp/netdisk-audit'
MAX_DELETE = 200


# ---------------------------------------------------------------- pure helpers
def token() -> str:
    """Access token from local.json; fails loudly when missing."""
    data = (cfg.load().get('netdisk') or {})
    value = data.get('access_token') or ''
    if not value:
        raise SystemExit('local.json 缺少 netdisk.access_token（用 openapi 授权链接拿一个）')
    return value


def norm(path: str) -> str:
    """Normalise a remote path for comparisons."""
    return '/' + '/'.join(part for part in (path or '').replace('\\', '/').split('/') if part)


def name_of(path: str) -> str:
    return norm(path).rsplit('/', 1)[-1]


def parent_of(path: str) -> str:
    """Directory part; ``/data/a/x.txt`` → ``/data/a``."""
    head = norm(path).rsplit('/', 1)[0]
    return head or '/'


def is_volume(name: str) -> bool:
    """Split-archive part: `.z01`, `.part2.rar`, `7z.001`."""
    return bool(VOLUME.search(name or ''))


def is_archive(name: str) -> bool:
    return bool(ARCHIVE.search(name or ''))


def is_copy(name: str) -> bool:
    """Downloaded-twice suffix: `foo(1).zip`."""
    return bool(COPY.search(name or ''))


def top_of(path: str) -> str:
    parts = norm(path).strip('/').split('/')
    return parts[0] if parts and parts[0] else ''


def trusted_md5(item: dict) -> str | None:
    """Full 32-hex md5 or ``None``.

    Baidu masks the digest for some files ("9a3b4c5d***"); the masked value is
    not unique per content, so grouping on it merges unrelated files.  Anything
    that is not a complete hex digest is therefore untrusted.
    """
    value = (item.get('md5') or '').strip()
    return value.lower() if MD5_FULL.fullmatch(value) else None


def choose_keeper(paths: list[str]) -> str:
    """Which copy to keep: no `(1)` suffix, then the shortest path."""
    return sorted(paths, key=lambda p: (is_copy(name_of(p)), len(p), p))[0]


def index_by_md5(files: list[dict], skipped: collections.Counter | None = None) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for item in files:
        if item.get('isdir'):
            continue
        md5 = trusted_md5(item)
        if md5 is None:
            if skipped is not None:
                skipped['md5 不可信'] += 1
            continue
        groups[md5].append(item)
    return groups


def dup_plan(files: list[dict], min_size: int = 0) -> dict:
    """Duplicates that are safe to delete.

    A group only counts when every member shares the full md5, the size and the
    parent folder.  Cross-folder copies stay: the user may keep the same backup
    in two categories on purpose.  Archives, split volumes and ``(1)`` copies
    are skipped as well, and every skip is counted by reason.
    """
    skipped: collections.Counter = collections.Counter()
    groups = index_by_md5(files, skipped)
    plan = []
    for md5, group in groups.items():
        if len(group) < 2:
            continue
        if len({(item.get('size') or 0) for item in group}) > 1:
            skipped['大小不一致'] += 1
            continue
        if len({parent_of(item['path']) for item in group}) > 1:
            skipped['跨目录'] += 1
            continue
        names = [name_of(item['path']) for item in group]
        if any(is_volume(name) for name in names):
            skipped['压缩包/分卷'] += 1
            continue
        if any(is_copy(name) for name in names):
            skipped['(1) 副本'] += 1
            continue
        if any(is_archive(name) for name in names):
            skipped['压缩包/分卷'] += 1
            continue
        if (group[0].get('size') or 0) < min_size:
            continue
        keeper = choose_keeper([item['path'] for item in group])
        for item in group:
            if item['path'] == keeper:
                continue
            plan.append({'delete': item['path'], 'keep': keeper,
                         'size': item.get('size') or 0, 'md5': md5})
    plan.sort(key=lambda row: -row['size'])
    return {'plan': plan, 'skipped': dict(skipped),
            'bytes': sum(row['size'] for row in plan)}


def human(size: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(size) < 1024 or unit == 'GB':
            return ('%.1f %s' % (size, unit)) if unit != 'B' else ('%d B' % size)
        size /= 1024.0
    return '%.1f GB' % size


def paginate(fetch, path: str, limit: int = 1000) -> list[dict]:
    """Drive a ``(batch, has_more, cursor)`` fetcher until the tree ends."""
    out: list[dict] = []
    cursor = 0
    while True:
        batch, has_more, cursor = fetch(path, cursor, limit)
        out.extend(batch)
        if not has_more or not batch:
            return out


# ------------------------------------------------------------------- reporting
def write_mark(plan: dict, out: Path) -> None:
    """Human-readable plan; the header states that nothing was deleted."""
    lines = ['# 网盘可删清单（完整 md5 + 同大小 + 同目录）', '',
             '> 本命令只生成清单，不会删除任何文件。确认后用 '
             '`rm --plan <json> --yes` 执行。', '',
             '- 删除 %d 个，回收 %s' % (len(plan['plan']), human(plan['bytes'])),
             '- 跳过：' + ('，'.join('%s %d 组' % (k, v) for k, v in plan['skipped'].items())
                          or '无'), '',
             '| 删除 | 保留 | 大小 |', '|---|---|---:|']
    for row in plan['plan']:
        lines.append('| `%s` | `%s` | %s |'
                     % (row['delete'], row['keep'], human(row['size'])))
    if not plan['plan']:
        lines.append('| （无） | — | — |')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_plan(plan: dict, out: Path) -> None:
    """Machine-readable plan consumed by ``rm --plan``."""
    payload = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
               'plan': plan['plan'], 'skipped': plan['skipped'], 'bytes': plan['bytes']}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def collect_paths(args_paths: list[str], plan_path: str | None) -> list[dict]:
    """Rows to delete: explicit paths plus (optionally) a JSON plan."""
    rows: list[dict] = []
    seen = set()
    if plan_path:
        payload = json.loads(Path(plan_path).read_text(encoding='utf-8'))
        for row in payload.get('plan') or []:
            path = norm(row.get('delete') or '')
            if path and path not in seen:
                seen.add(path)
                rows.append({'path': path, 'raw': path, 'size': row.get('size') or 0,
                             'keep': row.get('keep') or ''})
    for raw in args_paths:
        path = norm(raw)
        if path in seen:
            continue
        seen.add(path)
        rows.append({'path': path, 'raw': raw, 'size': 0, 'keep': ''})
    return rows


def check_paths(rows: list[dict], limit: int, force: bool) -> None:
    """Refuse obviously dangerous input instead of failing halfway."""
    if not rows:
        raise SystemExit('没有要删除的路径（要么给路径，要么给 --plan）')
    for row in rows:
        path, raw = row['path'], row.get('raw') or row['path']
        if path == '/' or not path.strip('/'):
            raise SystemExit('拒绝删除根目录：%s' % raw)
        if raw.replace('\\', '/').endswith('/'):
            raise SystemExit('拒绝删除目录路径（只能是文件）：%s' % raw)
        if any(ch in path for ch in '*?') or '\\' in raw:
            raise SystemExit('拒绝删除含通配符/反斜杠的路径：%s' % raw)
    if len(rows) > limit and not force:
        raise SystemExit('一次要删除 %d 个，超过上限 %d；确认无误再加 --force'
                         % (len(rows), limit))


def print_plan(rows: list[dict]) -> None:
    print('待删除 %d 个文件（这是预览，未执行；加 --yes 才真正删除）：' % len(rows))
    for index, row in enumerate(rows, 1):
        keep = ('　（保留 %s）' % row['keep']) if row.get('keep') else ''
        size = ('　' + human(row['size'])) if row.get('size') else ''
        print('  [%d] %s%s%s' % (index, row['path'], size, keep))


def append_audit(entries: list[dict], log_dir: str) -> Path | None:
    """One JSONL line per path; the only long-lived record of a deletion."""
    if not entries:
        return None
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (time.strftime('%Y%m%d-%H%M%S') + '.jsonl')
    with path.open('a', encoding='utf-8') as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return path


def delete_and_report(client, rows: list[dict], log_dir: str = DEFAULT_LOG_DIR) -> int:
    """Delete, then report every path and write the audit log."""
    result = client.delete([row['path'] for row in rows])
    info = result.get('info') or []
    top = result.get('errno')
    entries, deleted, failed = [], 0, 0
    for index, row in enumerate(rows):
        code = 0
        if isinstance(info, list) and index < len(info) and isinstance(info[index], dict):
            code = info[index].get('errno') or 0
        elif top not in (None, 0):
            code = top
        if code == 0:
            deleted += 1
            print('  [ok]   %s' % row['path'])
        else:
            failed += 1
            print('  [失败 errno=%s] %s' % (code, row['path']))
        entries.append({'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'path': row['path'],
                        'size': row.get('size') or 0, 'keep': row.get('keep') or '',
                        'errno': code})
    log = append_audit(entries, log_dir)
    print('已删除 %d 个，失败 %d 个，共 %s'
          % (deleted, failed, human(sum(row.get('size') or 0 for row in rows[:deleted]))))
    if log is not None:
        print('审计日志：%s' % log)
    return 1 if failed else 0


# ---------------------------------------------------------------------- client
class Client:
    def __init__(self, access_token: str | None = None, retries: int = 6, pause: float = 1.2):
        self._token = access_token or token()
        self.retries = retries
        self.pause = pause

    def _request(self, url: str, params: dict, body: bytes | None = None) -> dict:
        params = dict(params)
        params['access_token'] = self._token
        parts = []
        for key, value in params.items():
            safe = '/' if key in ('dir', 'path') else ''
            parts.append('%s=%s' % (key, urllib.parse.quote(str(value), safe=safe)))
        req = urllib.request.Request('%s?%s' % (url, '&'.join(parts)),
                                     data=body, method='POST' if body else 'GET')
        if body:
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        delay = 6
        for _ in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                text = exc.read().decode('utf-8', 'ignore')
                if 'frequency' in text or exc.code == 429:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise SystemExit('netdisk %s: %s' % (exc.code, text[:200]))
        raise SystemExit('netdisk: 触发限频，稍后重试')

    def quota(self) -> dict:
        return self._request(QUOTA, {})

    def ls(self, path: str) -> list[dict]:
        data = self._request(API + 'file',
                             {'method': 'list', 'dir': path, 'start': 0, 'limit': 1000})
        return data.get('list') or []

    def _page(self, path: str, cursor: int, limit: int):
        data = self._request(API + 'multimedia',
                             {'method': 'listall', 'path': path, 'recursion': 1,
                              'start': cursor, 'limit': limit, 'order': 'name', 'desc': 0})
        batch = data.get('list') or []
        return batch, bool(data.get('has_more')), data.get('cursor', cursor + len(batch))

    def walk(self, path: str) -> list[dict]:
        return paginate(self._page, path)

    def delete(self, paths: list[str]) -> dict:
        body = urllib.parse.urlencode(
            {'filelist': json.dumps(paths, ensure_ascii=False)}).encode('utf-8')
        return self._request(API + 'file',
                             {'method': 'filemanager', 'opera': 'delete', 'async': 0}, body)

    def move(self, pairs: list[dict]) -> dict:
        body = urllib.parse.urlencode(
            {'filelist': json.dumps(pairs, ensure_ascii=False)}).encode('utf-8')
        return self._request(API + 'file',
                             {'method': 'filemanager', 'opera': 'move', 'async': 0}, body)

    def recycle_list(self, start: int = 0, limit: int = 100) -> dict:
        return self._request(RECYCLE, {'method': 'list', 'start': start, 'limit': limit})


# ------------------------------------------------------------------------ main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='百度网盘开放平台客户端')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('quota', help='容量')

    ls = sub.add_parser('ls', help='列目录')
    ls.add_argument('path', nargs='?', default='/')

    tree = sub.add_parser('tree', help='递归拉全树')
    tree.add_argument('path', nargs='?', default='/data')
    tree.add_argument('-o', '--out', default=DEFAULT_TREE)

    dups = sub.add_parser('dups', help='生成可删清单（不删除）')
    dups.add_argument('json', help='tree 输出的 json')
    dups.add_argument('-o', '--out', default=DEFAULT_MARK)
    dups.add_argument('--plan', default=DEFAULT_PLAN, help='JSON 清单，给 rm --plan 用')
    dups.add_argument('--min-size', type=int, default=0)

    rm = sub.add_parser('rm', help='删除（进回收站；先报后删）')
    rm.add_argument('paths', nargs='*')
    rm.add_argument('--plan', help='dups 生成的 JSON 清单')
    rm.add_argument('--yes', action='store_true', help='确认执行；不加只预览')
    rm.add_argument('--log-dir', default=DEFAULT_LOG_DIR, help='审计日志目录')
    rm.add_argument('--max', type=int, default=MAX_DELETE, help='一次最多删几个')
    rm.add_argument('--force', action='store_true', help='超过 --max 也执行')

    trash = sub.add_parser('trash', help='列回收站（只读）')
    trash.add_argument('--limit', type=int, default=50)

    mv = sub.add_parser('mv', help='移动')
    mv.add_argument('src')
    mv.add_argument('dest_dir')

    args = parser.parse_args(argv)

    if args.cmd == 'dups':
        rows = json.loads(Path(args.json).read_text(encoding='utf-8'))
        plan = dup_plan(rows, args.min_size)
        write_mark(plan, Path(args.out))
        write_plan(plan, Path(args.plan))
        print('可删 %d 个 / %s，跳过 %s' % (len(plan['plan']), human(plan['bytes']),
                                        plan['skipped'] or '无'))
        print('清单：%s（JSON：%s）——本命令不会删除任何文件' % (args.out, args.plan))
        return 0

    if args.cmd == 'rm':
        rows = collect_paths(args.paths, args.plan)
        if not args.yes:
            print_plan(rows)
            return 0
        check_paths(rows, args.max, args.force)
        return delete_and_report(Client(), rows, args.log_dir)

    client = Client()
    if args.cmd == 'quota':
        data = client.quota()
        print('已用 %s / 总 %s' % (human(data.get('used') or 0), human(data.get('total') or 0)))
    elif args.cmd == 'ls':
        rows = client.ls(args.path)
        for item in rows:
            kind = 'D' if item.get('isdir') else 'F'
            print(kind, '%13d' % (item.get('size') or 0), item.get('path'))
        print('— %d 项' % len(rows))
    elif args.cmd == 'tree':
        rows = client.walk(args.path)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        files = [row for row in rows if not row.get('isdir')]
        trusted = sum(1 for row in files if trusted_md5(row))
        print('%d 条目 / %d 文件 / %s -> %s'
              % (len(rows), len(files), human(sum(f.get('size') or 0 for f in files)), out))
        print('其中 %d 个文件 md5 完整可用，%d 个被打码（去重时会跳过）'
              % (trusted, len(files) - trusted))
    elif args.cmd == 'trash':
        data = client.recycle_list(0, args.limit)
        if data.get('errno'):
            print('回收站读取失败：errno=%s %s'
                  % (data.get('errno'), data.get('errmsg') or ''), file=sys.stderr)
            return 1
        rows = data.get('list') or []
        for item in rows:
            print('%s  %s' % (human(item.get('size') or 0), item.get('path')))
        print('— %d 项%s' % (len(rows), '（还有更多，调大 --limit）' if data.get('has_more') else ''))
    elif args.cmd == 'mv':
        result = client.move([{'path': args.src, 'dest': args.dest_dir,
                               'newname': name_of(args.src)}])
        failures = [entry for entry in (result.get('info') or [])
                    if isinstance(entry, dict) and entry.get('errno')]
        print('移动 %s → %s：errno=%s%s'
              % (args.src, args.dest_dir, result.get('errno'),
                 '' if not failures else '，失败 %d 个' % len(failures)))
        return 1 if failures or result.get('errno') else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
