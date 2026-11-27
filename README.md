# kf-resources-management

kf 所有资源的管理入口：隐私、配置、项目、git、网盘、skill、服务器、文档位置。只回答「放在哪、属于谁、怎么登记」，规范与操作分别去 `kf-standard` 与 `kf-personal-service`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | 资源大类、登记入口与边界 |
| `references/` | 各资源册子：隐私、项目、清单与分层、配置、git、skill、服务器、文档位置 |
| `scripts/` | 登记与校验脚本（从 `kf-personal-service` 迁入） |
| `local.example.json` | 本机配置样例，只留占位符 |

## 与其他 skill 的关系

| 关系 | 说明 |
| --- | --- |
| 规范 | `kf-standard`：文字、配图、图表、文档权限与读取分层 |
| 操作 | `kf-personal-service`：VPN、远程、剪贴板、GUI、格式转换 |
| 流程 | `kf-personal-development`：调研到发布的流程与闸门 |
| 产物 | `kf-proposal-exports`：页片、配图产物、文字质量优化 |

## 使用

先跑 `notes.py check` 拿到必读清单，再按 `SKILL.md` 的阶段路由读一册。新增资源（项目、仓库、网盘路径、skill）先登记再引用，不散写在文档里。
