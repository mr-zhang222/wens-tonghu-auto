# 部署手册（Windows · 从零到跑起来）

## ⚠️ 先说结论：GitHub Actions 这条路本项目走不通

2026-10-01 云端实测（workflow run 36749808897，预检步骤）：

```
===== DNS 解析 =====
219.131.174.220 n-cq10.wens.com.cn cq.wens.com.cn     ← 解析正常
===== 首页 =====
curl: (28) Connection timed out after 45002 milliseconds
status=000  dns=0.054253s  connect=0.000000s  total=45.002462s
                           ^^^^^^^^^^^^^^^^^^ TCP 连接从未建立
```

**Diagnosis：DNS 通、TCP 不通。** GitHub 的托管 runner 位于境外（Azure），
而 `cq.wens.com.cn` 只对国内网络开放 —— 这是物理链路问题，
**不是凭据问题、不是代码问题，重试和重跑导出脚本都不会变好。**

→ **执行环境必须换成国内常开设备。** 代码、仓库、secret 全部可复用，只换"谁来跑"。
→ 推荐直接看下面「**第 0 步 · 在本机定时跑（当前采用）**」，GitHub 那套可留作代码备份。

---

## 第 0 步 · 在本机定时跑（推荐，已验证可用）

本机直连同呼流畅（且不需要代理），配合 Edge 自带 H.264 解码，**已实测把课程从 30% 推到 90%+**。

**手动跑一次**（勘察模式，不刷课，用来确认环境正常）：

```bash
cd /c/Users/mrzhang/WorkBuddy/2026-09-30-22-08-25/wens-tonghu-auto
INSPECT=1 cmd //c "local\\run_daily.bat"
tail -30 local/logs/run.log
```

**正式刷课**（去掉 `INSPECT=1` 即可）：

```bash
cmd //c "local\\run_daily.bat"
```

日志写在 `local/logs/run.log`（UTF-8，已强制编码，不会乱码）。

**注册成每天自动跑的任务计划**（在 PowerShell 里执行一次，不需要管理员）：

```powershell
# 方法一（当前使用的方法，推荐）：Set-ScheduledTask
$action  = New-ScheduledTaskAction -Execute "C:\Users\mrzhang\WorkBuddy\2026-09-30-22-08-25\wens-tonghu-auto\local\run_daily.bat" `
           -WorkingDirectory "C:\Users\mrzhang\WorkBuddy\2026-09-30-22-08-25\wens-tonghu-auto"
$trigger = New-ScheduledTaskTrigger -Daily -At "21:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "wens-tonghu-auto daily" -Action $action -Trigger $trigger -Settings $settings -Force

# 方法二（等价写法）：
schtasks /Create /TN "wens-tonghu-auto daily" /SC DAILY /ST 21:00 `
  /TR "\"C:\Users\mrzhang\WorkBuddy\2026-09-30-22-08-25\wens-tonghu-auto\local\run_daily.bat\"" `
  /F
