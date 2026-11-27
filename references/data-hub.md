# 跨机器项目清单：私有数据仓

私有数据仓是**传输层**，不是代码仓，也不是备份仓。它只放两台电脑之间需要对齐的
项目清单和可移植配置；源码留在各自仓库，大文件/密钥不进来。

仓库地址不硬编码在技能里：本机 clone 路径填 `local.json` 的 `data_hub.path`，
`owner/repo` 填 `data_hub.repo`（`gh_mailbox.py` 用它）。

## 目录

```text
<hub>/
  README.md
  kf-personal-service/       # 每个 skill 一个顶层文件夹
    machines/
      README.md
      <HOST>.json            # 每台电脑只写自己的文件：项目清单
    skills/
      <SKILL>/<HOST>.json    # 每台电脑只写自己的文件：该 skill 的 local.json
    config/
      <HOST>.json            # 旧布局，只读兼容，新布局导出后可以删
```

一台机器一个文件，文件名用 `COMPUTERNAME`。不要改另一台机器的文件。
其他 skill 接入时建自己的 `<skill>/` 目录，不混放；skill 配置由
kf-personal-service 统一管，集中放在 `skills/` 下。

## 开发/测试项目仓（`kf-development-project`）

个人数据仓管「个人使用数据」；**测试项目（含测试代码）、测试夹具和测试用的包目录**放另一个私有仓
`<owner>/kf-development-project`，按项目存放，两边不混：

| 仓 | 放什么 | 不放什么 |
|---|---|---|
| `kf-personal-data`（个人数据仓） | `machines/`、`skills/`、`bookmarks/`、`sync/`；`kf-personal-development/test-status/`（可选的测试清单机器副本）；主 vault 的包目录 `locally-save/`（hub 工作区子目录，跟随 `main`） | 测试项目、测试包、夹具、原始日志 |
| `kf-development-project`（测试项目仓） | 每个项目一个 `<项目名>-test/` 目录（测试代码 + 夹具 + 测试用的包目录），另有 `utils/`、`logs/` | 个人配置、凭据、API key、cert、真实笔记、主 vault 状态 |

本机实际有哪些测试项目、各自对应哪个项目，查项目总账
（`scripts/projects/registry.py status` 的 `test_repo`），**本文档不维护名单**：
名单写在这里必然过期，写漏一次就会让人以为某个项目没有测试仓。

- **测试清单是内容，不是测试数据**：哪个项目、哪一版、测了哪些方面、结论和缺口，
  写在各项目自己的 Obsidian 笔记 `# tests` 一节（跟着 vault 同步）；跨项目总览在
  `study/personal/theory/data/project list.md` 的 `## tests`。
  `kf-personal-data` 里的 `test-status/<HOST>.json` 只是可选的机器副本（给
  `<kf-dev>/scripts/test_status.py` 校验），清单本身以笔记为准。
  测试代码、夹具、测试包、原始日志仍放 `kf-development-project`。写法见
  `kf-personal-development` 仓的 `references/post-dev-check.md#记录测试状态`。

### 分支与结构

```text
main:
  README.md / .gitignore
  <项目名>-test/                  # 每个项目一个：test/ 代码 + 本机 src/node_modules 链接
  utils/test-vault/           # canonical 测试 vault 夹具：10 篇 note + notes/empty-folder
  utils/seed-test-vault.ps1    # -Clean 重建目标 vault，不复制 README
  logs/                          # 开发日志；大文件 gitignore
obsidian/lsave-test:             # orphan 分支，只做 locally-save 测试用的包目录
  .gitignore                     # full/
  README.md
```

- **测试按项目存放**：每个项目一个顶层目录（`<项目名>-test/`），测试代码、夹具、测试用的包目录、
  `full/` 都在这个仓里；目录与项目的对应关系记在项目总账的 `test_repo` 字段；
- `src` / `node_modules` 只是本机链接，不进 git；测试项目运行 `npm test` 前先建链接；
- 测试用的包目录：clone 该分支，clone 根直接做插件 `bundleDir`；`full/` 只留本机、不进 git；
- 测试 vault：用 `seed-test-vault.ps1 -Target <vault> -Clean` 从夹具重建，两边夹具必须一致
  （否则 `stateId` 的 `dirs` 不一致）；
- 初始完整包按需在机器之间单独传，不放进仓库；
- 夹具是仓库里的测试数据，不是用户笔记；用户 Obsidian 笔记仍然只读。

导出、交接、应用、校验和 REST 自动化的操作细节见
[locally-save.md](locally-save.md)。

## 清单格式

