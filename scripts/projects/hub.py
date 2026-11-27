#!/usr/bin/env python3
"""Export/validate the per-machine project manifest for the private data hub.

The private data repo (``data_hub.repo``/``data_hub.path`` in local.json)
is a transport layer, not a code repo:

    machines/<HOST>.json             one file per computer: project inventory
    skills/<SKILL>/<HOST>.json       one file per skill per computer: skill config
    machines/README.md               protocol

Only logical project names, local folder names, remotes and roles belong here.
Skill config is each installed skill's ``local.json``: machine paths become
``<machine-local>`` and credentials are dropped (names only).  ``config/<HOST>.json``
is the legacy single-file layout; it is still read as a fallback so a machine
that has not switched yet can be imported from.

Usage:
    python scripts/projects/hub.py export
    python scripts/projects/hub.py export --output machines/<HOST>.json
    python scripts/projects/hub.py validate machines/<HOST>.json
    python scripts/projects/hub.py compare [--dir machines] [--json]
    python scripts/projects/hub.py skills list [--dir skills] [--json]
    python scripts/projects/hub.py skills export [--skill NAME] [--output FILE]
    python scripts/projects/hub.py skills diff [--dir skills] [--json]
    python scripts/projects/hub.py skills import --from <HOST> [--skill NAME] [--apply]
    python scripts/projects/hub.py skills validate <file-or-dir>
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402
from projects.registry import registry  # noqa: E402
from projects import hub_publish  # noqa: E402

SCHEMA = 1
NAMESPACE = 'kf-personal-service'
CONFIG_DIR = 'config'            # legacy layout, read-only fallback
SKILLS_DIR = 'skills'
SKILL_CONFIG = 'local.json'
SKILL_KIND = 'skill-config'
DEFAULT_HARNESS = '~/.dsh/skills'
MACHINE_LOCAL = '<machine-local>'
# Values that only make sense on the machine that wrote them.
LOCAL_PATH = re.compile(r'^\s*(?:[A-Za-z]:[\\/]|\\\\|/|~[\\/])')
# Keys whose value is a credential; only the key name is exported.
SECRET_KEY = re.compile(r'(secret|token|password|passwd|api[_-]?key)', re.I)
# Book-keeping about the local copy of a file, not a setting.
LOCAL_STATE_KEYS = {'sha256', 'size', 'mtime', 'checked_at', 'added_at',
                    'release_path'}


def export(output: Path) -> dict:
    projects = []
    for item in registry()['items']:
        path = Path(item['path'])
        projects.append({
            'name': item.get('name') or path.name,
            'local_dir': path.name,
            'remote': item.get('remote', ''),
            'role': item.get('role', 'unknown'),
            'tags': item.get('tags') or [],
        })
    manifest = {
        'schema_version': SCHEMA,
        'machine': os.environ.get('COMPUTERNAME') or os.uname().nodename,
        'generated_at': _dt.datetime.now().isoformat(timespec='seconds'),
        'projects': projects,
        'notes': 'local_dir is the folder name only; paths are machine-specific',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='\n')
    return manifest


def validate(path: Path) -> list[str]:
    errors = []
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        return [f'cannot read {path}: {exc}']
    if data.get('schema_version') != SCHEMA:
        errors.append('schema_version must be 1')
    for key in ('machine', 'projects'):
        if not data.get(key):
            errors.append(f'missing {key}')
    for index, item in enumerate(data.get('projects') or []):
        if not item.get('name'):
            errors.append(f'projects[{index}] missing name')
        for value in (item.get('name'), item.get('local_dir')):
            if value and any(char in str(value) for char in ('\\', ':')):
                errors.append(f'projects[{index}] contains a machine path: '
                              f'{value}')
    return errors


def _repo_key(url: str) -> str:
    """Normalize GitHub remotes so ssh/https forms of one repo compare equal."""
    text = str(url).strip().lower()
    for prefix in ('ssh://git@ssh.github.com:443/', 'ssh://git@github.com/',
                   'git@github.com:', 'https://github.com/',
                   'http://github.com/'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith('.git'):
        text = text[:-4]
    return text


def compare(machines_dir: Path) -> dict:
    """Diff every ``<HOST>.json``: who has which project, remote mismatches."""
    manifests: dict[str, dict] = {}
    for path in sorted(machines_dir.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            continue
        host = data.get('machine') or path.stem
        manifests[host] = {
            item['name']: item for item in (data.get('projects') or [])
            if isinstance(item, dict) and item.get('name')
        }
    hosts = sorted(manifests)
    names = sorted({name for items in manifests.values() for name in items})
    missing, mismatched, styles = [], [], []
    for name in names:
        present = [host for host in hosts if name in manifests[host]]
        remotes = {host: str(manifests[host][name].get('remote') or '')
                   for host in present}
        keys = {host: _repo_key(url) for host, url in remotes.items() if url}
        if len(set(keys.values())) > 1:
            mismatched.append({'name': name, 'remotes': remotes})
        elif len(set(remotes.values())) > 1:
            # Same repo, different URL form (ssh vs https): worth a note only.
            styles.append({'name': name, 'remotes': remotes})
        absent = [host for host in hosts if host not in present]
        if absent:
            missing.append({
                'name': name,
                'remote': next((url for url in remotes.values() if url), ''),
                'present_on': present,
                'missing_on': absent,
                'local_dir': {host: manifests[host][name].get('local_dir')
                              for host in present},
            })
    return {'machines': hosts, 'project_count': len(names),
            'missing': missing, 'remote_mismatch': mismatched,
            'remote_style': styles}


def _hub_root() -> Path:
    hub = str(cfg.get('data_hub', 'path', '')).strip()
    return Path(hub) if hub else Path('.')


def _publish_dir(relative: str, message: str) -> int:
    """把这次写进数据仓的目录发布出去（单提交 + 强推，实现在 hub_publish.py）。"""
    hub = _hub_root().resolve()
    if not (hub / '.git').is_dir():
        print(f'[hub] {hub} 不是 git 仓库，跳过发布', file=sys.stderr)
        return 3
    return hub_publish.publish(hub, message, paths=[relative])


def _machines_dir() -> Path:
    return _hub_root() / NAMESPACE / 'machines'


def _skills_dir() -> Path:
    return _hub_root() / NAMESPACE / SKILLS_DIR


def _legacy_config_dir() -> Path:
    return _hub_root() / NAMESPACE / CONFIG_DIR


def _machine_name() -> str:
    return os.environ.get('COMPUTERNAME') or 'machine'


def _clean(value, path: str, machine_local: list[str],
           secrets: list[str]):
    """Replace machine-only values with a marker, drop credentials."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            child = f'{path}.{key}' if path else str(key)
            if key in LOCAL_STATE_KEYS:
                continue
            if SECRET_KEY.search(str(key)):
                secrets.append(child)
                continue
            out[key] = _clean(item, child, machine_local, secrets)
        return out
    if isinstance(value, list):
        return [_clean(item, f'{path}[]', machine_local, secrets)
                for item in value]
    if isinstance(value, str) and LOCAL_PATH.match(value):
        machine_local.append(path)
        return MACHINE_LOCAL
    return value


