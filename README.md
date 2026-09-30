# 同呼学习平台 · 自动刷课（GitHub Actions 版）

把《一句话让 WorkBuddy 自动领积分，关机也照领》那篇里的方法，落到**同呼网页版的学习平台**上。

- 入口：`https://cq.wens.com.cn/mobile.html?form=nbj_user_mob_home&appid=501136478&...`
- 运行位置：**方案三 · GitHub Actions**（关机也照跑）
- 结果推送：**飞书**
- 登录方式：**手机号 + 短信验证码** → 决定了"云端拿不到登录态，必须本机导出凭据"

---

## 这个项目照着那篇文章的三条硬约束写

| 原文约束 | 落到本项目 |
|---|---|
| **先领后派**，顺序不能改 | **先查后做**：先扫出没学完的任务，再逐个刷；已完成的直接跳过（`learn_adapter.scan_pending()` → `run()`） |
| **接口名以实际请求为准** | 所有关键字/选择器集中在 `src/learn_adapter.py` 顶部常量；第一次跑用勘察模式落盘真实 DOM，不照抄网上旧接口 |
| **解释器路径不写死版本号** | 只在 Actions 里用 `actions/setup-python` 固定大版本，脚本内不出现任何绝对解释器路径 |

外加一条原文的容错理念：**单节课失败不影响整体结论**，明细里单独标出来。

---

## 真实页面结构与运行逻辑（2026-09-30 用真实账号实测并跑通）

```
入口 mobile.html → 学习平台首页（推荐课程 / 知识智库 / 底部「首页·分类·我的」）
  └「我的」→ 当年学习总课时 / 我的课程 / 我的专题 / …
      └「我的课程」→ 课程卡片：`<课程名> 学分: 3 课时: 3 进度： 30%`（可按 未完成/已完成 筛选）
          └ 点课程 → 课程目录（共 N 讲）
              · 未学完：`视频 <讲名> 00:06:46 0% 开始学习`
              · 已学完：`视频 <讲名> 00:13:12 100% 再次学习`
          └ 播放器（video.js 系）：打开即自动续播到上次位置（暂停态），倍速 0.5x~1.5x
```

脚本按这条链路走：**扫进度 <100% 的课程 → 进目录挑没有「再次学习」的讲 → 播放/拽进度 →
回读讲次的 100% 标记 → 最后回「我的课程」回读总进度**。结论一律以平台自己的状态为准。

两个实测过的坑（脚本已内置对策）：

- 这套 SPA 偶发「资源文件加载失败 / 点击重试」—— 遇到会自动点重试或刷新；
- 课程目录的状态标记渲染偏慢 —— 解析会多等几轮，直到每讲都带状态才动手，
  避免把已完成的讲重复刷一遍。

进度上报机制见下文「快速模式」一节（saveView 位置心跳 + saveHour 学时账本，
无独立完成事件，完成判定由服务端按学时结算）。

---

## 三步上手

### 第 1 步 · 本机导出登录态（只做一次，之后每周刷新）

```bash
pip install -r requirements.txt
python -m playwright install chromium
python local/export_login.py
```

会弹出一个浏览器窗口。**用手机号 + 短信验证码登录同呼**，点到能看见学习平台入口，
**登录成功脚本会自动收工**（每 2 秒检测一次会话票据，不用回终端按回车）。产物在 `login_state/`：

- **`storage_state.gz.b64` ← 复制整段内容，填到 secret `WENS_STORAGE_STATE`（约 18 KB）**
- `storage_state.b64` ← 未压缩版（约 139 KB，**超过 GitHub secret 的 48 KB 硬上限**，仅留档）
- `cookie.txt` ← 备用，填到 secret `WENS_COOKIE`

> 为什么要 gzip：GitHub 对单个 secret 有 **48 KB 硬上限**，而 Playwright 导出的 storage_state 里
> 95% 是前端页面缓存（`irep-m-*`）。压缩后约 18 KB，无损 —— `src/settings.py` 按字节魔数自动识别，
> 未压缩的旧格式也照样兼容。

> 为什么不能直接在云端登录：短信验证码没人帮你收。所以只能"本机登录一次，把会话搬过去"，
> 正是原文方案三里"用刷新脚本把凭证注入仓库 secret"那一步。

**认证到底靠什么（2026-09 实测抓包确认）：**

