 ## 结论先放前面

你要做的东西可以定义成：**企业员工 Agent 中台 / Agent Cloud Workspace**。它不是单纯“企业版 ChatGPT”，也不是完全复制 Manus，而是把下面几层打通：

**员工入口**：聊天、任务、文件、浏览器、云端工作区。
**企业资源层**：知识库、数据库、业务系统、SaaS、账号权限。
**Agent 执行层**：planner / executor / tool calling / browser / code / sandbox / artifact。
**治理层**：多租户、SSO、RBAC、审计、计费、成本控制、审批、人机协同。
**交付层**：行业模板、企业定制 skill、技能广场、可复用 workflow，降低冷启动。

CREAO 更像是一个**自然语言驱动的 Agent App / Workflow 平台**，Manus 更像是一个**带云端电脑的通用自主执行 Agent**。你们做企业服务，最有价值的切入不是“做一个更通用的 Manus”，而是做一个**可私有化治理、可定制技能、可连接企业资源、可交付行业方案的 Manus-like 企业 Agent 平台**。

---

# 1. CREAO 是什么

基于公开资料，CREAO 现在的核心定位不是普通 chatbot，而是：

> **用户通过对话创建、运行、复用 AI Agent / Workflow / Skill，并把这些 Agent 应用到具体业务角色中。**

它的电商运营页面强调的能力包括：批量生成商品描述、连接 CSV 或店铺数据、按照品牌语气写 SEO 描述、支持 Shopify / eBay / Amazon / Etsy 场景、跟踪竞品价格、给 watcher 发送 offer、回复买家消息等。也就是说，它不是只回答问题，而是把“运营动作”封装成 agent 执行任务。([Creao AI][1])

CREAO 的 Agent Builder 页面展示了几个关键模块：**Dynamic Workflows、AI Agents、Scheduled Runs、Skills & Connectors、Memory、Workspaces、Models**。这说明它的产品抽象已经接近你提到的“agent 中台”：有工作流、有技能、有连接器、有记忆、有 workspace，也有定时执行。([Creao AI][2])

更值得借鉴的是 CREAO 2.0 的设计思路：它把能力拆成可组合的 skills，每个 skill 有明确接口；系统根据自然语言生成结构化的实体、表单、技能和应用；workspace 级的数据层可以被 agent 长期使用。这类设计本质上是在减少 LLM 的自由发挥，把 agent 变成“可组合、可复用、可治理”的业务软件。([Creao AI][3])

CREAO 还专门写过云端 agent 基础设施：它提到桌面 agent 默认是单用户、单机器、单进程，而云端 agent 会遇到 sandbox、共享硬件、定时触发、HTTP 触发、agent-to-agent 触发、凭证隔离、文件系统持久化等问题。它的建议包括：把 agent 环境冻结成 sandbox snapshot、平台 runner 和用户环境热切换、secrets 不放进 sandbox、用 host-side API bridge 管 OAuth token、用短期 JWT 授权每次运行。这个对你们的“云端虚拟环境 + 企业员工 agent”非常关键。([Creao AI][4])

**我的判断：CREAO 值得拆解的不是聊天 UI，而是这四个产品抽象：**

| 抽象            | CREAO 启发        | 企业版应该强化什么                       |
| ------------- | --------------- | ------------------------------- |
| Agent App     | 用自然语言生成业务 agent | 企业模板、版本管理、审批发布                  |
| Skill         | 可复用能力单元         | 权限声明、测试、计费、审计、签名                |
| Workspace     | Agent 的数据与上下文空间 | tenant / org / team / user 多级隔离 |
| Cloud Runtime | 云端执行、定时、API 触发  | sandbox 隔离、secrets 管理、VPC、日志追踪  |

`agent.creao.ai/chat` 公开页面本身是前端应用入口，公开可读资料不足以确认其内部实现细节；上面的判断主要来自 CREAO 官网、Agent Builder、业务落地页和其云端 agent 基础设施文章。

---

# 2. Manus-like 产品到底是什么

Manus 官方文档把 Manus 描述为一个 autonomous general AI agent，可以作为“virtual colleague”完成从计划、执行到交付的端到端任务，并且拥有自己的计算机环境。([Manus][5])

从技术形态看，Manus-like 产品通常包含：

1. **Planner**：把用户目标拆成子任务。
2. **Executor**：调用浏览器、文件、代码、搜索、API 等工具执行。
3. **Cloud Computer / Sandbox**：不是只在聊天框里想，而是在云端环境里操作文件、浏览器和终端。
4. **Artifact Delivery**：交付网页、报告、表格、PPT、代码、自动化结果。
5. **Long-running Task**：任务可以跑很久，中间暂停、恢复、观察、重试。
6. **Human-in-the-loop**：涉及登录、付款、发邮件、发消息、删改数据时需要人确认。

E2B 对 Manus 的拆解里提到 Manus 不是单个 LLM agent，而是 planner / executor 协同，并运行在完整云端计算机中；E2B sandbox 可以提供浏览器、终端、文件系统等环境。([E2B][6])

OpenAI 的 ChatGPT agent 也走向类似方向：它不只回答，还能使用工具、浏览器和连接器执行任务；OpenAI 的 Computer Use API 也支持模型通过点击、输入、滚动、截图等方式操作浏览器或桌面环境。([OpenAI][7])

**但企业版不能照搬 Manus 的“自由探索式 agent”。**
企业客户要的是：权限可控、数据可控、成本可控、结果可复核、动作可审计、技能可交付。你们真正的差异化应该是：

> **Manus 的云端执行体验 + CREAO 的 skill/workflow 复用 + 企业级权限治理 + 行业定制交付。**

---

# 3. 竞品地图

## 3.1 通用自主 Agent / Super Agent

| 产品                       | 定位                                                                                  | 可借鉴点                                  | 对你们的启发                    |
| ------------------------ | ----------------------------------------------------------------------------------- | ------------------------------------- | ------------------------- |
| **Manus**                | 通用 autonomous agent，拥有自己的云端电脑，能计划、执行、交付任务                                           | 云端电脑、长任务、artifact 交付、planner/executor | 适合借鉴交互体验和执行范式，但企业治理需要自己加强 |
| **ChatGPT Agent**        | OpenAI 的 agent 模式，结合工具、浏览器、连接器执行任务                                                  | 任务型对话、工具组合、连接器、human takeover         | 企业版需要围绕私有知识、审批和审计做增强      |
| **Genspark Super Agent** | 自主执行型 no-code assistant，整合多模型、多工具、多 MCP 集成，覆盖 research、content、data、phone、email 等任务 | 多专用 agent、工具矩阵、文档/表格/设计/开发 agent      | 适合作为“多能力入口”的体验参考          |
| **Flowith**              | Canvas-first / agentic workspace                                                    | 无限画布、任务编排、可视化上下文                      | 可借鉴为企业任务空间，而不只是聊天流        |

Genspark 官方帮助页显示，它把 Super Agent 定义为能 think / plan / act 的自主助手，并整合 30+ 模型、150+ 工具、700+ MCP 集成，同时有 Slides、Sheets、Doc、Designer、Developer 等专用 agent。([Genspark][8])

## 3.2 企业级 Agent 平台

| 产品                                                     | 定位                                                | 强项                                                          | 你们要避开的正面战场                                   |
| ------------------------------------------------------ | ------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------- |
| **Microsoft Copilot Studio**                           | 企业创建、管理、发布 Copilot / Agent 的平台                    | M365、Graph、Teams、企业身份和治理                                    | 不要硬碰 M365 生态；做跨系统、行业定制、国产/私有部署               |
| **Google Gemini Enterprise / Vertex AI Agent Builder** | Google 企业 agent 平台，强调 build、scale、govern、optimize | Google Cloud、Vertex AI、企业搜索、模型生态                            | 不要只做模型平台；要做交付型 agent workspace               |
| **AWS Bedrock AgentCore**                              | 在 AWS 上安全部署和运营生产级 agents                          | Runtime、identity、observability、tool access、enterprise scale | AWS 更偏底层云平台，你们可以做上层业务中台                      |
| **Salesforce Agentforce**                              | CRM 与业务流程里的企业数字劳动力                                | CRM 数据、工作流、渠道动作                                             | 不要从 CRM 单点打；可以做跨 CRM / ERP / 电商 / OA 的 agent |

Microsoft Copilot Studio 支持用自然语言或图形界面构建 agent，并发布为独立 agent 或 Microsoft 365 Copilot agent。([Microsoft][9]) Google 把 Gemini Enterprise Agent Platform 定位为统一的企业 agent 平台，用于构建、扩展、治理和优化企业 agent。([Google Cloud][10]) AWS Bedrock AgentCore 面向生产级 agent 的部署与运营，强调安全、规模化、权限和治理。([AWS Documentation][11]) Salesforce Agentforce 则强调在企业数据、工作流、渠道中部署数字劳动力。([Salesforce][12])

## 3.3 No-code / AI Workforce / Workflow Agent

| 产品                | 定位                                        | 可借鉴点                                                     |
| ----------------- | ----------------------------------------- | -------------------------------------------------------- |
| **Relevance AI**  | 低代码 / 无代码构建 AI agents 和 multi-agent teams | Agent workforce、knowledge、tools、marketplace、团队协作         |
| **Gumloop**       | 面向企业的多人协作 AI agent builder                | IT 管控、模型限制、guardrails、spend policy                       |
| **Zapier Agents** | 依托 Zapier 生态构建可连接 9000+ apps 的 agents     | SaaS 连接器、自动化动作、非技术用户上手                                   |
| **n8n**           | Workflow automation + AI agents           | 可视化 workflow、500+ 集成、human-in-the-loop、self-host         |
| **Dify**          | 开源 LLM app platform                       | RAG、workflow、agent、model management、observability、plugin |

Relevance AI 官方文档把平台定义为可构建 agents 和 multi-agent teams 的低/无代码平台，包含 agents、workforces、knowledge、tools、marketplace 等模块。([Relevance AI][13]) Gumloop 强调多人协作 agent builder，并让 IT 控制访问、模型限制、guardrails 和成本策略。([Gumloop: Build AI agents for work][14]) Zapier Agents 重点是让用户基于公司知识和 9000+ apps 快速构建自定义 agent。([Zapier][15]) n8n 的 AI agent 页面强调生产可控、500+ 集成、代码支持和 human-in-the-loop guardrails。([n8n][16]) Dify 则提供 workflow、RAG、agent、模型管理和可观测性能力。([GitHub][17])

## 3.4 国内相关平台

| 产品                       | 定位                         | 借鉴点                                 |
| ------------------------ | -------------------------- | ----------------------------------- |
| **Coze / 扣子**            | 通过自然语言开发智能体、工作流、插件、知识库、记忆等 | Skill / workflow / bot builder / 分发 |
| **阿里云百炼 / Model Studio** | 企业级大模型与 Agent 应用开发平台       | RAG、插件、MCP、Agent Store、企业模型服务       |
| **腾讯元器**                 | 智能体创建与分发平台                 | 零代码 agent、工作流、知识库、发布渠道              |
| **百度千帆**                 | 企业级大模型与 Agent 平台           | Agent 引擎、工具、MCP、模型服务、企业服务           |

Coze 文档把 skills 拆成插件、工作流、触发器，并支持知识与记忆等能力。([Coze Docs][18]) 阿里云 Model Studio 的单智能体应用支持连接模型、知识库和外部工具，并通过意图识别、规划和工具调用完成任务。([Alibaba Cloud Help Center][19]) 百度千帆企业级平台围绕 Agent 引擎、工具、MCP、模型服务和企业服务构建。([Baidu Cloud][20])

---

# 4. 开源项目与技术借鉴

下面是你们最应该重点研究的项目组合。

## 4.1 Agent 应用平台层

| 项目             | 类型                                   | 值得借鉴                                          | 注意点                              |
| -------------- | ------------------------------------ | --------------------------------------------- | -------------------------------- |
| **Dify**       | 开源 LLM app platform                  | Workflow、RAG、Agent、模型管理、observability、插件市场    | 更像 LLM 应用平台，不是完整 Manus-like 云端电脑 |
| **n8n**        | Source-available workflow automation | 可视化 workflow、集成生态、human-in-the-loop、self-host | 强 workflow，弱自主云端执行               |
| **Open WebUI** | 自托管 AI 平台                            | RBAC、RAG、插件、MCP/OpenAPI、模型/agent 权限           | 更适合作为企业 AI portal 借鉴             |

Dify 官方 GitHub 将其定义为开源 LLM 应用开发平台，整合 AI workflow、RAG pipeline、agent capabilities、model management 和 observability；Dify 插件机制也支持把模型供应商、API 和自定义工具模块化接入 workspace。([GitHub][17]) n8n 强调 workflow 与 AI agent 结合，支持自托管、500+ 集成、人机协同和可控生产流程。([n8n][16]) Open WebUI 则值得参考其自托管、RAG、RBAC、插件、MCP/OpenAPI 和模型/agent 访问控制设计。([GitHub][21])

## 4.2 Agent Loop / Multi-agent 编排层

| 项目                            | 类型                                | 值得借鉴                                                         | 适用场景                                  |
| ----------------------------- | --------------------------------- | ------------------------------------------------------------ | ------------------------------------- |
| **LangGraph**                 | Stateful agent workflow framework | 长任务、持久化、人机协同、memory、debug、production deployment              | 适合做你们的核心 agent runtime                |
| **CrewAI**                    | Multi-agent / crews / flows       | 角色型 agent、multi-agent team、flows、guardrails、memory、knowledge | 适合做垂直任务团队，如销售、运营、客服                   |
| **Microsoft Agent Framework** | 企业 agent/workflow framework       | AutoGen + Semantic Kernel 方向，state、telemetry、graph workflow  | 适合 Azure / .NET 生态客户                  |
| **AutoGen**                   | Multi-agent framework             | 事件驱动多 agent、directed workflows                               | 官方已提示新项目优先看 Microsoft Agent Framework |

LangGraph 官方文档强调它适合 long-running、stateful workflows 和 agents，并提供 persistence、human-in-the-loop、memory、debugging 与生产部署能力。([Docs by LangChain][22]) CrewAI 官方文档强调 agents、crews、flows，并包含 memory、knowledge、observability、guardrails、RBAC、触发器和长期 workflow。([CrewAI Documentation][23]) Microsoft Agent Framework 被描述为结合 AutoGen 的多 agent 抽象和 Semantic Kernel 的企业能力，并提供 state management、telemetry、graph workflows 等能力；AutoGen 文档则提示新项目优先使用 Microsoft Agent Framework。([Microsoft Learn][24])

## 4.3 Manus-like 云端执行层

| 项目                          | 类型                                | 值得借鉴                                                    |
| --------------------------- | --------------------------------- | ------------------------------------------------------- |
| **Suna / Kortix**           | 开源/self-hostable Manus-like agent | 组织级 agent、真实电脑操作、共享记忆、RBAC、secrets、audit、microVM 隔离     |
| **OpenHands**               | Coding agent / agent canvas / SDK | 云端/本地工作区、自动化任务、Slack/GitHub/Linear 集成、skills/plugins    |
| **OpenManus**               | General AI agent framework        | Manus-like agent loop、工具集成、开源实验参考                       |
| **AgenticSeek**             | 本地 Manus 替代                       | 本地隐私、浏览、代码、任务规划、语音                                      |
| **Browser-use**             | Browser automation for agents     | 真实浏览器操作、恢复循环、自定义工具、自托管                                  |
| **Stagehand / Browserbase** | AI browser automation             | act / extract / observe / agent primitives，适合网页操作 skill |

