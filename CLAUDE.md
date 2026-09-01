# 老席留学IP数字专家 · 项目规则

> 项目文档：`docs/`（需求与决策记录、架构书、PRD、RFC、智能体团队简介、plans/、archive/、iteration/、讨论记录/）
> 核心设计原则：皮肤/大脑解耦，真多bot（6 独立飞书应用，主控 @派单 + 专家自触发）+ 单bot 兜底，派单为主 + 偶发圆桌。

## 目录结构

```
docs/              设计文档（需求记录/架构书/PRD/RFC/计划/归档/迭代记录/变更记录/讨论记录）
  archive/         文档旧版本归档（改文档前先拷贝：原名-YYYYMMDD-HHMM.md）
  iteration/       迭代记录（每次功能迭代一篇，与 archive 分开不混）
  badcases/        坏例收集库（真实群错误行为：原话/根因/修复/回归测试，改代码前扫索引防复发）
  踩坑记录/        架构开发踩坑汇总（问题/根因/解法/来源，按类别分：LLM/意图/流水线/排期/上下文/提示词/运维）
  变更记录/        当日变更流水（改动前必读、改动后必记）
  讨论记录/        老板问题→整改对照（按日一篇，记"为什么改"，与变更记录时间流水互补）
src/               Python 源码
  pipe.py          管道协议（消息/回执数据结构）
  skin/            皮肤层（身份解析、lark-cli 收发）
  brain/           大脑层（小席主控、意图识别、会话记忆、排期表、熔断、专家）
knowledge/         事实层（只读：人设/业务事实/家长画像/爆款规律/合规红线/案例库/直播语料/家长常问与成单解答/竞对分析）
 风格/<用户>/      说话风格分库（静态：创作要求.md，老席默认；动态学习规则落 memory/学习规则-<用户>.md，不写 knowledge）
 竞对分析/        市场情报库（竞对图谱/中新教育对比=人工润色；热点话题=机器覆盖，脚本同步；竞对线索=refresh 原样存）
清洗工作流/        清洗能力（知识入库前一步：处理规范/原始稿/处理记录，见清洗工作流/CLAUDE.md）
workspace/产出/    工作层（排期表.md、脚本产出）
workspace/素材/    用户投喂的泛类内容（记素材入库，席小题出选题时读入）
memory/            学习层（token账本、学习规则、数据回流；学习规则按风格用户分库：学习规则.md=老席，学习规则-<用户>.md=其他）
logs/              运行日志（按天 .log，次日 gzip 压缩，保留 30 天）
config/            roles.json 身份映射 + bots.json（6 bot 注册表，均 gitignore，不入库）
scripts/           辅助脚本（sync_radar.py：雷达产出→竞对分析/热点话题.md 同步）
radar/             新加坡热点雷达（独立采集项目，每日产出热点；config/output/登录态均 gitignore）
```

## 运行日志

- 所有操作自动记录到 `logs/system-YYYY-MM-DD.log`：收发、路由派单、LLM 调用（模型/用量/耗时）、排期变更、token 熔断。
- 次日把前一天日志 gzip 压缩成 `.log.gz`；超过 30 天的压缩日志自动删除。
- 日志不记密钥/token/消息全文（只记前 60 字）。

## 文档归档与迭代记录

- 改任何文档前，先把旧版本拷贝到 `docs/archive/`，命名 `原文件名-YYYYMMDD-HHMM.md`。
- 每次功能迭代完成后，在 `docs/iteration/` 写一篇迭代记录（日期/目标/改动清单/架构影响/验证/遗留）。
- `docs/archive/` 与 `docs/iteration/` 分开存放，不要混。
- **变更记录（必读必写）**：改动任何文件前，先读 `docs/变更记录/` 最近记录 + `docs/iteration/`，恢复上下文，防止丢失上下文导致出错；每次改动完成后，立即在当日 `docs/变更记录/YYYY-MM-DD.md` 追加一行（`HH:MM · 文件 · 改动摘要`）。

## 硬约束（不可违反）

