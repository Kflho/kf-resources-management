#!/usr/bin/env python3
"""Check ``D:\\data`` against the user's data layout rules.

The rules come from the Obsidian notes the user registered with ``notes.py``
(``data note.md`` for the folder template, ``software list.md`` for the
software catalogue), not from whatever the disk happens to look like::

    data/online   frequently synced data (currently only the Obsidian vault)
    data/local    software data that is not synced (source, chat stores, saves)
    data/local/software/<category>/   default home for anything unclear
    netdisk /softwares/{common,profession,recreation}/   installers, archives

A note (``.md``) belongs to the vault, a config file to git
``kf-personal-service/settings/``, a PDF to the netdisk.  ``check`` reports
files that sit in the wrong layer; it never moves or deletes anything.

Layout levels come from ``software note.md`` (``recreation`` lives *inside*
``software``) and ``data note.md`` (lifting a level for emphasis is allowed but
the user decides).  The split that matters is *resource* vs *software data*:
video files and game bodies go to a sibling ``recreation``; subtitle projects,
player configs, question banks and game saves stay under
``software/recreation/<app>``.  Do not restructure the local tree on your own:
both arrangements are "correct", the choice is the user's.

Commands:
    python scripts/data/layout.py check [--root D:/data] [--limit 40]
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

MEDIA = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.psd', '.clip', '.abr',
         '.mp4', '.mkv', '.mov', '.avi', '.flv', '.mp3', '.flac', '.wav', '.heic'}
CONFIG = {'.ini', '.dc5', '.ssf', '.cfg', '.conf', '.reg', '.toml', '.properties', '.xml'}
NOTE = {'.md'}
DOC = {'.pdf'}

# folder names whose content is software configuration (git settings/), not data
SETTINGS_DIRS = {'vivaldi', 'digital_clock', 'fence', 'potplayer', 'sogou', '搜狗输入法'}

# subtrees exempt from the software rules: code projects own their assets, chat
# stores own their media (voice messages, stickers)
EXEMPT_SOFTWARE = {'software/programming', 'software/communication'}

# project documents keep their names inside a repo, they are not personal notes
DOC_NAMES = {'readme.md', 'claude.md', 'agents.md', 'changelog.md', 'skill.md',
             'memory.md', 'license.md', 'history.md'}

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.venv',
             'temp', '.trash', '_pkg'}


def norm(rel: str) -> str:
    return '/'.join(part for part in str(rel).replace('\\', '/').split('/') if part)


def _under(rel: str, prefix: str) -> bool:
    return rel == prefix or rel.startswith(prefix + '/')


def classify(rel: str) -> tuple[str, str]:
    """Return ``(channel, reason)`` for one relative path.

    Channels: ``obsidian``, ``git-settings``, ``netdisk``, ``local``,
    ``online``.
    """
    rel = norm(rel)
    suffix = Path(rel).suffix.lower()
    first, _, rest = rel.partition('/')

    if first == 'online' and _under(rest, 'software/common/obsidian'):
        return 'obsidian', 'Obsidian 库正文与插件存储走 remotely-save + locally-save'
    if suffix in NOTE:
        return 'obsidian', '笔记（.md）只在 Obsidian 库'
    if _under(rest, 'software'):
        parts = rest.split('/')
        if SETTINGS_DIRS & set(parts):
            return 'git-settings', '软件配置进 git settings/'
        if _under(rest, 'software/common') and suffix in CONFIG:
            return 'git-settings', '软件配置进 git settings/'
    if suffix in DOC:
        return 'netdisk', 'PDF 文档归网盘'
    if first == 'online':
        return 'online', '要频繁同步的数据'
    if first == 'local':
        return 'local', '不同步的数据（默认层）'
    return 'unknown', '不在 data 两棵树里'


def violations(files: list[str], exempt: tuple[str, ...] = ()) -> list[dict]:
    """Report files that sit in the wrong layer.

    ``exempt`` holds relative directories (git working trees) that keep their
    own layout: a project owns its README, its images and its config files.
    """
    rows = []
    for rel in files:
        rel = norm(rel)
        first, _, rest = rel.partition('/')
        suffix = Path(rel).suffix.lower()
        channel, _reason = classify(rel)
        name = rel.rsplit('/', 1)[-1].lower()
        exempt_here = (name in DOC_NAMES
                       or any(_under(rest, d) or rest == d for d in EXEMPT_SOFTWARE)
                       or any(_under(rel, root) for root in exempt))

        if channel == 'obsidian':
            in_vault = first == 'online' and _under(rest, 'software/common/obsidian')
            if not in_vault and not exempt_here:
                rows.append({'path': rel, 'rule': 'note-outside-vault',
                             'hint': '笔记只在 Obsidian 库，data 树不放 .md'})
            continue
        if channel == 'git-settings':
            if not exempt_here:
                rows.append({'path': rel, 'rule': 'config-in-data',
                             'hint': '配置应在 git settings/，data 树只留数据'})
            continue
    return rows


def scan(root: Path) -> list[str]:
    """Relative paths of every file under ``root`` (skipping build/cache dirs)."""
    root = Path(root)
    out = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            rel = str((Path(base) / name).relative_to(root)).replace('\\', '/')
            out.append(rel)
    return out


def repo_roots(root: Path) -> tuple[str, ...]:
    """Relative paths of directories that hold a git working tree."""
    root = Path(root)
    roots = []
    for base, dirs, _names in os.walk(root):
        if '.git' in dirs:
            roots.append(str(Path(base).relative_to(root)).replace('\\', '/'))
            dirs[:] = [d for d in dirs if d != '.git']
    return tuple(roots)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='data 目录分层检查')
    sub = parser.add_subparsers(dest='cmd', required=True)
    check = sub.add_parser('check', help='按规则报告放错层的文件')
    check.add_argument('--root', default='D:/data')
    check.add_argument('--limit', type=int, default=40)
    check.add_argument('--no-repo-exempt', action='store_true',
                       help='连 git 工作树里的文件也一起检查')
    args = parser.parse_args(argv)

    root = Path(args.root)
    files = scan(root)
    exempt = () if args.no_repo_exempt else repo_roots(root)
    rows = violations(files, exempt)
    if not rows:
        print('%s：%d 个文件，没有违反分层规则' % (root, len(files)))
        return 0

    counted = collections.Counter(row['rule'] for row in rows)
    print('%s：%d 个文件，%d 条违规（跳过 %d 个 git 工作树）'
          % (root, len(files), len(rows), len(exempt)))
    for rule, count in counted.most_common():
        print('  %-20s %d' % (rule, count))
    for row in rows[:args.limit]:
        print('  %-20s %s' % (row['rule'], row['path']))
    return 1


if __name__ == '__main__':
    sys.exit(main())