Suna/Kortix 是最值得你们研究的开源 Manus-like 项目之一。它强调 open-source/self-hostable、组织级 specialist agents、shared memory、real computers/actions，并且列出 microVM isolation、members/groups/roles、per-resource permissions、secrets manager、audit trail、human-in-the-loop gates、on-prem/VPC/air-gapped 等企业安全能力。([GitHub][25]) OpenHands 的 Agent SDK 面向代码相关 agent，支持 Python/REST API、本地或 ephemeral Docker/Kubernetes workspace，以及 skills/plugins/marketplace。([GitHub][26]) Browser-use 则提供真实浏览器/computer action space、persistent tools、recovery loops、自托管或云端运行能力。([GitHub][27])

## 4.4 Sandbox / 云端虚拟环境

| 方案                           | 值得借鉴                                     | 使用建议               |
| ---------------------------- | ---------------------------------------- | ------------------ |
| **E2B**                      | AI agent cloud sandbox、浏览器、代码、文件、隔离环境    | MVP 阶段最快接入         |
| **Daytona**                  | Stateful agent runtime、快速 sandbox、隔离运行环境 | 适合做可持久 workspace   |
| **Firecracker**              | AWS 开源 microVM，面向安全多租户 serverless        | 长期自建 runtime 的底层参考 |
| **Kata Containers / gVisor** | 容器级隔离增强                                  | 企业私有化部署时可选         |

E2B 把自己定位为 AI agent cloud，提供安全 sandbox、桌面 sandbox 和云端虚拟电脑环境；Daytona 强调 fast、scalable、stateful infrastructure for AI agents，并提供隔离运行环境；Firecracker 是 AWS 开源的 microVM 技术，目标是在多租户场景下结合虚拟机隔离与容器速度。([E2B][28])

## 4.5 Memory / Knowledge / Connector 标准

| 层         | 推荐参考                                                                         |
| --------- | ---------------------------------------------------------------------------- |
| **长期记忆**  | Mem0、Zep / Graphiti、LangGraph Memory                                         |
| **知识库**   | Dify RAG、LlamaIndex、LangChain、Haystack、Qdrant / Milvus / Weaviate / pgvector |
| **连接器协议** | MCP                                                                          |
| **工具注册**  | MCP server + 内部 tool registry + 权限 scope                                     |
| **评测与观测** | Langfuse、LangSmith、OpenTelemetry、Helicone、Phoenix                            |

MCP 是一个开源标准，用来把 AI 应用连接到外部工具、数据和 workflow；它的工具规范要求每个工具暴露唯一名称、描述、输入 schema 等信息。这个非常适合你们做“企业 skill / connector 标准”。([Model Context Protocol][29])

---

# 5. 你们产品的推荐定位

我建议不要把产品定义为“企业版 Manus”，而是定义为：

> **企业员工 Agent 中台：为每个员工提供可控的云端 AI 工作区，并让企业沉淀可复用的知识、技能、流程和 Agent。**

这句话里面有四个关键词：

1. **每个员工**：不是只给老板或运营团队用，而是员工级入口。
2. **可控云端工作区**：每个任务有 sandbox、文件、浏览器、工具、日志。
3. **企业资源共享**：知识库、系统 API、账号权限、业务数据。
4. **可复用能力沉淀**：skill、workflow、agent template、行业方案。

---

# 6. 推荐产品模块

## 6.1 企业员工端

| 模块                  | 能力                                        |
| ------------------- | ----------------------------------------- |
| Chat / Task Console | 员工输入目标，agent 拆解任务、执行、交付                   |
| Cloud Workspace     | 每个任务有文件区、浏览器、终端、运行日志、artifact             |
| Artifact Center     | 报告、表格、PPT、网页、代码、数据文件统一管理                  |
| Personal Memory     | 用户偏好、常用格式、常用客户、工作习惯                       |
| Team Shared Memory  | 团队 SOP、过往项目、客户资料、模板                       |
| Agent Library       | 员工选择销售 agent、运营 agent、客服 agent、数据分析 agent |

## 6.2 管理员端

| 模块                   | 能力                                   |
| -------------------- | ------------------------------------ |
| Tenant / Workspace   | 企业、部门、团队、项目空间隔离                      |
| SSO / SCIM           | 企业身份、组织架构、入离职同步                      |
| RBAC / ABAC          | 角色、资源、数据、工具、动作权限                     |
| Knowledge Governance | 文档权限继承、ACL-aware RAG、数据脱敏            |
| Tool Governance      | 哪些 agent 能调用哪些 API、哪些动作需审批           |
| Cost Control         | 用户/部门/agent/model/token/sandbox 成本统计 |
| Audit Log            | prompt、tool call、数据访问、文件操作、审批记录      |
| Policy Center        | 禁止外发、禁止访问某些域名、敏感动作审批                 |

## 6.3 Skill / Workflow 广场

这是你们区别于 To C agent 的核心。

| 模块                | 能力                                            |
| ----------------- | --------------------------------------------- |
| Skill 上传          | 开发者上传 skill 包、MCP server、API wrapper、workflow |
| Skill Manifest    | 声明输入输出、权限、计费、依赖、版本、测试                         |
| Skill Review      | 安全扫描、secret 检查、权限审核、人工审核                      |
| Skill Versioning  | 版本发布、灰度、回滚、兼容性检查                              |
| Skill Marketplace | 企业内部技能广场、行业模板、官方 skill、第三方 skill              |
| Skill Analytics   | 调用量、成功率、耗时、成本、失败原因                            |

一个 enterprise skill manifest 可以这样设计：

```yaml
id: ecommerce.competitor_price_monitor
version: 1.3.0
name: 竞品价格监控
description: 监控指定商品在竞品平台的价格、库存和促销变化
owner: solutions/ecommerce
input_schema:
  type: object
  required:
    - product_list
    - competitor_urls
  properties:
    product_list:
      type: array
    competitor_urls:
      type: array
output_schema:
  type: object
  properties:
    report_url:
      type: string
    price_changes:
      type: array
permission_scopes:
  - browser.read
  - storage.write
  - knowledge.read:ecommerce
  - notification.send:slack
approval_required:
  - external_message.send
runtime:
  sandbox: browser
  timeout_seconds: 1800
billing:
  meter:
    - llm_tokens
    - sandbox_minutes
    - browser_actions
tests:
  - tests/price_monitor_smoke.yaml
  - tests/price_monitor_acl.yaml
evals:
  - evals/accuracy.yaml
  - evals/format_compliance.yaml
```

---

# 7. 推荐技术架构

## 7.1 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│                        Enterprise Portal                     │
│ Chat / Task / Agent Store / Skill Store / Admin / Billing    │
└───────────────────────────────┬──────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────┐
│                         Control Plane                         │
│ Tenant / IAM / RBAC / Policy / Billing / Audit / Evaluation  │
└───────────────┬───────────────────────┬──────────────────────┘
                │                       │
┌───────────────▼──────────────┐ ┌──────▼──────────────────────┐
│        Agent Runtime          │ │       Knowledge & Memory     │
│ Planner / Executor / Tools    │ │ RAG / ACL / Memory / Index   │
│ LangGraph / Agents SDK        │ │ Vector DB / Object Storage   │
└───────────────┬──────────────┘ └──────┬──────────────────────┘
                │                       │
┌───────────────▼───────────────────────▼──────────────────────┐
│                         Tool Gateway                          │
│ MCP / SaaS Connectors / Internal APIs / Approval / Secrets    │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│                      Execution Plane                          │
│ Sandbox / Browser / Terminal / FileSystem / Code / Scheduler │
│ E2B / Daytona / Kubernetes / Firecracker / gVisor / Kata      │
└──────────────────────────────────────────────────────────────┘
```

## 7.2 Control Plane

这是企业中台最关键的部分，不能留到后面补。

| 能力  | 技术建议                                                                |
| --- | ------------------------------------------------------------------- |
| 多租户 | `tenant_id`、`workspace_id`、`org_id`、`user_id` 全链路强制传递               |
| 身份  | SSO / OIDC / SAML / SCIM                                            |
| 权限  | RBAC + ABAC，资源级 ACL                                                 |
| 审计  | 所有 prompt、tool call、sandbox action、文件读写、知识库检索都记录                    |
| 计费  | token、model、sandbox minute、browser action、tool call、storage、egress  |
| 策略  | model policy、data policy、network policy、tool policy、approval policy |
| 观测  | trace、step log、cost log、latency、success rate、error taxonomy         |

OpenAI Agents SDK 的 tracing 会记录 model calls、tool calls、handoffs、guardrails 和 custom spans，这类 trace 结构可以作为你们 run log / audit log 的参考。([OpenAI Developers][30])

## 7.3 Agent Runtime

企业 agent runtime 不建议一开始做完全自由的“自主思考循环”。更稳的结构是：

```text
User Task
  ↓
Intent / Risk Classifier
  ↓
Context Retrieval
  ↓
Plan as DAG
  ↓
Policy Check
  ↓
Execute Step
  ↓
Observe Result
  ↓
Self-check / Eval
  ↓
Need Approval? ── yes ── Human Approve
  ↓ no
Continue / Retry / Escalate
  ↓
Deliver Artifact
  ↓
Write Memory + Trace + Cost
```

建议支持三种模式：

| 模式                   | 说明                                | 适用                |
| -------------------- | --------------------------------- | ----------------- |
| **Chat Agent**       | 问答、知识检索、轻工具调用                     | 企业知识助手            |
| **Workflow Agent**   | 固定流程 + LLM 节点 + 工具节点              | 报销、客服、销售线索、运营 SOP |
| **Autonomous Agent** | planner/executor 循环，云端 sandbox 执行 | 研究、数据分析、网页操作、复杂交付 |

OpenAI 对 agent 的定义也强调：当应用需要自己管理 orchestration、tool execution、approvals、state 时，需要 agent orchestration，而不是单次模型调用。([OpenAI Developers][31])

## 7.4 Sandbox / 虚拟环境

你们要“开虚拟环境给企业员工”，这里必须分清三类环境：

| 类型                  | 能力                         | 适用               |
| ------------------- | -------------------------- | ---------------- |
| **Browser Sandbox** | 浏览器、网页登录、网页操作、截图、下载        | 电商运营、销售调研、网页自动化  |
| **Code Sandbox**    | Python/Node、文件处理、数据分析、脚本执行 | 数据分析、报表、代码生成     |
| **Desktop Sandbox** | 图形桌面、浏览器、文件管理器、办公软件        | Manus-like 全能力体验 |

关键设计：

1. **每个任务一个 run sandbox**，默认隔离。
2. **每个用户一个 persistent workspace snapshot**，保存文件、环境、浏览器状态。
3. **secrets 不进入 sandbox**，由 host-side tool gateway 注入短期凭证。
4. **网络出站白名单**，企业可以限制 agent 访问的域名。
5. **高危动作审批**，例如发邮件、提交表单、付款、删除数据、修改 CRM。
6. **sandbox snapshot + replay**，方便审计、复现和 debug。

CREAO 的云端 agent 基础设施文章明确强调：凭证不能和 agent 运行环境放在一起，secrets 应留在执行边界之外，通过 host-side API bridge 注入 OAuth token，并用短期 JWT 和 IP allowlist 约束每次运行。([Creao AI][4])

---

# 8. 企业知识库与记忆设计

## 8.1 知识库

企业知识库不能只是“上传文档 + embedding”。

必须支持：

| 能力            | 说明                                                       |
| ------------- | -------------------------------------------------------- |
| ACL-aware RAG | 检索时强制过滤用户有权限看的文档                                         |
| 多级空间          | 企业级、部门级、项目级、个人级                                          |
| 文档血缘          | 来源、上传人、版本、更新时间、权限继承                                      |
| 混合检索          | dense vector + sparse BM25 + metadata filter + rerank    |
| 引用追踪          | 回答必须带来源，便于企业复核                                           |
| 敏感信息处理        | PII、合同、财务、人事数据分级                                         |
| 数据连接器         | Google Drive、SharePoint、Notion、Confluence、飞书、钉钉、企业微信、数据库 |

非常重要的一点：
**不要只在 ingestion 阶段做权限过滤。查询阶段也必须带上 tenant、workspace、user、group、document ACL。**

推荐检索条件类似：

```sql
WHERE tenant_id = :tenant_id
  AND workspace_id IN (:allowed_workspaces)
  AND document_acl && :user_acl_groups
  AND sensitivity_level <= :user_clearance
```

## 8.2 记忆

企业 agent 的 memory 建议分层：

| Memory 类型      | 内容                      | 权限             |
| -------------- | ----------------------- | -------------- |
| User Memory    | 用户偏好、输出格式、常用客户、常用语言     | 用户私有           |
| Team Memory    | 团队 SOP、项目上下文、复用案例       | 团队共享           |
| Company Memory | 公司政策、品牌语气、产品知识          | 企业共享           |
| Agent Memory   | 某个 agent 的历史执行经验、失败案例   | agent owner 管理 |
| Task Memory    | 某次任务的中间状态、文件、plan、trace | run 级别         |

“自进化”不要直接让 agent 改自己。安全做法是：

```text
运行日志 → 失败归因 → 改进建议 → 生成 skill/prompt/workflow patch
     → 自动测试/eval → 人工审批 → 灰度发布 → 回滚
```

这才是企业可接受的 self-evolving。

---

# 9. Skill 广场应该怎么做

你们的 skill 广场是商业化关键。

## 9.1 Skill 类型

| 类型                  | 示例                           |
| ------------------- | ---------------------------- |
| API Skill           | 查询 CRM、创建工单、查库存、生成报价         |
| Browser Skill       | 登录后台、抓取竞品、更新商品信息             |
| Document Skill      | 生成合同、解析 PDF、做 PPT、写周报        |
| Data Skill          | SQL 查询、BI 报表、Excel 分析        |
| Communication Skill | 发 Slack、发邮件、生成客服回复           |
| Workflow Skill      | 多步骤 SOP，例如“新品上架流程”           |
| Agent Template      | 销售助理、客服质检、运营分析、HR onboarding |

## 9.2 Skill 发布流程

```text
开发 / 上传
  ↓
Manifest 校验
  ↓
权限声明
  ↓
安全扫描
  ↓
测试样例
  ↓
Eval
  ↓
管理员审核
  ↓
发布到企业内部技能广场
  ↓
调用统计 / 成本 / 成功率 / 失败日志
```

## 9.3 Skill 权限模型

每个 skill 都应该声明权限，而不是让 agent 任意调用。

示例：

```json
{
  "skill_id": "crm.create_lead",
  "version": "2.1.0",
  "required_scopes": [
    "crm.lead.read",
    "crm.lead.write"
  ],
  "risk_level": "medium",
  "requires_approval": false,
  "allowed_roles": [
    "sales",
    "sales_ops"
  ],
  "data_access": {
    "tenant_scope": true,
    "customer_pii": true,
    "financial_data": false
  }
}
```

---

# 10. Multi-agent 怎么做更稳

一开始不要做“无限 swarm”。企业场景更适合**有边界的专家协作**。

## 推荐结构

```text
Manager Agent
  ├── Research Agent
  ├── Browser Agent
  ├── Data Analyst Agent
  ├── Document Agent
  ├── QA / Verifier Agent
  └── Delivery Agent