| Cookie | 作用 |
|---|---|
| `KERPSESSIONIDwens-prod` | **主会话票据**，苍穹（ierp）接口认证靠它 |
| `IsolatorSpanwens-prod` | 会话隔离标识，与上面成对 |
| `at` / `uuid` / `gl` / `scode…` | 门户侧辅助 cookie |

`local/export_login.py` 跑完会自检这几个 cookie 在不在，**没拿到 `KERPSESSIONID*` 就说明没真正登录成功**，
会直接提示你重跑。视频本身是 HLS 切片（`/media/transform/<日期>/<媒体ID>/720p/index_00XX.ts`），
所以浏览器必须是能解 H.264 的真机 Chrome —— 这也是 `browser.py` 优先 `channel="chrome"` 的另一个原因。

### 第 2 步 · 建仓库、填 secret

新建仓库（**建议 public**，见下方"坑"一节），把这个目录推上去，然后：

`Settings → Secrets and variables → Actions`

**Secrets**

| 名称 | 必填 | 说明 |
|---|---|---|
| `WENS_STORAGE_STATE` | 二选一 | `login_state/storage_state.gz.b64` 的全文（gzip+base64，约 18 KB） |
| `WENS_COOKIE` | 二选一 | 手动从 DevTools 复制的 Cookie 字符串 |
| `FEISHU_WEBHOOK` | 是 | 飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook |
| `FEISHU_SECRET` | 否 | 机器人开了"签名校验"才填 |

**Variables**

| 名称 | 默认 | 说明 |
|---|---|---|
| `WENS_LEARN_URL` | 同呼移动端入口 | 学习平台有独立地址就换成它 |
| `PLAYBACK_RATE` | `2.0` | 倍速；平台若按真实时长校验，改 `1.0` |
| `MAX_MINUTES` | `180` | 单次时间预算，超出留到下次补做 |

### 第 3 步 · 先勘察，再放它自己跑

第一次不要直接刷。去 `Actions → 同呼学习平台 · 自动刷课 → Run workflow`，
勾上 **inspect**，跑完下载 `artifacts`：

- `page.png` 整页截图
- `dom.html` 真实 DOM
- `inspect.json` 页面里所有可点文案 + video 元素列表 + 命中"待学"关键字的项

拿这三个文件跟 `src/learn_adapter.py` 顶部的 `PENDING_KEYWORDS` / `NEXT_KEYWORDS` 对一遍，
把文案改成真实值。改完再手动跑一次正常模式，收到飞书推送就说明通了。

之后每天北京时间 07:30 自动跑，你只在断网、或者登录态过期那几天多看一眼。

---

## 目录结构

```
wens-tonghu-auto/
├── .github/workflows/daily.yml   # 定时任务（UTC 23:30 = 北京 07:30）
├── local/export_login.py         # 本机导出登录态 → 喂给 secret
├── src/settings.py               # 配置全走环境变量
├── src/browser.py                # 浏览器会话；优先真机 Chrome（编解码器）
├── src/learn_adapter.py          # ★ 三个约束都在这：先查后做 / 以实际为准 / 失败隔离
├── src/feishu.py                 # 飞书推送（带签名）
├── src/main.py                   # 编排 + 汇总消息 + 退出码
├── PROMPTS.md                    # 三套方案的提示词（可直接丢给 WorkBuddy）
└── config.example.yaml           # 配置说明（凭据不落仓库，只是文档）
```

---

## 几个真会踩的坑（都是实测过的）

1. **视频"在，但不动"。** Playwright 自带的 Chromium 不含 H.264 / AAC 专有编解码器，
   国内学习平台的 MP4/HLS 基本放不出来，表现为 `video` 元素存在、`currentTime` 永远是 0。
   → `src/browser.py` 按 **Chrome → 系统 Edge → 自带 Chromium** 的顺序降级启动：
   GitHub 的 ubuntu runner 预装 Google Chrome（第一项即中）；本机只装了 Edge 也能直接跑，
   Edge 与 Chrome 同源、同样带 H.264 解码，**不必额外下载浏览器构建**。

2. **私有仓库的免费额度不够。** Free 计划私有仓库 2000 分钟/月。刷视频每天一小时就 1800 分钟，
   很容易见底。→ **用 public 仓库**：公共仓库的 Actions 分钟数不计费，而 secret 不会随代码公开。
   实在要用私有仓库，就把 `PLAYBACK_RATE` 调到 3~4。

