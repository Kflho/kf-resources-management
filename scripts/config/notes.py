#!/usr/bin/env python3
"""Registered constraint documents the user expects the agent to follow.

The user keeps a few notes that define how work must be laid out and written
(project folders, file/data rules, note format).  They are machine-local, so
the registry lives in the skill's gitignored ``local.json`` and never reaches
the release repo:

    "notes": {
      "vault": "D:/data/online/software/common/obsidian",
      "docs": [
        {"name": "project note",
         "path": "D:/.../project note.md",
         "purpose": "项目文件夹与笔记模板",
         "tags": ["layout"],
         "sha256": "…16 hex…", "size": 4761,
         "mtime": "2026-10-21T01:21:42",
         "added_at": "…", "checked_at": "…"}
      ],
      "projects": [
        {"name": "sc 03 note", "path": "<vault>/…/sc 03/sc 03 note.md",
         "sha256": "…", "size": 0, "mtime": "…", "checked_at": "…"}
      ]
    }

Commands:
    python scripts/config/notes.py list
    python scripts/config/notes.py add "<path>" --purpose "…" [--name N] [--tags a,b]
                                    [--read startup|on-demand]
    python scripts/config/notes.py remove <name-or-path>
    python scripts/config/notes.py check [--refresh]
    python scripts/config/notes.py ack [name…]
    python scripts/config/notes.py show <name> [--head 40]
    python scripts/config/notes.py context

两类笔记、两种读法：

- **项目笔记**（vault 里的 ``<项目> note.md``，按 ``sk/sc/ko/pg`` + 编号自动发现，跳过
  ``.trash``，根取 ``notes.vault`` 或 ``bookmarks.vault``）：``check`` 把**改过的**列进
  ``must_read`` —— 启动时读它们，里面写着接下来的开发和修改工作；
- **参考文档**（``notes.docs``：``project note`` 模板、``data note``、``note note`` 等）：
  默认按需读，改过只列进 ``deferred``，**启动时不读**，任务真用到再 ``show``。

**启动只读当前项目**：``check`` 先看当前工作目录落在哪个已登记项目里（``projects.items``
的 ``path`` 取最长前缀），只把**这个项目**的笔记改动放进 ``must_read``；其他项目的改动
列进 ``other_projects``（只有名字），**不读** —— 等真在那个项目里干活再读。
``--project <名字>`` 显式指定，``--all`` 退回旧行为（所有改过的项目笔记都进 ``must_read``）。

``context`` prints a compact list to hand to an agent at session start, so the
user does not have to paste the same constraints again.  ``check`` compares the
recorded fingerprint with the file on disk.

``ack`` 只更新内容摘要、不读内容：不带名字时只 ack 按需读的参考文档（项目笔记照旧报），
读完项目笔记后用 ``ack "<项目> note"`` 收尾。``check --refresh`` 是「读过了，全部更新」。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402

DEFAULT_HEAD = 40
READ_MODES = ('startup', 'on-demand')
DEFAULT_READ = 'on-demand'
# 项目笔记的命名：`sk 02 note.md` / `sc 03 note.md` / `ko 01` / `pg 04`
PROJECT_NOTE = re.compile(r'^(?:sk|sc|ko|pg)\s*\d+', re.IGNORECASE)


def _read_mode(item: dict) -> str:
    mode = str(item.get('read') or DEFAULT_READ).strip().lower()
    return mode if mode in READ_MODES else DEFAULT_READ


def docs() -> list[dict]:
    data = cfg.load().get('notes') or {}
    return [item for item in (data.get('docs') or [])
            if isinstance(item, dict) and item.get('path')]


def _save(notes: dict) -> None:
    """只改传入的键（docs / projects），别把 notes 里其它配置抹掉。"""
    full = cfg.load()
    merged = dict(full.get('notes') or {})
    merged.update(notes)
    full['notes'] = merged
    path = cfg.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def _vault_root() -> Path | None:
    """vault 根：`obsidian.vault` → `notes.vault` → `bookmarks.vault`（与 registry 同一套）。"""
    data = cfg.load()
    notes = data.get('notes') or {}
    raw = ((data.get('obsidian') or {}).get('vault')
           or notes.get('vault')
           or (data.get('bookmarks') or {}).get('vault'))
    if not raw:
        return None
    root = Path(str(raw)).expanduser()
    return root if root.is_dir() else None


def project_docs() -> list[dict]:
    """vault 里的项目笔记：`<项目> note.md`，启动时改过就要读的那批。

    两种来源合在一起（按路径去重）：
    1. 项目总账里登记的 `note`（vault 相对路径，`registry.py` 那套）；
    2. 按名字认：`sk` / `sc` / `ko` / `pg` + 编号，跳过 `.trash`。
       这样换机器、新建项目都不用改配置。
    """
    root = _vault_root()
    if root is None:
        return []
    found: dict[str, dict] = {}
    items = (cfg.load().get('projects') or {}).get('items') or []
    for item in items:
        note = str(item.get('note') or '').strip()
        if not note.lower().endswith('.md'):
            continue  # note 也可能是一句说明，不是路径
        path = (root / note).resolve()
        if path.is_file():
            found[str(path).lower()] = {'name': path.stem, 'path': str(path)}
    for path in root.rglob('* note.md'):
        if '.trash' in path.parts or not PROJECT_NOTE.match(path.name):
            continue
        found[str(path.resolve()).lower()] = {'name': path.stem,
                                              'path': str(path.resolve())}
    return sorted(found.values(), key=lambda item: item['name'].lower())


def _project_cache() -> list[dict]:
    data = cfg.load().get('notes') or {}
    return [item for item in (data.get('projects') or [])
            if isinstance(item, dict) and item.get('name') and item.get('path')]


def _registry_items() -> list[dict]:
    return (cfg.load().get('projects') or {}).get('items') or []


def _note_key(item: dict) -> str:
    """项目在 vault 里的 `<项目> note` 名字（项目笔记的键）。"""
    note = str(item.get('note') or '').strip()
    return Path(note).stem if note.lower().endswith('.md') else ''


def current_project(cwd: Path | None = None) -> dict | None:
    """当前工作目录落在哪个已登记项目里（按最短路径算，取最长的前缀匹配）。

    启动读取只认这个项目：别的项目的笔记改动只报名字，等真在那个项目里干活再读。
    目录不在任何项目里就返回 ``None``（那就一份项目笔记都不读）。
    """
    try:
        here = Path(cwd or Path.cwd()).resolve()
    except OSError:
        return None
    best: tuple[Path, dict] | None = None
    for item in _registry_items():
        raw = str(item.get('path') or '').strip()
        if not raw:
            continue
        try:
            root = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if here != root and root not in here.parents:
            continue
        if best is None or len(str(root)) > len(str(best[0])):
            best = (root, item)
    if best is None:
        return None
    root, item = best
    return {'project': str(item.get('name') or root.name), 'root': str(root),
            'note': _note_key(item)}


def find_project(key: str) -> dict | None:
    """按项目名或项目笔记名找已登记项目。"""
    needle = key.strip().lower()
    for item in _registry_items():
        name = str(item.get('name') or '').strip().lower()
        note = _note_key(item).lower()
        if needle and needle in (name, note):
            return {'project': str(item.get('name') or ''), 'root': str(item.get('path') or ''),
                    'note': _note_key(item)}
    return None


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec='seconds')


def _fingerprint(path: Path) -> dict:
    blob = path.read_bytes()
    stat = path.stat()
    return {
        'sha256': hashlib.sha256(blob).hexdigest()[:16],
        'size': stat.st_size,
        'mtime': _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec='seconds'),
    }


def _match(key: str, items: list[dict] | None = None) -> dict | None:
    needle = key.strip().lower()
    for item in (docs() if items is None else items):
        names = {str(item.get('name', '')).lower(),
                 Path(item['path']).stem.lower(),
                 str(item['path']).lower()}
        if needle in names:
            return item
    return None


def _match_project(key: str, entries: list[dict]) -> dict | None:
    needle = key.strip().lower()
    for item in entries:
        names = {str(item.get('name', '')).lower(),
                 Path(item['path']).stem.lower(),
                 str(item['path']).lower()}
        if needle in names:
            return item
    return None


def _tags(value: str) -> list[str]:
    return [part.strip() for part in value.split(',') if part.strip()]


def add(path: str, purpose: str = '', name: str = '',
        tags: str = '', read: str = '') -> dict:
    target = Path(path).expanduser()
    if not target.is_file():
        raise ValueError(f'不是文件：{target}')
    target = target.resolve()
    items = docs()
    label = (name or target.stem).strip()
    item = next((d for d in items if d.get('name') == label), None)
    if item is None:
        item = next((d for d in items
                     if Path(d['path']).resolve() == target), None)
        if item is not None:
            label = item['name']
    created = item is None
    if created:
        item = {'name': label, 'path': str(target), 'added_at': _now()}
        items.append(item)
    item['path'] = str(target)
    if purpose:
        item['purpose'] = purpose
    elif 'purpose' not in item:
        item['purpose'] = ''
    if tags:
        item['tags'] = _tags(tags)
    if read:
        mode = read.strip().lower()
        if mode not in READ_MODES:
            raise ValueError(f'read 只能是 {" / ".join(READ_MODES)}：{read}')
        item['read'] = mode
    item.update(_fingerprint(target))
    item['checked_at'] = _now()
    _save({'docs': items})
    return {'name': label, 'created': created, 'path': str(target),
            'purpose': item['purpose'], 'read': _read_mode(item)}


def remove(key: str) -> dict:
    items = docs()
    item = _match(key, items)
    if item is None:
        raise ValueError(f'没有登记：{key}')
    index = next(i for i, entry in enumerate(items) if entry is item)
    del items[index]
    _save({'docs': items})
    return {'name': item['name'], 'removed': True, 'count': len(items)}


def check(refresh: bool = False, project: str | None = None,
          all_projects: bool = False) -> dict:
    """改过的文档分两堆：当前项目/启动必读进 ``must_read``，其它项目只报名字。

    ``project`` 显式指定项目；不给就按当前工作目录认；``all_projects=True`` 退回
    「所有改过的项目笔记都算启动必读」的旧口径。
    """
    items = docs()
    rows = []
    for item in items:
        path = Path(item['path'])
        exists = path.is_file()
        row = {'name': item['name'], 'path': str(path), 'exists': exists,
               'read': _read_mode(item)}
        if exists:
            stamp = _fingerprint(path)
            row['changed'] = stamp['sha256'] != item.get('sha256')
            row.update({'size': stamp['size'], 'mtime': stamp['mtime']})
            if refresh:
                item.update(stamp)
                item['checked_at'] = _now()
        else:
            row['changed'] = None
        rows.append(row)

    cache = {str(item['name']).lower(): item for item in _project_cache()}
    projects, fresh = [], []
    for entry in project_docs():
        path = Path(entry['path'])
        try:
            stamp = _fingerprint(path)
        except OSError:
            continue
        known = cache.get(str(entry['name']).lower())
        changed = known is None or known.get('sha256') != stamp['sha256']
        row = {'name': entry['name'], 'path': str(path), 'exists': True,
               'changed': changed}
        row.update({'size': stamp['size'], 'mtime': stamp['mtime']})
        projects.append(row)
        entry.update(stamp)
        entry['checked_at'] = _now() if refresh else (known or {}).get(
            'checked_at', '')
        fresh.append(entry)

    if refresh:
        _save({'docs': items, 'projects': fresh})

    scope = {'project': '', 'root': '', 'note': '', 'source': 'none'}
    if not all_projects:
        if project:
            found = find_project(project)
            if found is None:
                raise ValueError(f'项目总账里没有这个项目：{project}')
            scope = {**found, 'source': 'arg'}
        else:
            found = current_project()
            if found:
                scope = {**found, 'source': 'cwd'}

    must_read = [row['name'] for row in rows
                 if row['changed'] and row['read'] == 'startup']
    in_scope, other_projects = [], []
    for row in projects:
        if not row['changed']:
            continue
        scoped_in = (all_projects
                     or (bool(scope['note'])
                         and row['name'].strip().lower() == scope['note'].strip().lower()))
        if scoped_in:
            must_read.append(row['name'])
            in_scope.append(row['name'])
        else:
            other_projects.append(row['name'])
    return {
        'scope': scope,
        'docs': rows,
        'projects': projects,
        'missing': [r['name'] for r in rows if not r['exists']],
        'changed': [r['name'] for r in rows if r['changed']],
        'must_read': must_read,
        'in_scope': in_scope,
        'other_projects': other_projects,
        'deferred': [r['name'] for r in rows
                     if r['changed'] and r['read'] != 'startup'],
    }


def ack(keys: list[str] | None = None) -> dict:
    """记下当前内容摘要但不读内容。

    - 不给名字：只 ack 按需读的参考文档；**项目笔记不碰**（启动该读的照旧报）；
    - 给名字：a 哪个记哪个（读完项目笔记后用它收尾）。
    """
    items = docs()
    projects = {str(item['name']).lower(): item for item in _project_cache()}
    scanned = project_docs()
    targets: list[tuple[str, dict]] = []
    if keys:
        for key in keys:
            item = _match(key, items)
            if item is not None:
                if not any(kind == 'doc' and seen is item for kind, seen in targets):
                    targets.append(('doc', item))
                continue
            entry = _match_project(key, scanned)
            if entry is None:
                raise ValueError(f'没有登记或这个 vault 里没有：{key}')
            if not any(kind == 'project' and seen['name'] == entry['name']
                       for kind, seen in targets):
                targets.append(('project', entry))
    else:
        targets = [('doc', item) for item in items
                   if _read_mode(item) != 'startup']

    acked, skipped = [], []
    for kind, item in targets:
        path = Path(item['path'])
        if not path.is_file():
            skipped.append({'name': item['name'], 'reason': '文件不存在'})
            continue
        stamp = _fingerprint(path)
        if kind == 'doc':
            item.update(stamp)
            item['checked_at'] = _now()
            item['acked_at'] = _now()
        else:
            projects[str(item['name']).lower()] = {
                **item, **stamp, 'checked_at': _now(), 'acked_at': _now(),
            }
        acked.append(item['name'])
    _save({'docs': items, 'projects': list(projects.values())})
    return {
        'acked': acked,
        'skipped': skipped,
        'skipped_startup': [item['name'] for item in items
                            if _read_mode(item) == 'startup'],
        'note': '只记内容摘要、没读内容；用到时 show 读到的仍是当前版本',
    }


def context() -> str:
    items = docs()
    projects = project_docs()
    lines = ['## 约束文档（项目笔记改了要读；参考文档按需读）']
    if projects:
        lines.append(f"- **项目笔记**（`<项目> note.md`，自动认 sk/sc/ko/pg）："
                     f"vault 里 {len(projects)} 份；`check` 的 `must_read` 列出改过的那几份")
    for item in items:
        path = Path(item['path'])
        state = '' if path.is_file() else '（文件不存在）'
        purpose = item.get('purpose') or '未写用途'
        mode = '启动必读' if _read_mode(item) == 'startup' else '按需读'
        lines.append(f"- **{item['name']}**（{mode}）：{purpose}｜`{path}`{state}")
    if not items and not projects:
        return '未登记约束文档，vault 里也没认出项目笔记：python scripts/config/notes.py add <path> --purpose "…"'
    return '\n'.join(lines)


def show(key: str, head: int = DEFAULT_HEAD) -> str:
    item = _match(key)
    if item is None:
        item = _match_project(key, project_docs())
    if item is None:
        raise ValueError(f'没有登记：{key}')
    path = Path(item['path'])
    if not path.is_file():
        raise ValueError(f'文件不存在：{path}')
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    body = '\n'.join(lines[:head])
    tail = f'\n…（共 {len(lines)} 行）' if len(lines) > head else ''
    purpose = item.get('purpose') or '项目笔记'
    return f"# {item['name']}：{purpose}\n{path}\n\n{body}{tail}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list')
    add_p = sub.add_parser('add')
    add_p.add_argument('path')
    add_p.add_argument('--purpose', default='')
    add_p.add_argument('--name', default='')
    add_p.add_argument('--tags', default='')
    add_p.add_argument('--read', choices=READ_MODES, default='')
    rm_p = sub.add_parser('remove')
    rm_p.add_argument('key')
    check_p = sub.add_parser('check')
    check_p.add_argument('--refresh', action='store_true')
    check_p.add_argument('--project', default='',
                         help='指定项目（项目名或项目笔记名）；不给就按当前目录认')
    check_p.add_argument('--all', action='store_true', dest='all_projects',
                         help='旧口径：所有改过的项目笔记都算启动必读')
    ack_p = sub.add_parser('ack')
    ack_p.add_argument('keys', nargs='*')
    show_p = sub.add_parser('show')
    show_p.add_argument('key')
    show_p.add_argument('--head', type=int, default=DEFAULT_HEAD)
    sub.add_parser('context')

    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            print(json.dumps({'docs': docs()}, ensure_ascii=False, indent=2))
        elif args.command == 'add':
            print(json.dumps(add(args.path, args.purpose, args.name,
                                 args.tags, args.read),
                             ensure_ascii=False, indent=2))
        elif args.command == 'remove':
            print(json.dumps(remove(args.key), ensure_ascii=False, indent=2))
        elif args.command == 'check':
            result = check(args.refresh, args.project or None, args.all_projects)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result['missing'] else 0
        elif args.command == 'ack':
            print(json.dumps(ack(args.keys or None), ensure_ascii=False, indent=2))
        elif args.command == 'show':
            print(show(args.key, args.head))
        else:
            print(context())
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