```

## 三种编排方式

| 方式              | 说明                      | 推荐程度      |
| --------------- | ----------------------- | --------- |
| Handoff         | 一个 agent 完成后交给另一个 agent | 高         |
| Agents as Tools | Manager 把专家 agent 当工具调用 | 高         |
| Debate / Swarm  | 多 agent 自由讨论            | 低，成本高且不可控 |

OpenAI Agents SDK 也支持 handoffs 和 multi-agent orchestration，适合参考“一个 agent 将任务委派给另一个 agent”的结构。([OpenAI Developers][32])

企业场景中，multi-agent 的价值不是“看起来聪明”，而是：

1. **权限隔离**：客服 agent 没有财务权限，财务 agent 没有发外部邮件权限。
2. **专业能力隔离**：数据分析、网页操作、文档生成、审核分工。
3. **可观测性**：每个 agent 的输入、输出、成本、失败原因可追踪。
4. **可替换性**：某个专家 agent 可以升级，不影响整体流程。

---

# 11. 企业版与 To C Agent 的核心区别

| 维度 | To C Agent | 企业 Agent 中台              |
| -- | ---------- | ------------------------ |
| 用户 | 个人         | 员工、团队、部门、企业              |
| 数据 | 用户个人上下文    | 企业知识、业务系统、权限数据           |
| 执行 | 自由探索       | 受控执行、审批、审计               |
| 技能 | 通用工具       | 企业定制 skill、行业 workflow   |
| 记忆 | 个人偏好       | 个人/团队/企业/agent 多级 memory |
| 成本 | 用户自己承担     | 部门预算、成本中心、调用配额           |
| 权限 | 简单登录       | SSO、RBAC、ABAC、资源级 ACL    |
| 安全 | 用户自行判断     | 合规、审计、数据隔离、风控            |
| 交付 | 产品自助使用     | 售前诊断、定制 skill、上线培训、持续运营  |

所以你们的 PMF 不在“通用能力超过 Manus”，而在：

> **帮企业把高频业务动作产品化、skill 化、agent 化，并让员工安全复用。**

---

# 12. 最适合先做的 MVP

## MVP 目标

先做一个企业可以真正上线的版本：

> **企业员工可以在一个平台里使用公司知识库、调用被授权的技能、让 agent 在云端环境中完成任务，并由管理员管理权限、成本和审计。**

## MVP 模块优先级

| 优先级 | 模块                               | 说明                         |
| --- | -------------------------------- | -------------------------- |
| P0  | Tenant / Workspace / User / Role | 多租户基础，必须第一天做               |
| P0  | 企业知识库                            | ACL-aware RAG、来源引用         |
| P0  | Chat + Task Console              | 员工入口                       |
| P0  | Skill Registry                   | 技能注册、权限、版本                 |
| P0  | Tool Gateway                     | MCP/API/tool 调用入口          |
| P0  | Run Trace / Audit                | 每次 agent 运行可复盘             |
| P1  | Cloud Sandbox                    | 文件、浏览器、代码执行                |
| P1  | Human Approval                   | 高风险动作审批                    |
| P1  | Cost Metering                    | token、sandbox、tool call 成本 |
| P1  | Skill Marketplace                | 企业内部 skill 复用              |
| P2  | Multi-agent Templates            | 销售、运营、客服、数据分析              |
| P2  | Self-evolving Pipeline           | 基于日志自动提出优化建议               |

---

# 13. 第一批行业场景建议

你提到“根据企业需求做定制 skill 或功能，减少冷启动”，这个方向非常对。建议先选高频、低风险、ROI 容易证明的场景。

## 13.1 电商运营

这和 CREAO 的电商运营定位最接近。CREAO 已经在公开页面强调商品描述、竞品价格、买家消息、Shopify/eBay/Amazon/Etsy 等场景。([Creao AI][1])

可做 skills：

| Skill  | 价值                            |
| ------ | ----------------------------- |
| 商品上新助手 | 标题、卖点、SEO 描述、图片 alt text、类目建议 |
| 竞品价格监控 | 定时抓取竞品价格、库存、促销                |
| 店铺客服草稿 | 结合订单、物流、售后政策生成回复              |
| 差评归因   | 分析差评、退款、投诉原因                  |
| 运营周报   | 自动汇总 GMV、转化率、广告、库存、竞品动态       |

## 13.2 销售 / GTM

| Skill              | 价值                   |
| ------------------ | -------------------- |
| Account Research   | 自动研究目标客户、组织架构、新闻、技术栈 |
| CRM Enrichment     | 补全联系人、公司信息、销售阶段      |
| Proposal Generator | 基于客户资料生成方案书          |
| Meeting Brief      | 会前简报、风险点、推荐话术        |
| Follow-up Draft    | 会后纪要、邮件、下一步行动        |

## 13.3 客服 / 售后

| Skill               | 价值             |
| ------------------- | -------------- |
| Ticket Triage       | 工单分类、优先级、情绪识别  |
| Reply Draft         | 基于知识库和订单信息生成回复 |
| QA Review           | 质检客服回复是否合规     |
| Refund Policy Agent | 判断退款、换货、赔付规则   |
| Escalation Agent    | 判断是否升级人工或主管    |

## 13.4 内部知识与行政

| Skill                  | 价值                |
| ---------------------- | ----------------- |
| Policy Q&A             | 人事、财务、法务、IT 政策问答  |
| Onboarding Agent       | 新员工入职任务、资料、权限申请   |
| Reimbursement Agent    | 报销规则解释、材料检查       |
| Contract Review Draft  | 合同条款初筛、风险点标注      |
| Meeting / Report Agent | 会议纪要、周报、月报、OKR 汇总 |

---

# 14. 技术选型建议

## 快速 MVP 组合

| 层             | 推荐                                                          |
| ------------- | ----------------------------------------------------------- |
| 前端            | Next.js / React                                             |
| 后端            | FastAPI / NestJS                                            |
| 数据库           | PostgreSQL                                                  |
| 缓存/队列         | Redis + BullMQ / Celery / Temporal                          |
| 对象存储          | S3 / MinIO                                                  |
| 向量库           | pgvector 起步；规模上来后 Qdrant / Milvus / Weaviate                |
| Agent Runtime | LangGraph 优先；OpenAI Agents SDK 可作为 OpenAI 模型链路参考            |
| Workflow      | n8n / Temporal / 自研 DAG                                     |
| Sandbox       | MVP 用 E2B / Daytona；后期 Kubernetes + Firecracker/Kata/gVisor |
| Browser       | Browser-use / Stagehand / Playwright                        |
| Tool Protocol | MCP + 内部 tool registry                                      |
| Observability | OpenTelemetry + Langfuse / LangSmith / Phoenix              |
| 权限            | OIDC/SAML + RBAC/ABAC                                       |
| 部署            | 云端 SaaS 起步；中后期支持 VPC / 私有化                                  |

## 架构取舍

| 方案                | 优点                             | 缺点                          | 建议                  |
| ----------------- | ------------------------------ | --------------------------- | ------------------- |
| 基于 Dify 二开        | 快速拥有 RAG、workflow、agent、插件     | Manus-like sandbox 和企业治理仍要补 | 适合快速验证企业知识库 + skill |
| 基于 n8n 二开         | workflow 和集成强                  | Agent 原生体验不足                | 适合流程自动化客户           |
| 基于 Suna/Kortix 研究 | 最接近 Manus-like self-host agent | 需要评估成熟度、稳定性、许可证和代码质量        | 强烈建议技术调研            |
| 从 LangGraph 自研    | 可控、长期架构清晰                      | 初期开发量大                      | 适合做长期核心 runtime     |
| 接 E2B/Daytona     | 快速获得 sandbox                   | 成本、供应商依赖、私有化限制              | MVP 阶段合适            |

我的建议是：

```text
MVP：LangGraph + PostgreSQL + pgvector + MCP + E2B/Daytona + Next.js
并行研究：Dify、n8n、Suna/Kortix、Browser-use
长期：自研 control plane + skill marketplace + enterprise governance
```

---

# 15. 安全与治理必须前置

企业 agent 最大风险不是“回答错”，而是“拿着权限做错事”。

OWASP LLM Top 10 已经把 prompt injection、insecure output handling、model DoS、supply chain、sensitive information disclosure、excessive agency 等列为 LLM 应用风险。([OWASP][33])

你们需要从第一版就做这些限制：

| 风险               | 控制措施                                          |
| ---------------- | --------------------------------------------- |
| Prompt Injection | 工具输出和用户指令分层；系统策略不可被文档覆盖                       |
| Excessive Agency | 高危动作审批；最小权限；动作白名单                             |
| 数据泄漏             | ACL-aware RAG；敏感数据脱敏；外发检测                     |
| 供应链风险            | skill 签名、扫描、审核、依赖锁定                           |
| 凭证泄漏             | secrets 不进入 sandbox；短期 token；host-side bridge |
| 成本失控             | max steps、budget、timeout、model routing、缓存     |
| 多租户串数            | tenant_id 强制过滤；测试覆盖；审计                        |
| 浏览器误操作           | domain allowlist、表单提交审批、支付/删除禁止自动执行           |

---

# 16. 推荐落地路线

## 阶段 1：企业 Agent Portal

目标：先让企业员工能用起来。

交付内容：

1. 企业空间、用户、角色、SSO。
2. 企业知识库，支持权限和来源引用。
3. Chat / Task Console。
4. 3～5 个高频 custom skills。
5. 运行日志、基础成本统计。
6. 管理员后台。

可卖的版本：

> “企业知识 + 企业技能 + 员工 AI 工作台。”

## 阶段 2：Cloud Agent Runtime

目标：从问答升级到执行。

交付内容：

1. 云端 sandbox。
2. 浏览器操作。
3. 文件生成与管理。
4. 定时任务。
5. Human approval。
6. Agent run replay。
7. Tool gateway / MCP gateway。

可卖的版本：

> “企业员工的云端 AI 助理，可以执行任务、生成交付物、调用企业系统。”

## 阶段 3：Skill Marketplace

目标：把交付能力产品化。

交付内容：

1. Skill manifest。
2. Skill 上传、审核、发布、版本管理。
3. 企业内部技能广场。
4. 行业模板库。
5. Skill 使用分析、成功率、成本、失败原因。
6. 第三方 / 解决方案团队 skill 生态。

可卖的版本：

> “企业 Agent 能力中台，业务技能可沉淀、可复用、可治理。”

## 阶段 4：Self-evolving Enterprise Agent

目标：用运行数据反哺技能和流程。

交付内容：

1. 运行失败聚类。
2. Prompt / workflow / skill patch 自动生成。
3. 自动 eval。
4. 人工审批。
5. 灰度发布。
6. 自动回滚。

可卖的版本：

> “企业 Agent 持续优化平台。”

---

# 17. 最值得重点拆解的 10 个对象

按优先级：

| 优先级 | 对象                          | 研究目的                                               |
| --- | --------------------------- | -------------------------------------------------- |
| 1   | **CREAO**                   | Skill / workflow / workspace / cloud runtime 抽象    |
| 2   | **Manus**                   | 云端电脑、长任务、planner/executor、artifact 交付              |
| 3   | **Suna/Kortix**             | 开源 Manus-like + 企业安全能力                             |
| 4   | **Dify**                    | RAG、workflow、plugin、model management、observability |
| 5   | **n8n**                     | workflow、集成生态、人机协同                                 |
| 6   | **LangGraph**               | stateful agent loop、persistence、human-in-loop      |
| 7   | **Relevance AI**            | AI workforce、marketplace、agent team                |
| 8   | **Gumloop / Zapier Agents** | 企业多人协作、SaaS 连接器、IT 管控                              |
| 9   | **E2B / Daytona**           | 云端 sandbox 基础设施                                    |
| 10  | **MCP**                     | 企业 tool / skill 标准协议                               |

---

# 18. 一句话产品建议

你们可以把第一版做成：

> **一个面向企业员工的云端 Agent 工作台：员工可以在聊天中调用公司知识库、企业系统和定制技能；Agent 在隔离云环境中执行任务、产出文件和结果；管理员可以统一管理权限、审计、成本、技能发布和知识共享。**

这比“做一个 Manus 竞品”更容易形成企业服务壁垒。Manus 的优势是通用自主执行，你们的优势应该是：

**企业交付能力、行业 skill 沉淀、权限治理、私有知识、可控执行、低冷启动。**

[1]: https://creao.ai/for/ecommerce-operators "CREAO AI | Create and run your own agents and workflows"
[2]: https://creao.ai/ai-agent-builder "AI Agent Builder: Build Free, No-Code AI Agents | CREAO"
[3]: https://creao.ai/blog/creao-2.0-composable-capabilities-and-structural-guidance-for-ai-workspaces "CREAO 2.0: Build Custom AI Workspaces Without Coding"
[4]: https://creao.ai/blog/building-cloud-agent-infrastructure "Building Cloud Agent Infrastructure: What's Different, and What We Learned"
[5]: https://manus.im/docs/introduction/welcome?utm_source=chatgpt.com "Welcome - Manus Documentation"
[6]: https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers?utm_source=chatgpt.com "How Manus Uses E2B to Provide Agents With Virtual ..."
[7]: https://openai.com/index/introducing-chatgpt-agent/?utm_source=chatgpt.com "Introducing ChatGPT agent: bridging research and action"
[8]: https://www.genspark.ai/helpcenter/super-agent "Super Agent | Genspark Help Center"
[9]: https://www.microsoft.com/en-us/microsoft-365-copilot/microsoft-copilot-studio?utm_source=chatgpt.com "Microsoft Copilot Studio | Create AI Agents"
[10]: https://cloud.google.com/products/gemini-enterprise-agent-platform?utm_source=chatgpt.com "Gemini Enterprise Agent Platform (formerly Vertex AI)"
[11]: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html?utm_source=chatgpt.com "Overview - Amazon Bedrock AgentCore"
[12]: https://www.salesforce.com/agentforce/?utm_source=chatgpt.com "Agentforce: The AI Agent Platform - Salesforce"
[13]: https://relevanceai.com/docs/get-started/introduction "Introduction - Relevance AI Documentation"
[14]: https://www.gumloop.com/?utm_source=chatgpt.com "Gumloop: Build AI agents for work"
[15]: https://zapier.com/agents?utm_source=chatgpt.com "Build AI teammates with Zapier Agents"
[16]: https://n8n.io/ai-agents/ "Build Custom AI Agents With Logic & Control | n8n Automation Platform"
[17]: https://github.com/langgenius/dify "GitHub - langgenius/dify: Production-ready platform for agentic workflow development. · GitHub"
[18]: https://docs.coze.cn/guides_agent_overview?utm_source=chatgpt.com "功能概述"
[19]: https://help.aliyun.com/zh/model-studio/single-agent-application?utm_source=chatgpt.com "智能体应用-大模型服务平台百炼(Model Studio) - 阿里云文档"
[20]: https://cloud.baidu.com/doc/qianfan/index.html?utm_source=chatgpt.com "百度千帆·大模型服务及Agent开发平台"
[21]: https://github.com/open-webui/open-webui "GitHub - open-webui/open-webui: User-friendly AI Interface (Supports Ollama, OpenAI API, ...) · GitHub"
[22]: https://docs.langchain.com/oss/python/langgraph/overview "LangGraph overview - Docs by LangChain"
[23]: https://docs.crewai.com/ "CrewAI Documentation - CrewAI"
[24]: https://learn.microsoft.com/en-us/agent-framework/overview/ "Microsoft Agent Framework Overview | Microsoft Learn"
[25]: https://github.com/kortix-ai/suna "GitHub - kortix-ai/suna: The Company AI Command Center · GitHub"
[26]: https://github.com/OpenHands/software-agent-sdk "GitHub - OpenHands/software-agent-sdk: A clean, modular SDK for building AI agents with OpenHands V1. · GitHub"
[27]: https://github.com/browser-use/browser-use "GitHub - browser-use/browser-use:  Make websites accessible for AI agents. Automate tasks online with ease. · GitHub"
[28]: https://e2b.dev/?utm_source=chatgpt.com "E2B | The Enterprise AI Agent Cloud"
[29]: https://modelcontextprotocol.io/docs/getting-started/intro?utm_source=chatgpt.com "Model Context Protocol"
[30]: https://developers.openai.com/api/docs/guides/agents/integrations-observability?utm_source=chatgpt.com "Integrations and observability | OpenAI API"
[31]: https://developers.openai.com/api/docs/guides/agents?utm_source=chatgpt.com "Agents SDK | OpenAI API"
[32]: https://developers.openai.com/api/docs/guides/agents/orchestration?utm_source=chatgpt.com "Orchestration and handoffs | OpenAI API"
[33]: https://owasp.org/www-project-top-10-for-large-language-model-applications/?utm_source=chatgpt.com "OWASP Top 10 for Large Language Model Applications"
如果你问的是 **CREAO**：
**公开资料显示，它的沙盒执行层接入了 E2B，不是完全从零自研虚拟化沙盒。** CREAO 的隐私文档在 subprocessor 里明确列了 **“E2B — Sandbox execution — Code, files during execution”**，同时列了 AWS 负责云基础设施、数据存储和计算。文档还写到 AI 生成代码运行在隔离 sandbox 中，不能访问其他用户数据，session 结束后销毁；数据保留表里写 sandbox 环境是 **session duration + 30 min idle** 后销毁。([CREAO Documentation][1])

如果你问的是 **Manus**：
**公开资料显示 Manus 也不是从零自研底层沙盒，而是基于 E2B 的虚拟电脑能力；但它不是简单“调一个在线 API”这么薄，E2B 文章写到 Manus 在自己的机器上运行 E2B，也就是偏 self-hosted / managed infra 的形态。** E2B 的案例文章明确写 Manus 使用 E2B 提供完整虚拟电脑，底层使用 Firecracker microVM；文章还写 Manus 当时半天接入部署 E2B，并且 “Manus is running E2B on their machines”。([E2B][2])

所以更准确的拆法是：

| 对象        | 沙盒底层                                      | 控制面/产品层                                             | 判断                                      |
| --------- | ----------------------------------------- | --------------------------------------------------- | --------------------------------------- |
| **CREAO** | 明确列 E2B 为 sandbox execution subprocessor  | 自己做 agent、workflow、connector、权限、billing、memory 等平台层 | **第三方沙盒 + 自研平台控制面**                     |
| **Manus** | E2B / Firecracker microVM；且有自托管 E2B 的公开说法 | 自己做 Manus Sandbox 产品体验、agent loop、文件、任务、协作、恢复等      | **E2B 底座 + 自研 agent/runtime/product 层** |

Manus 自己的文档把 Manus Sandbox 描述成“每个任务分配的完全隔离云虚拟机”，包含网络、文件系统、浏览器、软件工具，并且 sandbox 可以 sleep / awake / recycle / recreate；这说明它在产品体验上包装成了自己的 **Manus Sandbox**，但公开 E2B 案例说明底层虚拟电脑能力来自 E2B。([Manus][3]) Manus 还有一个更持久的 **Cloud Computer**，官方帮助中心称其为 cloud-hosted Ubuntu Linux VM，可以 24/7 运行应用、bot、脚本，与临时 sandbox 区分开来。([Manus Help Center][4])

---

## 对你们的启发

你们做企业 Agent 中台，**第一阶段不建议从零自研 sandbox 底层**。更合理的是分层：

```text
Agent 产品层：自研
多租户 / 权限 / 计费 / 审计：自研
Skill / MCP / connector gateway：自研
Sandbox orchestration：先半自研
底层虚拟化：先接 E2B / Daytona / 类似方案
```

也就是说：

```text
用户任务
  ↓
