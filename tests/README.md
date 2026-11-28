# kf-resources-management 测试入口

脚本测试在外部测试仓，不复制进 skill：

```text
D:\data\local\software\programming\kf\projects\kf-development-project\kf-resources-management-test
python -m pytest -q
```

## 覆盖映射

| 能力 | 测试文件 |
| --- | --- |
| `registry.py`：项目登记、状态、note 关联 | `test_registry.py`、`test_registry_note.py` |
| `hub.py`：总账校验、信息导出 | `test_hub_validate.py`、`test_info_management.py` |
| `syncmap.py`、仓库新鲜度 | `test_syncmap.py`、`test_repo_freshness.py` |
| `bookmarks.py` | `test_bookmarks.py` |
| `netdisk.py` | `test_netdisk.py` |
| `notes.py`、笔记约定 | `test_notes.py`、`test_note_conventions.py` |
| 项目目录与文件归属 | `test_project_layout.py` |
| 隐私交接 | `test_privacy_handoff.py` |
| GitHub CLI | `test_github_cli.py` |
| `scripts/projects/gh.py` | `test_github_cli.py` |
| Obsidian / locally-save 文档契约 | `test_obsidian_docs.py`、`test_locally_save_docs.py` |
| skill 清单与安装 | `test_hub_skills.py` |
| service 工具调用 | `test_service_tools.py` |

## 人工验证

以下脚本依赖 GitHub 或数据仓远端，只做人工验证：

- `scripts/projects/gh_mailbox.py`：需要 GitHub 私有仓与 `gh` 登录；
- `scripts/projects/hub_publish.py`：需要数据仓远端。

- 真实 vault 路径与归档；
- 百度网盘客户端上传 / 下载；
- GitHub 账号登录与发布入口；
- 跨机器清单的实际同步。

2026-11-28 复核：`python -m pytest -q` 85 passed / 9 skipped。
