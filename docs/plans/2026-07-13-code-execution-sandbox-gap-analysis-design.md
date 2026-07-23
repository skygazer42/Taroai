# 代码执行沙盒 —— 对标差距分析与演进路线图

> **状态（2026-07-21）：历史差距快照，部分结论已过期。** 仓库现已包含真实 `E2BSandboxAdapter`，当前在线主路径也已实测 E2B 命令和文件交付；文中“E2B 无真实实现”及以 gVisor 为近期主脊的判断不再用于发布决策。现状以 [CREAO 收口审计](2026-07-21-creao-completion-audit.md) 为准。

**目标:** 以 ChatGPT Code Interpreter(公开逆向信息)与开源标杆(E2B / gVisor / Firecracker / Modal)为参照系,系统评估 Taroai **代码执行沙盒**(`sandbox.command` 这条线)的能力差距,并给出可直接转成实现 plan 的 P0/P1/P2 演进路线图。

**范围:** 只覆盖 `sandbox.command`(create / execute / file / snapshot / destroy)。不含 `browser.action` 浏览器自动化,不含工件/存储与 lease/密钥链路(那些另行评估)。

**参照系说明:** ChatGPT Code Interpreter 闭源,其实现细节来自社区逆向探测,本文标注为「推测」;开源标杆来自公开设计文档,标注为「事实」。creao.ai 因闭源且无可考证公开资料,**不作为对标基线**。

---

## §0 背景与方法

### 为什么是这个参照系

- ChatGPT Code Interpreter 是「不受信任代码执行沙盒」最成熟的产品形态,其公开可考证的做法(gVisor 用户态内核、全禁网、`/mnt/data` 可写目录、~120s 超时、会话即弃、Jupyter kernel 执行)恰好覆盖我们要评估的每个维度。
- E2B / gVisor / Firecracker / Modal 是可落地、可自建的开源/可自托管基线,直接对应我们的 provider 选型。

### 评估方法

对每个能力维度,统一按 **「标杆怎么做 → Taroai 现状(锚定 `sandbox/*.py` 与 `infra/` 配置)→ 差距 → 补法 → 优先级」** 展开。现状全部来自源码与部署清单实测,不采信旧 plan 的自述。

### 优先级定义

- **P0** — 解锁「敢跑不受信任多租户代码」的安全前置 + 产品核心体验(Code Interpreter 式持久 kernel);安全前置与核心能力并存。
- **P1** — 对标标杆的体验与能力(DX、性能、快照)。
- **P2** — 强多租户与规模化(microVM、托管服务、per-tenant 隔离、受控出站)。

---

## §1 现状速览

**架构:** `Agent Runtime → Tool Gateway → Sandbox Adapter(契约)→ provider`。契约见 `sandbox/adapter.py`,provider 选择见 `sandbox/factory.py`。

**Provider 谱系(`factory.py:10-40`):**

| provider | 实现 | 隔离取法 | 定位 |
|---|---|---|---|
| `local_process` | `process.py` | 无隔离,进程内工作区 | 本地 PoC(非本地环境被 config 禁用) |
| `docker` | `docker.py` 进程内 `docker run` | 共享内核 + caps drop + 非 root + 只读根 + 禁网 + host bind mount | 本地开发 / 单机 |
| `k8s` | `http.py` → sandbox controller → `kubernetes.py` | 共享内核 + PSA restricted + NetworkPolicy + TTL + 镜像白名单 | **当前企业默认** |
| `e2b` | `http.py`(复用同一 controller 契约) | 走 HTTP 契约,**无真实 E2B 实现** | 占位 / 计划中 |