你们自己的 Agent Runtime / Planner / Policy Engine
  ↓
你们自己的 Tool Gateway / Secret Bridge / Audit
  ↓
E2B / Daytona / 自托管 microVM sandbox
  ↓
浏览器 / Python / Node / Shell / 文件系统 / artifact
```

CREAO 的云端 agent 基础设施文章也印证了这个分层思路：它重点讲的是 **host-side API bridge、短期 JWT、IP allowlist、credential 不进入 sandbox、billing/logs/metrics 跨 sandbox 边界统一走 bridge**。这类东西属于平台控制面和安全边界，应该由你们自己掌控，而不是完全交给沙盒供应商。([Creao AI][5])

---

## 你们可以怎么选

### MVP 阶段

用 **E2B / Daytona / Browserbase / Browser-use + Playwright** 这类现成方案，把精力放在：

1. 企业账号体系。
2. 多租户隔离。
3. Skill 注册和权限声明。
4. 企业知识库 ACL。
5. Tool Gateway。
6. Run trace / audit log。
7. 文件和 artifact 管理。
8. 成本计量。

这一阶段的目标不是证明“我们能造 VM”，而是证明“企业员工真的会用 agent 完成工作”。

### 企业化阶段

做 **self-hosted sandbox orchestration**：

```text
Kubernetes / Nomad
  + Firecracker / Kata / gVisor / E2B self-host
  + 镜像模板
  + 快照恢复
  + per-run network policy
  + secret bridge
  + artifact storage
  + audit replay
```

如果客户要求私有化、VPC、数据不出域、金融/政企合规，再推进自托管或自研 microVM 层。

### 长期阶段

你们可以逐步替换底层：

| 阶段      | 沙盒策略                                                                              |
| ------- | --------------------------------------------------------------------------------- |
| 0-6 个月  | 接 E2B / Daytona / Browserbase，快速验证产品                                              |
| 6-12 个月 | 支持 self-hosted sandbox，做企业私有化选项                                                   |
| 12 个月后  | 根据成本和合规压力，自研 microVM orchestration                                                |
| 成熟期     | 多种 runtime：browser sandbox、code sandbox、desktop sandbox、persistent cloud computer |

---

## 我的判断

**CREAO：沙盒执行层接入 E2B，平台控制面自己做。**
**Manus：底层使用 E2B / Firecracker microVM，并且有自托管 E2B 的公开说法；产品层和 agent loop 是 Manus 自己做。**

你们最适合的路线是：

> **不要一开始自研底层 sandbox；先用成熟 sandbox 服务做 MVP，把差异化放在企业权限、知识库共享、skill 广场、工具网关、审计、计费和行业定制。**

真正的壁垒不是“谁能启动一个隔离 VM”，而是：

```text
企业资源权限
+ 可复用 skill
+ 安全 tool gateway
+ agent 执行轨迹
+ 行业交付模板
+ 私有化能力
+ 成本和审计治理
```

这部分才是企业客户会付钱、也最难被通用 agent 直接替代的地方。

[1]: https://docs.creao.ai/trust-and-safety/privacy "Privacy - CREAO Documentation"
[2]: https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers "How Manus Uses E2B to Provide Agents With Virtual Computers — E2B Blog"
[3]: https://manus.im/blog/manus-sandbox "Understanding Manus sandbox - your cloud computer"
[4]: https://help.manus.im/en/articles/15392111-what-is-the-cloud-computer "What is the Cloud Computer? | Manus Help Center"
[5]: https://creao.ai/blog/building-cloud-agent-infrastructure "Building Cloud Agent Infrastructure: What's Different, and What We Learned"


有，而且不少。但要先分清楚：**目前没有一个开源项目完整覆盖你们说的“企业 agent 中台”全套能力**，比如多租户、员工 workspace、权限、知识库共享、技能市场、沙箱运行时、计费、审计、agent loop、multi-agent、self-evolving 全都做到生产级。更现实的做法是把开源项目分层看。

我会把它们分成三类：

1. **接近 CREAO / 企业 agent workspace 的完整平台**
2. **可以当底层 agent runtime / sandbox / memory / orchestration 的基础设施**
3. **可以补企业中台能力的观测、网关、自动化、知识库组件**

---

# 1. 最接近你们方向的开源/源码可用项目

## 1.1 Kortix / Suna：最像“企业 Agent OS”的方向

这个是目前我看到和你们描述最接近的。它自称是 **Company AI Command Center**，核心概念是把公司的 agents、skills、connectors、memory、policy 都放进一个可版本化的 repo 里；支持自托管、agents 并行、共享 memory、真实执行、skills、connectors、secrets、triggers、human approval、audit trail、per-resource permissions、microVM isolation 等。([GitHub][1])

它很像你们说的：

* 企业员工 agent 工作台
* skills 复用
* 共享公司 memory / context
* agent 在隔离 sandbox 里执行
* 定时 / webhook 触发
* secrets 注入
* 审批与审计
* self-evolving 通过 change request / merge 进入主分支

但注意一点：它 README 里同时出现 “open source” 和 “source-available” 的说法，而且 LICENSE 明确限制你把它作为 hosted/managed service 提供给第三方使用。也就是说，**它非常值得研究产品形态和架构，但不适合直接拿来改成你们自己的 SaaS 服务，除非你们完成法律核查或获得授权**。([GitHub][1])

**适合你们重点研究：非常高。**
它最值得你们看的不是代码，而是“公司即 repo”“skills 即文件”“agent 修改公司资产必须走 review/merge”这个设计。

---

## 1.2 Coze Studio：最适合参考“Agent Builder + 资源管理”

Coze Studio 是字节 Coze 的开源版本，GitHub 上写的是 Apache-2.0 license。它定位是 all-in-one AI agent development tool，核心模块包括 prompt、RAG、plugin、workflow、agent 构建、app 构建、knowledge base、database、API、Chat SDK 等。([GitHub][2])

它和你们的对应关系：

| 你们要做的                 | Coze Studio 对应模块                 |
| --------------------- | -------------------------------- |
| agent 创建              | Build agent                      |
| workflow / agent loop | Workflow canvas                  |
| 知识库                   | Knowledge bases                  |
| 技能 / 工具               | Plugins                          |
| API                   | OpenAPI / Chat SDK               |
| 记忆                    | agent memory                     |
| 低代码交付                 | no-code / low-code agent builder |

它更偏“agent 开发平台”，而不是完整企业 agent 中台。公开 README 里有安全提醒，比如公开网络部署前要评估注册、Python workflow code node、SSRF、横向越权等风险。([GitHub][2])

**适合你们重点研究：很高。**
尤其适合研究：资源模型、agent builder、workflow builder、plugin/knowledge/workflow 如何组织。

---

## 1.3 Dify：成熟的 agentic workflow + RAG 平台参考

Dify 定位是 production-ready platform for agentic workflow development，官网也强调 agentic workflows、RAG pipelines、integrations、observability。([GitHub][3])

它适合参考：

* 应用 / agent / workflow 的抽象
* RAG pipeline
* 模型 provider 管理
* 调试、发布、观测
* 面向企业/开发者的 AI 应用平台

但 Dify 使用的是 **Dify Open Source License**，基于 Apache 2.0 但有额外条件；如果你们要做商业 SaaS，需要认真看 license，不建议不审查就直接 fork 做商业化。([GitHub][3])

**适合你们重点研究：高。**
它更像“企业 AI 应用开发平台”，不是员工级 agent OS，但成熟度高。

---

## 1.4 MaxKB：中文企业知识库 + agent 平台参考

MaxKB 是 1Panel-dev 的项目，定位是“开源企业级智能体平台”。它集成 RAG pipeline、agentic workflow、MCP tool-use、模型无关能力，场景包括智能客服、企业内部知识库、学术研究和教育。许可证是 GPLv3。([GitHub][4])

它和你们的“企业员工共享公司资源”很贴近：

* 企业知识库
* 文档上传 / 网页抓取
* 自动分段 / 向量化
* workflow 编排
* MCP 工具调用
* 第三方系统嵌入
* 私有模型 / 公有模型接入

限制是：GPLv3 对商业产品集成有传染性风险，尤其你们要做可交付平台时，必须法务确认使用方式。

**适合你们重点研究：高。**
特别适合研究中文企业客户、知识库问答、RAG + workflow 的落地方式。

---

## 1.5 Flowise：视觉化 agent / workflow builder

Flowise 是 Apache-2.0 license，定位是 “Build AI Agents, Visually”，支持 self-host，适合快速搭建 agentic workflow、RAG、工具链、LangChain 风格组件。([GitHub][5])

它适合你们参考：

* 低代码 canvas
* 节点式 agent workflow
* LangChain 组件化
* 快速 PoC

但如果面向企业中台，它还缺很多东西：复杂权限、多租户、审计、企业知识权限继承、技能市场治理、计费、运行时隔离等。另外，这类可执行节点平台部署到公网时安全要求很高；Flowise 曾经出现过高危执行类漏洞并被报道需要升级和隔离公网暴露实例。([TechRadar][6])

**适合你们重点研究：中高。**
适合做原型，不适合作为最终企业中台内核直接套。

---

## 1.6 Activepieces / n8n：工作流自动化和连接器参考

这类不是纯 agent 平台，但很适合参考“连接企业工具”和“自动化触发”。

Activepieces 的 Community Edition 是 MIT，企业功能是商业 license；它定位为 AI agents & MCPs automation platform，可以作为 Zapier/n8n 类自动化底座参考。([GitHub][7])

n8n 是 source-available / fair-code，使用 Sustainable Use License 和 Enterprise License；它强调 self-hostable、400+ integrations、AI capabilities、custom code、workflow automation。([GitHub][8])

对你们来说，这类项目的价值在于：

* connector 市场
* workflow trigger
* webhook / schedule
* 企业 SaaS 集成
* 节点执行模型
* 自动化运行日志

但它们不是完整 agent 中台，更像“业务自动化引擎”。

---

# 2. 更底层的 agent runtime / sandbox / memory 项目

如果你们要做自己的企业中台，下面这些更像“底层零件”。

## 2.1 OpenHands：组织级 coding agent / agent automation

OpenHands 现在很值得看。它是 self-hosted developer control center for coding agents and automations，可以运行 OpenHands、Claude Code、Codex、Gemini 或 ACP-compatible agent；支持本地、Docker、VM、公司基础设施、云后端；也支持 schedule / webhook 触发，以及 Slack、GitHub、Linear、Notion 等集成。([GitHub][9])

它的官网强调 org-wide automation、自托管、隔离 sandbox、企业 SSO/RBAC/audit/budget controls/private cloud 等。([OpenHands][10])

License 上，OpenHands 主仓说明 enterprise 目录走单独 license，其他内容 MIT。([GitHub][11])

**适合你们研究：非常高，但偏工程研发场景。**
它不是通用企业员工 agent 中台，但它的“agent server + canvas + automation server + sandbox backend”很值得借鉴。

---

## 2.2 Agent Zero：一个 agent 一个完整 Linux/桌面环境

Agent Zero 很适合研究你们说的“给企业员工开虚拟环境”。它是一个 Docker 容器里的完整 Linux system，带桌面、browser、terminal、LibreOffice、plugin hub、skills、projects、memory、secrets、knowledge、multi-agent cooperation。([GitHub][12])

它更偏个人/开发者 power user，但它证明了一种形态：

> agent 不只是调用 API，而是拥有一个真实计算机环境，可以浏览器、终端、文件、桌面软件一起用。

对企业中台来说，它启发很大：

* 每个 agent run 对应一个隔离 workspace
* agent 能操作浏览器、文件、表格、PPT
* skills 可以动态加载
* 项目级隔离 memory/secrets/knowledge
* superior agent 可以创建 subagents

**适合你们研究：高。**
尤其适合 runtime / sandbox / 文件产物 / agent 可视化执行。

---

## 2.3 E2B：AI agent 的安全沙箱基础设施

E2B 是 open-source infrastructure，用来在云端安全隔离 sandbox 里运行 AI-generated code；它提供 Python / JavaScript SDK 创建 sandbox、执行命令。([GitHub][13])

它很适合作为你们的底层 runtime 参考：

* 每次 agent run 一个 sandbox
* 代码执行隔离
* 文件产物保存
* 依赖安装
* 资源配额
* 云端运行

如果你们要自己做“企业虚拟环境”，可以重点研究 E2B 的抽象，而不是从 Kubernetes job / Firecracker / Docker isolation 全部从零摸索。

---

## 2.4 Letta：长期记忆 / stateful agents

Letta 原来叫 MemGPT，定位是 advanced memory / stateful agents。它支持 Letta Code、本地 agent、skills、subagents、Letta API、Python/TypeScript SDK，许可证是 Apache-2.0。([GitHub][14])

它适合你们研究：

* agent memory 分层
* long-term memory
* agent identity
* self-improving agents
* memory block 设计
* stateful agent API

对企业版来说，你们不能只做“用户偏好记忆”，还需要“组织记忆”：公司 SOP、项目规则、团队决策、部门偏好、agent playbook。Letta 的 memory-first 思路值得参考。

---

## 2.5 LangGraph / CrewAI：multi-agent / agent loop 编排框架

LangGraph 是低层 orchestration framework，用于构建 long-running、stateful agents，强调 durable execution、human-in-the-loop、short-term/long-term memory、debugging、production deployment。([GitHub][15])

CrewAI 是 MIT license 的 Python multi-agent automation framework，提供 Crews 和 Flows 两种抽象：一个偏角色协作，一个偏事件驱动流程。([GitHub][16])

它们不提供完整企业平台，但很适合做你们自己的 agent runtime 编排层：

```text
Planner
  → Research Agent
  → Tool Executor
  → Reviewer
  → Publisher
  → Human Approval
