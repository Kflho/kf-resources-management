# 3. GitHub CLI（gh）

> 从 SKILL.md 下沉。

`gh` 管账号、仓库、PR、issue 和 release。网络命令走 VPN 任务表：

```bash
python scripts/projects/gh.py status          # 装没装、登录没有、scope、当前 remote
python scripts/projects/gh.py status --net    # 再查当前仓库（需要网络）
python scripts/projects/gh.py login           # 打印登录命令
python scripts/projects/gh.py repos --user <github-user>
python scripts/projects/gh.py repo
```

- 登录：`gh auth login --hostname github.com --git-protocol https --web \
  --scopes repo,workflow,gist,read:org`；token 存系统 keyring，不写 `local.json`、
  `.env` 或 shell 配置；
- 不打印 token，不运行 `gh auth token`；`gh auth status` 只显示掩码；
- 网络命令走 `tasks.py run -- ...`；任务表已含 `gh repo`、`gh api`、`gh pr`、
  `gh release`；
- 改仓库名：`gh repo rename <new> --yes` 后更新本地 remote 和总账；
- 换账号：`gh auth logout` 再 `login`。
