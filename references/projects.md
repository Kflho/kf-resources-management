# 本地项目仓库总账

只登记**有远程仓库**的项目（`git remote` 非空）。本地-only 的 git 仓库不登记，
避免总账里出现永远推不出去、也无需同步的路径。

## 存在哪里

skill 的 gitignored `local.json`：

```json
{
  "projects": {
    "roots": ["<projects-root>"],
    "items": [
      {"name": "sk_04", "path": "<absolute-path>",
       "role": "user-facing", "tags": ["skill"],
       "test": "pytest -q", "note": "<vault>/skill/sk 04 note.md",
       "test_repo": "kf-development-project/kf-personal-service-test",
       "added_at": "2026-10-20T09:18:24"}
    ]
  }
}
```

`path` 是项目顶层目录。`test` 和 `note` 是可选字段：测试命令和 Obsidian 笔记路径，
供 `kf-personal-development` 读取。`test_repo` 也是可选字段：这个项目的测试仓在哪，
格式是 `<测试项目仓在总账里的名字>/<仓里的目录名>`，例如
`kf-development-project/obsidian-note-tidy-test`。它记的是仓库内的相对位置，换机器后按
总账里测试项目仓那条的本地路径重新拼，绝对路径不会进总账。

这是机器级配置：项目路径因机器而异，不随发布仓走，换机器重新 `scan` 并补 `test_repo`。

## 一个仓库，两种用途（默认）

项目默认只有一份仓库，开发和日常使用共用：开发期的小工具、临时数据、本机配置靠
`.gitignore` 排除，不为了发布再开一份镜像仓库。总账里一个项目一条记录，`path`
同时是开发目录和使用目录。

只有发布物必须是源码子集时才拆发布仓（本 skill 自己就是这样：`release.json`
白名单加 `dev/` 隔离）。拆分是例外，不是默认。

## 命令

```bash
python scripts/projects/registry.py list
python scripts/projects/registry.py status
python scripts/projects/registry.py status --require-note --check-test-repo
python scripts/projects/registry.py add <path> --name sk_01 --tags skill
python scripts/projects/registry.py add <path> --name pg_01 --tags research \
  --test "pytest -q" --note "postgraduate/pg 01 note.md"
python scripts/projects/registry.py set <name> \
  --test-repo "kf-development-project/obsidian-note-tidy-test"
python scripts/projects/registry.py remove <name-or-path>
python scripts/projects/registry.py role <name-or-path> user-facing
python scripts/projects/registry.py roots
python scripts/projects/registry.py roots add <root-dir>
python scripts/projects/registry.py scan --depth 5 --add
python scripts/projects/registry.py prune
python scripts/projects/registry.py github --user <github-user> --depth 5 --add
```

- `add`：路径必须是 git 仓库且有 remote，否则报错；登记时把当前 remote 一并写进
  总账，仓库改名后重跑一次 `add` 就能刷新；
- `--test` / `--note`：`test` 是测试命令（给 kf-dev 读），`note` 是 **Obsidian 项目笔记的
  vault 相对路径**（如 `postgraduate/pg 01 note.md`）；传空字符串清空；
- `--test-repo`：测试仓位置，写成 `<测试项目仓名>/<目录名>`（如
  `kf-development-project/obsidian-note-tidy-test`）。测试项目仓本身也作为普通条目登记
  （`role` 用 `test`），所以两台机器各自 `scan` 一次后，这个相对写法在两边都能解析；
  传空字符串清空；
- `status --check-test-repo`：登记的每个 `test_repo` 都要能解析到本机目录，否则退出 1。
  测试仓搬走、改名、没 clone 时由它报出来，别靠人记；
- **项目和笔记必须关联**：内容型项目（`tags` 含 `skill` / `script` / `koishi` / `plugin` /
  `research`）登记时必须给 `--note`，且文件要真实存在；`status --require-note` 就是这把闸门
  （缺 note 或指向不存在的文件 → 退出 1）。纯代码仓 / 领域根这类条目不强制；
- 笔记里的 `# tests` 与 `project list.md` 的 `## tests` 都要和这个项目对得上
  （写法见 `kf-personal-development/references/post-dev-check.md`）；
- `role`：标记 `user-facing` / `development` / `test` / `archive`；`test` 用于测试项目仓本身。
  新登记默认 `unknown`，发布前必须问用户；
- `scan`：在 roots 下找 `.git` 加 remote，找到即停，不进入子目录；
- `prune`：删除路径不存在或已无 remote 的条目；
- `status`：显示 branch、head、dirty、remote，以及 `test` / `note` 是否存在
  （`no_note` / `note_exists`）。

## 扫描规则

