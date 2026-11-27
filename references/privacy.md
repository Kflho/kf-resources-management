# 隐私数据：哪些算、放哪、怎么查

服务侧会碰到比"通用密钥"更多的东西。本文只列**类别与出现位置**，扫描规则在
`kf-personal-development`（通用）+ 数据仓（service 专属）。

## 哪些算隐私

| 类别 | 例子 | 出现位置 |
|---|---|---|
| 凭据 | GitHub token、百度 `access_token`、Obsidian API key、`controller_secret`、cookie / session | 本机 `local.json`；**不进发布仓、不进文档** |
| 主机与账号 | 服务器 IP、SSH 用户、机器名（`<HOST>`）、邮箱 | 本机 `local.json` 的 `servers` / `relay`；跨机清单在数据仓 `machines/` |
| 本机路径 | `local.json` 里的安装目录、vault、包目录、`AppData` | 本机 `local.json`；文档写占位符 `<盘符>:\...` |
| 个人资料 | 书签、笔记正文、软件配置快照 | 数据仓 `bookmarks/`、`skills/`；主 vault 由 locally-save 搬运 |

规则：**实际值只进 `local.json` 或数据仓**；文档、代码、脚本示例一律占位符
（`<HOST>`、`<tencent-host>`、`<盘符>:\Users\<name>\...`）。

## 怎么查

```bash
python <kf-dev>/scripts/privacy/scan.py --root <仓库> \
  --rules <data_hub.path>/kf-personal-service/privacy/rules.json
```

- 规则文件只放**模式**（token 字段、Bearer、百度 token、公网 IP、邮箱、host 字段、
  SSH 目标、AppData 路径），外加 `skip`（文档占位 IP、`example.com`、公共 SSH 写法）；
- 行里有 `<...>` 占位符时整行跳过——写示例用尖括号就不会被当成泄露；
- 设计内的默认路径（`C:/kf-relay`、`D:/data`）进仓库根的 `.privacy-allow.json`；
- 有发现退出 1；`--json` 给出 `file/line/kind`，便于逐条确认。

## 边界

- 不扫历史（旧提交里的密钥要看 `gitleaks` / detect-secrets）；
- 不判真假：形似就报，确认是占位符就改写成 `<...>`；
- 扫描不能替代人工：**文档里的真实主机、邮箱、本机路径**这类语义泄露，扫不到就得靠读。
