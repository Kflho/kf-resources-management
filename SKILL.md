---
name: kf-resources-management
description: kf 相关资源的管理：隐私数据（放 kf-personal-data）、配置数据（书签、软件设置、api-key、ssh 密钥）、项目登记与分工、git 与 GitHub、百度网盘、skill 配置与安装、服务器资源、文档位置与仓库归属。要查「东西放在哪、属于哪个库、谁能动」时用它；规范条目见 kf-standard，使用与操作见 kf-personal-service。
---

# kf-resources-management

kf 的所有资源只有这一个入口：隐私、配置、项目、git、网盘、skill、服务器、文档位置。**规范怎么写**去 `kf-standard`，**怎么用**去 `kf-personal-service`，这里只回答「放在哪、属于谁、怎么登记」。

## 能力分层

| 层 | 内容 | 何时加载 |
| --- | --- | --- |
| **常驻** | 本文件（资源大类与登记入口）；会话开始跑 `notes.py check` | 每次会话 |
| **选择性** | `references/` 各册，按触发读一册 | 对应任务出现时 |
| **隔离线** | 隐私值与机器专属信息只进私有数据仓与本机配置 | 随时有效 |

## 阶段路由

册子已经迁到本仓 `references/`；登记与校验脚本仍由 `kf-personal-service` 提供，脚本迁入前按下表的位置调用。

| 资源 | 触发 | 册子 | 入口 |
| --- | --- | --- | --- |
| 隐私数据 | 隐私、token、密钥、本机路径、用户信息 | [privacy](references/privacy.md) | `<kf-dev>/scripts/privacy/scan.py`；数据进 `kf-personal-data` |
| 项目登记 | 项目在哪、属于哪个库、对应哪个笔记、测试仓在哪 | [projects](references/projects.md) | `scripts/projects/registry.py` |
| 跨机器清单与数据分层 | 两台机器、网盘放哪一层、线下保存与同步映射 | [data-hub](references/data-hub.md)、[locally-save](references/locally-save.md) | `scripts/projects/hub.py`、`scripts/data/` |
| 配置数据 | 书签、软件设置、api-key、ssh 密钥 | [bookmarks](references/bookmarks.md) | `scripts/config/bookmarks.py` |
| git 与 GitHub | 仓库与远程、发布入口、agent 信箱 | [gh](references/gh.md)、[agent-issues](references/agent-issues.md) | `scripts/projects/gh.py`、`gh_mailbox.py` |
| skill 配置与安装 | skill 装在哪、清单、启用与停用 | [skill-install](references/skill-install.md) | `scripts/config/skills.py` |
| 服务器资源 | watcher、Koishi 等长期运行资源 | `<kf-service>/references/servers.md` | `<kf-service>/scripts/remote/servers.py` |
| 文档位置与笔记清单 | 文档放哪、面向谁、笔记怎么读 | [notes](references/notes.md) | `scripts/config/notes.py` |

## 边界

- 规范怎么写（文字、配图、图表、文档权限与读取分层）：`kf-standard`。
- 怎么操作（VPN、远程、剪贴板、GUI、格式转换）：`kf-personal-service`。
- 开发流程与发布：`kf-personal-development`；产物形态：`kf-proposal-exports`。

## 隔离线

1. 隐私值与机器专属信息只进 `local.json` 与私有数据仓 `kf-personal-data`，不进任何发布仓。
2. 文档里写路径一律用占位符（`<HOST>`、`<path-to>`），不写本机绝对路径。
3. 笔记核心内容只读；要改先停下报告。

## 参考

资源大类与登记命令以 `references/` 各册为准；总账没有的项目先登记再动。能力清单与激活检查见 `kf-personal-service` 的 `<kf-service>/scripts/capabilities.py`。