```

**改时间**（比如从 21:00 改成别的点）：

```powershell
Set-ScheduledTask -TaskName "wens-tonghu-auto daily" -Trigger (New-ScheduledTaskTrigger -Daily -At "22:30")
```

管理它：

```powershell
schtasks /Query /TN "wens-tonghu-auto daily" /V /FO LIST   # 查看
schtasks /Run   /TN "wens-tonghu-auto daily"               # 立刻跑一次
schtasks /Delete /TN "wens-tonghu-auto daily" /F           # 删除
```

> **注意**：已设置 `-StartWhenAvailable`，所以**错过时间会自动补跑**（比如 21:00 电脑关机，
> 开机后系统会尽快补上）。本项目是**幂等**的，哪天多跑一次、晚跑一次都不会出错。

**另外两个可行方案**（都需要一台国内常开设备，你没有的话就选上面这个）：

| 方案 | 需要什么 | 特点 |
|---|---|---|
| 青龙面板 | 一台国内常开设备（NAS / 软路由 / 旧电脑 / 国内服务器） | 7×24 不依赖你的主力机；网页管任务、看日志 |
| 自托管 runner | 同上 + 装 GitHub runner | 仍用 GitHub 的触发和日志界面，执行在你自己机器上 |

---

## 第 1 步 · 本机导出登录态（必须你本人操作）

```bash
cd /c/Users/mrzhang/WorkBuddy/2026-09-30-22-08-25/wens-tonghu-auto
"C:/Users/mrzhang/.workbuddy/binaries/python/envs/default/Scripts/python.exe" local/export_login.py
```

会发生什么：

1. 弹出一个浏览器窗口，自动打开同呼登录页；
2. **你手动登录**（手机号 + 短信验证码）；
3. **登录成功后就自动收工** —— 脚本每 2 秒检测一次会话票据（`KERPSESSIONID*`），
   检测到就立刻导出，**不用回终端按回车**（想手动控制就加 `--manual`）；
4. 产物写到 `login_state/`：
   - **`storage_state.gz.b64` ← ★ 第 4 步要填进 secret 的就是它的全文（约 18 KB）**
   - `storage_state.b64` ← 未压缩版（约 139 KB，**超过 GitHub secret 的 48 KB 硬上限**，会被拒收，仅留档）
   - `cookie.txt` ← 备用，同样能填进一个 secret

> 为什么要压缩：GitHub 对单个 secret 有 **48 KB 硬上限**（官方限制），而 Playwright 导出的
> storage_state 里 95% 都是前端页面缓存（`irep-m-*`），原始 base64 约 139 KB 会被直接拒收。
> gzip 后只剩约 18 KB，无损 —— 服务端 `src/settings.py` 会自动识别魔数并解压，旧格式也照样兼容。

脚本会自动自检：**没拿到 `KERPSESSIONID*` 就说明没真正登录成功**，
此时产物会写到 `login_state/failed/` 而**不会覆盖上一次可用的凭据**，重跑即可。
（这两个文件等同于你的登录态，别外传、别提交 —— `.gitignore` 已经把它们排除在仓库之外。）

---

## 第 2 步 · 建 GitHub 仓库（可选，仅作代码备份）

> 因为上面那条"云端连不上同呼"的结论，第 2~5 步**不再是跑起来的必要步骤**。
> 仍建议保留这个仓库：代码有云端备份、换机器时 clone 即可。
> 已经建好的 `mr-zhang222/wens-tonghu-auto` 就继续用着，不用管它跑不跑。

1. 打开 <https://github.com/new>；
2. Repository name 填 `wens-tonghu-auto`；
3. 可见性选 **Public** —— 公共仓库的 Actions 分钟数不计费（私有仓库 2000 分钟/月，刷视频一周就烧完）。
   secret 不会随代码公开，放心；
4. **不要**勾选 “Add a README / .gitignore / license”（我们要推本地已有的）；
5. 点 Create repository，页面会显示一段 HTTPS 地址，形如
   `https://github.com/<你的用户名>/wens-tonghu-auto.git` —— 第 3 步要用。

---

## 第 3 步 · 提交并推送

**提交我已经替你做完了**（仓库已 `git init`、17 个文件已提交，凭据文件确认 0 个入库），
你只需要接上远端再推。把 `<你的用户名>` 换成你的 GitHub 用户名：

```bash
cd /c/Users/mrzhang/WorkBuddy/2026-09-30-22-08-25/wens-tonghu-auto
git remote add origin https://github.com/<你的用户名>/wens-tonghu-auto.git
git push -u origin main
```

推送时会让你登录 GitHub（浏览器弹窗或粘贴 Personal Access Token）。

### ⚠️ 两个必踩的坑（本机实测）

**坑 1 · `workflow` 权限。** 如果推送报：

```
! [remote rejected] main -> main
(refusing to allow an OAuth App to create or update workflow
 `.github/workflows/daily.yml` without `workflow` scope)
```

这是 GitHub 的硬规则：token 必须带 `workflow` 权限才能创建/修改 `.github/workflows/` 下的文件
（防绕过审批）。用 gh 登录的默认权限只有 `repo`/`read:org`/`gist`，**不含 workflow**。补上：

```bash
gh auth refresh -h github.com -s workflow
```

会再弹一次设备码，去 <https://github.com/login/device> 输码授权即可。之后 `gh auth status`
应显示 `Token scopes: 'gist', 'read:org', 'repo', 'workflow'`。

**坑 2 · 代理 + TLS 后端。** 本机直连 GitHub 不通，必须走本地代理；而 git 默认用 Windows
自带的 schannel 做 TLS，穿本地代理会握手失败（报 `CONNECT tunnel failed, response 502`
或 `schannel: failed to receive handshake`）。仓库级配置已写好，换台机器照抄这两行：

```bash
git config http.proxy http://127.0.0.1:57097   # 换成你实际的本地代理端口
git config http.sslBackend openssl
```

即便这样，这台机器到 GitHub 仍有约 **3/8 的瞬时失败率**（代理抖动，curl 侧成功率 8/8
说明代理本身没坏）。所以**推送失败先重跑一次**，通常第二次就过 —— 不是配置错了。