```

如果你们不想一开始就写一套 agent graph engine，可以从 LangGraph/CrewAI 里选一个做底层实验。

---

# 3. 企业中台需要补的基础设施项目

## 3.1 RAGFlow：企业知识库 / context engine

RAGFlow 是 Apache-2.0 的 RAG engine，强调把 RAG 和 agent capabilities 融合，提供 context layer、ETL、文档解析、pre-built agent templates。([GitHub][17])

它适合做你们的：

* 文档解析
* 企业知识库
* 复杂 PDF / 文档理解
* RAG pipeline
* citation / grounded answer
* agent context layer

如果你们第一阶段想主打“共享公司资源能力”，RAGFlow、MaxKB、Dify 都值得看。

---

## 3.2 LiteLLM：模型网关 / 计费 / 限额

LiteLLM 是 open-source AI Gateway，可以统一调用 100+ LLM providers，并提供 virtual keys、spend tracking、guardrails、load balancing、admin dashboard。([GitHub][18])

这正好对应你们的：

* 多模型接入
* 企业 API key 管理
* 部门预算
* 用户限额
* 模型路由
* 成本统计
* failover / load balancing

企业 agent 中台最好不要让每个 agent 直接拿模型 key。正确做法是统一走 model gateway。

---

## 3.3 Langfuse / Phoenix / Promptfoo：观测、评测、回归测试

Langfuse 是 open-source LLM engineering platform，支持 tracing、prompt management、evaluations、metrics、debugging，可以 self-host。([GitHub][19])

Promptfoo 是 MIT licensed 的 LLM evals / red-teaming CLI 和库，可以做 prompt、RAG、agent 的自动化评测。([GitHub][20])

这块对“self-evolving”尤其重要。企业里不要让 agent 自动改自己然后直接上线，建议做成：

```text
run logs
  → 自动总结失败/成功模式
  → 生成改进 proposal
  → 跑 eval / regression test
  → owner 审批
  → 发布 agent/skill 新版本
```

Langfuse / Promptfoo 就可以作为这条链路的基础设施参考。

---

# 4. 我建议你们优先研究的清单

如果只选 8 个，我会这样排优先级：

| 优先级 | 项目                     | 为什么看                                                |
| --- | ---------------------- | --------------------------------------------------- |
| P0  | **Kortix/Suna**        | 最像“企业 Agent OS / Company AI Command Center”         |
| P0  | **Coze Studio**        | agent builder、workflow、plugin、knowledge、API 体系完整    |
| P0  | **MaxKB**              | 中文企业知识库 + agent workflow，很贴近国内企业交付                  |
| P0  | **OpenHands**          | self-hosted agent server、sandbox backend、自动化、企业治理参考 |
| P1  | **Dify**               | 成熟 AI app / agentic workflow / RAG 平台               |
| P1  | **E2B**                | agent sandbox runtime 参考                            |
| P1  | **Letta**              | memory / stateful agents / self-improving 参考        |
| P1  | **Langfuse + LiteLLM** | 观测、评估、模型网关、成本控制                                     |

---

# 5. 如果你们要快速做 MVP，可以这样组合

我会建议你们不要直接 fork 一个大平台改，而是用开源项目拆分参考，自己做企业壳和核心数据模型。

## MVP 参考架构

```text
企业门户 / Admin Console
  ├── Tenant / Org / Workspace / User / Role
  ├── Agent Store
  ├── Skill Store
  ├── Knowledge Base
  ├── Connector Center
  ├── Billing / Quota / Cost
  └── Audit / Approval

Agent Runtime
  ├── LangGraph / CrewAI：agent loop & multi-agent orchestration
  ├── E2B / Docker / microVM：sandbox execution
  ├── Browser-use / Steel / Playwright：browser tool
  ├── Letta-style memory：personal/team/company memory
  └── Scheduler / Webhook / API trigger

Enterprise Context
  ├── RAGFlow / MaxKB-style document pipeline
  ├── pgvector / Milvus / Elasticsearch
  ├── permission-aware retrieval
  └── citation / source tracking

Platform Infra
  ├── LiteLLM：model gateway, budget, rate limit
  ├── Langfuse：trace, prompt version, eval
  ├── Keycloak / Authentik：SSO/RBAC
  ├── Postgres：metadata
  └── Object Storage：files/artifacts/logs
```

---

# 6. 对你们最有价值的产品判断

你们要做的东西，不要简单对标 Dify/Coze/Flowise 这种 **agent builder**。更好的定位是：

> **企业 Agent Enablement Platform：给每个员工一个可控 agent workspace，让公司知识、工具、skills、memory、流程可以被安全复用。**

所以你们应该重点学习：

* **Kortix/Suna**：公司级 agent OS 的产品叙事和 self-evolving via repo/review
* **Coze Studio**：agent / workflow / plugin / knowledge 的 builder 体验
* **MaxKB/RAGFlow**：企业知识库和 RAG 落地
* **OpenHands/Agent Zero/E2B**：真实 sandbox runtime
* **LiteLLM/Langfuse**：企业级模型网关、成本、观测和评测

最大的机会不是“又做一个 Dify”，而是做：

> **企业可交付的 agent 中台：多租户 + 权限 + 知识共享 + 私有 skill 市场 + 沙箱运行 + 审计审批 + 成本治理 + 定制交付。**

这里开源项目很多，但真正把这些拼成企业级交付平台的，还没有特别成熟的标准答案。

[1]: https://github.com/kortix-ai/suna "GitHub - kortix-ai/suna: The Company AI Command Center · GitHub"
[2]: https://github.com/coze-dev/coze-studio "GitHub - coze-dev/coze-studio: An AI agent development platform with all-in-one visual tools, simplifying agent creation, debugging, and deployment like never before. Coze your way to AI Agent creation. · GitHub"
[3]: https://github.com/langgenius/dify "GitHub - langgenius/dify: Production-ready platform for agentic workflow development. · GitHub"
[4]: https://github.com/1Panel-dev/MaxKB?ref=www.cosmocode.app "GitHub - 1Panel-dev/MaxKB at www.cosmocode.app · GitHub"
[5]: https://github.com/flowiseai/flowise "GitHub - FlowiseAI/Flowise: Build AI Agents, Visually · GitHub"
[6]: https://www.techradar.com/pro/security/top-open-source-ai-platform-flowise-hit-by-maximum-level-security-issue?utm_source=chatgpt.com "Top open source AI platform Flowise hit by maximum-level security issue"
[7]: https://github.com/activepieces/activepieces?utm_source=chatgpt.com "activepieces/activepieces: AI Agents & MCPs ..."
[8]: https://github.com/n8n-io/n8n "GitHub - n8n-io/n8n: Fair-code workflow automation platform with native AI capabilities. Combine visual building with custom code, self-host or cloud, 400+ integrations. · GitHub"
[9]: https://github.com/OpenHands/openhands "GitHub - OpenHands/OpenHands:  OpenHands: AI-Driven Development · GitHub"
[10]: https://www.openhands.dev/ "OpenHands | The Open Platform for Cloud Coding Agents"
[11]: https://github.com/OpenHands/OpenHands/blob/main/LICENSE "OpenHands/LICENSE at main · OpenHands/OpenHands · GitHub"
[12]: https://github.com/agent0ai/agent-zero "GitHub - agent0ai/agent-zero: Agent Zero AI framework · GitHub"
[13]: https://github.com/e2b-dev/e2b "GitHub - e2b-dev/E2B: Open-source, secure environment with real-world tools for enterprise-grade agents. · GitHub"
[14]: https://github.com/letta-ai/letta "GitHub - letta-ai/letta: Platform for stateful agents: AI with advanced memory that can learn and self-improve over time. · GitHub"
[15]: https://github.com/langchain-ai/langgraph "GitHub - langchain-ai/langgraph: Build resilient agents. · GitHub"
[16]: https://github.com/crewaiinc/crewai "GitHub - crewAIInc/crewAI: Framework for orchestrating role-playing, autonomous AI agents. By fostering collaborative intelligence, CrewAI empowers agents to work together seamlessly, tackling complex tasks. · GitHub"
[17]: https://github.com/infiniflow/ragflow "GitHub - infiniflow/ragflow: RAGFlow is a leading open-source Retrieval-Augmented Generation (RAG) engine that fuses cutting-edge RAG with Agent capabilities to create a superior context layer for LLMs · GitHub"
[18]: https://github.com/BerriAI/litellm "GitHub - BerriAI/litellm: Python SDK, Proxy Server (AI Gateway) to call 100+ LLM APIs in OpenAI (or native) format, with cost tracking, guardrails, loadbalancing and logging. [Bedrock, Azure, OpenAI, VertexAI, Cohere, Anthropic, Sagemaker, HuggingFace, VLLM, NVIDIA NIM] · GitHub"
[19]: https://github.com/langfuse/langfuse "GitHub - langfuse/langfuse:  Open source AI engineering platform: LLM evals, observability, metrics, prompt management, playground, datasets. Integrates with OpenTelemetry, LangChain, OpenAI SDK, LiteLLM, and more. YC W23 · GitHub"
[20]: https://github.com/promptfoo/promptfoo?utm_source=chatgpt.com "Promptfoo: LLM evals & red teaming"
可以做，而且方向很清晰：你要做的不是“Dify/Coze 那种 agent builder 的平替”，而是 **CREAO / Manus 这种“通用执行型 Agent Workspace”**。

我先给结论：

> **最值得借鉴的开源项目：Suna、Agent Zero、OpenHands、OpenManus、E2B、browser-use、Open Computer Use、Agent-S。**
> **最值得借鉴的底层技术：沙箱虚拟电脑、浏览器控制、Planner/Executor agent loop、artifact 生成、skill/plugin 系统、MCP connector、memory、run trace、human approval、agent-to-workflow 沉淀。**

但要注意：**没有一个开源项目可以直接拿来变成 CREAO/Manus 竞品**。你们更现实的打法是：
**自研产品壳 + 借鉴/复用开源 runtime、browser、orchestration、RAG、observability、model gateway。**

---

# 1. CREAO / Manus 这类产品的本质

CREAO 的公开文档说得很直接：用户可以直接在 `agent.creao.ai` 开始聊天，super agent 可以生成 web app、分析数据集、写报告、抓网页、生成图表、发邮件、构建 PDF、自动化 workflow，并且“在完整 Linux sandbox 中运行代码、安装包、调用 API、产出真实文件”。CREAO 还支持把一次成功 chat 保存成 reusable agent，带结构化输入、skill definition、版本控制和定时运行。([CREAO Documentation][1])

Manus 的定位也类似：官网把自己描述成“action engine”，能 create slides、build website、develop desktop apps、design；它的 Browser Operator 还可以在用户当前浏览器上下文中运行，使用已有登录态和 active tabs 来执行多步网页任务。([Manus][2])

E2B 对 Manus 的技术拆解非常关键：Manus 不是单个 LLM agent，而是一个更复杂的协调系统，先由 planner agent 拆解任务，再由 executor agents 用 web browsing、file search、terminal commands 等工具执行；同时 Manus 需要一个完整 cloud computer，里面能跑 Python、JavaScript、Bash、Chromium browser、filesystem，并能 pause/resume。([E2B][3])

所以你们要做的竞品，可以定义为：

> **General-purpose Agent Computer / Agent Workspace**
> 用户用 chat 下达目标，系统在云端虚拟电脑里计划、浏览、写代码、操作文件、生成交付物，并把成功流程沉淀为可复用 agent / skill / workflow。

---

# 2. 最值得看的开源项目

## 第一梯队：最像 CREAO / Manus 的项目

| 项目                | 借鉴价值                                                                                                                                                                                                  | 适合怎么用                                                      | 许可证/风险                                                                                              |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **Kortix / Suna** | 最像“公司级 agent command center”。它把 agents、skills、connectors、automations、memory 放在一个 repo 里，session 在独立 Linux sandbox + branch 里跑，agent 产出的改动通过 change request 审批进入 main。([GitHub][4])                    | 重点研究产品形态、数据模型、自进化机制、skill/memory/repo 化设计                  | **不要直接 fork 做 SaaS**。它现在是 Elastic License 2.0，明确限制把软件作为 hosted/managed service 提供给第三方。([GitHub][5]) |
| **Agent Zero**    | 非常像“给 agent 一台完整电脑”。一个 Docker container 里有完整 Linux system、desktop、plugin hub、skills；它还提供可视化 Linux desktop canvas，agent 可以操作 GUI 软件、terminal、files。([GitHub][6])                                       | 重点研究云端电脑、桌面流、plugin/skill、agent 工作台                        | MIT，商业集成相对友好。([GitHub][7])                                                                          |
| **OpenHands**     | 偏 coding agent，但它的“agent control center”非常值得借鉴：self-hosted、支持 OpenHands/Claude Code/Codex/Gemini/ACP agents、本地/Docker/VM/云后端、schedule/webhook automations、Slack/GitHub/Linear/Notion 集成。([GitHub][8]) | 借鉴 agent server、任务 canvas、automation、sandbox backend、长任务执行 | 主体 MIT，但 enterprise 目录单独许可证。([GitHub][9])                                                           |
| **OpenManus**     | 明确是 Manus 的开源复刻方向，适合看最小 agent loop、tool use、planning 的实现。项目 README 也说明它是“simple implementation”。([GitHub][10])                                                                                        | 适合快速 PoC，不适合直接当生产平台                                        | MIT。([GitHub][11])                                                                                  |

我的判断：
**如果你们要做 CREAO/Manus 竞品，Suna 是最值得产品层研究的，Agent Zero 是最值得 runtime/UI 研究的，OpenHands 是最值得任务控制台和工程化研究的，OpenManus 是最值得快速验证 agent loop 的。**

---

## 第二梯队：底层 runtime / browser / computer-use

| 项目                    | 作用                                                                                                                                 | 借鉴点                                                                         |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| **E2B**               | AI agent sandbox 基础设施，提供安全隔离的 cloud sandbox，支持 JS/Python SDK 创建 sandbox 并执行命令。([GitHub][12])                                       | Manus 类产品的关键底座：microVM / sandbox / command / filesystem / session lifecycle |
| **Open Computer Use** | 基于 E2B Desktop Sandbox 的开源 computer-use demo，支持键盘、鼠标、shell、live display streaming、pause and prompt。([GitHub][13])                  | 借鉴“云端桌面 + 用户实时观看 + 可暂停接管”的体验                                                |
| **browser-use**       | AI browser agent，基于 Playwright，让 agent 可以自动化网页；项目 MIT，生态很活跃。([GitHub][14])                                                         | 借鉴 browser action abstraction、DOM/视觉结合、代理/stealth/并发浏览器能力                   |
| **Agent-S**           | 开源 computer-use agent 框架，目标是让 agent 像人一样操作电脑，覆盖 Linux/Mac/Windows，并强调 memory、planning、grounding、computer automation。([GitHub][15]) | 借鉴 GUI grounding、屏幕理解、鼠标键盘控制、OSWorld 评测思路                                   |

这里最关键的是：
**你们不要只做“浏览器插件/网页自动化”，也不要只做“代码解释器”。CREAO/Manus 的壁垒是完整执行环境：browser + terminal + filesystem + packages + artifacts + session resume。**

---

## 第三梯队：Agent builder / RAG / workflow 平台

| 项目                     | 借鉴价值                                                                                                                                        | 风险                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| **Coze Studio**        | 字节开源的 all-in-one agent development platform，包含 agent、workflow、plugin、knowledge、API/SDK 等，适合参考 builder、plugin、knowledge 的产品组织。([GitHub][16]) | Apache 2.0，相对友好。([GitHub][16])                     |
| **Dify**               | 成熟的 agentic workflow + RAG + app 平台，适合参考 workflow、RAG、model provider、应用发布。([GitHub][17])                                                    | Dify license 有额外条件，未经授权不能用源码运营多租户环境。([GitHub][18]) |
| **RAGFlow**            | RAG engine + agent capabilities，适合做企业知识库、文档解析、可信引用、混合检索。([GitHub][19])                                                                      | Apache 2.0。([GitHub][20])                          |
| **MaxKB**              | 中文企业知识库 + RAG + workflow + MCP tool-use，适合看国内企业知识库 agent 的交付形态。([GitHub][21])                                                               | GPLv3，商业集成要谨慎。([GitHub][22])                       |
| **Activepieces / n8n** | 自动化 connector/workflow 生态。Activepieces 是 MIT，n8n 是 fair-code/Sustainable Use License。([GitHub][23])                                         | 可以借鉴 connector 和 workflow，不建议把 n8n 直接嵌进商业 SaaS 核心  |

这类项目更像“构建工具”，而 CREAO/Manus 更像“执行型 AI 员工”。你们可以借鉴它们的 builder、知识库、connector，但核心差异化还是要放在 **agent computer runtime + artifacts + long-running task reliability**。

---

## 第四梯队：编排、记忆、观测、计费基础设施

| 模块                     | 推荐项目          | 借鉴点                                                                                                             |
| ---------------------- | ------------- | --------------------------------------------------------------------------------------------------------------- |
| Agent orchestration    | **LangGraph** | 长任务、stateful agents、human-in-the-loop、短期/长期 memory、失败恢复。([GitHub][24])                                          |
| Multi-agent            | **CrewAI**    | 角色型 multi-agent、Crews/Flows、生产级多 agent workflow。([GitHub][25])                                                  |
| Memory                 | **Letta**     | Stateful agents、长期记忆、agent identity、持续学习。([GitHub][26])                                                         |
| Model gateway          | **LiteLLM**   | 统一 100+ LLM providers、virtual keys、spend tracking、guardrails、load balancing、admin dashboard。([GitHub][27])      |
| Observability          | **Langfuse**  | LLM tracing、prompt management、eval、debug、cost/latency 观测。([GitHub][28])                                         |
| Eval / red-team        | **Promptfoo** | LLM eval、agent/RAG 测试、red teaming，MIT。([GitHub][29])                                                            |
| Connector protocol     | **MCP**       | 标准化 tools/resources/prompts，让 agent 调外部系统。MCP spec 明确 tools 可让模型查询数据库、调 API、执行计算。([Model Context Protocol][30]) |
| Agent interoperability | **A2A**       | agent-to-agent 通讯协议，适合未来不同 agent 系统互通。([Google Developers Blog][31])                                            |

---

# 3. 你们要做的产品，建议拆成 7 层

## 3.1 Chat + Artifact Workspace

用户看到的不是普通聊天框，而是：

```text
左侧：对话 / 任务目标
中间：agent plan / todo / run timeline
右侧：artifact preview / browser / terminal / files
底部：human approval / pause / resume / rerun / save as agent
```

必须支持：

* 流式展示 agent 正在做什么；
* 计划图 / TODO list；
* 浏览器 live view；
* terminal 输出；
* 文件树；
* 产物预览：HTML、PDF、PPT、Excel、图片、代码、报告；
* 用户可暂停、补充指令、批准敏感动作；
* 一键“保存为 agent”。

CREAO 的强点就是“chat once, run forever”：一次成功会话可以变成带输入表单、版本控制、集成和定时运行的 agent。([Creao AI][32])

---

## 3.2 Agent Orchestrator

建议第一版不要做太复杂，先做一个稳定的四段式：

```text
Planner
  → Executor
  → Reviewer
  → Publisher