def _has_marker(value) -> bool:
    if value == MACHINE_LOCAL:
        return True
    if isinstance(value, dict):
        return any(_has_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_marker(item) for item in value)
    return False


def _merge(target: dict, incoming: dict, prefix: str,
           changes: list[dict], skipped: list[str]) -> None:
    for key, value in incoming.items():
        path = f'{prefix}.{key}' if prefix else key
        if value == MACHINE_LOCAL:
            skipped.append(path)
            continue
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            _merge(target[key], value, path, changes, skipped)
            continue
        if isinstance(value, list) and _has_marker(value):
            # List entries are positional; a mixed machine/local list is not
            # safe to merge, so keep the local copy.
            skipped.append(path)
            continue
        if target.get(key) != value:
            changes.append({'key': path, 'local': target.get(key),
                            'remote': value})
            target[key] = value


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'无法读取 {path}: {exc}') from exc
    return data if isinstance(data, dict) else {}


def _save_config(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')


def _harness_dirs() -> list[Path]:
    raw = cfg.get('skills', 'harness_dirs') or [DEFAULT_HARNESS]
    if isinstance(raw, str):
        raw = [raw]
    dirs: list[Path] = []
    for item in raw:
        path = Path(os.path.expanduser(str(item).strip()))
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def _installed_skills() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for base in _harness_dirs():
        for child in sorted(base.iterdir()):
            if child.is_dir() and child.name not in found:
                found[child.name] = child
    return found


def _config_rel(skill: str) -> str:
    mapping = cfg.get('skills', 'configs') or {}
    value = mapping.get(skill) if isinstance(mapping, dict) else None
    return Path(str(value or SKILL_CONFIG)).as_posix()


def _skill_source(skill: str, directory: Path) -> Path:
    return directory / _config_rel(skill)


def export_skill(skill: str, directory: Path, output: Path) -> dict:
    machine_local: list[str] = []
    secrets: list[str] = []
    source = _config_rel(skill)
    path = directory / source
    payload = {
        'schema_version': SCHEMA,
        'kind': SKILL_KIND,
        'skill': skill,
        'machine': _machine_name(),
        'generated_at': _dt.datetime.now().isoformat(timespec='seconds'),
        'source': source,
        'config': _clean(_load_json(path) if path.is_file() else {},
                         '', machine_local, secrets),
        'machine_local': sorted(set(machine_local)),
        'secrets_omitted': sorted(set(secrets)),
        'notes': 'machine_local and secrets_omitted are names only; '
                 'fill those in on the other machine',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8', newline='\n')
    return payload


def export_skills(skill: str | None = None,
                  output: Path | None = None) -> dict:
    installed = _installed_skills()
    if skill:
        if skill not in installed:
            raise ValueError(f'skill not installed: {skill}')
        names = [skill]
    else:
        names = sorted(installed)
    if output is not None and len(names) != 1:
        raise ValueError('--output needs --skill, one file per skill')
    written, skipped = [], []
    for name in names:
        source = _skill_source(name, installed[name])
        if not source.is_file():
            skipped.append({'skill': name,
                            'reason': f'no {_config_rel(name)}'})
            continue
        target = (output if output is not None and len(names) == 1 else
                  _skills_dir() / name / f'{_machine_name()}.json')
        payload = export_skill(name, installed[name], target)
        written.append({'skill': name, 'path': str(target),
                        'machine_local': payload['machine_local'],
                        'secrets_omitted': payload['secrets_omitted']})
    if not written:
        raise ValueError('no installed skill has a local config to export')
    return {'machine': _machine_name(), 'skill_dir': str(_skills_dir()),
            'written': written, 'skipped': skipped}


def _skill_snapshots(directory: Path) -> list[tuple[str, str, Path, dict]]:
    """(skill, host, path, payload) for every exported skill config."""
    out: list[tuple[str, str, Path, dict]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(directory.glob('*/*.json')):
        payload = _load_json(path)
        skill = str(payload.get('skill') or path.parent.name)
        host = str(payload.get('machine') or path.stem)
        out.append((skill, host, path, payload))
        seen.add((skill, host))
    legacy = _legacy_config_dir()
    if legacy.is_dir():
        for path in sorted(legacy.glob('*.json')):
            payload = _load_json(path)
            host = str(payload.get('machine') or path.stem)
            if (NAMESPACE, host) in seen:
                continue
            out.append((NAMESPACE, host, path, payload))
    return out


def list_skills(directory: Path | None = None) -> dict:
    directory = directory or _skills_dir()
    installed = _installed_skills()
    snapshots: dict[str, list[str]] = {}
    for name, host, _path, _payload in _skill_snapshots(directory):
        snapshots.setdefault(name, []).append(host)
    rows = []
    for name, path in sorted(installed.items()):
        source = _skill_source(name, path)
        rows.append({
            'skill': name,
            'config': _config_rel(name),
            'exists': source.is_file(),
            'size': source.stat().st_size if source.is_file() else 0,
            'hub_machines': sorted(snapshots.get(name, [])),
        })
    return {'machine': _machine_name(), 'hub_dir': str(directory),
            'harness_dirs': [str(item) for item in _harness_dirs()],
            'skills': rows}


def diff_skills(directory: Path | None = None) -> dict:
    directory = directory or _skills_dir()
    installed = _installed_skills()
    report: dict = {'local': _machine_name(), 'skills': {}}
    for skill, host, path, payload in _skill_snapshots(directory):
        entry = report['skills'].setdefault(
            skill, {'installed': skill in installed, 'machines': {}})
        local: dict = {}
        if skill in installed:
            source = _skill_source(skill, installed[skill])
            if source.is_file():
                local = _load_json(source)
        merged = copy.deepcopy(local)
        changes: list[dict] = []
        skipped: list[str] = []
        _merge(merged, payload.get('config') or {}, '', changes, skipped)
        entry['machines'][host] = {
            'source': str(path),
            'legacy': path.parent.name == CONFIG_DIR,
            'changes': changes,
            'machine_local': skipped,
            'secrets_to_fill': payload.get('secrets_omitted') or [],
        }
    return report


def import_skills(host: str, apply: bool = False, skill: str | None = None,
                  directory: Path | None = None) -> dict:
    directory = directory or _skills_dir()
    installed = _installed_skills()
    targets = [(name, path, payload)
               for name, other, path, payload in _skill_snapshots(directory)
               if other == host and (not skill or name == skill)]
    if not targets:
        raise ValueError(f'no skill config from {host} in {directory}')
    results = []
    for name, source, payload in targets:
        if name not in installed:
            results.append({'skill': name, 'source': str(source),
                            'skipped': f'not installed on {_machine_name()}'})
            continue
        config_path = _skill_source(name, installed[name])
        local = _load_json(config_path) if config_path.is_file() else {}
        merged = copy.deepcopy(local)
        changes: list[dict] = []
        skipped: list[str] = []
        _merge(merged, payload.get('config') or {}, '', changes, skipped)
        applied = bool(apply and changes)
        if applied:
            _save_config(merged, config_path)
        results.append({'skill': name, 'source': str(source),
                        'target': str(config_path), 'applied': applied,
                        'changes': changes, 'machine_local': skipped,
                        'secrets_to_fill': payload.get('secrets_omitted') or []})
    return {'from': host, 'skills': results}


def validate_skill_config(path: Path) -> list[str]:
    errors = []
    try:
        data = _load_json(path)
    except ValueError as exc:
        return [str(exc)]
    if data.get('schema_version') != SCHEMA:
        errors.append('schema_version must be 1')
    if data.get('kind') != SKILL_KIND:
        errors.append(f'kind must be {SKILL_KIND}')
    skill = data.get('skill')
    if not skill:
        errors.append('missing skill')
    elif path.parent.name != skill:
        errors.append(f'skill "{skill}" does not match folder '
                      f'"{path.parent.name}"')
    if not data.get('machine'):
        errors.append('missing machine')
    if not isinstance(data.get('config'), dict):
        errors.append('missing config object')
    for key in ('machine_local', 'secrets_omitted'):
        if not isinstance(data.get(key), list):
            errors.append(f'{key} must be a list')
    for value in data.get('machine_local') or []:
        if not isinstance(value, str):
            errors.append(f'machine_local entry is not a key name: {value!r}')

    def walk(value, path_: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f'{path_}.{key}' if path_ else str(key)
                if SECRET_KEY.search(str(key)):
                    errors.append(f'{child} looks like a credential key')
                walk(item, child)
        elif isinstance(value, list):
            for item in value:
                walk(item, f'{path_}[]')
        elif isinstance(value, str) and LOCAL_PATH.match(value):
            errors.append(f'{path_} holds an absolute path: {value}')

    walk(data.get('config') or {}, '')
    return errors


def validate_skill_tree(path: Path) -> list[str]:
    if path.is_file():
        return validate_skill_config(path)
    if not path.is_dir():
        return [f'not a file or directory: {path}']
    files = sorted(path.glob('*/*.json'))
    errors = []
    for item in files:
        errors.extend(f'{item.parent.name}/{item.name}: {error}'
                      for error in validate_skill_config(item))
    if not files:
        errors.append(f'no <skill>/<host>.json under {path}')
    return errors


def _print_compare(report: dict) -> None:
    print('machines: ' + ', '.join(report['machines']))
    print(f"{report['project_count']} project(s)")
    for item in report['missing']:
        where = ', '.join(f'{host}={item["local_dir"].get(host)}'
                          for host in item['present_on'])
        print(f"missing on {', '.join(item['missing_on'])}: "
              f"{item['name']} ({where})")
    for item in report['remote_mismatch']:
        print(f"remote mismatch: {item['name']}")
        for host, url in item['remotes'].items():
            print(f'  {host}: {url}')
    for item in report['remote_style']:
        print(f"same repo, different URL form: {item['name']}")
        for host, url in item['remotes'].items():
            print(f'  {host}: {url}')


def _print_skills_list(report: dict) -> None:
    rows = report['skills']
    if not rows:
        print('no skill found under: '
              + ', '.join(report['harness_dirs'] or [DEFAULT_HARNESS]))
        return
    width = max([len('skill')] + [len(row['skill']) for row in rows])
    print(f"{'skill'.ljust(width)}  config      hub")
    for row in rows:
        state = 'yes' if row['exists'] else 'no'
        hub = ', '.join(row['hub_machines']) or '-'
        print(f"{row['skill'].ljust(width)}  {row['config']} ({state})  {hub}")
    print('hub dir: ' + report['hub_dir'])


def _print_skills_export(report: dict) -> None:
    for item in report['written']:
        print(f"wrote {item['skill']}: {item['path']}")
        if item['machine_local']:
            print('  machine-local: ' + ', '.join(item['machine_local']))
        if item['secrets_omitted']:
            print('  credentials dropped: '
                  + ', '.join(item['secrets_omitted']))
    for item in report['skipped']:
        print(f"skipped {item['skill']}: {item['reason']}")


def _print_skills_diff(report: dict) -> None:
    print('local machine: ' + report['local'])
    if not report['skills']:
        print('no skills/<SKILL>/<HOST>.json in the hub yet')
    for skill, detail in report['skills'].items():
        flag = '' if detail['installed'] else ', not installed here'
        print(f'{skill}{flag}')
        for host, item in detail['machines'].items():
            label = ' (legacy config/)' if item['legacy'] else ''
            print(f"  {host}{label}: {len(item['changes'])} differing value(s)")
            for change in item['changes']:
                print(f"    {change['key']}: {change['local']!r} -> "
                      f"{change['remote']!r}")
            if item['machine_local']:
                print('    machine-local: ' + ', '.join(item['machine_local']))


def _print_skills_import(report: dict) -> None:
    print(f"from {report['from']}")
    if not report['skills']:
        print('  nothing to import')
    for item in report['skills']:
        if item.get('skipped'):
            print(f"  {item['skill']}: skipped, {item['skipped']}")
            continue
        state = 'applied' if item['applied'] else 'dry run, add --apply'
        print(f"  {item['skill']}: {len(item['changes'])} change(s), {state}")
        for change in item['changes']:
            print(f"    {change['key']}: {change['local']!r} -> "
                  f"{change['remote']!r}")
        if item['machine_local']:
            print('    machine-local, left as is: '
                  + ', '.join(item['machine_local']))
        if item['secrets_to_fill']:
            print('    credentials to fill by hand: '
                  + ', '.join(item['secrets_to_fill']))


def _skills_command(args) -> int:
    def emit(report, printer) -> None:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            printer(report)

    try:
        if args.action == 'list':
            emit(list_skills(Path(args.dir) if args.dir else None),
                 _print_skills_list)
            return 0
        if args.action == 'export':
            emit(export_skills(args.skill or None,
                               Path(args.output) if args.output else None),
                 _print_skills_export)
            if args.publish:
                return _publish_dir(f'{NAMESPACE}/{SKILLS_DIR}',
                                    f'skill config: {_machine_name()}')
            return 0
        if args.action == 'diff':
            emit(diff_skills(Path(args.dir) if args.dir else None),
                 _print_skills_diff)
            return 0
        if args.action == 'import':
            emit(import_skills(args.host, args.apply, args.skill or None,
                               Path(args.dir) if args.dir else None),
                 _print_skills_import)
            return 0
        errors = validate_skill_tree(Path(args.path))
        for item in errors:
            print('ERROR', item)
        print(f'{len(errors)} error(s)')
        return 0 if not errors else 1
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    export_p = sub.add_parser('export')
    export_p.add_argument('--output', default='')
    export_p.add_argument('--publish', action='store_true',
                          help='写完直接发布（单提交 + 强推，见 hub_publish.py）')
    validate_p = sub.add_parser('validate')
    validate_p.add_argument('path')
    compare_p = sub.add_parser('compare')
    compare_p.add_argument('--dir', default='')
    compare_p.add_argument('--json', action='store_true')

    skills_p = sub.add_parser('skills')
    skills_sub = skills_p.add_subparsers(dest='action', required=True)
    sk_list = skills_sub.add_parser('list')
    sk_list.add_argument('--dir', default='')
    sk_list.add_argument('--json', action='store_true')
    sk_export = skills_sub.add_parser('export')
    sk_export.add_argument('--skill', default='')
    sk_export.add_argument('--output', default='')
    sk_export.add_argument('--json', action='store_true')
    sk_export.add_argument('--publish', action='store_true',
                           help='写完直接发布（单提交 + 强推，见 hub_publish.py）')
    sk_diff = skills_sub.add_parser('diff')
    sk_diff.add_argument('--dir', default='')
    sk_diff.add_argument('--json', action='store_true')
    sk_import = skills_sub.add_parser('import')
    sk_import.add_argument('--from', dest='host', required=True)
    sk_import.add_argument('--skill', default='')
    sk_import.add_argument('--dir', default='')
    sk_import.add_argument('--apply', action='store_true')
    sk_import.add_argument('--json', action='store_true')
    sk_validate = skills_sub.add_parser('validate')
    sk_validate.add_argument('path')

    args = parser.parse_args(argv)
    if args.command == 'skills':
        return _skills_command(args)
    if args.command == 'export':
        host = os.environ.get('COMPUTERNAME') or 'machine'
        default = _machines_dir() / f'{host}.json'
        output = Path(args.output) if args.output else default
        print(json.dumps(export(output), ensure_ascii=False, indent=2))
        if args.publish:
            return _publish_dir(f'{NAMESPACE}/machines', f'inventory: {host}')
        return 0
    if args.command == 'compare':
        directory = Path(args.dir) if args.dir else _machines_dir()
        report = compare(directory)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_compare(report)
        return 0
    errors = validate(Path(args.path))
    for item in errors:
        print('ERROR', item)
    print(f'{len(errors)} error(s)')
    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(main())
