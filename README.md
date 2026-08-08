# 老席留学IP数字专家

老席 IP 获客自动化系统：一个跑在飞书群里的多 agent 数字专家团队，围绕「新加坡留学」老席 IP 做选题、写脚本、审核、学习、复盘的闭环。

系统由**主控小席**统一调度，**6 个独立飞书 bot** 各司其职，老板（或产品角色）在群里 @小席 派活，专家按流水线自触发产出。

## 核心设计

- **皮肤/大脑解耦**：`skin/` 只做收发 + 身份解析 + 权限拦截，不做业务判断；`brain/` 只做决策，不直接调飞书。
- **真多 bot**：6 个独立飞书应用（主控 @派单 + 专家自触发），另有单 bot 兜底模式。
- **小席规划器**：主控一次 LLM 调用产出「自然回复 + 可选动作」，用大模型原生语言理解能力听老板说话，只有真需要系统落地才写 `【行动】` JSON。
- **垂类知识库当参考，不当考试答案**：专家 prompt 保留承重锚点，其余引导性组织，避免为了约束丢原本能力。
- **规则类型化**：学习规则分「表达偏好 / 事实规则 / 内容策略 / 禁止事项」，按硬度分组注入，避免一条例子被学成绝对真理。

## 角色

| 标签 | 身份 | 职责 |
|---|---|---|
| 【小席】 | 大脑层顶层 | 规划器路由 / 排期 / 熔断 / 派单，老板的左右手 |
| 【席小题】 | 专家 | 选题 + 调研合体 |
| 【席小文】 | 专家 | 写手，内嵌老席风格 |
| 【席小核】 | 专家 | 审核合规 + 核实 + 外部搜索（红线本地拦截先行） |
| 【席小习】 | 专家 | 反馈学习，把老板改法提炼成规则 |
| 【席小盘】 | 专家 | 数据回流，飞轮闭环 |

专家只响应主控 @派单；主控随时在场兜底；老板最高权限。

## 目录结构

```
docs/              设计文档（架构书 / PRD / RFC / 迭代记录 / 变更记录 / 讨论记录 / archive）
src/               Python 源码
  pipe.py          管道协议（消息/回执数据结构）
  skin/            皮肤层（身份解析、lark-cli 收发、多 bot 启动）
  brain/           大脑层（小席规划器、会话记忆、排期表、熔断、规则库、专家）
knowledge/         事实层（只读：人设 / 业务事实 / 家长画像 / 爆款规律 / 合规红线 / 竞对分析）
workspace/         工作层（排期表、脚本产出、素材）
memory/            学习层（学习规则、数据回流）
config/            roles.json 身份映射 + bots.json 注册表 + secrets.json（均 gitignore，不入库）
radar/             新加坡热点雷达（独立采集项目）
scripts/           辅助脚本
logs/              运行日志（按天，保留 30 天）
```

## 运行

前置：本机装好 `lark-cli` 并登录；填好 `config/secrets.json`（模型 key）、`config/bots.json`（6 个 bot 的 open_id）、`config/roles.json`（身份映射）。

真多 bot 启动（每角色一进程）：

```bash
cd src && python3 -m skin.multi_main --role <小席|席小题|席小文|席小核|席小习|席小盘>
```

单 bot 兜底模式：

```bash
cd src && python3 -m skin.lark_bridge events
```

## 验证

项目用标准 `unittest`，从 `src` 目录运行：

```bash
cd src && python3 -m brain.test_controller   # 小席主控（含规划器）
cd src && python3 -m brain.test_planner      # 规划器
cd src && python3 -m brain.test_intent       # 意图兜底
cd src && python3 -m brain.test_rules        # 规则库
cd src && python3 -m brain.test_session      # 会话记忆
cd src && python3 -m brain.test_scheduler    # 排期表
cd src && python3 -m brain.test_material     # 素材库
cd src && python3 -m skin.test_identity      # 身份解析
cd src && python3 -m skin.test_lark_bridge   # 皮肤收发
cd src/brain/experts && python3 test_experts.py  # 专家
cd src && python3 test_pipe.py               # 管道协议
```

## 安全

- `config/`（含飞书 open_id、模型 API key）、token 账本、运行日志、雷达登录态均 gitignore，禁止提交。
- 密钥 / token / open_id 不进代码、不进 commit、不进日志。
- 仓库公开前先自查：无 token / appSecret / open_id 文件。

## 文档

- 架构书 / PRD：`docs/`
- 迭代记录：`docs/iteration/`
- 变更流水：`docs/变更记录/`