```

每个 session 里保存：

```text
user_goal
plan
subtasks
tool_calls
browser_actions
terminal_commands
files_created
artifacts
cost
errors
human_interventions
final_result
```

技术上可以用 LangGraph 做 state machine，也可以自研一个轻量 graph engine。关键不是“有没有 multi-agent 名字”，而是：

* 每一步可观测；
* 每一步可重试；
* 每一步可中断；
* 每一步有权限边界；
* 出错后能恢复上下文；
* 能把成功轨迹转成 agent template。

---

## 3.3 Sandbox / Virtual Computer

这是产品成败的核心。

Manus 之所以像“真正干活”，不是因为 prompt，而是它有完整 cloud computer：browser、terminal、filesystem、代码运行、包安装、长时间 session、pause/resume。E2B 的 Manus 案例里也提到，Docker 启动慢且不像完整 OS，Manus 最终需要 Firecracker microVM 这类更完整的隔离环境。([E2B][3])

你们可以分阶段：

### MVP 阶段

```text
Kubernetes Job / Docker sandbox
  + Playwright Chromium
  + Python/Node runtime
  + mounted workspace
  + artifact storage
```

### 生产阶段

```text
Firecracker microVM / E2B-like sandbox
  + per-session isolated VM
  + network policy
  + secret injection
  + resource quota
  + filesystem snapshot
  + suspend/resume
  + audit log
```

### 企业阶段

```text
tenant base image
department runtime policy
agent runtime snapshot
private dependency cache
egress allowlist
DLP / secret scanning
human approval before write actions
```

---

## 3.4 Browser / Computer Use

这里有两条路线：

### 路线 A：云端浏览器

适合大多数任务：

```text
agent → Playwright/browser-use → cloud Chromium → screenshots/DOM/actions
```

优点是可控、可审计、可复现。
缺点是登录态、验证码、企业内网系统会麻烦。

### 路线 B：本地浏览器 operator

Manus Browser Operator 的思路是：直接在用户当前浏览器上下文里运行，使用用户已有登录态、active tabs 和本地 IP。([Manus][33])

你们可以后面做一个 Chrome extension：

```text
cloud agent plan
  → local extension receives action
  → user browser executes click/type/extract
  → result streamed back to cloud
```

这对 CRM、LinkedIn、Google Ads、Shopify、飞书、企业微信、内网后台特别有价值。

---

## 3.5 Skills / Plugins / Connectors

你们要做 CREAO/Manus 竞品，skill 不是“prompt 模板”，而是：

```text
skill = instructions + tools + schemas + examples + tests + permissions + runtime deps
```

可以参考 CREAO 的 skill/connectors 设计：CREAO 区分 skills 和 connectors，skills 是任务指令包，connectors 是通过 MCP/API 连接外部服务；它还支持通过 GitHub repo、ZIP、SKILL.md 创建 custom skill。([CREAO Documentation][1])

建议你们的 skill 结构：

```yaml
name: ecommerce_listing_optimizer
version: 1.2.0
description: Optimize product listing for Shopify and Amazon
inputs:
  - product_url
  - target_keywords
permissions:
  tools:
    browser: read
    shopify: write_requires_approval
    filesystem: write
runtime:
  python:
    packages:
      - pandas
      - beautifulsoup4
tests:
  - sample_input: ...
    expected_artifact: listing_report.md
```

企业版要补：

* skill 审批；
* skill 版本；
* skill 回滚；
* skill 权限；
* skill 使用成本；
* skill 运行成功率；
* skill 评价；
* skill 私有市场。

---

## 3.6 Memory / Knowledge

CREAO 的方向是 agent 记住 context、preferences、decisions；Letta 这类项目则更强调 stateful agents 和长期 memory。([Creao AI][32])

但企业竞品不能只做个人记忆，要分层：

```text
Personal Memory
  员工偏好、输出格式、常用语言

Workspace Memory
  项目背景、当前目标、团队约定

Company Memory
  品牌口径、产品资料、SOP、合规要求

Agent Memory
  某个 agent 的历史运行经验、失败模式、成功步骤

Skill Memory
  某类任务的最佳实践和样例
```

知识库也不要只做 RAG。要做：

* 文档解析；
* 权限继承；
* 引用来源；
* 过期检测；
* 命中统计；
* agent 使用记录；
* 敏感信息过滤；
* tool action 前的上下文校验。

RAGFlow / MaxKB 可以重点参考企业知识库、文档解析、RAG workflow 和 agent tool-use。([GitHub][19])

---

## 3.7 Observability / Billing / Governance

CREAO/Manus 这类产品会非常烧钱，因为每个 run 都可能包含：

* 多轮 LLM 调用；
* 浏览器 session；
* sandbox 运行时间；
* 文件生成；
* 外部 API；
* retry；
* multi-agent 并行。

所以从第一天就要有：

```text
run_id
tenant_id
user_id
agent_id
model_calls
tool_calls
sandbox_seconds
browser_seconds
tokens
cost
artifacts
approval_events
errors
```

技术组件可以这样配：

```text
LiteLLM       → 模型网关、key、限额、成本
Langfuse      → trace、prompt、eval、latency、cost
Promptfoo     → agent/skill 回归测试和 red-team
Postgres      → metadata
Redis/Queue   → task scheduling
S3/MinIO/R2   → artifacts/files/logs
```

LiteLLM 适合做模型统一网关和成本控制，Langfuse 适合做 LLM/agent trace 与评估，Promptfoo 适合做上线前测试和 red-team。([GitHub][27])

---

# 4. 推荐技术架构

我建议你们不要直接 fork 一个大项目，而是自研 control plane，复用开源模块。

```text
Frontend
  ├── Chat UI
  ├── Run Timeline
  ├── Browser Live View
  ├── Terminal / Logs
  ├── File Tree
  ├── Artifact Preview
  └── Agent Builder / Skill Store

API / Control Plane
  ├── Tenant / Workspace / User / Role
  ├── Agent Registry
  ├── Skill Registry
  ├── Connector Center
  ├── Knowledge Base
  ├── Memory Service
  ├── Billing / Quota
  ├── Audit Log
  └── Approval Flow

Agent Orchestrator
  ├── Planner
  ├── Executor
  ├── Browser Agent
  ├── Code Agent
  ├── Research Agent
  ├── Reviewer
  └── Publisher

Runtime Plane
  ├── Sandbox Manager
  ├── Browser Manager
  ├── Filesystem / Artifact Store
  ├── Secret Injection
  ├── Network Policy
  ├── Snapshot / Resume
  └── Resource Quota

Tool / Connector Layer
  ├── MCP Servers
  ├── OAuth Connectors
  ├── Internal APIs
  ├── Browser Actions
  ├── Terminal Commands
  └── File Operations

Infra
  ├── Postgres
  ├── Redis / Queue
  ├── Object Storage
  ├── Vector DB / Search
  ├── LiteLLM
  ├── Langfuse
  └── Promptfoo
```

---

# 5. 三种落地路线

## 路线 A：最快做 demo

适合 2-4 周验证。

```text
OpenManus / LangGraph
  + browser-use
  + E2B cloud sandbox
  + Next.js chat UI
  + S3 artifact storage
  + LiteLLM
```

能做出来：

* 用户输入任务；
* agent 拆解步骤；
* 打开浏览器；
* 跑代码；
* 生成报告/表格/网页；
* 展示运行日志；
* 下载 artifacts。

缺点：

* 多租户弱；
* 企业权限弱；
* long-running reliability 弱；
* skill marketplace 弱。

---

## 路线 B：可商业 PoC

适合 2-3 个月。

```text
自研 Control Plane
  + LangGraph orchestration
  + E2B / self-host sandbox
  + browser-use / Playwright
  + RAGFlow document pipeline
  + LiteLLM model gateway
  + Langfuse tracing
  + Promptfoo eval
  + Postgres / Redis / S3
```

能做出来：

* 企业 tenant；
* workspace；
* agent run；
* artifact preview；
* basic skills；
* connectors；
* run history；
* cost tracking；
* approval；
* scheduled runs；
* API trigger。

这条路线最适合你们现在的企业服务方向。

---

## 路线 C：企业级 CREAO/Manus 竞品

适合 6-12 个月。

```text
自研 Agent OS
  + microVM runtime
  + browser operator extension
  + private skill marketplace
  + enterprise memory
  + permission-aware RAG
  + SSO/RBAC/ABAC
  + audit/compliance
  + eval/regression
  + self-evolving proposal system
