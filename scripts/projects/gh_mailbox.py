#!/usr/bin/env python3
"""GitHub Issues mailbox for two-agent collaboration.

The private data repo configured as ``data_hub.repo`` in ``local.json`` is the
transport.  Each task is one issue; comments are progress/results; labels route
it to a machine.

    python scripts/projects/gh_mailbox.py init
    python scripts/projects/gh_mailbox.py send --to OTHER --subject "..." --body "..."
    python scripts/projects/gh_mailbox.py inbox
    python scripts/projects/gh_mailbox.py result --issue 3 --status done --summary "..."
    python scripts/projects/gh_mailbox.py show --issue 3

Labels: ``agent-mailbox``, ``to:<host>``, ``kind:<instruction|question|result|status>``.
All commands use the local authenticated ``gh`` CLI; GitHub access goes through
the VPN task list (``gh issue`` is a configured task).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import config as cfg  # noqa: E402

STATE = Path(__file__).resolve().parents[2] / 'dev' / 'mailbox-seen.json'
LABELS = ('agent-mailbox', 'kind:instruction', 'kind:question',
          'kind:result', 'kind:status')


def default_repo() -> str:
    """``owner/repo`` of the private hub; no default, the skill ships none."""
    repo = str(cfg.get('data_hub', 'repo', '')).strip()
    if not repo:
        raise ValueError('set data_hub.repo in local.json to your private hub '
                         '(owner/repo)')
    return repo


def _host(explicit: str = '') -> str:
    return (explicit or os.environ.get('COMPUTERNAME') or 'unknown').lower()


def _labels_for(host: str) -> list[str]:
    return [f'to:{host.lower()}']


def build_title(to: str, subject: str) -> str:
    return f'[to:{to.lower()}] {subject.strip()}'


def build_body(host: str, to: str, kind: str, body: str,
               reply_to: str = '') -> str:
    lines = [
        f'**from:** {host}',
        f'**to:** {to}',
        f'**kind:** {kind}',
        f'**created_at:** {_dt.datetime.now().isoformat(timespec="seconds")}',
    ]
    if reply_to:
        lines.append(f'**reply_to:** {reply_to}')
    lines.extend(['', body.strip(), ''])
    return '\n'.join(lines)


def _gh(args: list[str]) -> str:
    result = subprocess.run(['gh', *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _json(args: list[str]):
    output = _gh(args)
    return json.loads(output) if output else []


def ensure_labels(repo: str, extra: tuple[str, ...] = ()) -> None:
    existing = {item['name'] for item in _json(
        ['label', 'list', '--repo', repo, '--limit', '200', '--json', 'name'])}
    needed = list(LABELS) + list(extra)
    for host in ('kahotat', 'other', 'unknown'):
        needed.append(f'to:{host}')
    for name in needed:
        if name in existing:
            continue
        _gh(['label', 'create', name, '--repo', repo,
             '--description', 'agent mailbox', '--color', '1D76DB'])


def send(to: str, subject: str, body: str, kind: str, host: str,
         repo: str = '', dry_run: bool = False) -> dict:
    repo = repo or default_repo()
    title = build_title(to, subject)
    payload = build_body(host, to, kind, body)
    labels = ['agent-mailbox', f'kind:{kind}', *_labels_for(to)]
    if dry_run:
        return {'title': title, 'body': payload, 'labels': labels}
    ensure_labels(repo, (f'to:{to.lower()}',))
    out = _gh(['issue', 'create', '--repo', repo, '--title', title,
               '--body', payload, '--label', ','.join(labels)])
    return {'issue': out, 'title': title, 'labels': labels}


def inbox(host: str, repo: str = '', all_tasks: bool = False) -> list:
    repo = repo or default_repo()
    args = ['issue', 'list', '--repo', repo, '--state', 'open',
            '--label', 'agent-mailbox', '--limit', '100',
            '--json', 'number,title,body,labels,createdAt,url,author']
    if not all_tasks:
        args.extend(['--label', f'to:{host.lower()}'])
    return _json(args)


def result(issue: int, status: str, summary: str, host: str,
           repo: str = '', close: bool = True) -> dict:
    repo = repo or default_repo()
    body = (f'**from:** {host}\n**kind:** result\n'
            f'**status:** {status}\n**at:** '
            f'{_dt.datetime.now().isoformat(timespec="seconds")}\n\n'
            f'{summary.strip()}\n')
    _gh(['issue', 'comment', str(issue), '--repo', repo, '--body', body])
    try:
        _gh(['issue', 'edit', str(issue), '--repo', repo,
             '--add-label', 'kind:result'])
    except RuntimeError:
        pass
    if close:
        _gh(['issue', 'close', str(issue), '--repo', repo])
    return {'issue': issue, 'status': status, 'closed': close}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n',
                     encoding='utf-8', newline='\n')


def watch(host: str, repo: str = '', interval: int = 300,
          once: bool = False, agent_command: str = '',
          dry_run: bool = False, agent_stdin: bool = False) -> int:
    """Poll inbox and optionally hand each new issue to a headless agent."""
    repo = repo or default_repo()
    state = {}
    if STATE.is_file():
        try:
            state = json.loads(STATE.read_text(encoding='utf-8-sig'))
        except (OSError, json.JSONDecodeError):
            state = {}
    while True:
        issues = inbox(host, repo)
        for issue in issues:
            key = str(issue.get('number'))
            prior = state.get(key, {})
            if prior.get('state') == 'done':
                continue
            if prior.get('state') == 'failed' and prior.get('attempts', 1) >= 3:
                continue
            print(json.dumps(issue, ensure_ascii=False, indent=2))
            if not agent_command:
                state[key] = {'state': 'seen', 'at': _dt.datetime.now().isoformat(
                    timespec='seconds')}
                _write_state(state)
                continue
            prompt = (
                f'你是 {host} 上的 agent。当前目录就是 skill 根目录，'
                '用 kf-personal-service 的流程处理下面这个 GitHub issue。\n'
                f"编号：#{issue.get('number')}\n"
                f"标题：{issue.get('title')}\n"
                f"链接：{issue.get('url')}\n\n"
                f"{issue.get('body')}\n\n"
                '要求：先核对本地仓库/文件再回答；只做 issue 要求的任务；'
                '破坏性操作先确认；结束时必须回写结果，命令是\n'
                f'  python scripts/projects/gh_mailbox.py --host {host} result '
                f"--issue {issue.get('number')} --status done --summary \"...\"\n"
                '失败或阻塞用 --status failed/blocked 加 --keep-open，让 issue 保持打开。'
            )
            if dry_run:
                print('[dry-run]', agent_command, prompt[:200])
                continue
            command = shlex.split(agent_command, posix=False)
            resolved = shutil.which(command[0])
            if resolved:
                command[0] = resolved
            # .cmd wrappers drop everything after the first newline of an argv
            # argument, so pipe the prompt on stdin instead when asked.
            command = command + (['-'] if agent_stdin else [prompt])
            code = subprocess.run(
                command, cwd=str(Path(__file__).resolve().parents[2]),
                input=prompt if agent_stdin else None, text=True,
                encoding='utf-8', errors='replace').returncode
            still_open = any(str(item.get('number')) == key
                             for item in inbox(host, repo))
            state[key] = {
                'state': 'done' if code == 0 and not still_open else 'failed',
                'at': _dt.datetime.now().isoformat(timespec='seconds'),
                'returncode': code,
                'attempts': int(prior.get('attempts', 0)) + 1,
                'open_after': still_open,
            }
            _write_state(state)
        if once:
            return 0
        time.sleep(max(30, interval))


PID_FILE = STATE.parent / 'mailbox-watch.pid'


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon(action: str, host: str, repo: str, interval: int,
           agent_command: str, agent_stdin: bool) -> int:
    """Start/stop/query a detached watcher for the current session.

    The watcher is deliberately not scheduled: it only runs while a skill
    session asks for it.
    """
    pid = 0
    try:
        pid = int(PID_FILE.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        pid = 0
    alive = _pid_alive(pid)
    if action == 'status':
        print(json.dumps({'running': alive, 'pid': pid if alive else 0},
                         ensure_ascii=False, indent=2))
        return 0
    if action == 'stop':
        if alive:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True)
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        print(json.dumps({'stopped': True, 'pid': pid}, ensure_ascii=False,
                         indent=2))
        return 0
    if alive:
        print(json.dumps({'running': True, 'pid': pid, 'already': True},
                         ensure_ascii=False, indent=2))
        return 0
    argv = [sys.executable, str(Path(__file__).resolve()), '--host', host,
            'watch', '--interval', str(interval)]
    if agent_stdin:
        argv.append('--agent-stdin')
    if agent_command:
        argv += ['--agent-command', agent_command]
    flags = 0
    if os.name == 'nt':
        flags = (getattr(subprocess, 'DETACHED_PROCESS', 0)
                 | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
    log = STATE.parent / 'mailbox-watch.log'
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(log, 'ab') as sink:
        proc = subprocess.Popen(argv, cwd=str(Path(__file__).resolve().parents[2]),
                                stdin=subprocess.DEVNULL, stdout=sink,
                                stderr=sink, creationflags=flags)
    PID_FILE.write_text(str(proc.pid), encoding='utf-8')
    print(json.dumps({'started': True, 'pid': proc.pid, 'log': str(log)},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='',
                        help='owner/repo; defaults to data_hub.repo in local.json')
    parser.add_argument('--host', default='')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    send_p = sub.add_parser('send')
    send_p.add_argument('--to', required=True)
    send_p.add_argument('--subject', required=True)
    send_p.add_argument('--body', required=True)
    send_p.add_argument('--kind', choices=('instruction', 'question', 'status'),
                        default='instruction')
    send_p.add_argument('--dry-run', action='store_true')
    inbox_p = sub.add_parser('inbox')
    inbox_p.add_argument('--all', action='store_true')
    result_p = sub.add_parser('result')
    result_p.add_argument('--issue', type=int, required=True)
    result_p.add_argument('--status', choices=('done', 'failed', 'blocked'),
                          default='done')
    result_p.add_argument('--summary', required=True)
    result_p.add_argument('--keep-open', action='store_true')
    show_p = sub.add_parser('show')
    show_p.add_argument('--issue', type=int, required=True)
    watch_p = sub.add_parser('watch')
    watch_p.add_argument('--host', dest='watch_host', default='')
    watch_p.add_argument('--interval', type=int, default=300)
    watch_p.add_argument('--once', action='store_true')
    watch_p.add_argument('--agent-command', default='')
    watch_p.add_argument('--agent-stdin', action='store_true',
                         help='append "-" and pipe the prompt on stdin '
                              '(needed for dsh headless under cmd.exe)')
    watch_p.add_argument('--dry-run', action='store_true')
    watch_p.add_argument('--daemon', choices=('start', 'stop', 'status'),
                         default='',
                         help='run/stop a detached watcher for this session')
    args = parser.parse_args(argv)
    host = _host(args.host)
    try:
        repo = args.repo or default_repo()
        if args.command == 'init':
            ensure_labels(repo)
            print('labels ready')
        elif args.command == 'send':
            print(json.dumps(send(args.to, args.subject, args.body, args.kind,
                                  host, repo, args.dry_run),
                             ensure_ascii=False, indent=2))
        elif args.command == 'inbox':
            print(json.dumps(inbox(host, repo, args.all),
                             ensure_ascii=False, indent=2))
        elif args.command == 'result':
            print(json.dumps(result(args.issue, args.status, args.summary,
                                    host, repo, not args.keep_open),
                             ensure_ascii=False, indent=2))
        elif args.command == 'watch':
            if args.daemon:
                return daemon(args.daemon, args.watch_host or host, repo,
                              args.interval, args.agent_command,
                              args.agent_stdin)
            return watch(args.watch_host or host, repo, args.interval,
                         args.once, args.agent_command, args.dry_run,
                         args.agent_stdin)
        else:
            data = _json(['issue', 'view', str(args.issue), '--repo', repo,
                          '--json', 'number,title,body,comments,labels,url'])
            print(json.dumps(data, ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
