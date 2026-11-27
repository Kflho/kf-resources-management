#!/usr/bin/env python3
"""Skill ledger: what every machine must have installed in the harness.

The canonical list is ``scripts/config/skill-list.json`` (tracked, versioned).
Machine details live in ``local.json``::

    "skills": {
      "harness_dirs": ["~/.dsh/skills"],   # override the default list
      "disabled": ["scipilot-figure-skill"]  # installed elsewhere / not wanted
    }

Entries with ``project`` are local repos (``projects.items``) and install as a
junction to the dev tree, so edits take effect without re-syncing.  When the
skill sits in a subdirectory of the repo, add ``subdir`` (e.g.
``"subdir": "proposal-exports"``) and both checks and install use that path.
Entries without ``project`` are third-party copies: the ledger only checks that
they are there and says how to install them.

Commands:
    python scripts/config/skills.py list [--json]
    python scripts/config/skills.py check [--json]
    python scripts/config/skills.py install <name>... [--force]
    python scripts/config/skills.py scan
    python scripts/config/skills.py dirs

``check`` exits 1 when a required skill is missing, misnamed, or a junction
pointing somewhere else; optional entries are reported but never fail it.
GitHub is behind the VPN task table, so pulls use
``python scripts/vpn/tasks.py run --vpn -- python scripts/projects/sync.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402
from projects import registry as reg  # noqa: E402

LIST = Path(__file__).resolve().parent / 'skill-list.json'
BAD = {'missing', 'no-skill-md', 'wrong-name', 'link-elsewhere',
       'harness-missing'}


def ledger() -> dict:
    data = json.loads(LIST.read_text(encoding='utf-8-sig'))
    local = (cfg.load().get('skills') or {})
    dirs = local.get('harness_dirs') or data.get('harness_dirs') or []
    disabled = set(local.get('disabled') or [])
    entries = [item for item in data.get('skills') or []
               if isinstance(item, dict) and item.get('name')
               and item['name'] not in disabled]
    return {'dirs': [Path(os.path.expanduser(str(item))) for item in dirs],
            'skills': entries}


def frontmatter_name(directory: Path) -> str:
    path = directory / 'SKILL.md'
    if not path.is_file():
        return ''
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    match = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
    if not match:
        return ''
    for line in match.group(1).splitlines():
        if line.lower().startswith('name:'):
            return line.split(':', 1)[1].strip().strip('"\'')
    return ''


def project_dir(name: str) -> Path | None:
    for item in reg.registry()['items']:
        if item.get('name') == name:
            path = reg.item_git_path(item)
            return path if path.is_dir() else None
    return None


def project_source(entry: dict) -> Path | None:
    """Skill directory for a ledger entry: repo root plus optional ``subdir``."""
    root = project_dir(str(entry.get('project') or ''))
    if not root:
        return None
    subdir = str(entry.get('subdir') or '').strip()
    if not subdir:
        return root
    target = root / subdir
    return target if target.is_dir() else None


def _is_link(path: Path) -> bool:
    try:
        return os.path.realpath(path) != os.path.normpath(str(path))
    except OSError:
        return False


def inspect(entry: dict, harness: Path) -> dict:
    target = harness / entry['name']
    row = {'skill': entry['name'], 'origin': entry.get('origin', ''),
           'optional': bool(entry.get('optional')),
           'harness': str(harness), 'target': str(target)}
    if not harness.is_dir():
        row['status'] = 'harness-missing'
        return row
    if not target.exists():
        row['status'] = 'missing'
        return row
    row['name_in_skill'] = frontmatter_name(target)
    if not row['name_in_skill']:
        row['status'] = 'no-skill-md'
    elif row['name_in_skill'] != entry['name']:
        row['status'] = 'wrong-name'
    elif entry.get('project'):
        source = project_source(entry)
        row['source'] = str(source) if source else ''
        if source and Path(os.path.realpath(target)) != Path(
                os.path.realpath(source)):
            row['status'] = 'link-elsewhere' if _is_link(target) else 'copied'
        else:
            row['status'] = 'ok'
            row['mode'] = 'link' if _is_link(target) else 'copy'
    else:
        row['status'] = 'ok'
        row['mode'] = 'link' if _is_link(target) else 'copy'
    return row


def rows() -> list[dict]:
    data = ledger()
    if not data['dirs']:
        raise ValueError('no harness dirs; set skills.harness_dirs in local.json')
    return [inspect(entry, harness)
            for entry in data['skills'] for harness in data['dirs']]


def scan() -> dict:
    data = ledger()
    known = {entry['name'] for entry in data['skills']}
    extra = []
    for harness in data['dirs']:
        if not harness.is_dir():
            continue
        for child in sorted(harness.iterdir()):
            if not child.is_dir() or child.name in known or child.name.startswith('.'):
                continue
            extra.append({'skill': child.name, 'harness': str(harness),
                          'name_in_skill': frontmatter_name(child)})
    return {'installed_but_unlisted': extra,
            'unlisted': len(extra), 'listed': len(known)}


def install(name: str, force: bool = False) -> dict:
    data = ledger()
    entry = next((item for item in data['skills'] if item['name'] == name), None)
    if not entry:
        raise ValueError(f'{name} is not in skill-list.json; add it first')
    if not entry.get('project'):
        raise ValueError(f'{name} is third-party: install it by hand, the '
                         f'ledger only records it')
    source = project_source(entry)
    if not source:
        suffix = (f' (subdir {entry["subdir"]!r})' if entry.get('subdir') else '')
        raise ValueError(f'project {entry["project"]!r}{suffix} not in '
                         f'local.json projects.items, or its path is missing')
    if not (source / 'SKILL.md').is_file():
        raise ValueError(f'{source} has no SKILL.md; check "subdir" in '
                         f'skill-list.json')
    done = []
    for harness in data['dirs']:
        target = harness / name
        if target.exists():
            if Path(os.path.realpath(target)) == Path(os.path.realpath(source)):
                done.append({'target': str(target), 'result': 'already'})
                continue
            if not force:
                raise ValueError(f'{target} exists and points elsewhere; '
                                 f'pass --force to replace it')
            if _is_link(target):
                target.unlink()
            else:
                subprocess.run(['rmdir', str(target)], check=False)
                if target.exists():
                    raise ValueError(f'{target} is a directory, not a junction; '
                                     f'move it by hand')
        harness.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J',
                                 str(target), str(source)],
                                capture_output=True, text=True,
                                encoding='utf-8', errors='replace')
        if result.returncode != 0:
            raise ValueError(f'mklink failed: {result.stderr or result.stdout}')
        done.append({'target': str(target), 'result': 'linked'})
    return {'skill': name, 'source': str(source), 'actions': done}


def _table(items: list[dict]) -> str:
    header = ('skill', 'status', 'mode', 'origin', 'harness')
    body = [[str(item.get('skill', '')), str(item.get('status', '')),
             str(item.get('mode', '')), str(item.get('origin', '')),
             str(Path(item.get('harness', '')).name)] for item in items]
    widths = [max([len(header[i])] + [len(line[i]) for line in body])
              for i in range(len(header))]
    lines = ['  '.join(cell.ljust(widths[i]) for i, cell in enumerate(header)),
             '  '.join('-' * width for width in widths)]
    lines += ['  '.join(cell.ljust(widths[i]) for i, cell in enumerate(line))
              for line in body]
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Skill ledger: what every machine must have installed.')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'check', 'scan', 'dirs'):
        p = sub.add_parser(name)
        p.add_argument('--json', action='store_true')
    ins = sub.add_parser('install')
    ins.add_argument('names', nargs='+')
    ins.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)

    try:
        if args.command == 'dirs':
            data = ledger()
            print(json.dumps([str(item) for item in data['dirs']],
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == 'scan':
            result = scan()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for item in result['installed_but_unlisted']:
                    print(f"unlisted: {item['skill']}  ({item['harness']})")
                print(f"{result['listed']} listed, "
                      f"{result['unlisted']} installed but unlisted")
            return 0
        if args.command == 'install':
            done = [install(name, args.force) for name in args.names]
            print(json.dumps(done, ensure_ascii=False, indent=2))
            return 0

        items = rows()
        if args.json:
            print(json.dumps({'skills': items}, ensure_ascii=False, indent=2))
        else:
            print(_table(items))
            required = [item for item in items if not item['optional']]
            bad = [item for item in required if item['status'] in BAD]
            optional_bad = [item for item in items if item['optional']
                            and item['status'] in BAD]
            print(f"\n{len(required)} required, {len(bad)} problem(s)"
                  + (f"; {len(optional_bad)} optional not installed"
                     if optional_bad else ''))
            if bad:
                names = ', '.join(item['skill'] for item in bad)
                print(f'fix with: python scripts/config/skills.py install {names}',
                      file=sys.stderr)
        if args.command == 'check':
            bad = [item for item in items
                   if not item['optional'] and item['status'] in BAD]
            return 1 if bad else 0
        return 0
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