```

核心壁垒：

* sandbox 可靠性；
* 浏览器成功率；
* 权限和审计；
* 企业知识融合；
* skill 交付体系；
* artifact 质量；
* 长任务可恢复；
* 成本控制；
* agent run 到 reusable agent 的沉淀能力。

---

# 6. 功能优先级

## P0：MVP 必须有

```text
1. Chat task interface
2. Planner / executor agent loop
3. Cloud sandbox
4. Browser automation
5. Terminal/code execution
6. File/artifact generation
7. Run timeline
8. Pause / resume / stop
9. Human approval for risky actions
10. Save successful run as agent
```

## P1：开始像 CREAO/Manus

```text
1. Agent template + structured input form
2. Skill system
3. Connector system / MCP
4. Workspace files
5. Memory
6. Scheduled runs
7. API trigger
8. Cost tracking
9. Artifact preview panel
10. Team sharing
```

## P2：企业化竞争力

```text
1. Multi-tenant
2. RBAC / ABAC
3. SSO
4. Secret vault
5. Audit log
6. Permission-aware RAG
7. Runtime policy
8. Private skill marketplace
9. Department quota / billing
10. Agent eval / regression test
```

## P3：自进化

```text
1. Run feedback collection
2. Failure pattern extraction
3. Agent improvement proposal
4. Test set generation
5. Regression eval
6. Human approval
7. Version release
8. Rollback
```

企业场景下，self-evolving 不建议一开始做成“agent 自动改自己并上线”。更稳的方式是：

```text
agent run
  → 记录轨迹
  → 总结失败/成功模式
  → 生成改进建议
  → 自动跑测试
  → owner 审批
  → 发布新版本
```

---

# 7. 关键技术难点

## 7.1 长任务可靠性

Manus/CREAO 类产品会遇到大量长任务：

* 10-30 分钟研究；
* 多网页抓取；
* 生成 PPT；
* 写代码并调试；
* 跑数据分析；
* 访问多个 SaaS 后台。

必须做：

```text
checkpoint
retry
resume
tool timeout
browser crash recovery
sandbox keepalive
partial artifact save
run replay
```

---

## 7.2 浏览器成功率

浏览器 agent 最容易失败：

* 登录态；
* 验证码；
* 二次验证；
* 反爬；
* 动态 DOM；
* shadow DOM；
* iframe；
* 文件下载；
* 多 tab；
* popup；
* 权限弹窗。

建议做两套浏览器：

```text
Cloud Browser
  适合公开网页、数据抓取、常规操作

Local Browser Operator
  适合 CRM、广告后台、电商后台、企业内网、用户已登录系统
```

这正好对应 Manus Browser Operator 的方向：它利用用户当前浏览器会话和本地 IP 来减少登录和访问控制问题。([Manus][33])

---

## 7.3 安全和权限

你们做企业服务，这部分必须比 CREAO/Manus 更强。

重点是：

```text
agent 不能默认拿到所有数据
agent 不能默认调用所有工具
agent 不能默认执行 destructive actions
agent 不能默认读取 secrets
agent 不能默认把内部数据发到外部网站
```

权限应该按动作分级：

```text
read
write
publish
delete
financial
admin
external_send
pii_access
secret_access
```

高风险动作必须 human approval。

---

## 7.4 成本控制

一个 Manus-like run 的成本不只是 token：

```text
LLM token
browser runtime
sandbox runtime
proxy
storage
embedding
reranking
connector API
retry
multi-agent parallelism
```

所以计费对象最好是：

```text
seat
credits
agent run
sandbox minutes
browser minutes
premium skill
connector usage
storage
enterprise package
```

不要只按 token 卖，企业客户更容易理解“多少员工、多少 agent、多少任务、多少自动化额度”。

---

# 8. 我建议你们重点研究的项目顺序

按优先级：

1. **Suna**：研究产品形态、agent command center、repo 化 company brain、change request 自进化；但不要直接用于托管 SaaS，因为许可证限制 hosted/managed service。([GitHub][4])
2. **Agent Zero**：研究“一个 agent 一台 Linux 电脑”的 UX、plugin hub、desktop canvas。([GitHub][6])
3. **OpenHands**：研究 agent control plane、自动化、后端切换、工程化长任务。([GitHub][8])
4. **E2B / Open Computer Use**：研究 sandbox、desktop streaming、pause/resume、cloud computer。([GitHub][12])
5. **browser-use**：研究网页自动化和 browser agent 抽象。([GitHub][14])
6. **OpenManus**：研究最小 Manus-like loop。([GitHub][10])
7. **Coze Studio / Dify**：研究 builder、workflow、plugin、knowledge 的产品组织，但注意 Dify 多租户商业使用限制。([GitHub][16])
8. **RAGFlow / MaxKB**：研究企业知识库和 RAG + agent 落地。([GitHub][19])
9. **LiteLLM / Langfuse / Promptfoo**：研究模型网关、成本、trace、eval、red-team。([GitHub][27])

---

# 9. 一个更具体的 MVP 方案

我会建议你们第一版做成：

## 产品名内部定义

```text
Agent Workspace
```

## 用户体验

```text
用户输入：帮我调研 5 个竞品，生成对比表和一页总结
系统：
  1. 生成计划
  2. 打开浏览器查资料
  3. 抓取网页
  4. 运行代码整理数据
  5. 生成 markdown/report/xlsx
  6. 展示 artifacts
  7. 问用户是否保存为 agent
```

## 技术组合

```text
Frontend:
  Next.js + WebSocket/SSE + artifact preview + browser stream

Backend:
  FastAPI / Node / Go
  Postgres
  Redis queue
  S3/MinIO/R2

Agent:
  LangGraph or self-built state machine
  planner/executor/reviewer

Runtime:
  E2B first
  later self-host Firecracker/microVM

Browser:
  browser-use + Playwright

Knowledge:
  RAGFlow or custom pgvector pipeline

Model:
  LiteLLM gateway

Trace:
  Langfuse

Eval:
  Promptfoo

Auth:
  tenant/workspace/user/role from day one
```

## 第一批 vertical skills

你们面向企业服务，不要一开始做太泛。建议先做 3-5 个高频场景：

```text
竞品调研 agent
市场报告 agent
电商 listing agent
销售线索整理 agent
会议纪要转任务 agent
数据分析报告 agent
PPT/文档生成 agent
```

这类场景能体现 Manus/CREAO 的“真干活”：浏览、抓取、分析、生成文件，而不是只回答问题。

---

# 10. 最重要的判断

你们要做 CREAO/Manus 竞品，真正的壁垒不是：

```text
prompt
workflow canvas
RAG
multi-agent 名词
```

真正的壁垒是：

```text
1. 云端虚拟电脑稳定运行
2. 浏览器/终端/文件系统真实执行
3. 长任务可恢复
4. 产物质量高
5. 成功 run 可沉淀为 agent
6. skill 可复用
7. 企业权限和审计
8. 成本可控
9. 用户能看见、暂停、修正 agent 的过程
```

如果你们要最快做出来，我建议：

> **不要 fork Dify 或 Coze 做“另一个 builder”。**
> **先做一个 Manus-like execution workspace：chat + sandbox + browser + terminal + artifact + save-as-agent。**

这个方向更接近 CREAO/Manus 的真实竞争点，也更适合你们后续做企业服务和定制 skill 交付。

[1]: https://docs.creao.ai/faq "FAQ - CREAO Documentation"
[2]: https://manus.im/ "Manus: Hands On AI"
[3]: https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers "How Manus Uses E2B to Provide Agents With Virtual Computers — E2B Blog"
[4]: https://github.com/kortix-ai/suna "GitHub - kortix-ai/suna: The Company AI Command Center · GitHub"
[5]: https://github.com/kortix-ai/suna/blob/main/LICENSE "suna/LICENSE at main · kortix-ai/suna · GitHub"
[6]: https://github.com/agent0ai/agent-zero "GitHub - agent0ai/agent-zero: Agent Zero AI framework · GitHub"
[7]: https://github.com/agent0ai/agent-zero/blob/main/LICENSE?utm_source=chatgpt.com "license - agent0ai/agent-zero"
[8]: https://github.com/OpenHands/openhands "GitHub - OpenHands/OpenHands:  OpenHands: AI-Driven Development · GitHub"
[9]: https://github.com/OpenHands/OpenHands/blob/main/LICENSE?utm_source=chatgpt.com "OpenHands/LICENSE at main"
[10]: https://github.com/FoundationAgents/OpenManus "GitHub - FoundationAgents/OpenManus: No fortress, purely open ground.  OpenManus is Coming. · GitHub"
[11]: https://github.com/FoundationAgents/OpenManus/blob/main/LICENSE "OpenManus/LICENSE at main · FoundationAgents/OpenManus · GitHub"
[12]: https://github.com/e2b-dev/e2b "GitHub - e2b-dev/E2B: Open-source, secure environment with real-world tools for enterprise-grade agents. · GitHub"
[13]: https://github.com/e2b-dev/open-computer-use "GitHub - e2b-dev/open-computer-use: AI computer use powered by open source LLMs and E2B Desktop Sandbox · GitHub"
[14]: https://github.com/browser-use/browser-use "GitHub - browser-use/browser-use:  Make websites accessible for AI agents. Automate tasks online with ease. · GitHub"
[15]: https://github.com/simular-ai/agent-s "GitHub - simular-ai/Agent-S: Agent S: an open agentic framework that uses computers like a human · GitHub"
[16]: https://github.com/coze-dev/coze-studio?utm_source=chatgpt.com "coze-dev/coze-studio: An AI agent development platform ..."
[17]: https://github.com/langgenius/dify?utm_source=chatgpt.com "langgenius/dify: Production-ready platform for agentic ..."
[18]: https://github.com/langgenius/dify/blob/main/LICENSE "dify/LICENSE at main · langgenius/dify · GitHub"
[19]: https://github.com/infiniflow/ragflow?utm_source=chatgpt.com "RAGFlow is a leading open-source Retrieval-Augmented ..."
[20]: https://github.com/infiniflow/ragflow/blob/main/LICENSE "ragflow/LICENSE at main · infiniflow/ragflow · GitHub"
[21]: https://github.com/1Panel-dev/maxkb?utm_source=chatgpt.com "MaxKB is an open-source platform for building enterprise ..."
[22]: https://github.com/1Panel-dev/MaxKB/blob/main/LICENSE "MaxKB/LICENSE at main · 1Panel-dev/MaxKB · GitHub"
[23]: https://github.com/activepieces/activepieces?utm_source=chatgpt.com "activepieces/activepieces: AI Agents & MCPs ..."
[24]: https://github.com/langchain-ai/langgraph?utm_source=chatgpt.com "langchain-ai/langgraph: Build resilient agents."
[25]: https://github.com/crewaiinc/crewai?utm_source=chatgpt.com "crewAIInc/crewAI: Framework for orchestrating role-playing ..."
[26]: https://github.com/letta-ai/letta?utm_source=chatgpt.com "Letta (formerly MemGPT)"
[27]: https://github.com/BerriAI/litellm?utm_source=chatgpt.com "BerriAI/litellm: Python SDK, Proxy Server (AI Gateway) ..."
[28]: https://github.com/langfuse/langfuse?utm_source=chatgpt.com "langfuse/langfuse: 🪢 Open source AI engineering platform ..."
[29]: https://github.com/promptfoo/promptfoo?utm_source=chatgpt.com "Promptfoo: LLM evals & red teaming"
[30]: https://modelcontextprotocol.io/specification/2025-06-18/server/tools?utm_source=chatgpt.com "Tools"
[31]: https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/?utm_source=chatgpt.com "Announcing the Agent2Agent Protocol (A2A)"
[32]: https://creao.ai/blog/meet-the-new-creao-super-agent-that-can-run-your-work. "Meet the New CREAO Super Agent That Can Run Your Work"
[33]: https://manus.im/features/manus-browser-operator "Manus Browser Operator: Turn Any Browser into an AI Browser"
是的，你这个问题问到核心了：**CREAO / Manus 这种产品的 sandbox，大概率不是“纯自己写几段代码实现的功能”，而是“自研控制层 + 外部/自建虚拟化执行层 + 外部连接器服务”的组合。**

我先直接回答：

> **CREAO 公开资料里已经能看到：它的 sandbox execution 使用了 E2B 作为 subprocessor；Manus 也有 E2B 官方案例说它用 E2B 给 agent 提供 virtual computer。**
> 所以它们不是简单在自己后端开一个 Docker 跑代码，而是把“云端隔离电脑/沙盒执行环境”作为核心基础设施，部分接了专门的 sandbox 服务。CREAO/Manus 自己真正做重的是：agent loop、任务调度、权限、记忆、文件、artifact、connector、billing、UI、审批、快照策略这些控制层。

---

# 1. 先把 sandbox 和外部服务分开

你可以这样理解：

```text
Agent 产品
  ├── 1. 自研控制层
  │     ├── Chat UI
  │     ├── Agent Loop
  │     ├── Planner / Executor
  │     ├── 用户/企业/权限
  │     ├── 计费
  │     ├── 记忆
  │     ├── 文件和 artifact
  │     └── 审批/日志
  │
  ├── 2. 沙盒执行层
  │     ├── 云端 Linux 环境
  │     ├── Python / Node / Bash
  │     ├── 浏览器
  │     ├── 文件系统
  │     ├── 安装依赖
  │     └── 生成文件
  │
  └── 3. 外部服务连接层
        ├── LLM Provider: OpenAI / Anthropic / Gemini 等
        ├── Sandbox Provider: E2B / 自建 microVM / K8s
        ├── Connector: Gmail / Slack / GitHub / Shopify 等
        ├── Browser infra: cloud browser / proxy / captcha
        ├── Storage / CDN
        └── Billing / analytics / monitoring
```

**sandbox 只是执行环境，不等于整个 agent 系统。**

真正产品上的“Manus/CREAO 感”，来自这几层一起工作：

```text
用户说一句话
  → agent 规划任务
  → 创建/唤醒 sandbox
  → 在 sandbox 里开浏览器、跑代码、写文件
  → 需要 Gmail/Slack/Shopify 时走 connector
  → 产出 artifact
  → 记录成本和日志
  → 可以保存为 reusable agent
```

---

# 2. CREAO 的 sandbox 是怎么做的？

从公开资料看，CREAO 至少有这几个确定点。

## 2.1 每个 chat thread 有自己的 sandbox

CREAO 文档说，每个 chat thread 都有独立 sandbox；同一个 thread 里安装的包、创建的文件、运行进程会在消息之间保留；30 分钟不活跃后 sandbox 会暂停，之后可以恢复。agent run 也会创建隔离 sandbox，并从 agent 保存的环境快照启动。([CREAO Documentation][1])

也就是说，CREAO 的设计不是：

```text
所有用户共用一个运行环境
```

而是：

```text
Thread A → Sandbox A
Thread B → Sandbox B
Agent Run 1 → Sandbox from snapshot
Agent Run 2 → Sandbox from snapshot
```

这就是它能让 agent “安装 pandas 后下条消息继续用”的原因。

---

## 2.2 agent 可以把环境保存成 snapshot

CREAO 文档写得很明确：当你从一次 chat 创建 agent 时，原始 chat session 里的环境会被保存，包括 Python 包、Node 包、系统工具、文件和配置；以后每次运行这个 agent 都从这个 saved snapshot 开始，而不是从空白环境开始。([CREAO Documentation][2])

所以它大概是这种机制：

```text
一次成功任务
  → 当前 sandbox 里安装了依赖、生成了脚本、保存了配置
  → 点击 Create Agent
  → 平台保存 sandbox snapshot
  → 下次运行 agent
  → 从 snapshot 克隆一个新 sandbox
