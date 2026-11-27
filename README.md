# kf-resources-management

kf 所有资源的管理入口：隐私、配置、项目、git、网盘、skill、服务器、文档位置。只回答「放在哪、属于谁、怎么登记」。

## 目录

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | 资源大类、登记入口与边界 |
| `references/` | 九册资源说明：项目、清单与分层、线下保存、书签、skill、git、信箱、隐私、笔记 |
| `scripts/` | 登记与校验脚本：`projects/`、`data/`、`config/` |
| `local.example.json` | 本机配置样例，只留占位符 |

## 资源册子

每类资源一册，册子写口径，命令是入口；两者冲突时以册子为准。

| 资源 | 册子 | 命令 |
| --- | --- | --- |
| 项目登记 | `references/projects.md` | `scripts/projects/registry.py` |
| 跨机器清单与数据分层 | `references/data-hub.md` | `scripts/projects/hub.py`、`scripts/data/` |
| 线下保存与同步映射 | `references/locally-save.md` | `scripts/projects/sync.py` |
| 书签与配置数据 | `references/bookmarks.md` | `scripts/config/bookmarks.py` |
| skill 配置与安装 | `references/skill-install.md` | `scripts/config/skills.py` |
| git 与 GitHub | `references/gh.md` | `scripts/projects/gh.py` |
| agent 信箱 | `references/agent-issues.md` | `scripts/projects/gh_mailbox.py` |
| 隐私数据 | `references/privacy.md` | `kf-personal-development` 的扫描脚本；数据进 `kf-personal-data` |
| 文档位置与笔记 | `references/notes.md` | `scripts/config/notes.py` |

## 本机配置

`local.json`（gitignored）分节：`projects`（库根与条目）、`data_hub`（私有数据仓路径与远程）、`github`（账号）、`skills`（harness 目录与例外）、`notes`（vault 与文档清单）、`netdisk`（开放平台 token）、`servers`（长期运行资源）。字段含义见 `local.example.json`。

## 仓库分工

| 仓库 | 放什么 |
| --- | --- |
| `kf-personal-data` | 隐私值、跨机器清单、测试状态副本 |
| `kf-development-project` | 测试代码与开发数据 |
| 各 skill 仓 | 只放可发布内容，本机路径走 `local.json` |

## 校验

```bash
python scripts/projects/registry.py status       # 总账与本机一致性
python scripts/projects/hub.py validate          # 清单结构
python <kf-dev>/scripts/check_all.py --skill .   # 文风、可读性、文本一致性、隐私
```