**一句话隔离定位:最高档 = 共享宿主内核。** gVisor / microVM 这一档:**控制面已就位**——shipped 配置默认启用并强制 gVisor(`configmap.yaml:70-71`、`helm/values.yaml:97-98`、各 `*.env.example`),lifecycle/install 校验也已 gate `runtime_isolation`(`lifecycle_verification.py:649`、`install_validation.py:2563`);**真缺口在数据面**——集群上没有 `RuntimeClass` 对象、节点未装 runsc handler(`infra/` 无 `kind: RuntimeClass`),且 adapter 代码默认(`kubernetes.py:48-49` 空/False)与部署默认(gvisor/true)不一致。

**已是优势、非差距的部分:** 三级作用域(tenant/workspace/run)、会话上限、lease 句柄注入(不落原始密钥)、审计与计量(`sandbox_minutes`/`artifact_bytes`)、release gate 校验脚本(`scripts/verify-*sandbox*.sh`)、私有部署打包(compose/helm/k8s)。

---

## §2 参照系基线

### ChatGPT Code Interpreter(推测,社区逆向)

| 维度 | 做法 |
|---|---|
| 隔离 | 容器 + **gVisor**(用户态内核,syscall 不直达宿主),跑在 K8s |
| 网络 | **完全禁外网**(无出口) |
| 文件系统 | `/mnt/data` 可写,其余基本只读 |
| 生命周期 | 单次执行超时 ~120s;会话空闲 ~20 分钟销毁,状态丢失 |
| 执行 | **持久 Jupyter kernel**,变量/import 跨 cell 保留,支持图片/表格富输出 |
| 权限 | 无 sudo,危险 capabilities 丢弃 |

### 开源标杆(事实)

| 标杆 | 隔离边界 | 冷启动 | 关键特性 |
|---|---|---|---|
| **gVisor**(runsc) | 用户态内核拦截 syscall | 接近容器 | 收窄内核攻击面;可作 K8s RuntimeClass;GKE Sandbox 原生支持 |
| **Firecracker** | 硬件虚拟化 microVM,独立内核 | ~125ms | AWS Lambda/Fargate 底座;snapshot 恢复 |
| **Kata Containers** | 每容器一 microVM | 秒级 | 兼容 OCI/K8s,经 RuntimeClass 接入 |
| **E2B** | Firecracker microVM | 秒级(snapshot) | 专为 LLM Agent 设计,SDK + 受控出站,可自托管 |
| **Modal** | gVisor + microVM | 快 | 托管,受控出站,GPU |

**对齐点:** Taroai 现有 `docker`/`k8s` 相当于「容器档」,标杆的强隔离都在**容器档之上**(gVisor 用户态内核 → microVM 硬件边界)。补的正是这一档。

---

## §3 能力维度差距矩阵

### 3.1 隔离强度(内核边界)—— 最高优先

- **标杆:** ChatGPT = gVisor 用户态内核;强多租户 = Firecracker/Kata microVM 硬件边界。
- **现状:**
  - `docker.py`:`--network none`(`:106-107`)、`--cap-drop ALL`(`:150-151`)、`--user 65532:65532`(`:145-146`)、`--read-only`(`:148-149`)、`--security-opt no-new-privileges`(`:48-50`)、`--tmpfs /tmp`(`:154-155`)。**无显式 seccomp profile**,**无 `--runtime`**(用宿主默认 runc)。
  - `kubernetes.py`:Pod `securityContext` runAsNonRoot / drop ALL / readOnlyRootFilesystem / `seccompProfile: RuntimeDefault`(`:1112-1143`);Namespace PSA `restricted`(`sandbox-runtime-policy.yaml:8-13`)。`runtimeClassName` 钩子存在(`:1164-1165`),仅当 `runtime_class_required and runtime_class_name` 时能力位 `runtime_isolation=True`(`:81-83`);**adapter 代码默认为空/False**(`:48-49`)。
  - **部署与校验(易漏,需实测部署清单):** 所有企业 profile 默认已开启并强制 gVisor——`configmap.yaml:70-71`、`helm/values.yaml:97-98`、`docker-compose.yml:114-115`、`cloud/byoc/private.env.example` 均 `RUNTIME_CLASS_NAME=gvisor` + `REQUIRED=true`,经 `sandbox-controller.yaml:182-191` 注入 controller;lifecycle/install 校验已 gate `runtime_isolation_declared`(`lifecycle_verification.py:419-420,649`、`install_validation.py:1880-1881,2563`)。
