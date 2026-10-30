# 微信群采集与简短整理

在 macOS 上通过窗口截图和系统 OCR，定时读取指定微信群的新增可见文字。可配合 OpenClaw，把已采集文字整理成简短摘要。

**目前仍在本地验证阶段。**这不是微信官方 API，也不是聊天数据库导出工具；OCR、发言者归属和消息完整性都有局限，不保证高活跃群一定能在五分钟内采完。

## 工作方式

1. **采集**：一个 launchd 任务每 300 秒运行一次，正常每群访问一次；必要时在当前群向上补采，结束后尽量恢复原聊天和前台应用。
2. **补采**：先保存本轮最新一屏，再边向上滚动边采集。程序检查相邻页面的有序重叠，接到上次断点后按旧到新拼接。正常补采不需要再向下采一遍；中途到达的新消息留到下一轮。
3. **返回最新**：优先使用右下角能明确识别的“回到最新／底部”或新消息按钮。按钮不存在、无法确认或点击未到底时，回退到大步向下滚动。两条路径都以连续两次向下滚动后画面不变确认底部；返回过程不重复入库、不推进断点。无标签箭头不会按固定坐标猜着点击。
4. **整理（可选）**：OpenClaw 的另一个只读任务处理已保存文字，不操作微信。摘要插入同一份 OCR 摘抄上方，保留原文；没有待处理内容时不通知。

### 已接入的提速措施

- 本地脚本连续截图、滚动和检查，不等待模型逐屏回复。
- 只识别主窗口；像素完全未变化时复用 OCR。
- 根据画面稳定性和重叠情况调整等待与滚动步长，缺口时缩小步长补截。
- 切群等待目标标题和画面就绪，不依赖逐群模型调用。
- 记录整轮耗时、切群／滚动／OCR 耗时、未完成群和积压状态。

这些优化不改变缺口处理原则：断点找不到、重复或拼接不确定时，不把未采到的内容标成完成。

## 环境要求

- macOS 图形登录会话，微信已登录；不支持无桌面的服务器采集。
- Python 3、Swift 编译器及 Xcode Command Line Tools；Python 代码不依赖第三方包。
- 实际执行采集的终端或宿主具有屏幕录制、辅助功能权限；系统需要时还须允许控制 System Events。
- 目标群能在微信左侧列表中看到，窗口布局没有被其他窗口／面板干扰。
- AI 整理需另有可运行定时任务的 OpenClaw 和模型配置；仅安装本仓库不会自动产生 AI 摘要。

采集会短暂切换前台窗口。锁屏、休眠、用户同时操作微信、窗口布局变化都可能导致本轮未完成。

## 安装与首次运行

```bash
git clone https://github.com/KK-cyber11/wechat-group-monitor.git
cd wechat-group-monitor
cp groups.json groups.local.json
```

编辑 `groups.local.json`，例如：

```json
{
  "groups": [
    {"id": "g0001", "match": "示例工作群"},
    {"id": "g0002", "match": "示例学习群"}
  ]
}
```

- `id` 是本地稳定编号，不是微信原生群 ID；使用简单字母和数字，不要在已有数据时随意改编号。
- `match` 是群名中可识别且独特的片段。当前采用标题包含匹配，请勿配置同名或相互重叠、可能误认的片段。
- 优先读取 `groups.local.json`，不存在才读取仓库示例 `groups.json`。真实群名不要提交到 Git。

编译并手动检查一轮（会操作微信）：

```bash
bash scripts/build.sh
python3 scripts/collect_all_groups.py
```

首次访问建立当前可见屏幕的断点，不等于采集全部历史；后续运行才识别断点之后的新增内容。当前断点由三条有序 OCR 文本组成，不能等同于三条经过完整气泡解析的微信消息。

确认本机权限和群配置后，安装五分钟采集任务：

```bash
bash scripts/install-schedule.sh
```

任务标签是 `com.wechat-group-monitor`，plist 在 `~/Library/LaunchAgents/`。安装器会替换同标签任务，绑定本次下载目录的实际路径，并立即运行一次；已有配置应先检查和备份，勿同时从多个目录启动采集器。不同目录的锁不共享。

查看或暂停任务：

```bash
launchctl print "gui/$(id -u)/com.wechat-group-monitor"
launchctl bootout "gui/$(id -u)/com.wechat-group-monitor"
```

`bootout` 只卸载本次登录会话中的任务；下次登录仍可能加载现有 plist。需要长期停用时，将该 plist 移出 `~/Library/LaunchAgents/` 留作备份。再次安装可恢复。

## 配置 AI 简短整理（可选）

采集和整理分开运行：安装脚本只安装桌面采集任务，不创建 OpenClaw 任务。

