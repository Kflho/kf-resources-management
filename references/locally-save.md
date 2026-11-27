# 两台电脑同步 Obsidian（locally-save）

locally-save 插件把 vault 导成一个 `.lsave` 包，另一台机器应用；不经过网络。
本文记**操作流程和校验**，插件源码在 `sc_03`，测试项目/测试分支/夹具在 `kf-development-project`。

## 概念

| 名词 | 含义 |
|---|---|
| full（完整包） | 整个 vault 的快照；第一次交接、或链断了才用；大（主库约 500 MB） |
| changes（更新包） | 从某个基准到当前状态的增量；日常交接用，小 |
| generation | 本机状态代数，每次应用/导出递增 |
| stateId | 整库的内容摘要（SHA-256），含 files / dirs / unverified 三个数 |
| baselineHash | 包的基准 SHA-256 值；通常要求 `baselineHash === 本机 fullHash`，另有两个放行例外（见下） |
| 包目录 | 通常是一个 git 仓库，两台机器各 clone 一份、互相 push/pull |

`baselineHash` 不是「只有严格相等才接」。`checkAncestor` 的放行条件：

- 严格相等：`baselineHash === 本机 bundle.fullHash`；
- **无需应用**：`targetBaselineHash === 本机 fullHash` —— 应用后与本机状态相同，没有要做的；
- **算一遍应用后的状态**：本机当前清单 + 包 entries − 包点名删除 的内容摘要 == 头部 `targetBaselineHash`，
  说明应用完就到达包声明的目标状态（两边在同一历史线上）；
- 兄弟包/危险包仍然拒收；都不匹配才需要导出 full 镜像。

`stateId` 里的 **dirs 也参与比较**：两边空目录集合不同就会一直 mismatch。测试 vault 必须用
`kf-development-project` 的 canonical 夹具 + `seed-test-vault.ps1 -Clean` 重建，不要各自维护。

## 首次交接

1. A 机打开 vault，确认配置（包目录、方向 both），导 **full**；
2. full 放到 A 的 shelf，`git add/commit/push`（`full/` 默认 gitignore，需临时 `-f` 或单独传）；
3. B 机 `git pull`，在插件里应用这个完整包（或 REST 触发，见下）；
4. 两边核对 `stateId / files / dirs`，一致后再开始日常增量。

主库全量大，**优先传更新包**；只有在 `checkAncestor` 拒收（基准不一致）时才用 full 镜像。

## 日常增量

1. A 改完笔记 → 导 changes（`<vault>-更新-<base>代到<target>代-状态<stateId>-<id>.lsave`）；
2. 包进 shelf，commit/push；
3. B `git pull` → 应用（both 方向会弹确认框，点「应用」）；
4. B 把应用后的 `generation / stateId / files / dirs` 发回 A 核对；
5. 反方向同理。

## shelf 与 git

- 主库里的包目录：`<hub>/locally-save/`，跟着 hub 的 `main` 一起同步（子目录形态，0.16 起）；
- 插件只提交本次改动：外层仓库里暂存或未跟踪的文件不受影响；拉取只允许快进，绝不 rebase hub；
- `full/` 由包目录的 `.gitignore` 排除，完整包仍走 U 盘 / 网盘；
- 测试用的包目录不再维护：插件 git 测试改用临时目录。

## 应用与确认（自动化要点）

- `both` 方向收到包默认弹 `ConfirmModal`「收到对方的更新包」，等用户选「应用 / 取消」；
  取消记 `dismissed`，重挑语义见下；
- **首选（`sc_03 f7d73c1` 起）**：打开设置里的 **`autoApplyIncomingBoth`**
  （设置 → 应用与校验，默认关）。打开后 `both` 接收更新包**不再弹确认**，直接按
  `mirror/alignToPeer` 应用；REST `sync-now` 不会挂起，适合无人值守。默认关时行为不变；
- 自动化取消：对窗口发 **Esc**（= 取消），不要靠坐标点按钮；
- 旧版没有 `autoApplyIncomingBoth` 时，才用 workaround：临时把 `syncDirection` 改
  `download`（仅拉、无确认）→ `app:reload`；reload 后设置没生效再重启 Obsidian → `sync-now`；
  应用完改回 `both`；
- `app:reload` 通常能让新设置生效（我们主库/测试库都验证过）；只有 reload 后行为没变才需要
  整个重启。

REST 端口按 vault 错开：测试库 `27124`、主库 `27126`；API key 只在插件
`data.json` 里，不贴消息、不进仓库。

## 校验

应用后读 `sync-state.json`：