- **差距:** 最高档容器仍共享宿主内核;Docker 无显式 seccomp;**gVisor 控制面已就位但数据面未落地**——集群无 `RuntimeClass` 对象、节点无 runsc handler(`infra/` 无 `kind: RuntimeClass`);现有 gate 只验「声明了 runtime isolation」,未验「Pod 实际由 runsc 运行」;adapter 代码默认(空/False)与部署默认(gvisor/true)不一致。
- **补法(P0):** ① 新增 gVisor `RuntimeClass`(runsc)清单 + 按目标平台安装节点 runsc handler(GKE Sandbox 原生 / EKS·AKS 自装);② 补**集群侧 e2e** 断言 Pod 真正落在 gVisor runtime(而非仅 `runtime_isolation_declared=True`),非 gVisor 节点创建被拒;③ 收敛 `kubernetes.py:48-49` 代码默认与 shipped 配置一致。→ 补齐后内核边界即达 ChatGPT 同款(env 配置与 verify gate 已具备,无需重做)。
- **威胁校验清单(内嵌):** 内核逃逸 = 共享内核 ✗(gVisor 收窄)/ 越权读写 = 非 root + drop caps + 只读根 ✓ / 资源耗尽 = mem·cpu·pids·quota ✓(见 3.4)/ 跨租户 = 共享内核 + 单 namespace ✗(见 3.7)。

### 3.2 网络隔离与出站控制

- **标杆:** ChatGPT 全禁;E2B/Modal 受控出站(域名白名单/代理)。
- **现状:** 全禁。Docker create 拒绝非 `DISABLED`(`docker.py:75-78`)+ `--network none`;K8s 同样拒绝(`kubernetes.py:93-96`)+ per-session `NetworkPolicy` default-deny Ingress+Egress(`:1181-1198`)+ Namespace 级 default-deny(`sandbox-runtime-policy.yaml:63-78`)。`SandboxNetworkMode` 有 `ALLOWLIST`/`OPEN`(`models.py:12-15`)但 provider 未实现。
- **差距:** 无受控出站(allowlist/open 是枚举占位)。装包/联网只能靠预装镜像。
- **补法(P1,产品已确认需联网):** egress proxy + 域名白名单落地 `ALLOWLIST`;`OPEN` 模式不启用。**联网放大风险面(外泄/SSRF/回连),必须与 P0-1 的 gVisor 内核边界成对上线,不得只开网不升隔离。**

### 3.3 文件系统与工作区

- **标杆:** 单一可写目录(`/mnt/data`),其余只读,即用即弃。
- **现状:** Docker = **host bind mount** `{workspace_path}:/workspace` + `chmod 0o777`(`docker.py:97-99, 110-111`);K8s = `emptyDir` workspace + memory `emptyDir` /tmp(`kubernetes.py:1150-1162`),更干净。路径穿越防护齐全(`docker.py:443-462`、`kubernetes.py:1418-1443`);只读根两者都有;工件限定 `/workspace/artifacts/`。
- **差距:** Docker 的 host bind mount + `0777` 是 PoC 级(逃逸后可动宿主目录);Docker 侧无工作区大小配额(K8s 有 `ephemeral-storage` sizeLimit)。
- **补法(P0,主要是收口):** 明确 Docker「仅本地开发」定位(config 已通过 `ENTERPRISE_SANDBOX_PROVIDERS={k8s,e2b}` 在生产/私有部署校验时禁用非企业 provider,`config.py:17, 474-489`);企业路径强制 K8s `emptyDir`。文档 + 校验断言双保险。

### 3.4 进程 · 资源配额

