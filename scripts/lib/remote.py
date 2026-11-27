#!/usr/bin/env python3
"""远程协作是否正在进行 —— 判据只有一份，`references/remote.md` 为准。

两处要用同一套信号：

- 占用型 GUI 任务（迅雷等）在会话期间**禁止执行**（`scripts/ui/tasks.py`）；
- WiFi 自动切换**只在会话期间启用**，平时保持当前网络（`scripts/network/wifi.py`）。

信号（不看服务）：

- 环境变量 `KF_REMOTE_SESSION=1`（换远控软件时手动声明）；
- `GameViewerServer.exe` / `GameViewer.exe` 在进程表里。

`GameViewerService` 是自启动服务、常驻，**不算**在远程协作。
"""
from __future__ import annotations

import os
import subprocess
import sys

ENV_FLAG = 'KF_REMOTE_SESSION'
TRUTHY = {'1', 'true', 'yes', 'on'}
REMOTE_PROCESSES = {'gameviewerserver.exe', 'gameviewer.exe'}


def process_names() -> list[str]:
    """当前进程名（拿不到就返回空：宁可少拦，不要因为探测失败崩掉）。"""
    if sys.platform != 'win32':
        return []
    try:
        output = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=20,
            encoding='mbcs', errors='replace',
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    if not output:
        return []
    return [line.split('"')[1] for line in output.splitlines()
            if line.startswith('"')]


def signals(processes: list[str] | None = None) -> list[str]:
    """命中的信号说明（空列表＝不在远程协作）。"""
    found = []
    raw = (os.environ.get(ENV_FLAG, '') or '').strip().lower()
    if raw in TRUTHY:
        found.append(f'{ENV_FLAG}=1')
    names = process_names() if processes is None else processes
    hits = sorted({name.strip().lower() for name in names} & REMOTE_PROCESSES)
    found.extend(f'{name}（远程会话进程）' for name in hits)
    return found


def active(processes: list[str] | None = None) -> tuple[bool, str]:
    """(是否在远程协作, 命中的信号说明) —— 调用方直接拿去打印。"""
    found = signals(processes=processes)
    return (True, '、'.join(found)) if found else (False, '')
