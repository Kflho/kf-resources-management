# 资源入口

本册列出各类资源的位置、归属与改动方式。主文档只列分组与触发词，具体任务按下列表格进入。

## 资产与清单

| 任务 | 先读 | 入口 |
| --- | --- | --- |
| 项目登记、查仓库与测试仓、项目分工 | [projects](projects.md) | `scripts/projects/registry.py` |
| 跨机器清单、数据放哪一层 | [data-hub](data-hub.md) | `scripts/projects/hub.py`、`scripts/data/` |
| 线下保存与两台机器的目录映射 | [locally-save](locally-save.md) | `scripts/projects/sync.py`、`scripts/apps/syncmap.py` |

## 配置与账号

| 任务 | 先读 | 入口 |
| --- | --- | --- |
| 书签、软件设置、api-key、ssh 密钥 | [bookmarks](bookmarks.md) | `scripts/config/bookmarks.py` |
| skill 装在哪、清单、启用与停用 | [skill-install](skill-install.md) | `scripts/config/skills.py` |
| 仓库与远程、发布入口 | [gh](gh.md) | `scripts/projects/gh.py` |
| agent 信箱 | [agent-issues](agent-issues.md) | `scripts/projects/gh_mailbox.py` |
| 隐私值与扫描 | [privacy](privacy.md) | `<kf-dev>/scripts/privacy/scan.py` |

## 文档与笔记

| 任务 | 先读 | 入口 |
| --- | --- | --- |
| 文档放哪、面向谁、笔记怎么读 | [notes](notes.md) | `scripts/config/notes.py` |

## 硬约束

项目、仓库、网盘路径与 skill 记录先写入总账或清单，再写入其他文档；顺序颠倒时会出现无法检索的路径。隐私值与机器信息只写入 `local.json` 与 `kf-personal-data`，发布仓中的文档只写占位符。本机只修改本机对应的清单，合并表由脚本生成。

## 本簇不做

规范怎么写去 `kf-standard`；怎么操作去 `kf-personal-service`；开发流程与发布去 `kf-personal-development`。