> 提交用的是占位身份（`wens-tonghu-auto` + GitHub noreply 邮箱），先跑通再说。
> 想换成你自己的，推之前执行：
> ```bash
> git config user.name "你的名字" && git config user.email "你的邮箱"
> git commit --amend --reset-author --no-edit
> ```

**提交前体检**（确认没把凭据带上去）：

```bash
git ls-files | grep -E "login_state|storage_state|artifacts" || echo "干净：凭据没进仓库"
```

---

## 第 4 步 · 填 Secret 和 Variables

仓库页 → **Settings → Secrets and variables → Actions**

### Secrets（点 New repository secret）

| 名称 | 值 |
|---|---|
| `WENS_STORAGE_STATE` | `login_state/storage_state.gz.b64` 的**全文**（一行长 base64，约 18 KB） |
| `FEISHU_WEBHOOK` | 飞书群机器人的 Webhook 地址 |
| `FEISHU_SECRET` | 机器人开了“签名校验”才填，否则留空不建 |

> 填 `WENS_STORAGE_STATE` 时**整段复制粘贴**即可，粘贴时夹带的换行/空格不影响 ——
> 代码会自动忽略并识别出这是 gzip 压缩过的登录态。
> 若 GitHub 提示 “secret 太大”，说明你复制成未压缩的 `storage_state.b64` 了，换成 `.gz.b64` 那个。

**更稳的写法（推荐）：用命令写，避免手动粘贴 18 KB 被截断**

```bash
./local/push_secret.sh            # 用已导出的 login_state/storage_state.gz.b64
./local/push_secret.sh --re-export  # 顺带重新登录一次再推送
```

脚本会自动找到 gh（含 Windows 默认安装路径）、校验大小没超 48 KB、写入 secret 并回显列表。
**每周刷新登录态就用它**，比在网页里粘贴可靠得多。

**怎么拿飞书 Webhook**：打开你的飞书群 → 右上角设置 → 群机器人 → 添加机器人 → 自定义机器人 →
起个名字 → 复制 Webhook 地址。若要安全设置，勾“签名校验”并把密钥一起填进 `FEISHU_SECRET`。

### Variables（切到 Variables 标签页，点 New repository variable）

| 名称 | 值 | 说明 |
|---|---|---|
| `FAST_MODE` | `1` | 启用快速模式（拽进度条），30 分钟的课约 3~4 分钟跑完 |
| `FAST_METHOD` | `seek` | 也可不填，默认就是 seek |
| `SEEK_STEP_SECONDS` | `10` | 被平台卡就调小到 5 |
| `SEEK_INTERVAL_MS` | `1200` | 给页面留上报时间 |
| `FINAL_TAIL_SECONDS` | `8` | 尾段自然播完，别改 0 |
| `WENS_LEARN_URL` | 留空 | 留空则用代码里的同呼移动端默认入口 |
| `MAX_MINUTES` | `180` | 单次时间预算，超出的留到下次 |

---

## 第 5 步 · 验收（先勘察，再放它跑）

**好消息：整套流程 2026-09-30 已用真实账号在本机实弹跑通**（一门 30% 的课被推到 90%+，
多数讲拿到平台「100% 再次学习」确认），Actions 上大概率一次就通。

1. 仓库页 → **Actions** 标签 → 左侧选「同呼学习平台 · 自动刷课」→ 右侧 **Run workflow**；
2. **第一次先勾上 `inspect`**，点绿色按钮。勘察不刷视频，会把**课程清单、每门进度、待学讲次、
   播放器时长**直接打在运行日志里（同时也产出截图与 DOM 到 `artifacts`）；
   （勘察模式只打印、不发飞书，属正常。）
3. 日志里看到「待学 N 门」且 `video 时长` 是个正常秒数（不是 0），就说明登录态和浏览器都正常；
   这时**再跑一次，这次不勾 inspect** —— 这才是真刷。收到飞书推送即部署成功。

飞书消息会写明每节课的结果：`已观看 / 已确认完成 / 失败原因`。
看到 **“平台已确认完成”** 就是全通了；若显示“已完成但未确认”，说明服务端还卡真实时长，
把 `SEEK_STEP_SECONDS` 调到 `2`、`SEEK_INTERVAL_MS` 调到 `1500` 再试。

之后北京时间**每天 21:00** 自动跑（本机计划任务，开机即执行；本项目幂等，晚跑不影响）。

---

## 诊断工具（local/ 下，纯本机用）

