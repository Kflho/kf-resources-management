#!/usr/bin/env python3
"""Manage the sync assignment list (文件同步.md): git vs Baidu Netdisk.

The note holds the human plan; this tool makes the two tables machine-checkable
so filling them in stays safe:

- **不重样**: a path may appear in one table only, and a parent/child of a path
  already assigned to the *other* channel is an overlap, not a second entry;
- **能落地**: every entry is resolved against a configured root and checked for
  existence and size, so a plan that no longer matches the disk shows up;
- **增量补充**: ``add`` appends one entry (dry-run unless ``--apply``), and
  ``suggest`` proposes a channel for a directory's children by size.

Config in ``local.json``::

    "syncmap": {
      "note": "<vault>/.../文件同步.md",
      "git_root": "<root the git table is relative to>",
      "netdisk_root": "<root the netdisk table is relative to, e.g. a drive>",
      "max_git_mb": 50
    }

Commands:
    python scripts/apps/syncmap.py list
    python scripts/apps/syncmap.py check [--json]
    python scripts/apps/syncmap.py add git <relative-path> [--apply]
    python scripts/apps/syncmap.py add netdisk <relative-path> [--apply]
    python scripts/apps/syncmap.py suggest <absolute-dir>

Exit codes: 0 clean, 1 problems found (overlap / unresolved / missing).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402

SECTIONS = {'git': 'git 同步表', 'netdisk': '百度网盘同步表'}
CHANNELS = tuple(SECTIONS)
ITEM = re.compile(r'^(\s*)\d+\.\s+(.+?)\s*$')


def note_path(explicit: str = '') -> Path:
    raw = explicit or str(cfg.get('syncmap', 'note', '') or '')
    if not raw:
        raise ValueError('syncmap.note not configured in local.json')
    return Path(raw).expanduser()


def _root(channel: str) -> Path:
    raw = str(cfg.get('syncmap', f'{channel}_root', '') or '')
    if not raw:
        raise ValueError(f'syncmap.{channel}_root not configured')
    return Path(raw).expanduser()


def parse(path: Path) -> dict:
    """Flatten each channel's nested numbered list into relative paths."""
    text = path.read_text(encoding='utf-8')
    current, stack, trees = None, [], {'git': [], 'netdisk': []}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            title = stripped.lstrip('#').strip()
            current = next((ch for ch, name in SECTIONS.items()
                            if name in title), None)
            stack = []
            continue
        if current is None:
            continue
        match = ITEM.match(line)
        if not match:
            if stripped and not stripped.startswith(('>', '|', '-')):
                stack = []
            continue
        depth = len(match.group(1).expandtabs(4)) // 4
        stack = stack[:depth] + [match.group(2).strip()]
        trees[current].append('/'.join(stack))
    result = {'note': str(path)}
    for channel in CHANNELS:
        all_paths = set(trees[channel])
        # 父目录只是层级，真正分配的是叶子：只保留没有子项的那些
        leaves = sorted(item for item in all_paths
                        if not any(other != item
                                   and other.startswith(item.rstrip('/') + '/')
                                   for other in all_paths))
        result[channel] = leaves
    return result


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def covering_entry(target: Path, trees: dict) -> tuple[str, str] | None:
    """Which (channel, entry) already covers this absolute path, if any."""
    for channel in CHANNELS:
        try:
            rel = target.resolve().relative_to(_root(channel).resolve())
        except (ValueError, OSError):
            continue
        rel = rel.as_posix()
        for entry in trees[channel]:
            if _under(rel, entry):
                return channel, entry
    return None


def overlap_report(trees: dict) -> list[dict]:
    problems = []
    for left in CHANNELS:
        other = 'netdisk' if left == 'git' else 'git'
        for path in trees[left]:
            for rival in trees[other]:
                if path == rival:
                    problems.append({'kind': 'duplicate', 'path': path,
                                     'channels': [left, other]})
                elif _under(path, rival) or _under(rival, path):
                    problems.append({'kind': 'nested', 'path': path,
                                     'other': rival,
                                     'channels': [left, other]})
    return problems


def measure(path: Path) -> dict:
    if not path.exists():
        return {'exists': False, 'mb': 0.0, 'files': 0}
    if path.is_file():
        size = path.stat().st_size
        return {'exists': True, 'mb': round(size / 1024 ** 2, 1), 'files': 1}
    total, count = 0, 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if name not in ('.git',)]
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
                count += 1
            except OSError:
                continue
    return {'exists': True, 'mb': round(total / 1024 ** 2, 1), 'files': count}


def check(trees: dict) -> dict:
    rows = []
    for channel in CHANNELS:
        root = _root(channel)
        for path in trees[channel]:
            target = root / path
            info = measure(target)
            rows.append({'channel': channel, 'path': path,
                         'root': str(root), 'resolved': str(target), **info})
    problems = overlap_report(trees)
    unresolved = [row for row in rows if not row['exists']]
    return {'note': trees['note'], 'rows': rows, 'problems': problems,
            'unresolved': len(unresolved),
            'clean': not problems and not unresolved}


