# 7. 文档管理（notes.py）

> 从 SKILL.md 下沉。

文档分三层，各管各的：

| 层 | 放哪 | 谁维护 |
|---|---|---|
| 规则与研究笔记 | 用户自己的笔记目录，路径登记在本机 `local.json` | 用户；本 skill 只登记路径和内容摘要 |
| 项目文档 | 项目 `README.md`、`bin/requirement_docs/`、`bin/reference_docs/` | 项目；规范见 `kf-personal-development/references/docs.md` |
| 会话笔记 | 用户点名时才写 Obsidian | 用户和 agent |

主 vault 里每类笔记记什么（`project list` / `<项目> note` / `note note` / `data note` / `skill note`）
见 [data-hub.md](references/data-hub.md#obsidian-文档分工谁记什么)；测试清单写在各项目笔记的 `# tests` 一节。

本 skill 管第一层的登记和变更检测，不管内容：

```bash
python scripts/config/notes.py list
python scripts/config/notes.py add "<path>" --name "data note" \
  --purpose "文件与数据管理规则" --tags naming [--read startup]
python scripts/config/notes.py check
python scripts/config/notes.py ack
python scripts/config/notes.py ack "sc 03 note"
python scripts/config/notes.py check --refresh
python scripts/config/notes.py show "sc 03 note" --head 60
python scripts/config/notes.py context
```

读法分两类，项目笔记不用登记路径：

| 读法 | 文档 | 会话开始 | 什么时候读 |
|---|---|---|---|
| 项目笔记 | vault 里 `<项目> note.md`（自动认 `sk`/`sc`/`ko`/`pg` + 编号，跳过 `.trash`） | `must_read` 只列**当前项目**改过的那份（按 cwd 认，`--project` 指定）；别的项目进 `other_projects`，只报名字 | 没改的不读；换项目时再读那一份 |
| 参考文档 | `local.json` 的 `notes.docs`：`project note`（模板）、`data note`、`note note`、`task note`、`sync note`、`obsidian scripts`、`study note`、`bookmark list`、`mcp list`、`agi note`、`skill note` | 报 `deferred` 只 `ack`，**不读** | 任务真用到时 `show` 当前内容 |

项目笔记用来知道**接下来的开发和修改工作**（`sc 03 note.md` 是 locally-save，`sk 02 note.md`
是本 skill 的开发方法）；改了就读，别每台机器手工加路径。vault 根取 `notes.vault`，
没写就用 `bookmarks.vault`；第一次跑会把现有项目笔记记成基线（可以一次 `ack` 全部）。

`ack` 只推迟、不认可：按需文档用到时 `show` 读到的是当时最新内容，不会按过期规则执行；
`check --refresh` 才是「读过了，全部记内容摘要」。`context` 输出「名字 + 读法 + 用途 + 路径」，
贴进会话。

- 路径只进 `local.json`（已 gitignore），不写进 README/SKILL/references，也不进
  发布仓；`local.example.json` 只留占位符；
- **权限分两档**：纯机械记录（`# tests`、同步状态这类）允许 agent 读写；笔记核心内容
  （概论、功能、规范）**仅读**，要改先停下来报告，等用户决定 —— 用户明确点名要改的才动手；
- **机器只读方法部分**：笔记里判据、步骤、约束、失败条件才是要执行的内容；心态、动机、自我评价、
  学习安排是写给人看的，读到也不照做、不写进 skill 或执行清单（分层规则见 `kf-personal-development`
  的 `<kf-dev>/references/method.md`「选择性读取」）；
- 本 skill 只登记路径，不复制内容；目录模板、README、Obsidian 写法在 development 的
  `docs.md`；
- 换机器：`hub.py skills export` 会带上条目名、用途和标签，路径变成
  `<machine-local>`，到新机器 `skills import --from <HOST> --apply` 再补路径；
- 不做全文检索、版本管理或自动改写，这些笔记的版本由用户自己的 vault 管。

## Obsidian 文档同步（只读快照）

主 vault 里与插件/脚本相关的说明文档（`obsidian scripts.md`）除了登记路径，还在
私有数据仓留一份**只读快照**：`<hub>/kf-personal-service/docs/obsidian/`。

- 快照只读：源笔记归用户，只有用户明确要求或规则变更时才刷新；
- 刷新：覆盖 `obsidian-scripts.md`，同步更新 `README.md` 里的源大小 / SHA256；
- 写入规则（多机单写方、写权交接）见同目录 `write-policy.md`；
- 快照不放绝对路径、不放 key；MCP 的 Obsidian REST 入口仍按 §13。