- 根目录来自 `local.json` 的 `projects.roots`，可用 `--root` 临时覆盖；
- 默认深度 3；
- 跳过 `node_modules`、`__pycache__`、`.venv`、`models`、`temp`、`dist`、`build`；
- 目录里有 `.git` 且 `git remote` 非空才登记；
- 只有 `package.json` 等标记、没有远程仓库的目录不登记。

## 命名与布局

项目名、本地文件夹、编号前缀、`src/`/`bin/` 分层和 README 规范见
`kf-personal-development/references/docs.md`。这里只记总账相关的三条：

- 远程地址的唯一真源是 `local.json` 的 `projects.items[].remote`（机器级、
  gitignored）；跨机器可见的副本是私库的 `machines/<HOST>.json`；
- 本地缺某个仓库时，按 `name` / `remote` 去 GitHub 找，clone 到对应编号目录，
  **不要新建空仓库顶替**，否则两边历史不一致；
- 仓库改名后，GitHub 重定向还能拉取，但 `github --user` 会匹配不上：重跑
  `registry.py add <path>` 刷新 `remote`，再导出清单。

**测试仓也进总账，分两种登记方式**：测试项目仓（通常叫 `kf-development-project`）作为
一条普通条目登记，`role` 写 `test`；每个有测试仓的项目在自己的条目里用 `test_repo`
指向它在测试项目仓里的目录。这样"某个项目的测试代码在哪"只用查一处，不用扫盘，也不用
在文档里维护名单。

测试夹具和测试代码都在测试项目仓里，本地不再保存包目录的克隆和测试 vault（0.16 起插件 git 测试用
临时目录，跑完即删）。仓库 clone 固定放 `kf/projects/kf-development-project`
（main 分支 + `obsidian/lsave-test` 放包的分支）；**不再保留** `test/lsave-test-vault/`、
`test/kf-development-project-init/`、`kf-development-project-shelves/obsidian-test/`
这些固定副本，手工复现场景时临时建。项目一律放 `<领域根>/<分类>/projects/<前缀>_<编号>/`，
个人数据仓是 `<领域根>/kf/projects/kf-personal-data`（盘符与领域根每台机器不同，
文档只写相对结构）。

测试用的临时产物（vault、包、pytest 缓存）放该项目的 `dev/`，不要留在盘根或领域根：

```bash
pytest -o cache_dir=<项目>/dev/.pytest_cache
```

`kf-development-project` 的分支、夹具和禁放内容见 [data-hub.md](data-hub.md) 的
「开发/测试项目仓」一节；本机有哪些测试项目、各自属于哪个项目，查
`registry.py status` 的 `test_repo`（文档不维护名单，避免过期）。
个人数据仓和主 vault 的包目录仍按个人配置管理，不并进测试项目仓。

判型脚本和标准都在 `kf-personal-development`：`<kf-dev>/scripts/projects/layout.py` 输出
`simple` / `code-library` / `formal` / `docs-only`，判断要不要拆 `src/`、`data/`、
`bin/`；标准见 `kf-personal-development` 的 `docs.md`。

## 跨机器目录结构一致

同一项目在两台电脑上的目录结构要一致，便于对比、同步和互相补文件。课程和科研项目
统一用「材料 + 仓库」两层：

```text
linear_system/
  bin/reference_docs/   # 课程 PDF、讲义、表格（不进 git）
  projects/             # 仓库 clone，根目录直接是 exercises/、experiments/
```

- 资料和代码分开：`bin/reference_docs/` 放材料，`projects/` 是 git 仓库；
- 仓库根直接放业务目录（`exercises/`、`experiments/`），不再套一层
  `linear_system_projects/`；
- 改结构或搬目录前，先在 relay 里问对面参考路径（`relay.py send`），确认后再动；
- 开发前先交换路径、分支、HEAD；按 `name` / `remote` 对齐，不套对方本地路径；
  缺仓库 clone 到对应编号目录，不新建空仓库。
- 两边结构不一致时，以对面整理过的结构为准，本机对齐后更新总账和 machines 清单。

## 隐私

- 项目绝对路径只写 `local.json`，发布仓不包含；
- `local.json` 已被 `.gitignore` 忽略，`release.py verify` 也会拦；
- 不要在 README 或 SKILL 里写具体用户目录；
- 相对仓库路径允许，绝对本机路径不允许进发布仓。

## 按 GitHub 账号对齐

```bash
python scripts/projects/registry.py github --user <github-user> --depth 5
```

输出三组：

- matched：本地 clone 与 GitHub 仓库匹配；
- github_not_local：账号里有、本地没 clone；
- local_not_on_github：本地有 remote，但不属于这个账号（旧账号、改名、已删）。

加 `--add` 把匹配项登记；`--replace` 用匹配项替换整张表（只保留该账号的仓库）。
GitHub 用公开 API，无 token 时有限流；失败不会改本地表。