```text
generation / stateId.id / stateId.files / stateId.dirs
bundle.fullGeneration / bundle.fullHash
bundleLog 最后几条（direction / base / target / stateId）
incoming 最后几条（applied / dismissed / needs-review）
```

`stateId` 与对面一致、dirs 相同才算对齐；`incoming` 里有 `needs-review` 要单独处理，
`dismissed` 是永久跳过（手动「立即同步」也不会重挑），想应用只能重新导出/手动应用。

## 失败日志（0.15.9 起）

插件目录下 `debug.log`（JSONL，单行一条，到 512KB 轮转成 `debug.log.1`），
路径 `<vault>/<configDir>/plugins/locally-save/debug.log`（默认 `.obsidian`）。
设置 → 应用与校验里的「调试日志」开关默认开。

- 接收更新包失败：`handleIncoming` catch 里写 `incoming.failed`，带包路径、方向；
  `sync-state.json` 的 `incoming[]` 那一条同时记 `error`（`name` / `message` / `stack`，
  截断 500 字），`needs-review` 也能直接看到原因；
- 另有 `window.error` / `unhandled.rejection` 两条记录，没有弹到界面上的异常也会写进去；
- 读法：REST `GET /vault/<configDir>/plugins/locally-save/debug.log`，
  或直接读文件；只看 `incoming.failed` 时用 `Select-String '"event":"incoming.failed"'`。
- 「基准不一致 / 算不出计划」这类 `planBundleApply` 抛错，message 和 stack 都在里面，
  要定位 283 那一类问题，先看这条日志，不要靠猜。

## 安全

- 应用前备份 `sync-state.json` + `data.json`（例如复制到 `temp/backup-<时间>/`）；
- 被覆盖/删除的文件进 vault 的 `.trash/locally-save`，能找回；
- 不同时在两台机器改同一批文件；主库应用选安静窗口；
- 用户 Obsidian 笔记只作为同步内容，不手工改、不重构；写操作只通过插件/同步流程；
- 不 force push、不删包（除非确认是测试用的包目录）。

## 分叉诊断：stateId / entries / 单写方

- `stateId` 只把「每个文件的 SHA-256 值 + 目录」排序后再做一次 sha256，**不掺 mtime**；
  `size/mtime` 只用于缓存失效，只有 >64MB / 读失败的文件才退化并计入 `unverified`。
- 包只带「导出方相对基准变过的 entries」；应用只覆盖 entries 点名的路径。
  本地自己改过、但不在 entries 里的文件会保留 —— 所以两边同时改同一批笔记时，
  应用对方包后 `stateId` 仍可能不同，这是**真实内容差异**，不是 mtime/假分叉。
- 典型现象：329 缺 `project note`；330 补上后修回；331/332 路径相同但
  `sk 02 note`、`project list` 内容不同，332 才修回。
- 约定：**同一同步周期只允许一个写方**；另一边只应用/回执；应用前关掉正在编辑的笔记；
  两边都必须改时，以最后一份包为准，或直接发 full。

## 故障对照

| 现象 | 原因 / 下一步 |
|---|---|
| 应用后 dirs 仍差几个 | 两边空目录集合不同；用 canonical 夹具重建，或查包 `emptyDirs` / `deliveredDirs` |
| 包一直不应用 | 基准 `baselineHash` 不匹配（先导 full）、或目标代数不大于本机、或被记 `dismissed` |
| 包校验失败（"文件可能在传输中损坏了"） | 包在某一段被当文本改了字节：git 包目录根要有 `.gitattributes`（`*.lsave binary`、`full/** -text`），网盘/聊天软件重传一份；`git check-attr binary -- <包>` 应显示 set |
| 只改 mtime 也出包 | 旧版 bug（`560a220` 已修：按内容 stateId 判断） |
| 两边状态数字一直差 | 队头有未消费的旧包/未应用通知；看 `incoming` 和 shelf `changes/` |
| shelf pull 冲突 | 两份包都保留（rename/rename），`git add -A` 后提交，不 force push |

## 已验过的行为（2026-10-22）

- **A（dismissed）**：both 模式接收更新包 → Esc 取消 → `incoming` 记 `dismissed`；
  再点「立即同步」不重挑，state 不变；
- **B（重复更新包）**：同起点导两次 changes，`changes/` 只留最新，旧包被
  `removeSupersededChanges` 清理；
- **C（删包后重新导出）**：删掉旧包后重新导出，生成新 bundleId/target，旧包不再存在；
- **主库对齐**：两台主库均 `277 / 3397906f04f2e675 / 10772 files / 244 dirs`，
  277 包的 8 个 `emptyDirs` 应用后补齐目录差。

