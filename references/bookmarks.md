# 书签（bookmarks）：Vivaldi ⇄ 私有仓 ⇄ Obsidian

> 入口：`scripts/config/bookmarks.py`；存储：私有数据仓
> `kf-personal-service/bookmarks/`（SKILL §8 的 hub）。
> 测试：`tests/test_bookmarks.py`。

## 目标流程

```text
Obsidian 笔记里编辑（## 目录 + - [标题](链接) / - 链接）
        │  note import（解析 Markdown）
        ▼
<hub>/kf-personal-service/bookmarks/machines/<HOST>.json   ← 每台机器只写自己这份
        │  merge（按归一化 URL 合并）
        ▼
<hub>/…/bookmarks/bookmarks.json                          ← 合并表，不手改
        │  note export / open
        ▼
Obsidian 笔记（初版/再生成）    Vivaldi 标签页（--new-window url…）
```

浏览器是 **Vivaldi**：直接读它自己的 `Bookmarks` JSON
（`<profile>/Bookmarks`，不用先导出 HTML），也能 `--profile` 指到别的实例。
Chromium 的 `date_added` 是 1601 起的微秒，脚本换算成 Unix 秒。

## 命令

```bash
# 浏览器 → 仓：不传文件就读 Vivaldi 当前书签
python scripts/config/bookmarks.py import
python scripts/config/bookmarks.py import some-export.html      # 旧 HTML 导出也认
python scripts/config/bookmarks.py import ~/notes/bookmarks.md  # Obsidian 笔记

# 仓 → 笔记（--machine 保留浏览器里的目录顺序；不传则用合并表）
python scripts/config/bookmarks.py note export --machine KAHOTAT
python scripts/config/bookmarks.py note import                 # 笔记改动收回仓
python scripts/config/bookmarks.py note open                   # 用 Obsidian 打开

# 仓 → 浏览器：把一组书签当标签页打开
python scripts/config/bookmarks.py open --folder 字幕 --limit 15

python scripts/config/bookmarks.py status
python scripts/config/bookmarks.py export --format html --out bookmarks.html
python scripts/config/bookmarks.py sync --message "bookmarks: <HOST>"
```

本机设置写在 `local.json`（gitignored，不随仓库走）：

```json
"bookmarks": {
  "vault": "<obsidian vault 根目录>",
  "note": "profession/…/vivaldi/bookmark list.md",
  "vivaldi_profile": "<Vivaldi profile 目录>",
  "readonly_vault": "true"
}
```

以 Obsidian 里你自己整理的书签笔记为权威副本，路径同时登记在 kf-service 的
`notes.py`（名字 `bookmark list`，tags `bookmarks,browser`）：`notes.py context`
能看到它；它是按需文档，同步书签前先 `check`，报 `changed` 就先 `show` 再按它执行。

**vault 只读的机器**（vault 从另一台机器同步、本机写入会被回退）把
`readonly_vault` 设成 `"true"`：`note export` 会直接把 Markdown 打到 stdout，
不碰 vault。要落盘就在权威副本那台机器上写，或者把这份文本复制过去。
`note import` 仍然读得到笔记（读不需要写权限）。

## 笔记格式

- 每个目录一个标题，层级就是书签的目录深度（`##` 起）；每个目录只出现一次，
  先写本目录的链接，再进子目录；
- 有真标题：`- [标题](链接)`；没标题、或标题本身就是网址（浏览器在页面没 title
  时会拿地址当标题，例如 `pixiv.net/`）：直接写链接；
- 列表符 `-` / `*` / `+` 和有序编号 `1.` / `1)` 都认——用户在 Obsidian 里改成
  编号列表不影响 `note import`；
- 默认不加任何说明文字、不加 frontmatter（需要时 `export --title/instructions` 可开）。

## 三条合并规则

1. **URL 是身份**：去掉 scheme、`www.`、尾斜杠和 `utm_*`/`spm`/`from` 这类跟踪参数，
   query 排序。同一页面在两台机器各存一次只留一条。
2. **`ADD_DATE` 取最早**。缺失（0）不参与比较，不会盖掉真实时间。
3. **标题/目录先到先得**：已有非空值不覆盖，后导入的机器不会改乱已整理好的目录。

合并结果按 `(folder, title, url)` 排序，便于 diff。

## 隐私边界

- 仓库是 **private**，但仍按「清单仓不放隐私内容」的规矩办：不存图标、
  不存绝对路径（`source` 只留文件名）、不存账号 token；
- 书签只进私有数据仓，**不进任何 skill 发布仓**；
- 提交前可跑 `kf-personal-development` 的 `scripts/privacy/scan.py --staged`。

## 写回 Vivaldi（书签栏 / 快速拨号）

```bash
python scripts/config/bookmarks.py vivaldi verify
python scripts/config/bookmarks.py vivaldi build --out new_Bookmarks.json
python scripts/config/bookmarks.py vivaldi apply --close --disable-sync
```

写回的是 `<profile>/Bookmarks`（Chromium JSON）。三条硬约束，都是实测踩出来的：

1. **checksum 必须对**。算法是 `id + 标题(UTF-16LE) + 类型(+url)` 的前序拼接取
   MD5；Vivaldi 把 `trash` 也一起算（上游 Chromium 只算 `bookmark_bar` /
   `other` / `synced`）。算错会被 Vivaldi 当损坏文件、从 `.bak` 回退。
   `vivaldi verify` 先用现有文件验证：`stored == computed` 才说明算法对得上。
2. **书签同步开着时不能写**。实测：写入并重启后，云端把旧书签合并回来——旧书签
   进了回收站、书签栏多出 21 条重复。`apply` 检测 `Preferences.sync.bookmarks`
   为真时拒绝；要硬写用 `--disable-sync`（只关书签同步）或 `--ignore-sync`。
3. **快速拨号靠 meta**：书签栏里那个文件夹带 `meta_info: {"Speeddial": "true"}`，
   Vivaldi 才会把它当快速拨号组。结构上只保留这一个文件夹，`other` / `synced` /
   回收站清空，等于「删除老版本所有书签」。

`apply` 会先优雅关闭 Vivaldi（`--close`），备份到
`<skill>/dev/backups/vivaldi/<时间戳>/`（`Bookmarks`、`Bookmarks.bak`、
`Preferences`），再原子替换并复算校验。

> 同步关掉后不会自己恢复。以后要重新打开，注意云端旧集合会再合并一次——
> 要么先在别的设备/云端清干净，要么接受重复后手动整理。

## 已知限制

- Vivaldi 的 `trash` 不导入（那是回收站，不是书签）；写回时整个清空；
- 写回不支持增量：`apply` 是按笔记整棵树替换书签栏（这正是「删掉老版本所有书签」
  要的效果），不做合并；
- 只解析 `NETSCAPE-Bookmark-file-1` 的 HTML 导出；浏览器私有 JSON 库不读；
- 不做标签（tag），也不支持拖放整理——整理在 Obsidian 或浏览器里做，仓库只做同步与检索。