def add(channel: str, path: str, apply: bool) -> dict:
    trees = parse(note_path())
    path = path.strip('/')
    if channel not in CHANNELS:
        raise ValueError(f'channel must be one of {CHANNELS}')
    if path in trees[channel]:
        return {'ok': True, 'changed': False, 'reason': 'already listed'}
    rival = 'netdisk' if channel == 'git' else 'git'
    for other in trees[rival]:
        if _under(path, other) or _under(other, path):
            raise ValueError(f'不重样：{path} 与 {rival} 表里的 {other} 重叠')
    target = _root(channel) / path
    covered = covering_entry(target, trees)
    if covered:
        where, entry = covered
        if where == channel:
            return {'ok': True, 'changed': False,
                    'reason': f'已被本表 {entry} 覆盖，不必重复列'}
        raise ValueError(f'不重样：{path} 已被 {where} 表的 {entry} 覆盖')
    info = measure(target)
    if not info['exists']:
        raise ValueError(f'路径不存在：{target}')
    entry = {'channel': channel, 'path': path, 'apply': apply, **info}
    if not apply:
        return {**entry, 'changed': False, 'dry_run': True}
    note = note_path()
    stamp = _dt.datetime.now().strftime('%Y%m%d%H%M%S')
    backup = note.parent / f'.{note.stem}.bak-{stamp}{note.suffix}'
    shutil.copy2(note, backup)
    text = note.read_text(encoding='utf-8')
    marker = f'## {SECTIONS[channel]}'
    index = text.find(marker)
    if index < 0:
        raise ValueError(f'找不到小节：{marker}')
    tail = text[index:]
    # append after the section's last numbered line
    lines = tail.split('\n')
    last = 0
    for i, line in enumerate(lines[1:], 1):
        if line.strip().startswith('#') and not line.strip().startswith('#'):
            break
        if i > 0 and line.strip().startswith('#'):
            break
        if ITEM.match(line):
            last = i
    insert_at = max(last, 1)
    lines.insert(insert_at + 1, f'1. {path}')
    note.write_text(text[:index] + '\n'.join(lines), encoding='utf-8')
    return {**entry, 'changed': True, 'backup': str(backup)}


def suggest(directory: Path, limit: int = 20) -> dict:
    trees = parse(note_path())
    max_git = float(cfg.get('syncmap', 'max_git_mb', 50) or 50)
    rows = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        info = measure(child)
        covered = covering_entry(child, trees)
        channel = 'netdisk' if info['mb'] >= max_git else 'git'
        row = {'name': child.name, 'mb': info['mb'], 'files': info['files'],
               'suggest': covered[0] if covered else channel,
               'covered_by': f'{covered[0]}:{covered[1]}' if covered else ''}
        rows.append(row)
    rows.sort(key=lambda row: row['mb'], reverse=True)
    return {'dir': str(directory), 'max_git_mb': max_git,
            'children': rows[:limit]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Manage the git / netdisk sync assignment list.')
    parser.add_argument('--note', default='',
                        help='override syncmap.note')
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('check')
    add_p = sub.add_parser('add')
    add_p.add_argument('channel', choices=CHANNELS)
    add_p.add_argument('path')
    add_p.add_argument('--apply', action='store_true')
    sug_p = sub.add_parser('suggest')
    sug_p.add_argument('dir')
    sug_p.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)

    try:
        if args.note:
            os.environ['KF_SYNC_SYNC_NOTE'] = args.note
        if args.command == 'list':
            trees = parse(note_path(args.note))
            if args.json:
                print(json.dumps(trees, ensure_ascii=False, indent=2))
            else:
                for channel in CHANNELS:
                    print(f'[{channel}] {len(trees[channel])} entrie(s)')
                    for path in trees[channel]:
                        print(f'  {path}')
            return 0
        if args.command == 'check':
            report = check(parse(note_path(args.note)))
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                for row in report['rows']:
                    flag = 'ok ' if row['exists'] else 'MISS'
                    print(f"{flag} {row['channel']:7s} {row['mb']:9.1f} MB "
                          f"{row['files']:6d}  {row['path']}")
                for item in report['problems']:
                    print(f"OVERLAP {item['kind']}: {item['path']}"
                          f" <-> {item.get('other', '')}")
                print(f"\n{len(report['rows'])} entries, "
                      f"{report['unresolved']} unresolved, "
                      f"{len(report['problems'])} overlap(s)")
            return 0 if report['clean'] else 1
        if args.command == 'add':
            print(json.dumps(add(args.channel, args.path, args.apply),
                             ensure_ascii=False, indent=2))
            return 0
        result = suggest(Path(args.dir).expanduser(), args.limit)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"dir: {result['dir']}  (git ≤ {result['max_git_mb']} MB)")
            for row in result['children']:
                mark = (f"covered({row['covered_by']})"
                        if row['covered_by'] else '')
                print(f"  {row['suggest']:7s} {row['mb']:9.1f} MB "
                      f"{row['files']:6d}  {mark:24s} {row['name']}")
        return 0
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