在 OpenClaw 中配置**一个**每五分钟检查的只读整理任务，使用 [reading-trigger.js](scripts/reading-trigger.js) 作为触发器正文，并遵循 [condense-captures.md](scripts/condense-captures.md)。若已有整理任务，更新它，不重复创建。触发器不是独立 Node.js 程序，需要 OpenClaw 提供 `exec` 和 `json`。

触发器默认从 `~/.openclaw/workspace/projects/wechat-group-monitor` 读取数据；安装在其他位置时，为触发器的命令执行环境设置 `WECHAT_MONITOR_ROOT` 为仓库绝对路径。仅在某个终端中 `export` 不一定影响后台 OpenClaw，请确认任务实际使用的环境。

核心命令均在项目根目录执行，不操作微信：

```bash
python3 scripts/collection_store.py due
python3 scripts/collection_store.py commit data/monitor/reading-result.json
```

`due` 每次最多返回三份待整理批次。整理结果格式、来源哈希校验、保存后通知和失败重试规则见上述正文。不要清空队列来处理失败；已经保存的摘要不会重复插入。

可选关注名单写入本机 `data/monitor/watch.json`：

```json
{"people": ["示例成员"]}
```

没有该文件或名单为空时不自行选择关注对象。摘要贴近原话，姓名用纯文本或 **Markdown 加粗**，不输出 HTML 颜色标签；视频／链接只归属可靠确认的相邻发言者，不推测视频内容。

采集与整理的两个时钟独立，通知可能晚一轮再加模型耗时。OCR 本身是本机处理；启用 AI 整理后，待整理文字会进入你配置的模型服务，因此不能把整个 AI 流程描述成完全离线。

## 输出与排查

| 路径 | 用途 |
| --- | --- |
| `data/groups/<id>/summary.md` | 每批摘要（若已生成）、OCR 摘抄、采集时间和可见消息时间 |
| `data/monitor/seen.json` | 各群断点与连接／积压状态 |
| `data/monitor/last-run.json` | 最近一次执行结果 |
| `data/monitor/runs.jsonl` | 历次运行记录 |
| `data/monitor/captures/` | 已采集文字批次，支持重试整理 |
| `data/monitor/evidence/` | 补采的本地 PNG 与 OCR 证据 |
| `data/monitor/backfill-progress/` | 未完成补采的已接受页面和恢复位置 |
| `data/monitor/motions/` | 未确认完成的桌面输入记录 |

优先看 `incomplete_groups`、各群 `action`、`backfill_status`、`elapsed_seconds`、`performance` 和 `over_interval`，不要仅凭顶层 `ok` 就认定所有群都采完。部分早期失败只记录 `error`。

- `not-in-sidebar`：把已配置群放到可见的左侧列表；不会搜索或打开未授权群。
- `hole`／`ambiguous`：重叠或断点不能可靠连接，保留原断点，需检查证据。
- `budget_exhausted`：本轮预算耗尽，已接受页面可供后续恢复；原位置变化时会停止，不能保证跨轮自动恢复成功。
- `unresolved desktop motion requires review`：输入结果不明确，避免重放。先核对窗口和记录，不要盲删状态来强行继续。
- `no on-screen WeChat window`：先检查微信是否打开、窗口是否可见，再核对执行宿主的权限。

当前整轮采集预算约 210 秒、单群补采预算最多 90 秒；返回最新有独立预算，底层命令也有超时。这些是停止／标记未完成的机制，不是五分钟内全量完成的保证。原聊天不在配置或可见侧栏中时，也不能保证自动切回原聊天。

## 检查与已知限制

离线回归检查（不操作微信）：

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖顺序拼接、重复断点、缺口、超时、单群补采和返回最新的双路径。真实按钮还依赖微信是否暴露明确的无障碍标签；离线测试不能证明当前客户端按钮已经现场点击成功。

[benchmark_capture.py](scripts/benchmark_capture.py) 用于监督下的历史采集测量：需要指定已配置群和屏幕中真实存在的时间标记。它会操作桌面、持有同一采集锁，并将输出隔离；不是只读界面探针，也不应作为第二个定时采集入口。

- OCR 不是逐字准确导出，重复发言、姓名、卡片和截断气泡可能造成识别或归属歧义。
- 未获取图片、视频、文件等原始载荷，截图中的可见内容不代表完整媒体内容。
- 不承诺无人值守、多日、高活跃群或锁屏环境下的完整性；需分别通过真实运行验证。
- 本项目不在微信中发送消息。

## 隐私与仓库内容

仓库仅发布代码、测试、文档和示例群配置。`data/`、`groups.local.json`、编译二进制和 Python 缓存均已忽略，不应上传聊天内容、截图、真实群名、断点或关注名单。

常规截图缓存位于系统临时目录；补采证据会保留在 `data/monitor/evidence/`，**目前没有自动清理策略**，不是“截图不落盘”。保留用于排查的证据时注意本机磁盘占用；确认不再用于恢复／核查后，再由用户管理保留周期。