- 皮肤层只做收发 + 身份解析 + 权限拦截，不做业务判断；大脑层不直接调 lark-cli。
- 智能体不能自动标「已发」——已发必须人工确认（发片同事回「已发布」）。
- 不确定事实走「问老板」，禁止静默编造。
- 知识库事实层只读；排期表主控唯一写权限；memory 学习层席小盘 agent 独写。
- 业务事实双写同步（人工维护时）：`knowledge/业务事实/` 在录音蒸馏项目有副本（`~/Desktop/录音文件蒸馏成文章工作流/老席录音蒸馏智能体/knowledge/业务事实/`）。人工改任何一份业务事实，必须同步改另一份，保持两边一致；改完在 `docs/变更记录/` 当日文件留痕。
- 异常一律「问老板」或「明说失败」，绝不静默。
- 代码默认无注释；注释只在 WHY 非显然时写。
- 不安装新的全局依赖。
- 密钥/token/open_id 不进代码、不进 commit、不进日志。

## GitHub 安全

- `config/roles.json`（含飞书 open_id）已 gitignore，禁止提交。
- lark-cli 凭证在 `~/.lark-cli/config.json`，不在项目内，不要复制进项目。
- 仓库公开前先自查：无 token/appSecret/open_id 文件。

## 验证命令

- 管道协议：`cd src && python3 test_pipe.py`
- 身份解析：`cd src && python3 -m skin.test_identity`
- bot 注册表：`cd src && python3 -m skin.test_bots`
- 排期表：`cd src/brain && python3 test_scheduler.py`
- 熔断：`cd src/brain && python3 test_circuit.py`
- 规则库：`cd src && python3 -m brain.test_rules`
- 数据回流：`cd src && python3 -m brain.test_reflow`
- 外搜核对：`cd src && python3 -m brain.test_search`
- 专家：`cd src/brain/experts && python3 test_experts.py`
- 会话记忆：`cd src && python3 -m brain.test_session`
- 意图识别：`cd src && python3 -m brain.test_intent`
- 素材库：`cd src && python3 -m brain.test_material`
- 小席：`cd src && python3 -m brain.test_controller`
- 真多bot 启动：`cd src && python3 -m skin.multi_main --role <小席|席小题|席小文|席小核|席小习|席小盘>`（每角色一进程；需先建 6 个飞书应用并填 `config/bots.json` 的 open_id）
- 单bot 兜底：`cd src && python3 -m skin.lark_bridge events`（未配 bots.json 时进程内派单）

## LLM 基本盘

- 专家出内容必须走大模型：MiMo API 主力（模型 `mimo-v2.5-pro`）+ DeepSeek 备用（在 `config/secrets.json` 改）。
- API key 只放 gitignored 的 `config/secrets.json`，不进代码/commit/日志。
- 架构：`src/brain/llm.py`（客户端）+ `knowledge.py`（知识库加载）+ 专家「读知识库→拼 prompt→调大模型」。
- 席小核红线词本地规则拦截先行，再交 LLM 全量审核。

## 开发顺序

P0 最小闭环：小席 + 皮肤收发 + 排期表 + 派单流水线 + 专家三件套（小题/小文/小核，LLM 驱动）。
P1：席小核外搜核对（已实现 v1：Wikipedia 免key + 不确定问老板）、席小习 diff 学习（已实现 v1：学规则/确认规则）、席小盘 agent 数据回流（已实现 v1：报数据→回验证结论）。
P2：圆桌模式（已实现 v2：三视角逐步输出）、写脚本流水线逐步输出、迁多 bot（只换皮肤层）。

## 角色速查

| 标签 | 身份 | 职责 |
|---|---|---|
| 【小席】 | 大脑层顶层 | 路由（LLM 意图识别，带 7 轮会话记忆）/排期/熔断/派单 |
| 【席小题】 | 专家 | 选题 + 调研合体 |
| 【席小文】 | 专家 | 写手，内嵌风格 |
| 【席小核】 | 专家 | 审核 + 核实 + 外部搜索 |
| 【席小习】 | 专家 | 反馈学习，自我迭代 |
| 【席小盘】 | 专家 | 数据回流，飞轮闭环 |

专家只响应主控 @派单（自触发，除圆桌）；主控随时在场兜底；老板最高权限。