- **标杆:** CPU/内存/进程数/时长硬限,防 fork 炸弹与资源耗尽。
- **现状:** Docker `--memory 1g`/`--cpus 1.0`/`--pids-limit 256`(`docker.py:139-144`);K8s limits+requests `cpu 1000m`/`mem 1Gi`/`ephemeral 2Gi`(`kubernetes.py:1126-1137`)+ ResourceQuota + LimitRange(`sandbox-runtime-policy.yaml:15-61`)+ `activeDeadlineSeconds` 硬超时(`:1106`);输出按 `max_output_chars` 截断(`docker.py:38`)。
- **差距:** 较完善。缺**命令级取消**(见 3.10);无 I/O(blkio)带宽限制。
- **补法(P1):** 补 cancel;可选 blkio 限制。

### 3.5 快照 · 持久化 · 会话生命周期

- **标杆:** snapshot/restore 秒级恢复;会话空闲即弃。
- **现状:** `snapshot` 只写**文件清单 JSON**(path+size),**不含内容、不含 rootfs**(`docker.py:252-289`、`kubernetes.py:379-421`),不可 restore。K8s TTL 强制(create 校验 `timeout ≤ max_session_ttl`,`:1227`;execute 校验过期,`:1317`);**Docker 无 TTL**(`session_ttl_enforced=False`,`docker.py:67`,`sleep infinity` 容器不自动过期,依赖 runtime destroy)。会话即弃(runtime 终态清理,含 `destroy_failed` 作为 release gate)。
- **差距:** snapshot 不可恢复;无 retention 策略;Docker 无 TTL(泄漏依赖 runtime 清理路径)。
- **补法(P1):** 真快照(工作区 tar → storage,或 `docker commit` / K8s VolumeSnapshot)+ restore;retention 配置;Docker 加 TTL reaper。

### 3.6 冷启动 · 性能 · 密度

- **标杆:** Firecracker ~125ms;E2B/ChatGPT 靠 snapshot 恢复达秒级以下。
- **现状:** 每次新建。Docker `run --detach`(`docker.py:100-126`);K8s `apply` + `wait --for=condition=Ready`(最长 60s,`kubernetes.py:170-200`)。**无池化 / 预热 / 快照恢复。**
- **差距:** 冷启动慢(K8s 尤甚);无 warm pool。
- **补法(P1):** warm pool 预热 + 认领;结合 3.5 快照恢复。

### 3.7 多租户边界

- **标杆:** 内核级边界 + 网络/资源/命名空间隔离。
- **现状:** tenant/workspace/run 三级作用域 + `_assert_scope` 校验;会话上限 50/20/3;K8s per-session `NetworkPolicy` + label;**所有 session Pod 在单一 namespace**。
- **差距:** 共享内核(强多租户硬伤);单 namespace(非 per-tenant);无 node pool 隔离。
- **补法:** gVisor/microVM 内核边界(P0/P2,见 3.1、§4);可选 per-tenant namespace / 专用 node pool + taints(P2)。

### 3.8 可观测 · 审计 · 计费

- **标杆:** 逐次执行审计、计量、逃逸/异常检测。
- **现状:** **优势项。** tool_call 审计、run events(`sandbox.command.executed` 等,不落原始 stdout)、计量、release gate、lifecycle evidence(`auth_challenge_enforced` / `session_destroy_confirmed`)。
- **差距:** 小。可补 per-session 实际资源峰值指标;运行时异常 syscall 检测(需 runtime 支持,如 Falco/gVisor 事件)。
- **补法(P2):** 指标 + 可选 runtime 安全事件采集。

### 3.9 镜像 · 供应链