刷课脚本**跑动期间不写逐讲日志**，汇总只在结束时一次性输出 —— 所以"跑了半天没动静"
最容易误判。怀疑没生效时，别盯着 `run.log`，用这三个工具直接问平台：

| 工具 | 用途 |
|---|---|
| `python local/probe_course.py` | **只读**。打印「我的课程」全量进度；带参数再展开某门课的讲次明细。可在正式跑动期间并排使用。 |
| `python local/probe_course.py 研发看板` | 展开指定课程（参数是课程名关键字）的每一讲进度。 |
| `python local/verify_tail.py 课程关键字` | 定点验证「尾段收尾」：跳到距结尾 8 秒处再调用 `_finish_tail`，回读平台是否记成 100%。**几秒钟出结论**，不用等 100 分钟的长视频。 |
| `python local/diag_tail.py 课程关键字` | 排查"播放/进度"类问题：测自然播放能不能推进 `currentTime`、seek 到结尾会不会触发 `ended`、页面可见性等。 |

⚠️ **本平台的三个反直觉事实**（都是实测出来的，别按常识猜）：

1. **课程进度按「讲次是否 100%」结算。** 一讲没跑到 100%，这门课的总进度就是 **0%**，
   跟已经看了多少分钟无关。所以中途去看进度条"没动"是正常的 —— 不代表没在刷。
2. **一讲动辄 100 分钟**（实测 01:41:44 = 6104 秒）。就算 8 倍速，一讲也要 ~13 分钟墙上时间。
   别用几分钟的短预算去做验收，那必然停在"超出时间预算"。
3. **平台播放器播完会立刻把 `currentTime` 复位成 0**（同时 `duration` 变 NaN）。
   任何"轮询看进度"的代码都必须**密集轮询 + 认这个复位**，
   否则会把"已经播完"读成"卡死在 0"，误判成失败。`_finish_tail()` 就是为此而写。

---

## 出问题时的对照表

| 飞书里看到 | 原因 | 怎么办 |
|---|---|---|
| `登录态失效，请重跑本机导出脚本` | cookie 过期（通常几天到两周） | 重做第 1 步，把新的 `storage_state.gz.b64` 覆盖 `WENS_STORAGE_STATE` |
| GitHub 报错 `secret 太大` / 存不进去 | 复制成了未压缩的 `.b64`（139 KB > 48 KB 上限） | 改用 `storage_state.gz.b64`（18 KB） |
| 报 `WENS_STORAGE_STATE 不是合法 base64` | 复制时截断/漏了字符 | 重新整段复制 `.gz.b64` 全文，或改用 `local/push_secret.sh` 由命令写入 |
| 一节课都没扫到 | 页面文案改版，关键字不再命中 | 跑一次 inspect，用 `inspect.json` 里的真实文案改 `src/learn_adapter.py` 顶部常量 |
| 视频元素在但 `currentTime` 不动 | 用了不带 H.264 的 Chromium | 已默认优先真机 Chrome；runner 自带，一般不会遇到 |
| Actions 跑两份/互相打架 | 上次没跑完又触发了一次 | 流水线已设并发锁，等它跑完即可 |
| 定时任务突然不跑了 | 仓库 60 天不活跃被 GitHub 自动停用 | 进 Actions 页点一下 Enable，或随便 commit 一次 |

**推送/登录阶段**（不在飞书里，出现在终端）：

| 终端报错 | 原因 | 怎么办 |
|---|---|---|
| `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope` | token 缺 `workflow` 权限 | `gh auth refresh -h github.com -s workflow`，再去设备页授权一次 |
| `CONNECT tunnel failed, response 502` / `schannel: failed to receive handshake` | git 用 Windows schannel 穿本地代理握手失败 | `git config http.sslBackend openssl` + `git config http.proxy <本地代理>` |
| `Failed to authenticate via web browser: ... Bad Gateway` | 到 GitHub 的代理瞬时抖动 | 重跑命令（重试循环一般第 2~3 次成功） |
| `could not read Username for 'https://github.com'` | 没配置凭据 | `gh auth setup-git` |

---

## 每周维护（1 分钟）

登录态会过期，建议每周重跑一次第 1 步，把新值覆盖 `WENS_STORAGE_STATE`。
这一步可以设成定时提醒，到点 WorkBuddy 会提醒你刷一次登录态。

---

## 边界

这套东西跑的是**你自己的账号**。公司对培训/考勤若有明确规定，用之前自己掂量一下；
账号安全与合规由使用者自负。