```json
{
  "schema_version": 1,
  "machine": "<HOST>",
  "generated_at": "2026-10-20T12:00:00",
  "projects": [
    {
      "name": "Matlab_project",
      "local_dir": "Matlab",
      "remote": "https://github.com/<owner>/<project>.git",
      "role": "user-facing",
      "tags": ["matlab"]
    }
  ],
  "notes": "local_dir is the folder name only; paths are machine-specific"
}
```

- `name` = 项目名 = 仓库名（技能仓库去 `-skill`）；
- `local_dir` 只写文件夹名，不写绝对路径；
- `remote` 写仓库地址，方便另一台电脑对齐；
- 不放 token、密钥、本机绝对路径、文件内容；
- 需要传大文件时用网盘/Git LFS，不要写进私有数据仓。

## 导出/校验

```bash
# 在本机生成 kf-personal-service/machines/<HOST>.json
python scripts/projects/hub.py export

# 校验 schema、机器路径
python scripts/projects/hub.py validate \
  kf-personal-service/machines/<HOST>.json
```

导出内容来自项目总账 `local.json` 的 `projects.items`；没有登记的本地-only
项目不会出现，需要先 `registry.py add`（仅有 remote 的）。

## 跨机器比对

```bash
python scripts/projects/hub.py compare            # 读 machines/*.json
python scripts/projects/hub.py compare --json
```

输出三类：

- `missing on <HOST>`：某台机器缺的仓库，按 `remote` clone 到对应编号目录，不要新建空仓库；
- `remote mismatch`：同名指向不同仓库，先核对再改，不要直接覆盖本地；
- `same repo, different URL form`：同一仓库的 ssh / https 写法差异，不用处理。

对齐顺序：先 `pull` 拿最新清单 → `compare` → 补齐缺的仓库 → 再导出自己的清单。

## Obsidian 文档分工（谁记什么）

主 vault 里每类笔记只记一类事情，别互相串：

| 笔记 | 记什么 | 怎么产出 |
|---|---|---|
| `project list.md` | 项目列表（每个项目一行，勾选状态）；末尾 `## tests` 是跨项目测试总览 | 项目目标 / 功能由 `kf-personal-development` 的流程落地；测试总览只写结论与数量 |
| `<项目> note.md` | 该项目的目标、功能、依赖 + `# tests` 覆盖清单 | 同上；测试清单按本项目的目标 / 功能 / 问题 / 模糊点覆盖，不是照抄别人的 |
| `note note.md` | 文字规范（写作口径、模板层级） | 用户维护，agent 只读 |
| `data note.md` | 文件与数据管理规则（分层、网盘 / git 分配） | 用户维护；实际数据位置以 `notes` 登记为准 |
| `skill note.md` | kf 调研过的 skill 与选型结论 | 用户维护；skill 台账另见 `skills.py` |

- **文本一致性**（用词、标题层级、非备忘录、链接可解析）按 `kf-personal-development`
  的 `<kf-dev>/references/post-dev-check.md` 执行，在这里不重复写一套；
- **测试清单以项目笔记为准**（`# tests` 一节），`kf-personal-data` 里的 JSON 只是可选的机器副本；
- 项目名、仓库名用连字符写法（`kf-personal-service`、`kf-development-project`），
  笔记里不写下划线变体。

## 两台电脑协作流程

```text
1. 拉取（GitHub 走 VPN 任务表）
   python scripts/vpn/tasks.py run -- git -C <hub> fetch origin main
   git -C <hub> reset --hard FETCH_HEAD        # 单提交仓库，不做 merge / rebase
   git -C <hub> gc --prune=now                  # 换掉旧提交后回收本机旧对象
2. 导出自己的清单
   python scripts/projects/hub.py export --output \
     <hub>/kf-personal-service/machines/<HOST>.json
3. 校验
   python scripts/projects/hub.py validate \
     <hub>/kf-personal-service/machines/<HOST>.json
4. 发布（只提交自己的文件，压成一个提交再推）
   python scripts/projects/hub_publish.py --hub <hub> -m "inventory: <HOST>" \
     --path kf-personal-service/machines/<HOST>.json
5. 另一台机器 fetch + reset --hard 后先 compare，补齐缺的仓库，
   按 name/remote 对齐，不直接套用 local_dir
```

- **数据仓只存数据，不留历史**：写入一律走 `hub_publish.py`（无父提交 +
  `push --force-with-lease`），不要用 `git commit` + `git push` 普通提交 ——
  2026-11-23 实测那样攒到了 1.48 GiB，拉取超时导致两台机器三个多小时没有同步；
