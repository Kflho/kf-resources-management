#!/usr/bin/env python3
"""把私有数据仓压成**一个提交**再推（仓库只存数据，历史没有用途）。

为什么要有这条约定：`kf-personal-data` 里放的是 skill 用的元数据（配置、测试记录、
书签快照），每次普通提交都会多留一份同样的 JSON；2026-11-23 实测 `.git` 到 1.48 GiB、
货架历史里 1917 份旧包、拉取超时导致三个多小时没有同步。所以写入数据仓时：

1. 暂存指定的路径（不给路径就 `-A`）；
2. 用索引内容生成**一个无父提交**；
3. 把分支指到它；
4. `git push --force-with-lease` 推上去（别的机器抢先推过就重试一次）；
5. 过期 reflog 并 `gc --prune=now`，本机仓库也不再随提交变大。

用法：

    python scripts/projects/hub_publish.py --hub <数据仓路径> -m "inventory: <HOST>"
    python scripts/projects/hub_publish.py --hub <数据仓路径> -m "…" --path a.json --path b/
    python scripts/projects/hub_publish.py --hub <数据仓路径> -m "…" --no-push

调用方也可以不走命令行：`publish(hub, message, paths=[...], run=..., on_conflict=...)`，
`run(args, network)` 用来替换执行 git 的方式（书签同步就是把它接到 VPN 任务表上）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(hub: Path, args: list[str], network: bool = False) -> int:
    """在本机数据仓里跑一条 git；network 只影响调用方是否走代理。"""
    return subprocess.call(["git", "-C", str(hub), *args])


def _capture(hub: Path, args: list[str]) -> str:
    """跑一条只读 git 命令并取 stdout；失败给空串。"""
    proc = subprocess.run(
        ["git", "-C", str(hub), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _branch(hub: Path) -> str:
    return _capture(hub, ["rev-parse", "--abbrev-ref", "HEAD"]) or "main"


def _prune(hub: Path, run) -> None:
    """过期 reflog 并回收对象：每次发布都会留下一个不再被引用的旧提交。"""
    run(["reflog", "expire", "--expire=now", "--all"], False)
    run(["gc", "--prune=now", "--quiet"], False)


def publish(
    hub: Path,
    message: str,
    paths: list[str] | None = None,
    no_push: bool = False,
    run=None,
    on_conflict=None,
) -> int:
    """把 `paths` 的内容写成一个无父提交并强推；返回进程退出码。

    `run(args, network)`：执行 git 的方式（默认直接调用 git）。
    `on_conflict()`：远程被别的机器抢先推过时调用一次，用来重新生成要提交的文件
    （书签同步用它重新合并快照）；返回非 0 表示放弃。
    """
    run = run or (lambda args, network=False: _run(hub, args, network))
    hub = Path(hub)
    branch = _branch(hub)

    staged = paths if paths else ["-A"]
    add = run(["add", *staged], False)
    if add != 0:
        print("[hub] 暂存失败", file=sys.stderr)
        return 3

    for attempt in (1, 2):
        tree = _capture(hub, ["write-tree"])
        if not tree:
            print("[hub] 读不出这次要提交的内容（write-tree 失败）", file=sys.stderr)
            return 3
        if tree == _capture(hub, ["rev-parse", "--verify", "--quiet", "HEAD^{tree}"]):
            print("[hub] 内容没有变化，未新建提交")
            return 0
        commit = _capture(hub, ["commit-tree", tree, "-m", message])
        if not commit:
            print("[hub] 生成提交失败（commit-tree）", file=sys.stderr)
            return 3
        if run(["update-ref", f"refs/heads/{branch}", commit], False) != 0:
            return 3
        if no_push:
            print("[hub] 已写成一个提交，未推送（--no-push）")
            return 0
        if run(["push", "--force-with-lease", "origin", branch], True) == 0:
            _prune(hub, run)
            print(f"[hub] 已推送（单提交）：{message}")
            return 0
        if attempt == 2:
            break
        # 别的机器先推过：取回远端，让调用方重新生成文件，再压一次
        if run(["fetch", "origin", branch], True) != 0:
            break
        if run(["reset", "--hard", "FETCH_HEAD"], False) != 0:
            break
        if on_conflict is not None and on_conflict() != 0:
            break
        if run(["add", *staged], False) != 0:
            break

    print("[hub] 推送失败（远程被抢先，重试后仍未成功）", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish the private data hub as one parentless commit.")
    parser.add_argument("--hub", required=True, help="data hub checkout path")
    parser.add_argument("-m", "--message", required=True, help="commit message")
    parser.add_argument("--path", action="append", default=[],
                        help="path to stage (repeatable); omit to stage everything")
    parser.add_argument("--no-push", action="store_true", help="build the commit, do not push")
    args = parser.parse_args(argv)
    hub = Path(args.hub).expanduser()
    if not (hub / ".git").is_dir():
        print(f"[hub] {hub} 不是 git 仓库", file=sys.stderr)
        return 2
    return publish(hub, args.message, paths=args.path or None, no_push=args.no_push)


if __name__ == "__main__":
    raise SystemExit(main())