3. **定时任务不准时。** GitHub 的 cron 会排队，延迟几分钟到几十分钟很常见。
   本项目是幂等的，晚跑不影响结果。

4. **60 天不活跃会被自动停用。** 仓库 60 天没有任何提交，定时任务会被 GitHub 关掉。
   偶尔手动触发一次，或者随便 commit 一下即可。

5. **登录态会过期。** 会话 cookie 通常几天到两周失效。建议每周重跑一次
   `local/export_login.py` 覆盖 secret —— 这一步正好可以交给 WorkBuddy 做每周提醒。

6. **看视频这件事本身有风险边界。** 它跑的是你自己的账号，请在
   公司考勤/培训管理规定的范围内使用，账号安全与合规由使用者自负。

---

## 快速模式（FAST_MODE=1）

2026-09 实测抓包，进度上报长这样：

```
POST /form/batchInvokeAction.do?appId=nbj_portal&f=nbj_user_mob_course&ac=customEvent
Content-Type: application/x-www-form-urlencoded;charset=utf-8
kd-csrf-token: <32位令牌>
signature: <sha256>-1359__length__132
ajax: true      cqappid: nbj_portal      kd-client-type: web
client-start-time: <毫秒>      traceId: <hex>      userId: <id>

pageId=<课件页会话ID>&appId=nbj_portal
&params=[{"key":"","methodName":"customEvent",
          "args":["nbj_play","saveView","{\"currentTime\":82.36021,\"type\":\"VIDEO\"}"],
          "postData":[]}]
```

三个关键结论：

- **进度字段是 `args[2]` 里的 `currentTime`** —— "看到第几秒"；
- **请求体里没有视频 ID** —— 服务端靠 `pageId` 反查你当前打开的课件页；
- **请求带 `signature`（前端私钥签名）和 `kd-csrf-token`** ——
  **所以"改 currentTime 重放请求"这条路是死的**：改了请求体，签名必然失效。

此外还有第二种心跳 **`saveHour`（学时账本）**：低频、批量打包上报，
args 为 `["nbj_play","saveHour","{\"id\":\"<事件id>\",\"coursewareId\":\"<课件id>\",\"hour\":<累计观看秒数>}]`。
其中 `coursewareId` 与视频 HLS 分片路径里的媒体资产 ID 一致。
**学完一节那一刻没有独立的"完成事件"** —— 抓包实证最后发出的仍只是又一批 `saveHour`，
即完成判定由服务端按累计学时结算，脚本无需补发任何"完成"调用。

于是 `FAST_METHOD` 默认取 `seek` —— **不解签名，只拽进度条**：

1. 真浏览器打开课件页、正常加载播放器；
2. 每 1.2 秒把 `video.currentTime` 往前拽 10 秒，**上报请求由页面自己发出**，
   `signature` / `kd-csrf-token` / `userId` 全部天然合法，脚本全程不碰网络层；
3. 最后 8 秒**不跳，让它自然播完** —— 平台自己的"播完"逻辑得靠这一段触发；
4. 刷新课件页回读"已完成"标记做校验。

30 分钟的课大约 3～4 分钟跑完，而且不依赖任何接口逆推，服务端改签名算法也不怕。

| 变量 | 默认 | 说明 |
|---|---|---|
| `FAST_MODE` | 关 | `1` 启用快速模式 |
| `FAST_METHOD` | `seek` | `seek` 拽进度条（推荐）；`replay` 重放请求（实验，会被 signature 拒） |
| `SEEK_STEP_SECONDS` | `10` | 每次往前拽多少秒，被平台卡就调小 |
| `SEEK_INTERVAL_MS` | `1200` | 两次拽之间等多久，给页面留上报时间 |
| `FINAL_TAIL_SECONDS` | `8` | 尾段自然播完的秒数 |
| `MAX_VIDEO_MINUTES` | `45` | 拿不到视频时长时的兜底 |

> 抓包时两个坑：站点接了 Bonree APM，网络面板里每几秒一条的 `index.00XX`、
> 发起程序显示 `BonreeSDK_JS.min.js` 的都是监控心跳（Bonree 补丁了 fetch，
> 所以业务请求的发起程序也显示它）—— 区分靠 URL，业务请求都是 `batchInvokeAction.do`。