```

这个是 CREAO/Manus 类产品非常关键的差异点。普通 agent 平台很多只是保存 prompt/workflow；CREAO 保存的是 **prompt/workflow + runtime environment**。

---

## 2.3 CREAO 的 sandbox execution 接了 E2B

CREAO 的 Privacy 页面列了 subprocessors，其中明确写到 **E2B — Sandbox execution — Code, files during execution**。这说明 CREAO 至少在公开披露层面，把 E2B 作为沙盒执行服务的一部分。([CREAO Documentation][3])

所以你问“是代码实现还是接外部服务”，对 CREAO 来说更准确的答案是：

> **sandbox 底层执行很可能接了 E2B；CREAO 自己做的是上层 agent 平台、snapshot 策略、runner、bridge、权限、文件、计费、UI、agent 复用。**

不是简单“全自研”，也不是“纯套 E2B”。E2B 解决的是：

```text
给 agent 一台隔离的云端小电脑
```

CREAO 自己解决的是：

```text
这台小电脑什么时候创建、怎么绑定用户、怎么保存快照、怎么接外部服务、怎么计费、怎么变成 agent app
```

---

# 3. Manus 的 sandbox 是怎么做的？

Manus 官方博客说，Manus Sandbox 是为每个任务分配的完全隔离 cloud virtual machine；里面有网络、文件系统、浏览器和软件工具，可以并行执行任务，不消耗用户本地资源。它还会保存用户上传附件、Manus 执行中生成的文件/artifacts，以及任务所需配置。([Manus][4])

E2B 官方案例进一步说，Manus 需要的不只是代码执行，而是一个完整 virtual computer；E2B 底层用 Firecracker microVM，sandbox 里可以跑 Python、JavaScript、Bash，并且可以用 Chromium、terminal、filesystem 等工具。([E2B][5])

所以 Manus 的架构大概是：

```text
Manus 自研：
  ├── Chat / UI
  ├── Planner agent
  ├── Executor agent
  ├── 任务状态
  ├── artifact 展示
  ├── 用户交互
  └── 产品体验

E2B 提供：
  ├── Firecracker microVM
  ├── 云端虚拟电脑
  ├── Python / JS / Bash
  ├── 浏览器
  ├── 文件系统
  ├── 长任务 session
  └── pause / resume 能力
```

也就是：**Manus 的“脑”和产品壳是自己做的，云端电脑底座接了 E2B。**

---

# 4. sandbox 本身通常有哪些实现方式？

这类产品的 sandbox 有 4 种常见实现等级。

## Level 1：普通 Docker

最简单，适合 demo：

```text
用户发起任务
  → 后端 docker run
  → 容器里跑 Python / Node / Browser
  → 任务结束销毁
```

优点是快、便宜、好做。
缺点是企业多租户安全压力大，尤其当 agent 会跑 LLM 生成代码时，普通容器不够放心。

---

## Level 2：Kubernetes Pod / Job

适合早期商业 PoC：

```text
Sandbox Manager
  → 创建 K8s Pod
  → 注入 run_id
  → 挂载临时 volume
  → 跑 agent runner
  → 上传 artifact
  → 删除 Pod
```

Google Cloud 现在也有 GKE Agent Sandbox 文档，核心就是用 GKE 创建隔离的 sandbox 环境来安全执行 AI-generated code；它还提到 SandboxTemplate、SandboxWarmPool 这种预热池概念。([Google Cloud Documentation][6])

这条路适合你们第一版自建。

---

## Level 3：microVM，例如 Firecracker / Unikraft / Kata

这是 Manus/E2B 这种更像 production 的方向。

```text
每个任务一个 microVM
  → 独立 kernel
  → 独立 filesystem
  → 独立网络边界
  → 比普通 VM 更轻
  → 比普通 container 隔离更强
```

E2B 官方案例明确说它底层用 Firecracker microVM 给 Manus 提供完整 virtual computer。([E2B][5])

Browser Use 也分享过类似架构：他们生产环境用 micro-VM，本地和 eval 环境用 Docker，并且每个 sandbox 只拿到 session token、control plane URL、session ID，不拿 AWS key、数据库凭证或 API token。([Browser Use][7])

---

## Level 4：完整 Cloud Desktop / Computer Use

这就是 Manus 体验最强的部分：

```text
云端虚拟电脑
  ├── Linux desktop
  ├── Chrome / Chromium
  ├── terminal
  ├── filesystem
  ├── office / browser / dev tools
  ├── live view
  └── pause / resume
```

这种体验可以让用户看到 agent 正在打开网页、点击、输入、下载文件、生成报告。

---

# 5. “有多少是连外面的服务？”

我按模块给你拆。

## 5.1 LLM 一定是外部服务，除非你们自部署模型

CREAO 文档列了 Anthropic、OpenAI、Google Cloud AI、MiniMax、Z.AI、xAI、OpenRouter、Sakana 等模型或模型网关作为 subprocessors。([CREAO Documentation][3])

所以 CREAO 这种产品至少会把 conversation messages/context 发给选定模型供应商。

你们做竞品时，也大概率是：

```text
用户请求 / 当前上下文
  → 你们的 Agent Orchestrator
  → LiteLLM / 自研模型网关
  → OpenAI / Anthropic / Gemini / Qwen / DeepSeek 等
```

---

## 5.2 Sandbox 执行层可以接外部服务

CREAO 公开披露 E2B 做 sandbox execution，处理 code/files during execution。([CREAO Documentation][3])
Manus 的 E2B 案例也说明了 Manus 用 E2B 的 virtual computer。([E2B][5])

所以这一层可以是：

```text
方案 A：接 E2B
方案 B：自己用 K8s/Docker 做
方案 C：自己用 Firecracker/Kata/Unikraft 做
方案 D：混合，早期 E2B，后期自建
```

对你们来说，我建议：

```text
MVP：E2B / Docker / K8s
商业 PoC：K8s + sandbox pool
企业级：Firecracker / Kata / BYOC / 私有云
```

---

## 5.3 Connector 一定会连外部服务

CREAO 把能力分成 Skills 和 Connectors。Skills 是 instruction packages；Connectors 是 MCP/API 集成，可以连接 Gmail、Google Sheets、Slack、GitHub、Shopify 等服务。([CREAO Documentation][8])

这类 connector 本质上不是 sandbox，而是：

```text
agent 需要发邮件
  → connector 调 Gmail API

agent 需要读表格
  → connector 调 Google Sheets API

agent 需要改 Shopify 商品
  → connector 调 Shopify API
```

关键点是：**这些外部服务最好不要让 sandbox 直接拿长期 token。**

更安全架构是：

```text
Sandbox
  → 用短期 run token 请求 Connector Gateway
  → Gateway 检查权限
  → Gateway 从 Secret Vault 取 OAuth token
  → Gateway 调 Gmail / Slack / Shopify
  → Gateway 把结果返回 sandbox / orchestrator
```

CREAO CTO 文章也讲了这个模式：长期 credential 不放进 sandbox；sandbox 调外部认证服务时走 sandbox 外的 API bridge，bridge 在 host side 附加 OAuth token，并用 IP allowlist + per-run short-lived JWT 校验。([Creao AI][9])

---

## 5.4 Browser Use 也可能接外部服务

CREAO 的 Browser Use 文档说，它用 stealth browser、residential proxies、CAPTCHA solving，用户可以创建 browser profile 保存登录 cookies，之后 agent 用 live browser 操作。([CREAO Documentation][10])

这说明 Browser Use 里通常还有这些外部或独立基础设施：

```text
Cloud browser
Residential proxy
CAPTCHA solving provider
Cookie/profile storage
Live browser streaming
```

这块你们要谨慎。企业版最好不要默认做“绕检测/社媒自动化”那种方向，可以做成：

```text
企业系统浏览器自动化
  ├── cloud browser
  ├── 企业允许域名白名单
  ├── 用户手动登录
  ├── session profile 加密保存
  ├── 高风险动作审批
  └── 完整审计日志
```

---

# 6. 所以他们到底哪些是自研，哪些是接外部？

以 CREAO / Manus 这类产品推断，可以这样分。

| 模块                   | 大概率做法                                  | 是否适合你们自研                   |
| -------------------- | -------------------------------------- | -------------------------- |
| Chat UI              | 自研                                     | 要自研                        |
| Agent Loop / Planner | 自研                                     | 要自研                        |
| Run Manager          | 自研                                     | 要自研                        |
| 用户/企业/权限             | 自研                                     | 要自研                        |
| Billing / credits    | 自研 + Stripe                            | 要自研业务层                     |
| Sandbox 执行           | CREAO 披露 E2B；Manus 有 E2B 案例            | 早期可接 E2B，后期可自建             |
| Browser automation   | 自研编排 + cloud browser/proxy/captcha 服务  | 早期用 Playwright/browser-use |
| LLM                  | 外部模型 API                               | 接模型网关                      |
| Connectors           | 自研 connector gateway + 外部 SaaS API/MCP | 核心要自研                      |
| Secrets              | 自研 secret vault / KMS                  | 企业版必须自研或用云 KMS             |
| 文件/artifacts         | 自研业务层 + S3/R2/CDN                      | 要自研业务层                     |
| Memory               | 自研业务层 + 向量库/数据库                        | 要自研                        |
| Observability        | 自研 + Langfuse/Sentry                   | 可接开源/商业服务                  |

一句话：

> **sandbox 底层可以接外部服务；但 agent 产品本身不能只靠接外部服务。你们真正要自研的是 control plane。**

---

# 7. 一个更准确的架构图

你们可以按这个架构理解：

```text
用户浏览器
  ↓
Chat / Workspace UI
  ↓
Backend Control Plane
  ├── Auth / Tenant / RBAC
  ├── Agent Registry
  ├── Skill Registry
  ├── Memory
  ├── File / Artifact Manager
  ├── Billing / Credits
  ├── Audit Log
  ├── Approval
  └── Run Manager
        ↓
Agent Orchestrator
  ├── Planner
  ├── Executor
  ├── Reviewer
  ├── Tool Router
  └── Trace Collector
        ↓
Sandbox Manager
  ├── E2B / K8s / Firecracker
  ├── Create sandbox
  ├── Resume sandbox
  ├── Snapshot sandbox
  ├── Kill sandbox
  └── Collect artifacts
        ↓
Sandbox / Cloud Computer
  ├── Linux
  ├── Python / Node / Bash
  ├── Chromium / Playwright
  ├── Filesystem
  ├── Workspace files
  └── Generated artifacts

外部服务通过 Gateway 连接：
  Sandbox / Orchestrator
    → Connector Gateway
    → Secret Vault
    → Gmail / Slack / GitHub / Shopify / CRM
```

重点边界：

```text
LLM 不直接碰密钥
Sandbox 不保存长期密钥
Connector Gateway 才能拿 OAuth token
Artifact Store 保存文件
Audit Log 记录每一步
```

---

# 8. 你们自己做的话，我建议怎么选？

## 阶段 1：最快验证产品

```text
E2B
+ browser-use / Playwright
+ LiteLLM
+ Langfuse
+ S3/R2/MinIO
+ Postgres
```

优点：

```text
最快做出 Manus-like 体验
不用先啃 microVM
团队可以先验证产品
```

缺点：

```text
成本受 E2B 影响
数据处理要看客户接受度
企业私有化不够强
底层能力受制于供应商
```

适合：

```text
demo
早期客户 PoC
低敏数据场景
```

---

## 阶段 2：商业 PoC / 企业服务

```text
Kubernetes sandbox
+ Docker image
+ Playwright browser
+ sandbox warm pool
+ network policy
+ secret gateway
+ artifact store
```

优点：

```text
成本可控
部署灵活
能做企业私有云/专属云
工程复杂度还可接受
```

缺点：

```text
隔离强度不如 microVM
安全加固要认真做
```

适合：

```text
企业内部 agent
知识库问答 + 文件生成
数据分析
低/中风险代码执行
```

---

## 阶段 3：真正企业级 agent cloud

```text
Firecracker / Kata / Unikraft microVM
+ snapshot restore
+ warm pool
+ egress control
+ secret bridge
+ browser profile isolation
+ BYOC
```

优点：

```text
隔离强
可对标 Manus/E2B
适合运行不可信代码
适合多租户 SaaS
```

缺点：

```text
工程成本高
运维复杂
启动速度、存储、网络、日志、快照都要自己做
```

适合：

```text
大规模商业化
多租户 SaaS
高敏企业客户
私有化交付
```

---

# 9. 最关键的产品判断

你现在不用纠结“一开始是不是必须自研 sandbox”。我会这样判断：

## 如果你们现在还在验证产品

接 E2B 或用 K8s Docker 先做，重点验证：

```text
用户是否愿意用 chat 派任务
agent 是否能稳定完成任务
artifact 是否有价值
成功任务是否能保存成 agent
企业是否需要 skill 定制
```

## 如果你们开始卖企业中台

必须逐步自研或可控化：

```text
sandbox lifecycle
secret gateway
connector permission
artifact storage
audit log
tenant isolation
network policy
cost control
```

## 如果你们要做 CREAO/Manus 正面竞品

最后一定要掌握：

```text
Sandbox Manager
Runtime Image
Snapshot
Resume
Browser Session
Connector Gateway
Run Trace
Cost Metering
```

因为这才是壁垒。

---

# 10. 最简单的一句话

**他们不是只写了一段“沙盒代码”。**

更准确是：

> **CREAO / Manus 的 sandbox 是一个“云端隔离电脑”能力。底层很可能接了 E2B 这类专业 sandbox/microVM 服务；上层 agent loop、任务调度、快照、权限、连接器、记忆、计费、artifact 展示是他们自己做的产品系统。**

你们要做竞品，最稳的路线是：

```text
第一版：接 E2B / K8s sandbox，把产品跑通
第二版：自研 Sandbox Manager + Connector Gateway
第三版：上 microVM / BYOC / 企业私有化
```

[1]: https://docs.creao.ai/faq "FAQ - CREAO Documentation"
[2]: https://docs.creao.ai/pro/agent-runtime-environment?utm_source=chatgpt.com "Agent Runtime Environment"
[3]: https://docs.creao.ai/trust-and-safety/privacy "Privacy - CREAO Documentation"
[4]: https://manus.im/blog/manus-sandbox "Understanding Manus sandbox - your cloud computer"
[5]: https://e2b.dev/blog/how-manus-uses-e2b-to-provide-agents-with-virtual-computers "How Manus Uses E2B to Provide Agents With Virtual Computers — E2B Blog"
[6]: https://docs.cloud.google.com/kubernetes-engine/docs/how-to/agent-sandbox "Isolate AI code execution with Agent Sandbox  |  GKE AI/ML  |  Google Cloud Documentation"
[7]: https://browser-use.com/posts/two-ways-to-sandbox-agents "How We Built Secure, Scalable Agent Sandbox Infrastructure"
[8]: https://docs.creao.ai/features/skills-and-connectors "Skills and Connectors - CREAO Documentation"
[9]: https://creao.ai/blog/building-cloud-agent-infrastructure "Building Cloud Agent Infrastructure: What's Different, and What We Learned"
[10]: https://docs.creao.ai/features/browser-use "Browser Use - CREAO Documentation"




 agent 用langchain  llama_index 以及 langgraph实现  
 前端的话需要听从我的要求  Page: https://agent.creao.ai/chat

1. Tgi "Claude Sonnet 5 Free plan | Upgrade How can I he…" <div>
   selector: [data-testid="chat-column"]
   locator: div "Claude Sonnet 5 Free plan | Upgrade How can I he…"
   inside: main "How can I help, luke?"
   react: SNn › LR › Tgi
   data-testid: chat-column
   layout: display:flex; flex-direction:column; align-items:normal; justify-content:normal; gap:normal; overflow:auto
   props: data-testid:chat-column  比如类似这样了 https://agent.creao.ai/chat  不要着急直接生成前端 