- **标杆:** 固定摘要镜像 + 预装科学计算栈。
- **现状:** `image_policy.py` 完善——要求 digest pin(`@sha256`)或 registry+非 latest tag,禁 broad pattern(`:37-59`);K8s create 强制(`kubernetes.py:1231-1237`)。**但:** 默认镜像 `sandbox_runtime_image="python:3.12-slim"` 是 **tag 非 digest**(`config.py:151`);**Docker provider 不调用 `image_policy`**(`docker.py` 无 import,直接用 `request.image`);`imagePullPolicy: IfNotPresent`。
- **差距:** 默认镜像未 pin;Docker 绕过白名单;无镜像签名(cosign);无预装数据栈的「标准沙盒镜像」(ChatGPT 预装数百包)。
- **补法:** P0 = 默认镜像 pin `@sha256` + Docker provider 接 `image_policy`;P1 = 构建标准沙盒镜像(numpy/pandas/matplotlib…)+ 可选 cosign 验签。

### 3.10 DX / 接口契约

- **标杆:** 持久 Jupyter kernel(有状态 REPL)+ 富输出(图/表/HTML)。
- **现状:** 一次性 `sh -lc`(docker/kubectl exec);结果仅 `stdout/stderr/exit_code`(`models.py:83-94`);文件 upload/download/list + snapshot。**无持久 kernel,无富输出。** 能力位有 `command_cancellation_supported` 字段(`models.py:153`)但 adapter 无 cancel 方法。
- **差距:** 无有状态 REPL(变量/import 不跨命令);无富输出;无 cancel。对「数据分析 / Code Interpreter 体验」是核心差距。
- **补法(P0,产品已确认对标 Code Interpreter 数据分析体验):** 沙盒内跑持久 kernel(`jupyter_client`/IPython)+ mimebundle 富输出 → storage;adapter 增 kernel 会话语义 + cancel。

---

## §4 隔离档位演进阶梯(路线图主脊)

| 档位 | 边界 | 现状 | 适用 | 落地成本 | 对 Adapter 的改动面 |
|---|---|---|---|---|---|
| `local_process` | 无 | 已有 | 本地 PoC | — | — |
| **Docker(容器)** | 共享内核 | 已有 | 本地开发/单机 | 低 | 收口为「仅本地」 |
| **K8s(容器)** | 共享内核 + PSA + NetworkPolicy + TTL | **当前企业默认** | 一般企业 | 已有 | — |
| **gVisor(用户态内核)** | syscall 收窄 | **配置默认已启用+强制、verify 已 gate;缺 RuntimeClass 对象+节点 runsc** | 对标 ChatGPT、多租户 | **低(清单+节点+集群侧 e2e)** | 无需改契约 |
| **microVM(Kata/Firecracker)** | 硬件虚拟化 | 无 | 强多租户/高价值 | 高(节点/嵌套虚拟化) | 经 RuntimeClass(Kata)或新 provider |
| **E2B(托管 microVM)** | 硬件虚拟化 | 占位无实现 | 快速拿强隔离 | 中(外部依赖) | 新增真实 E2B adapter |

**主脊结论:** 沿 `K8s容器 → gVisor → microVM/E2B` 走。gVisor 是性价比拐点(最小改动拿到用户态内核边界),microVM/E2B 是强多租户终局。

---

## §5 演进路线图

### P0 — 内核边界 + 供应链收口(解锁不受信任多租户执行)

| # | 项 | 对应维度 | 主要改动 | 验收 gate |
|---|---|---|---|---|
| P0-1 | gVisor 数据面落地(RuntimeClass 对象 + 节点 runsc) | 3.1 / 3.7 | 新增 runsc `RuntimeClass` 清单 + 节点安装;收敛代码默认 vs 部署默认(env/verify 已具备,不重做) | **集群侧 e2e** 断言 Pod 实际由 runsc 运行(非仅 `runtime_isolation_declared`);非 gVisor 节点创建被拒 |
| P0-2 | 默认镜像 digest pin + Docker 接镜像白名单 | 3.9 | `sandbox_runtime_image` 改 `@sha256`;`docker.py` 调 `image_policy` | 单测:非白名单/latest/未 pin 镜像被拒(docker 与 k8s 一致) |
| P0-3 | Docker 定位收口为「仅本地」 | 3.3 | 强化 config 校验 + 文档;host bind mount 风险明示 | 私有/生产部署选 `docker` 时 config 校验失败 |
| P0-4 | 持久 kernel + 富输出(对标 Code Interpreter) | 3.10 | 沙盒内 `jupyter_client` kernel;mimebundle → storage;adapter 增会话语义 | 变量/import 跨命令保留;图片/表格富输出落 storage 对象 |