- `hub.py export --publish` 与 `hub.py skills export --publish` 一条命令做完「写文件 +
  发布」，内部就是调 `hub_publish.py`；写别的东西（测试记录等）也用它，
  不要另写一份提交逻辑；
- 拉取用 `fetch` + `reset --hard`，因为本地和远端只有一个无父提交，没有共同祖先；
- 远程被别的机器抢先推过时 `hub_publish.py` 会取回远端重试一次，
  只提交自己的路径，不覆盖对方的文件；
- 同一台机器只有一个写入者；
- 只同步“清单/状态”，不同步工作区文件。

## skill 配置同步（`hub.py skills`）

每个 skill 的两台机器差异都在它自己的 `local.json` 里（端口、VPN 任务表、
服务器地址、项目路径）。这些文件不进发布仓，走私有数据仓：

```bash
python scripts/projects/hub.py skills list       # 装了哪些、谁有 local.json、hub 有哪些快照
python scripts/projects/hub.py skills export     # 写 <hub>/kf-personal-service/skills/<SKILL>/<HOST>.json
python scripts/projects/hub.py skills export --skill kf-personal-service
python scripts/projects/hub.py skills diff       # 本机 vs 各机器快照
python scripts/projects/hub.py skills import --from <HOST>          # 只预览
python scripts/projects/hub.py skills import --from <HOST> --apply  # 写回 local.json
python scripts/projects/hub.py skills validate <file-or-dir>
```

文件格式：

```json
{
  "schema_version": 1,
  "kind": "skill-config",
  "skill": "kf-personal-service",
  "machine": "<HOST>",
  "generated_at": "2026-10-22T12:00:00",
  "source": "local.json",
  "config": {"vpn": {"proxy_port": 7890}},
  "machine_local": ["vpn.flclash_exe", "projects.items[].path"],
  "secrets_omitted": ["vpn.controller_secret"]
}
```

- skill 名单来自 `skills.harness_dirs`（默认 `~/.dsh/skills`）；配置文件名默认
  `local.json`，可用 `skills.configs` 改成别的相对路径；
- 没有配置文件的 skill（第三方复制件）跳过，不凭空造配置；
- 路径类值（`vpn.flclash_exe`、`projects.roots`、`notes.docs[].path`）导出成
  `<machine-local>`，只记键名，导入时保留本机原值；
- 凭据类键（`*secret*`、`*token*`、`*password*`、`*api_key*`）值不出去，只记
  键名，导入后按 `secrets_to_fill` 手动填；
- 字典递归合并；列表里只要有一个占位符就整条跳过（位置会错位，不安全）；
- 旧布局 `config/<HOST>.json` 仍能被 `skills diff` / `skills import` 读到
  （标 `legacy`），两台机器都导出新布局后可以删；
- `import` 默认只预览，加 `--apply` 才写；写完执行一次
  `python <kf-dev>/scripts/privacy/scan.py --root <hub>`。

## 网盘：去重与删除（必须报告）

`scripts/data/netdisk.py` 管百度网盘开放平台。删除只能进回收站，属于不可逆操作，
所以规则硬编码：**任何去重和删除都先出清单、再逐条回报，并留审计日志**。

- 判重三条同时成立才算重复：完整 32 位 md5、同大小、**同一目录**；
- 服务端打码的 md5（含非十六进制字符）一律跳过，不参与判重；跨目录的相同文件
  视为用户有意保留的副本，同样跳过；
- `dups` 只写清单（`temp/nd_dups.md` 给人看、`temp/nd_plan.json` 给 `rm` 用），
  命令输出明确写「不会删除任何文件」；
- `rm` 默认只预览；加 `--yes` 才执行，执行后逐条打印 ok / 失败 errno，并把每个路径
  写成 `temp/netdisk-audit/<时间>.jsonl`（gitignored）；一次超过 200 个还需 `--force`；
- 拒绝根目录、目录路径（以 `/` 结尾）、含通配符或反斜杠的路径；
- `quota` / `ls` / `tree` / `trash` 是只读命令，随时可跑；`trash` 用来看回收站。

这套规则来自 2026-11 的一次事故：按打码 md5 分组后批量删除，把一批备份
（digital clock、美声时计、油猴插件、Vivaldi 主题）当成重复删掉，且删除没有任何记录。

## 隐私

私库仍是 Git 历史，写入前检查：

```bash
python <kf-dev>/scripts/privacy/scan.py --root <hub>
```

不要把 `local.json`、token、绝对路径、客户数据、原始素材提交进去。
配置导出文件只放可移植值，机器路径以 `<machine-local>` 占位。
