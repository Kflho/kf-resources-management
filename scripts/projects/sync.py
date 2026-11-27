#!/usr/bin/env python3
"""Sync every managed repo (registry + data hub) with its upstream.

Per repo: ``git fetch --all --prune``, compare ``HEAD`` with ``@{u}``, and
fast-forward **only** when the branch has no local commits ahead and the
tracked files are unmodified.  Never rebases, never forces, never rewrites
untracked files; a repo that can't be synced is reported, not repaired.

Sources, in order: every entry in ``projects.items`` (git path honours
``release_path``), then ``data_hub.path`` when it is a git checkout and not
already registered.

Commands:
    python scripts/projects/sync.py                 # fetch + fast-forward all
    python scripts/projects/sync.py --dry-run       # plan only, no network
    python scripts/projects/sync.py --check         # fetch + report only, no pull
    python scripts/projects/sync.py --no-fetch      # use cached remote refs
    python scripts/projects/sync.py --only sk kf    # name filter (substring)
    python scripts/projects/sync.py --skip hub      # name filter (substring)
    python scripts/projects/sync.py --json          # full rows for other scripts

GitHub is behind the VPN task table, so the usual invocation is:

    python scripts/vpn/tasks.py run --vpn -- python scripts/projects/sync.py

Exit codes: 0 all synced or already current, 1 at least one fetch/merge failed
(or, with ``--check``, at least one repo is behind). A ``skipped-*`` row is not
a failure: it is a repo that needs a human.

Statuses: ``up-to-date``, ``pulled``, ``ahead`` (local commits pending push),
``skipped-dirty``, ``skipped-diverged``, ``no-upstream``, ``no-git``,
``fetch-failed``, ``merge-failed``; with ``--dry-run`` also ``would-pull``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402
from projects import registry as reg  # noqa: E402

FAILED = {'fetch-failed', 'merge-failed'}


def _git(path: Path, *args: str, timeout: int = 180) -> tuple[int, str, str]:
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.setdefault('GIT_SSH_COMMAND',
                   'ssh -o BatchMode=yes -o ConnectTimeout=15')
    try:
        proc = subprocess.run(['git', '-C', str(path), *args],
                              capture_output=True, text=True, timeout=timeout,
                              encoding='utf-8', errors='replace', env=env)
    except subprocess.TimeoutExpired:
        return 124, '', f'timeout after {timeout}s'
    except OSError as exc:
        return 1, '', str(exc)
    return (proc.returncode, (proc.stdout or '').strip(),
            (proc.stderr or '').strip())


def _last(text: str, fallback: str = '') -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][:160] if lines else fallback


def targets() -> list[dict]:
    """Managed repos: registry items first, then the data hub."""
    found: list[dict] = []
    seen: set[str] = set()

    def add(name: str, path: Path, kind: str) -> None:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            return
        seen.add(key)
        found.append({'name': name, 'path': str(path), 'kind': kind})

    for item in reg.registry()['items']:
        path = reg.item_git_path(item)
        add(str(item.get('name') or path.name), path, 'project')

    hub = str(cfg.get('data_hub', 'path', '') or '').strip()
    if hub:
        hub_path = Path(hub).expanduser()
        if hub_path.is_dir():
            add(f'{hub_path.name} (hub)', hub_path, 'hub')
    return found


def sync_one(target: dict, fetch: bool = True, apply: bool = True,
             timeout: int = 180) -> dict:
    name, path = target['name'], Path(target['path'])
    row = {'name': name, 'kind': target['kind'], 'path': str(path)}

    if not (path / '.git').is_dir():
        row['status'] = 'no-git'
        return row
    if fetch:
        code, _, err = _git(path, 'fetch', '--all', '--prune', '--quiet',
                            timeout=timeout)
        if code != 0:
            row['status'] = 'fetch-failed'
            row['detail'] = _last(err, f'rc={code}')
            return row

    code, branch, _ = _git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
    if code != 0 or not branch or branch == 'HEAD':
        row['status'] = 'detached-or-empty'
        return row
    code, upstream, _ = _git(path, 'rev-parse', '--abbrev-ref', '@{u}')
    if code != 0 or not upstream:
        row.update(branch=branch, status='no-upstream')
        return row

    ahead = behind = 0
    code, counts, _ = _git(path, 'rev-list', '--left-right', '--count',
                           'HEAD...@{u}')
    if code == 0 and len(counts.split()) == 2:
        ahead, behind = (int(value) for value in counts.split())

    _, porcelain, _ = _git(path, 'status', '--porcelain')
    changes = [line for line in porcelain.splitlines() if line.strip()]
    tracked = [line for line in changes if not line.startswith('??')]
    row.update(branch=branch, upstream=upstream, ahead=ahead, behind=behind,
               dirty=len(tracked), untracked=len(changes) - len(tracked))

    if behind == 0:
        row['status'] = 'ahead' if ahead else 'up-to-date'
        return row
    if tracked:
        row['status'] = 'skipped-dirty'
        return row
    if ahead:
        row['status'] = 'skipped-diverged'
        return row

    before, _, _ = _git(path, 'rev-parse', '--short', 'HEAD')
    if not apply:
        row.update(status='would-pull',
                   detail=f'{before} -> {behind} new commit(s)')
        return row
    code, _, err = _git(path, 'merge', '--ff-only', upstream, timeout=timeout)
    after, _, _ = _git(path, 'rev-parse', '--short', 'HEAD')
    if code == 0:
        row.update(status='pulled', detail=f'{before} -> {after}')
    else:
        row.update(status='merge-failed', detail=_last(err, f'rc={code}'))
    return row


def select(rows: list[dict], only: list[str], skip: list[str]) -> list[dict]:
    def matches(row: dict, needles: list[str]) -> bool:
        haystack = (row['name'] + ' ' + row['path']).lower()
        return any(needle.lower() in haystack for needle in needles)

    chosen = rows
    if only:
        chosen = [row for row in chosen if matches(row, only)]
    if skip:
        chosen = [row for row in chosen if not matches(row, skip)]
    return chosen


def _table(rows: list[dict]) -> str:
    header = ('repo', 'branch', 'ahead', 'behind', 'dirty', 'untracked', 'status')
    body = [[str(row.get('name', '')), str(row.get('branch', '')),
             str(row.get('ahead', '')), str(row.get('behind', '')),
             str(row.get('dirty', '')), str(row.get('untracked', '')),
             f"{row['status']}"
             + (f"  ({row['detail']})" if row.get('detail') else '')]
            for row in rows]
    widths = [max(len(header[i]), *(len(line[i]) for line in body)) if body
              else len(header[i]) for i in range(len(header))]
    lines = ['  '.join(cell.ljust(widths[i]) for i, cell in enumerate(header))]
    lines.append('  '.join('-' * width for width in widths))
    lines += ['  '.join(cell.ljust(widths[i]) for i, cell in enumerate(line))
              for line in body]
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Fetch and fast-forward every managed repo '
                    '(registry + data hub). Never rebases or forces.')
    parser.add_argument('--dry-run', action='store_true',
                        help='no network, report what would be pulled')
    parser.add_argument('--check', action='store_true',
                        help='fetch but do not pull; exit 1 when any repo is '
                             'behind (for "is everything current?" checks)')
    parser.add_argument('--no-fetch', action='store_true',
                        help='skip fetch, use cached remote-tracking refs')
    parser.add_argument('--only', nargs='+', default=[], metavar='NAME',
                        help='sync only repos whose name/path matches')
    parser.add_argument('--skip', nargs='+', default=[], metavar='NAME',
                        help='skip repos whose name/path matches')
    parser.add_argument('--timeout', type=int, default=180,
                        help='seconds per git command (default 180)')
    parser.add_argument('--json', action='store_true',
                        help='print full rows as JSON')
    args = parser.parse_args(argv)

    rows = select(targets(), args.only, args.skip)
    if not rows:
        print('error: no managed repo matched (check local.json projects/data_hub)',
              file=sys.stderr)
        return 1

    fetch = not (args.dry_run or args.no_fetch)
    apply = not (args.dry_run or args.check)
    results = [sync_one(row, fetch=fetch, apply=apply, timeout=args.timeout)
               for row in rows]

    failed = [row for row in results if row['status'] in FAILED]
    behind = sum(1 for row in results if row['status'] == 'would-pull')
    summary = {
        'repos': len(results),
        'pulled': sum(1 for row in results if row['status'] == 'pulled'),
        'would_pull': behind,
        'up_to_date': sum(1 for row in results
                          if row['status'] == 'up-to-date'),
        'need_attention': sum(1 for row in results
                              if row['status'].startswith('skipped-')
                              or row['status'] == 'ahead'),
        'failed': len(failed),
    }

    if args.json:
        print(json.dumps({'summary': summary, 'repos': results,
                          'check': bool(args.check)},
                         ensure_ascii=False, indent=2))
        return 1 if failed or (args.check and behind) else 0

    print(_table(results))
    parts = [f"{summary['repos']} repos"]
    if summary['pulled']:
        parts.append(f"{summary['pulled']} pulled")
    if summary['would_pull']:
        parts.append(f"{summary['would_pull']} would pull")
    parts.append(f"{summary['up_to_date']} up to date")
    if summary['need_attention']:
        parts.append(f"{summary['need_attention']} need attention")
    if summary['failed']:
        parts.append(f"{summary['failed']} failed")
    print('\n' + ', '.join(parts))
    if summary['need_attention']:
        print('attention: 这些库和上游不一致（ahead / skipped-dirty / '
              'skipped-diverged）。停下来向用户报告现状并给出同步方案'
              '（先 pull --rebase、还是保留本地提交后由用户决定），'
              '不要自行 rebase、强推或覆盖，也不要沉默继续。',
              file=sys.stderr)
    if not fetch:
        print('note: no fetch, comparisons use the last fetched remote refs')
    if args.check and behind:
        print(f'check: {behind} repo(s) behind upstream (not pulled)',
              file=sys.stderr)
    return 1 if failed or (args.check and behind) else 0


if __name__ == '__main__':
    sys.exit(main())
