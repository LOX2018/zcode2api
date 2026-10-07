# zcode2api (zcode-hub)

**ZCode 账号运营 + 双协议网关一体机**：把池内 ZCode Coding Plan / Start Plan / API Key 账号额度，统一转换为标准的
**Anthropic Messages API**（`/v1/messages`）与 **OpenAI Chat Completions API**（`/v1/chat/completions`），向 Claude Code、Cline、Codex CLI、Chatbox、NextChat 等各类 Agent 与客户端提供高可用 API 服务。

自带一套完整的账号运营控制台：多账号池轮询、高可信成套桌面指纹仿真、官方首启安装序仿真、单账号并发控制、429/5xx/3012 阶梯容灾、请求全景监控、限时套餐自动与手动领取、OAuth 免密登录入池，以及阿里云无痕验证自动求解——开箱即用，自托管部署。

两种用法，按需选一种：

- **Windows 启动包**（推荐给不折腾的人）：装完双击图标，托盘常驻起服务，**不需要 Python、不需要源码、不需要 `npm install`**。见 [Windows 启动包](#windows-启动包)，二进制在 [Releases](https://github.com/LOX2018/zcode2api/releases)。
- **源码运行**（开发 / Linux / macOS）：见 [快速开始](#快速开始)。

---

## 核心能力

- **双协议兼容网关**：原生支持 `POST /v1/messages`（Anthropic Messages 协议，支持流式 SSE）与 `POST /v1/chat/completions`（OpenAI 格式双向翻译，支持流式与非流式），并提供 `GET /v1/models` 模型列表。
- **高可信桌面指纹池（一号一台）**：入池自动生成加权桌面成套 SKU（Mac Sequoia/Tahoe、Win11 主流分辨率等），每账号独立全新 UUIDv4 `device_mid`，杜绝宿主机云 Linux 内核共享导致的账号集群关联风险。
- **官方首启安装序仿真**：入池与指纹轮换自动执行 `client/configs` 与激活事件上报（`app_launch` / `app_daily_active`），持久化 `install_id` 与 `installed_at`。
- **反代与 CDN 基础设施头深度清洗**：自动剔除 `x-forwarded-*`、`forwarded`、`cf-*`、`cdn-loop`、`x-real-ip`、`via`、`cookie`、`referer`、`origin`、`accept-language` 等网络中间件头部，杜绝部署网络拓扑泄露与客户端伪装冲突。
- **单账号精细化并发控制**：支持单账号并发上限（默认 2，可在后台运行时热改，0 不限）。满并发账号智能跳过，不计入重试轮数；客户端断开、异常与等待期间即时释放并发槽位。
- **阶梯式故障转移与容灾策略**：
  - **429 智能退避重试**：优先根据上游 `Retry-After` 原地等待重试（等待期间释放并发槽位，防止阻塞）；重试耗尽无损换号且**不冷却账号**；Plan 通道 429 耗尽且同账号有 API Key 时自动切 `api.z.ai` 回退通道。
  - **5xx 熔断冷却**：重试 3 次后进入 300s 冷却，自动切下一个可用账号。
  - **3012/405 真风控隔离**：识别 "unusual activity" 立即标记 `DISABLED` 禁用账号保护资产，停止自动化空转，支持后台人工确认后一键启用恢复。
  - **401/403 鉴权失效标记**：非验证码的凭证失效直接标 `INVALID`。
- **全景请求监控与安全防护**：
  - 请求监控（控制台 `#usage` 页签）：实时展示在飞请求、历史请求耗时、状态码、模型、账号及错误堆栈。
  - 后台暴力破解防护：客户端 IP 连续登录失败达上限自动锁定 5 分钟。
  - 设置密钥脱敏回显：GET 设置接口密钥自动打码，保障安全性。
- **限时套餐领取（双轨机制）**：
  - 自动领取：入池即自动领取全部可领套餐，并有可配置的周期自动领取轮（默认 3600 秒，后台设置页可改，0 = 关闭），持续盯当期活动赠送。
  - 领取语义对齐官方 3.11.2：成功回执携带上游 `server_time` 与套餐窗口；1005「名额用完」附带名额恢复时间 `next_at`，领取轮按其退避（等待期不重试领取、不耗验证码，`next_at` 过后自动恢复）。
  - 浏览器过码：支持在前端调起阿里云滑块验证码进行手动领取，应对极严风控环境。
- **OAuth CLI 免密登录**：
  - 提供海外邮箱与 Google 登录直达引导卡片，解决上游 `chat.z.ai/auth` 隐藏第三方登录按钮问题。
  - 严格遵循官方 CLI 的干净请求头规范，JWT 授权入池秒级完成，API Key 兑换异步后台回填。
  - 跟进官方新协议：采用 init 响应服务端下发的 `poll_token` 轮询（未下发时回落自造，兼容旧协议）；poll 4xx 承载的 `code=3004` 明确映射为会话过期。

## 支持的账号类型

| Provider | 模式 | 说明 |
|----------|------|------|
| `zai` | `jwt` | Coding Plan 额度（Plan 通道，需无痕验证码），自动续领活动套餐 |
| `zai` | `apiKey` | Z.AI API Key（回退通道，免验证码） |
| `bigmodel` | `apiKey` | 智谱开放平台（Anthropic 兼容端点） |

> 一个 Z.AI 账号可同时持有 JWT 与 API Key：OAuth 登录后会一并入池，JWT 额度告罄或 429 耗尽时自动无缝走 API Key 回退通道。

## 快速开始

需要 Python 3.11+。虚拟环境里的可执行文件位置按平台不同：

```bash
# macOS / Linux
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # 按需修改密钥、端口等
.venv/bin/python cli.py serve   # 启动网关 + 后台 UI（默认 http://0.0.0.0:3000）
```

```powershell
# Windows（PowerShell；注意是 Scripts 不是 bin）
py -3.12 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe cli.py serve
```

> 用 `uv` 也一样：`uv sync` 或 `uv pip install -r requirements.txt --python .venv\Scripts\python.exe`。
> 本项目仓库内的 venv 由 uv 创建（Python 3.12），uv 建的 venv 默认不带 pip。

- 后台管理：`http://127.0.0.1:3000/admin/login`（登录**只需密码，没有用户名**；初始密码见 `.env` 的 `ZCODE_ADMIN_KEY`，首次启动写库后以库为准）
- 对话端点：
  - Anthropic 协议：`POST http://127.0.0.1:3000/v1/messages`（兼容 Claude Code、Cline）
  - OpenAI 协议：`POST http://127.0.0.1:3000/v1/chat/completions`（兼容 OpenAI 客户端）
  - 模型列表：`GET http://127.0.0.1:3000/v1/models`
  - 探活端点：`GET http://127.0.0.1:3000/meta`

> 使用 Z.AI **JWT 模式**需要 Node.js 求解验证码，首次先执行：`cd captcha_node && npm install`（依赖 `puppeteer-core`，还需要本机有 Chrome 或 Edge，见[验证码求解](#阿里云无痕验证求解)）。

## Windows 启动包

Python 侧全部冻结进 exe，`frontend/` 与 `captcha_node/` 作为 exe 同级资源随包（静态页与 Node 子进程必须读真实磁盘文件）。装完双击即起服务。

**下载**（[Releases](https://github.com/LOX2018/zcode2api/releases)，同一份内容的两种形态）：

| 产物 | 形态 | 体积 |
|------|------|------|
| `ZCodeHub-Setup-<ver>.exe` | Inno Setup 安装器：开始菜单/桌面快捷方式、可选开机自启、带卸载器 | 约 27 MB（装后约 113 MB，其中 `node_modules` 占 58 MB）|
| `ZCodeHub-<ver>-portable.zip` | 绿色目录，解压即用，不建快捷方式 | 约 40 MB |

**托盘是默认入口**（`ZCodeHubTray.exe`）：没有黑色窗口常驻，误点也不会把服务带走。右键托盘图标：

| 菜单项 | 说明 |
|--------|------|
| 打开管理后台 | 默认动作，**双击图标即触发** |
| 启动服务 / 重启服务 / 停止服务 | 按当前状态置灰；服务是托盘拉起的子进程 |
| 打开日志 | `data\hub.log` |
| 退出（同时停止服务） | 卸载或整目录搬走前请先点这里 |

可靠性：服务进程崩了自动重启；**180 秒内连续 3 次启动失败则停止重试**并把原因写进日志，不再空转；端口已被外部实例占用时只观察、不接管，避免两个实例互踩同一份账号池。

`ZCodeHub.exe`（控制台版）保留为**调试入口**：`serve --open-browser` 起服务并弹浏览器，窗口开着=服务在跑，关窗=停服。CLI 子命令与源码版一一对应，把 `python cli.py` 换成 `ZCodeHub.exe` 即可。

**依赖**：

- 不需要 Python、不需要源码、不需要 `npm install`——都冻结或随包了。
- JWT 模式的验证码求解要调用系统 **Node.js**（`captcha_node\solver_pw.js`）。没装 Node 时网关照常起、API Key 通道照常可用，只是 JWT 取 token 与自动领取套餐不可用。`node` 不在 PATH 就填 `ZCODE_NODE_PATH`。
- 需要 **Chrome 或 Edge**（Win10/11 自带 Edge 即满足）：按 Program Files / Program Files (x86) / LOCALAPPDATA 顺序自动探测，`ZCODE_CHROMIUM_PATH` 可覆盖，探测结果看 `ZCodeHub.exe status` 的「浏览器」一行。
- 出网：验证码页从阿里云 CDN（`o.alicdn.com`）加载官方 SDK，求解阶段必须能访问公网。

**密码与暴露面**：随包 `.env` 是安全默认——`ZCODE_HOST=127.0.0.1`（只本机）+ `ZCODE_ADMIN_KEY=zcode`（**随包默认口令，对外开放前务必改掉**）。后台登录只需密码、没有用户名；首次启动把密码写进库，之后以库为准，再改 `.env` 不生效，请用 `ZCodeHub.exe set-admin-key <新密码>`。把 `ZCODE_HOST` 改成 `0.0.0.0` 等于把整套后台和号池交给整个局域网——先换强密码再开。

**数据与迁移**：`ZCODE_DATA_DIR=data` 按 **exe 所在目录**解析（不是当前目录）。目录里 `accounts.db`（账号凭证、后台密码、网关 Key、用量）、`device_mid`（本机设备指纹）、`hub.log`。换机：`ZCodeHub.exe export accounts.json` 后在新机 `import accounts.json`，或整目录拷过去（拷之前先停服务，避免带走半截 WAL）。

**卸载**：控制面板 →「ZCode Hub」→ 卸载。程序文件与快捷方式删掉，**`data\` 保留**（里面有你的凭证，防误删）；确认不要了再手工删整个安装目录。

**两个边界，如实说明**：

1. 「不含源码」只覆盖 Python 侧：`frontend/` 的 HTML/JS 与 `captcha_node/` 的 `solver_pw.js`、`node_modules/` 仍是磁盘明文，需要这些也不可读得另行做 JS 混淆打包。
2. 本项目 license 为 `AGPL-3.0-only`：自己换机使用无碍；把这个安装包分发给他人（尤其对外提供 API 服务）须同时提供对应源码的获取途径。

**从源码构建**（Windows，PowerShell，在 `repo/` 下执行）：

```powershell
.\packaging\build.ps1                  # 冻结 exe → 组装 stage → 编译安装器
.\packaging\build.ps1 -Zip             # 顺带压一份绿色 zip
.\packaging\build.ps1 -SkipInstaller   # 只要 exe + stage，不碰 Inno Setup
.\packaging\build.ps1 -Clean           # 先清上一次产物
```

前置：`repo\.venv`（Python 3.11+，脚本会自装 PyInstaller）与 Inno Setup 6（`winget install JRSoftware.InnoSetup`）。产物落在 `packaging\output\`。

入包的 `data\` 默认是**空目录**；`-WithExistingData` 会把本机 `repo\data\` 一起打进去（含真实账号凭证，脚本会明确警告，切勿分发）。两处不显然的编码约束：`build.ps1` 必须保持纯 ASCII——PowerShell 5.1 按 cp936 读无 BOM 脚本，中文注释末尾的前导字节会吞掉换行、把下一行代码并入注释（用 `packaging\check_ascii.ps1` 自检）；`.iss` 反过来必须带 BOM，否则中文向导乱码。

## 快速上手一个账号

下面 `$PY` 指本平台解释器：macOS/Linux 是 `.venv/bin/python`，Windows 是 `.venv\Scripts\python.exe`；
Windows 启动包则把 `cli.py` 换成 `ZCodeHub.exe`。

```bash
# 方式一：OAuth 登录（免密，自动入池 + 自动兑换回退 Key，--no-browser 只打印链接）
$PY cli.py login zai

# 方式二：手动加入现成凭证（JWT 三段点分 或 API Key）
$PY cli.py add-account zai 名称 <jwt|key>
```

两种方式入池后都会自动跑一遍安装序（`client/configs` + 激活上报）并对 JWT 账号领取当期套餐。

## 命令行

```bash
python cli.py serve [--port 3000] [--open-browser]   启动网关 + 后台 UI
python cli.py login zai [--no-browser]   通过 OAuth 登录 Z.AI 并自动加入账号池
python cli.py add-account <zai|bigmodel> <name> <jwt|key>   添加轮询账号
python cli.py accounts [zai|bigmodel]    查看账号列表
python cli.py remove-account <provider> <id|name>   删除账号
python cli.py quota                      查看各账号实时额度
python cli.py status                     配置概览 + 随包资源自检
python cli.py set-admin-key <key>        设置后台密码
python cli.py export [file]              导出账号
python cli.py import <file>              导入账号
```

Windows 启动包里的 `ZCodeHub.exe` / `ZCodeHubTray.exe` 走同一套子命令（`ZCodeHub.exe status`、`ZCodeHub.exe accounts`）。
冻结态的 `status` 会额外自检四项并直接指出缺什么：前端目录、求解器脚本、浏览器探测结果、`zcode_system.json` 的段数。

## 后台 UI

单页控制台 `/admin/console`（页签用 URL hash 定位，可直接分享/收藏 `#accounts` 这类深链）：

| 页签 | 说明 |
|------|------|
| `#overview` 概览 | 号池健康、请求与成功率、token 与参考费用、上游模型与准入状态 |
| `#accounts` 账号池与授权 | 新增/导入/导出、启用禁用、**每号一张卡**（额度窗口 + 最近 20 次结果 + 累计）、套餐领取（自动/手动）、OAuth 授权入池 |
| `#access` 客户端接入 | Base URL 与两个对话端点、`/v1/models`、可复制的最小 curl、网关 API Key 的生成与删除 |
| `#models` 模型与计价 | 上游模型芯片墙、模型白名单（模式 + 手填名单）、计价参考表与开放平台价目折叠区 |
| `#usage` 用量与明细 | 实时请求耗时、状态码、上游模型、使用账号、失败原因，按账号/Key/模型聚合 |
| `#system` 系统参数 | 后台登录口令、额度刷新间隔、单账号并发上限、套餐自动领取轮、价目拉取间隔 |
| `#activity` 活动日志 | 服务启停、账号增删改、参数与 Key 变更、账号失效；落库存档，重启不清零，满 2000 条丢最旧 |

| 页面 | 说明 |
|------|------|
| `/admin/login` | 后台登录（Bearer 密钥鉴权，连续失败 IP 锁定防护，凭证加密存于浏览器 localStorage）|
| `/admin/accounts` → `#accounts`、`/admin/monitoring` → `#usage`、`/admin/settings` → `#system` | 旧多页入口，307 跳转到控制台对应页签 |

### 两个模型口径，别混

上游 ZCode **实际提供**什么模型，与白名单**放行**什么模型，是两件事，后台分开摆：

- 能力口径 = `GET /admin/api/settings` 的 `model_capability`：2026-09-05 实测钉定的常量
  （`GLM-5.3-Flash`、`GLM-5.3`，其余模型上游回 3006）∪ 由号池额度 `show_name` 推导出的权益。
  芯片墙用它，所以手填名单只写一个名字时，界面不会宣称「上游只有一个模型」。
- 策略口径 = `model_whitelist_effective`：`manual`（默认，只认手填）/ `static` / `dynamic` / `hybrid`
  四档，决定进入网关的请求放行哪些模型名。名单外直接拒，不占账号；上游真回 3006 则把
  「这个号不含这个模型」钉在该号上（默认 7 天自动失效），选号时跳过它而不判废整号。

### 「元」不是账单

定价数据源指向**智谱开放平台**的公开定价页，那是另一个产品的价目；ZCode 上游只给
`total_units / used_units / remaining_units`，从不按货币出账。因此后台里出现的每一个「元」
都是拿开放平台参考价折算的**估算**（芯片徽章前缀「参考」、费用卡写明「非 ZCode 账单」），
价目表里上游并不提供的模型折在「开放平台价目参考」区。定时拉取默认关闭
（`pricing_pull_interval = 0`），需要时点「立即拉取定价」；这条路径只读公开文档站、
不携带任何账号身份，失败一律保留上一份。

概览的流量与成本取自内存环形缓冲（最近 500 条，重启清零）——不是历史累计；要长期统计请外接采集。
各页签轮询遵循「切走即停」：离开页签或页面不可见时清掉定时器，回到前台自动续上；
OAuth 授权的轮询也挂在页签上，切走会停并提示。

账号池页实时展示每个账号的**状态（正常 / 额度用完 / 限流 / 异常 / 禁用）**、各模型剩余额度、
调用与失败次数；并提供「手动刷新额度」与「批量领取套餐」入口。

## 多账号轮询与故障转移

- 在账号池粘贴 Coding Plan JWT（3 段点分）或 API Key，每行一个即可加入轮询。
- 网关每次请求选择下一个「可用」账号，跳过用完 / 限流 / 异常 / 禁用的账号。
- **单账号并发限制**：所选账号在飞并发达上限时智能跳过，不排队、不计入 `MAX_ACCOUNT_ATTEMPTS` 计数。
- **额度用完**（余额为 0 / 上游 402 / 错误体含 quota、余额不足等关键词）→ 标记 `exhausted`，换下一个账号。
- **上游 429** → **不冷却账号**，按 Retry-After（封顶）原地等待重试；等待期间释放该账号并发槽。耗尽后换号，账号保持可用。Plan 通道 429 耗尽且同账号有 API Key 时切回退通道。
- **上游 5xx** → 重试 3 次后标记 `cooling` 冷却 300 秒，自动换下一个账号。
- **真风控（3012 / 405 unusual activity）** → 标记 `disabled` 并熔断停用，防止账号进一步受损，需在后台手动恢复。
- **鉴权失败 401/403（非验证码）** → 标记 `invalid`，需重新登录。
- **验证码过期（403 / code 3007）** → 原地刷新验证码对同一账号重试（最多 3 次）。
- 达到请求上限仍无可用账号 → 返回 `503 no_available_account`。

账号状态机：

```
            ┌─────────┐   额度用完(定期自动再探)    ┌──────────┐
  登录 ────▶│ ACTIVE  │◀─────────────────────────│ EXHAUSTED│
            │         │   5xx 重试耗尽(冷却 N 秒) └──────────┘
            │         │◀───────┐      ┌─────────┐
            │         │        ├──────│ COOLING │
            │         │        └─────▶└─────────┘
            │         │ 401/403(非验证码)
            │         │──────────────────────▶ INVALID（凭证失效，重新授权）
            │         │ 3012/405(真风控)
            │         │──────────────────────▶ DISABLED（风控保护，后台手动恢复）
            └─────────┘
```

> 成功响应可清除 `COOLING` / `EXHAUSTED`；额度恢复时后台监控也会自动回 `ACTIVE`。

## 阿里云无痕验证求解

JWT 账号调用上游时需携带阿里云无痕验证参数（请求头 `X-Aliyun-Captcha-Verify-Param`）。

**主路是真浏览器**：`captcha_node/solver_pw.js` 用 `puppeteer-core` 驱动系统里的 Chromium/Edge，在真环境里加载官方
`AliyunCaptcha` SDK（`initAliyunCaptcha(mode:popup)` → `startTracelessVerification()`）取回 verifyParam——真环境下无痕验证自动通过，不需要拖滑块。
早先的 **happy-dom 模拟 DOM 路线**（`captcha_node/solver.js`）自 2026-09 起被上游风控以「unusual activity」全拒，
只留作回滚：`ZCODE_CAPTCHA_SOLVER=legacy`。

- 首次使用前执行 `cd captcha_node && npm install`（依赖 `puppeteer-core`；启动包已把 `node_modules/` 随包）。
- `app/captcha.py` 以子进程方式调用（一进程一解，成功打印 `VERIFY_PARAM=` 后退出），并维护**预解 token 池**：
  热路径不等待，请求到来直接取一枚已解好的参数（亚毫秒），后台按库存下限持续补；verifyParam 实际时效约 2 分钟，
  池内 FIFO + 超龄丢弃；上游回挑战时整池作废（这批指纹可能已被盯上，继续复用只会连环 3007）。
- 真浏览器求解较重（10–40s/枚），因此池默认 `min1/max2`；内置并发去重与失败重试。
- 浏览器定位：`ZCODE_CHROMIUM_PATH` 显式指定优先，否则按常见安装路径自动探测 Chrome/Edge（见 `ZCodeHub.exe status`）。
- 配置与会话缓存兜底：`client/configs` 拉取失败时回落内置默认参数。

## 鉴权

- **后台鉴权**：所有 `/admin/api/*` 需 `Authorization: Bearer <后台密码>`（也支持 `?app_key=`），内置 IP 失败节流保护。
- **网关鉴权（可选）**：在「客户端接入」页生成网关 API Key（一把或多把，各自一个标签），`/v1/messages`、
  `/v1/chat/completions` 与 `/v1/models` 须携带 `Authorization: Bearer <key>` 或 `x-api-key: <key>`；
  无凭证 401，Key 不匹配或已被删除 403，一把都不剩则不校验。
  Key 明文只在生成的那一刻显示一次，列表永久脱敏，忘了只能删掉重建。

## 环境变量

`cp .env.example .env` 后按需修改（.env 永不入库）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZCODE_PORT` | 3000 | 服务端口 |
| `ZCODE_HOST` | 0.0.0.0 | 监听地址（启动包内的 `.env` 预置为 `127.0.0.1`）|
| `ZCODE_ADMIN_KEY` | `zcode` | 后台密码初始值，首次启动写入库后**以库为准**；改密码用 `set-admin-key`。开 `0.0.0.0` 前先换掉 |
| `ZCODE_DATA_DIR` | data | 数据目录（SQLite 存放处）；相对路径按 exe / 仓库根解析 |
| `ZCODE_FRONTEND_DIR` | frontend | 前端目录，可指向独立部署目录（包内 `statics` 仅兜底）|
| `ZCODE_QUOTA_REFRESH_INTERVAL` | 1800 | 后台刷新额度间隔（秒），0 关闭。高频连续查 billing 是风控主信号，故收敛到 30 分钟 |
| `ZCODE_COOLING_SECONDS` | 300 | 5xx 重试耗尽后的账号冷却时长（秒）|
| `ZCODE_ACCOUNT_CONCURRENCY` | 2 | 单账号并发上限，0 不限（运行时可在后台设置改，以库为准）|
| `ZCODE_CLAIM_ROUND_INTERVAL` | 3600 | 套餐自动领取轮间隔（秒），0 关闭（运行时可在后台设置改，以库为准）|
| `ZCODE_NODE_PATH` | node | 验证码求解所用 Node 可执行文件 |
| `ZCODE_CAPTCHA_SOLVER` | `pw` | `pw` = 真浏览器 `solver_pw.js`；`legacy` = 回滚到 happy-dom `solver.js`（2026-09 起被风控全拒）|
| `ZCODE_CHROMIUM_PATH` | 自动探测 | Chromium 内核浏览器路径；未配置时按 Windows/macOS/Linux 常见安装位置探测 |
| `ZCODE_CAPTCHA_RETRIES` | 4 | 单次求解失败重试次数 |
| `ZCODE_CAPTCHA_TIMEOUT` | 240 | 单次求解总超时（秒），含 Chromium 启动与 solver 进程内自旋 |
| `CAPTCHA_POOL_MIN` / `CAPTCHA_POOL_MAX` | 1 / 2 | 预解 token 池的目标库存与上限 |
| `CAPTCHA_TOKEN_TTL` | 95000 | 单枚 verifyParam 最大可用时长（ms，上游实际约 2 分钟）|
| `ZAI_UPSTREAM_URL` / `ZAI_FALLBACK_URL` / `BIGMODEL_UPSTREAM_URL` | — | 上游端点覆盖 |

## 开发与测试

```bash
$PY -m pytest            # 全量测试（Mock 上游，无真实网络）
$PY -m ruff check app tests
$PY -m mypy app/constants.py
```

测试全部走 **Mock 上游**：`tests/mock_upstream/` 模拟 Z.AI 被依赖的全部端点，
支持 `x-mock-scenario` 故障注入（具体见 `docs/testing`），不依赖真实网络，可离线回归。

## 文档

完整文档见 [`docs/README.md`](docs/README.md)：

- **开发**：架构 · 数据格式 · API 规范 · 上游协议 · 开发指南 · 路线图
- **测试**：测试策略 · 单元用例 · 互通向量 · 集成 E2E · 验收标准

## 技术栈

- Python 3.11+ · FastAPI · Uvicorn · httpx
- SQLite（账号 / 设置持久化，WAL 模式）
- Node.js + `puppeteer-core`（真浏览器里跑阿里云官方 SDK 求无痕验证参数）；`happy-dom` 模拟路线仅作回滚
- 交付：PyInstaller（onedir，`ZCodeHub.exe` + `ZCodeHubTray.exe` 共享 `_internal/`）+ Inno Setup 安装器；构建脚本在 `packaging/`

## 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。Releases 里的二进制产物由同一仓库的源码构建，对应源码即本仓库对应 tag。

## 免责声明

本仓库仅供学习、研究、个人实验与内部验证使用，不提供任何形式的商业授权、适用性保证或结果保证。

作者不因使用、修改、分发、部署或依赖本项目产生的任何直接或间接损失、账号封禁、数据丢失、
法律风险或第三方索赔负责。请勿将本项目用于违反服务条款、协议、法律或平台规则的场景；商业前请自行确认许可证与相关协议。