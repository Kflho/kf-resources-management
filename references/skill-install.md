# 安装到 harness 与 skill 清单

> 从 SKILL.md 下沉。

同步工具在 `kf-personal-development` 的 `<kf-dev>/scripts/skills/manage.py`，本仓库不留副本
（旧的 `scripts/sync_harness.py` 已删）。

```bash
# 本机开发：junction，改完立即生效
set AGENTS_SKILLS_DIR=%USERPROFILE%\.dsh\skills
python <kf-dev>/scripts/skills/manage.py sync <skill-dir> --link

# 换机器/交付：复制运行时文件 + SYNCED.json
python <kf-dev>/scripts/skills/manage.py sync <skill-dir> \
  --target %USERPROFILE%\.dsh\skills\<skill-name>
```

- 两个约定目录并存：Claude Code 读 `~/.agents/skills`，DeepSeek Harness 读
  `~/.dsh/skills`，DSH 兼容 CC 的 `.agents` 约定；各机器用哪个都合法；
- manage.py 默认目标是 `~/.agents/skills`；本机 DSH 用 `~/.dsh/skills`，所以要么设
  `AGENTS_SKILLS_DIR`（根目录），要么 `--target` 指到**具体 skill 目录**，不要靠猜；
- 复制模式把运行时文件一并复制过去（`SKILL.md`、`README.md`、`references/`、`scripts/`、`rules/`、
  `local.example.json`、`local.json`），`tests/`、`dev/`、`__pycache__` 不进去；
- 目标已是 junction 时先删链接再复制，不要往链接里写副本。

## 碰到问题先加载对应 skill

本 skill 只做资源和流程入口，开发/发布/出稿的方法不在本 skill 里重复。碰到下面几种情况，
**先 `skill` 加载对应 skill 再动手**：

| 碰到什么 | 先加载 | 看哪里 |
|---|---|---|
| 改 skill 代码、测试、发布、目录/gitignore/README | `kf-personal-development` | `<kf-dev>/references/skill-dev.md`、`release.md`、`docs.md` |
| 申报书、方案、答辩 PPT、配图、指标表述 | `kf-proposal-exports` | 它的 `SKILL.md` 与 `references/` |
| 机器、网络、SSH、项目总账、文档登记 | 本 skill | 上面各节 |

不加载就动手 = 流程错误；本 skill 发现开发/发布问题时也转给 `kf-personal-development`，
不在本 skill 另写一套。

## 每台电脑要装的 skill

清单在 `scripts/config/skill-list.json`（随仓库走，通用）；本机只管路径和例外：
`local.json` 的 `skills.harness_dirs`（默认 `~/.dsh/skills`）与 `skills.disabled`。

```bash
python scripts/config/skills.py list      # 清单 + 每项状态（ok/missing/link-elsewhere）
python scripts/config/skills.py check     # 必装项缺失时退出码 1
python scripts/config/skills.py dirs      # 实际使用的 harness 目录
python scripts/config/skills.py install kf-personal-development   # 从本地仓库建 junction
python scripts/config/skills.py scan      # 装了但没登记的 skill
```

- 有 `project` 的条目从 `projects.items` 找本地仓库，`install` 建 junction（`--force`
  覆盖已有）；skill 不在仓库根时加 `"subdir": "<子目录>"`（如
  `kf-proposal-exports` 的 `proposal-exports`），检查与安装都按该目录算；
- `origin: third-party` 的条目（`nature-*`、`scipilot`）只登记，`install` 提示手动装，
  不猜来源；`origin: embedded` 表示规则已内嵌在别的 skill（`humanizer-zh` 在
  `kf-proposal-exports` 的 `text_consistency.py` / 文本规范里），标 `optional` 即可；
- `optional: true`（申报书配图、字幕校对）缺失不算失败，`check` 只提示；
- 换机器：`hub.py skills import --from <HOST> --apply` 带来清单和路径，补完本机路径
  执行一次 `install`；skill 配置本身也走 `hub.py skills export`（见 §8）。