### P1 — 体验与能力对标

| # | 项 | 对应维度 | 主要改动 | 验收 gate |
|---|---|---|---|---|
| P1-1 | 命令 cancel | 3.4 / 3.10 | adapter 增 `cancel`;provider 实现 | 长命令可中途取消并审计 |
| P1-2 | 真快照/恢复 + retention | 3.5 | 工作区 tar → storage;restore 路径;retention 配置;Docker TTL reaper | snapshot 可 restore 出等价工作区 |
| P1-3 | 冷启动 warm pool | 3.6 | 预热池 + 认领 | 冷启动 P50 显著下降(设基线阈值) |
| P1-4 | 标准沙盒镜像 | 3.9 | 预装数据栈镜像 + pin | 镜像含约定包清单,digest 固定 |
| P1-5 | 受控出站 allowlist | 3.2 | egress proxy + 域名白名单落地 `ALLOWLIST`;`OPEN` 不启用 | 仅白名单域可达,其余被拒;与 P0-1 gVisor 成对上线 |

### P2 — 强多租户与规模化

| # | 项 | 对应维度 | 主要改动 | 验收 gate |
|---|---|---|---|---|
| P2-1 | microVM 档(Kata / Firecracker) | 3.1 / 3.7 | Kata RuntimeClass 或 microVM provider | 硬件边界 e2e 验证 |
| P2-2 | 真实 E2B provider | §4 | 实现 E2B adapter(air-gapped 除外) | 契约测试 + 非 air-gapped 集成 |
| P2-3 | per-tenant namespace / node pool | 3.7 | 租户级 namespace 或专用 node + taints | 跨租户调度隔离验证 |
| P2-4 | runtime 安全事件采集 | 3.8 | Falco/gVisor 事件 → 观测 | 异常 syscall 触发告警 |

---

## §6 风险与开放问题

- **私有部署 / air-gapped:** gVisor(runsc)可离线部署,适合 BYOC;**E2B 已被 config 明确禁用于 air-gapped**(`config.py:601-603`),故 P2-2 不覆盖离线客户。microVM 需节点支持嵌套虚拟化或裸金属。
- **托管 K8s 支持度:** GKE Sandbox 原生支持 gVisor;EKS/AKS 需自装 runsc + 配 containerd。P0-1 的落地清单需按目标平台分化。
- **gVisor 兼容性:** 部分 syscall / 原生库 / GPU 不被支持,存在性能损耗;需在标准沙盒镜像上跑兼容性基准。
- **成本 / 密度:** microVM 密度低于容器;warm pool 常驻占用资源。需在 P1-3 设定池大小与成本阈值。
- **产品已确认(2026-07-13):** ① Agent **需要联网** → 受控出站 `ALLOWLIST` 定为 P1(见 P1-5),`OPEN` 不启用,且必须与 P0-1 gVisor 成对上线;② **对标 Code Interpreter 数据分析体验** → 持久 kernel + 富输出定为 P0(见 P0-4)。

---

## 附:一句话总结

**现状是扎实的「容器档」沙盒(K8s + PSA restricted + NetworkPolicy + TTL + 镜像白名单),差在容器之上的内核边界。** 最高杠杆是 P0-1:gVisor 的 env 配置与 verify gate 已就位,只差在集群 ship `RuntimeClass` 对象 + 节点 runsc + 补集群侧 e2e + 收敛代码默认,即可一步对齐 ChatGPT 的用户态内核隔离;其余按 P1(cancel/快照/性能/受控出站)、P2(microVM/强多租户)推进。
