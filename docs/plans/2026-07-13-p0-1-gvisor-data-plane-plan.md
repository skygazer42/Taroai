# P0-1 gVisor 数据面落地 Implementation Plan

> **状态（2026-07-21）：当前发布路径已暂缓。** 在线版代码执行已采用真实 E2B microVM，gVisor 不再是 Compose/CREAO 对齐的发布前置条件。本文只适用于未来自托管或离线 Kubernetes 沙盒；除非重新选择该部署形态，不要据此扩展当前主路径。

**Goal:** 把 gVisor 从「控制面已配置但数据面未落地」推进到「集群上有 runsc `RuntimeClass` 对象、节点装了 runsc、且 verify 能证明 Pod 实际由 runsc 运行」,让 Taroai 沙盒的内核边界真正达到 ChatGPT Code Interpreter 同款(用户态内核)。

**Architecture:** 现状是 `Agent Runtime → Tool Gateway → HTTP Sandbox Adapter → Sandbox Controller → KubernetesSandboxAdapter → kubectl → Pod`。gVisor 的 env 配置(`configmap.yaml:70-71` 等)与「声明式」verify gate(`runtime_isolation_declared`)**已具备,本 plan 不重做**。本 plan 只补数据面四件事:① 新增 runsc `RuntimeClass` 清单(k8s + helm);② 节点 runsc handler 安装(平台分化文档 + 清单);③ 新增「effective」验证——从真实创建的 Pod 读回 `runtimeClassName` 断言实跑(区别于现有的「declared」);④ 收敛 `KubernetesSandboxAdapter` 代码默认使其脱离 controller 也 fail-safe。

**Tech Stack:** Python 3.12, Pydantic, FastAPI, pytest;Kubernetes(kustomize + Helm),gVisor(runsc RuntimeClass)。

**参照 spec:** `docs/plans/2026-07-13-code-execution-sandbox-gap-analysis-design.md`(§3.1、§5-P0-1)。

---

## 前置说明:不要重做的部分

以下已 shipped,**本 plan 不触碰**(重做会引入重复配置/校验):

- controller env 配置:`configmap.yaml:70-71`、`helm/values.yaml:97-98`、`docker-compose.yml:114-115`、`cloud/byoc/private.env.example` 均 `RUNTIME_CLASS_NAME=gvisor` + `REQUIRED=true`。
- controller 启动校验:`controller_service.py:99-112`(k8s provider 必须 required=true + name 非空,否则拒绝启动)。
- 「声明式」verify gate:`lifecycle_verification.py:649`、`install_validation.py:2563` 已 gate `runtime_isolation_declared`。
- Pod manifest 注入 `runtimeClassName`:`kubernetes.py:1164-1165`;从真实 Pod 读回 `runtimeClassName` 存入 `session.metadata`:`kubernetes.py:799-801`。

**declared vs effective 的定义(本 plan 的核心区分):**
- **declared**(已有):`capabilities.runtime_isolation` 来自 controller 配置(`controller_service.py:430-434`)——"配置说要求 gvisor"。
- **effective**(本 plan 新增):从真实创建的 Pod 读回的 `session.metadata["runtime_class_name"] == 期望值` 且 Pod 成功 Ready——"真的有一个 Pod 以 gvisor RuntimeClass 调度并就绪了"。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `infra/k8s/runtimeclass.yaml` | 创建 | 定义 `kind: RuntimeClass name: gvisor handler: runsc` |
| `infra/k8s/kustomization.yaml` | 修改 | 把 runtimeclass.yaml 加入 resources |
| `infra/helm/taroai/templates/runtimeclass.yaml` | 创建 | Helm 版 RuntimeClass(带 values 开关) |
| `infra/helm/taroai/values.yaml` | 修改 | 增加 `sandboxRuntimeClass` 开关与 handler |
| `infra/gvisor/README.md` | 创建 | 节点 runsc 安装的平台分化说明 |
| `infra/gvisor/eks-aks-runsc-installer.yaml` | 创建 | 自装平台的 runsc DaemonSet 安装清单 |
| `apps/api/src/taroai/deployment_evidence.py:31` | 修改 | `SandboxLifecycleVerificationResult` 增 effective 字段 |
| `apps/api/src/taroai/sandbox/lifecycle_verification.py` | 修改 | verify 主流程增 effective 检测 + 并入 passed |
| `apps/api/src/taroai/sandbox/kubernetes.py:48-49` | 修改 | 收敛 adapter 默认为 fail-safe |
| `apps/api/src/taroai/sandbox/controller_service.py:73-74` | 修改 | 收敛 controller settings 默认为 fail-safe |
| `tests/infra/test_gvisor_runtimeclass_manifest.py` | 创建 | 断言 RuntimeClass 清单与 kustomize 引用 |
| `tests/api/test_sandbox_lifecycle_runtime_effective.py` | 创建 | 断言 effective 检测逻辑 |
| `tests/api/test_sandbox_kubernetes.py` | 修改 | 修复因默认收敛受影响的用例 |

---

## Task 1: 新增 gVisor RuntimeClass 清单(k8s + Helm)

**Files:**
- Create: `infra/k8s/runtimeclass.yaml`
- Modify: `infra/k8s/kustomization.yaml`
- Modify: `tests/api/test_kubernetes_platform_deployment_contract.py:34-47`（kustomization resources 精确列表)
- Create: `infra/helm/taroai/templates/runtimeclass.yaml`
- Modify: `infra/helm/taroai/values.yaml`
- Modify: `tests/api/test_helm_packaging_contract.py`（`expected_templates` 集合)
- Test: `tests/infra/test_gvisor_runtimeclass_manifest.py`

> ⚠️ 两个既有精确匹配契约测试会因本 task 失败,必须在本 task 内同步更新以保持每 task 绿灯:`test_kubernetes_platform_deployment_contract.py:34-47`(kustomization `resources` 精确等于 12 项列表)、`test_helm_packaging_contract.py:180-184`(`templates/*` 文件集合相等)。

- [ ] **Step 1: 写失败测试**

先建目录:`mkdir -p tests/infra && touch tests/infra/__init__.py`

```python
# tests/infra/test_gvisor_runtimeclass_manifest.py
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml_documents(relative_path: str) -> list[dict]:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(text) if doc]


def test_runtimeclass_manifest_declares_runsc_handler():
    docs = _load_yaml_documents("infra/k8s/runtimeclass.yaml")
    runtime_classes = [d for d in docs if d.get("kind") == "RuntimeClass"]
    assert len(runtime_classes) == 1
    runtime_class = runtime_classes[0]
    assert runtime_class["apiVersion"] == "node.k8s.io/v1"
    assert runtime_class["metadata"]["name"] == "gvisor"
    assert runtime_class["handler"] == "runsc"


def test_kustomization_includes_runtimeclass():
    docs = _load_yaml_documents("infra/k8s/kustomization.yaml")
    kustomization = docs[0]
    assert "runtimeclass.yaml" in kustomization["resources"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/infra/test_gvisor_runtimeclass_manifest.py -v`
Expected: FAIL(`runtimeclass.yaml` 不存在 / `FileNotFoundError`）

- [ ] **Step 3: 创建 RuntimeClass 清单**

```yaml
# infra/k8s/runtimeclass.yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
  labels:
    app.kubernetes.io/name: taroai-sandbox-runtime
    app.kubernetes.io/component: sandbox-runtime
    app.kubernetes.io/part-of: taroai
handler: runsc
```

- [ ] **Step 4: 把它加入 kustomization**

在 `infra/k8s/kustomization.yaml` 的 `resources:` 列表最前面(在 `sandbox-runtime-policy.yaml` 之前)加一行 `  - runtimeclass.yaml`。RuntimeClass 是集群作用域对象——保留 kustomization 顶部的 `namespace: taroai` 不变即可(kustomize 不会给 cluster-scoped 对象强加 namespace)。

- [ ] **Step 4b: 同步 kustomization 契约测试**

`test_kubernetes_platform_deployment_contract.py:34-47` 精确断言 `resources` 列表——在列表最前面加一项 `"runtimeclass.yaml",`(与 Step 4 的插入位置一致),否则该契约测试立即 red。

- [ ] **Step 5: 跑测试确认通过(含契约回归)**

Run: `python -m pytest tests/infra/test_gvisor_runtimeclass_manifest.py tests/api/test_kubernetes_platform_deployment_contract.py -v`
Expected: PASS

- [ ] **Step 6: 若装了 kubectl,额外验证 kustomize 能构建**

Run: `kubectl kustomize infra/k8s | grep -A3 "kind: RuntimeClass"`（无 kubectl 则跳过,记录在提交信息里）
Expected: 输出含 `name: gvisor` 与 `handler: runsc`

- [ ] **Step 7: 创建 Helm 模板 + values 开关**

```yaml
# infra/helm/taroai/templates/runtimeclass.yaml
{{- if .Values.sandboxRuntimeClass.create }}
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: {{ .Values.sandboxRuntimeClass.name | quote }}
  labels:
    app.kubernetes.io/name: taroai-sandbox-runtime
    app.kubernetes.io/component: sandbox-runtime
    app.kubernetes.io/part-of: taroai
handler: {{ .Values.sandboxRuntimeClass.handler | quote }}
{{- end }}
```

在 `infra/helm/taroai/values.yaml` 增加(与既有 `TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME: gvisor` 对齐):

```yaml
sandboxRuntimeClass:
  create: true
  name: gvisor
  handler: runsc
```

- [ ] **Step 8: 若装了 helm,验证渲染**

Run: `helm template infra/helm/taroai | grep -A3 "kind: RuntimeClass"`（无 helm 则跳过）
Expected: 输出含 `name: "gvisor"` 与 `handler: "runsc"`

- [ ] **Step 8b: 同步 helm 打包契约测试**

`test_helm_packaging_contract.py:180-184` 断言 `templates/*` 文件集合相等——在 `expected_templates` 集合加一项 `"templates/runtimeclass.yaml",`,否则集合相等 red。

- [ ] **Step 8c: 跑 helm 契约测试确认通过**

Run: `python -m pytest tests/api/test_helm_packaging_contract.py -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add infra/k8s/runtimeclass.yaml infra/k8s/kustomization.yaml \
        infra/helm/taroai/templates/runtimeclass.yaml infra/helm/taroai/values.yaml \
        tests/api/test_kubernetes_platform_deployment_contract.py \
        tests/api/test_helm_packaging_contract.py \
        tests/infra/__init__.py tests/infra/test_gvisor_runtimeclass_manifest.py
git commit -m "feat(sandbox): ship gvisor RuntimeClass manifest for k8s and helm"
```

---

## Task 2: 节点 runsc handler 安装(平台分化)

> ⚠️ **无法在本地 TDD**:节点安装依赖真实集群与节点 OS,需在目标集群执行并验证。本 task 产出「清单 + 文档 + 目标集群验收清单」,验收发生在集群侧(见 Task 3 的集群侧 e2e)。

**Files:**
- Create: `infra/gvisor/README.md`
- Create: `infra/gvisor/eks-aks-runsc-installer.yaml`

- [ ] **Step 1: 写平台分化安装说明**

`infra/gvisor/README.md` 至少覆盖:

- **GKE**:用 GKE Sandbox——创建带 `--sandbox type=gvisor` 的 node pool,节点自带 runsc,`RuntimeClass gvisor`(Task 1)即生效。无需 DaemonSet。
- **EKS / AKS / 自建**:节点需自装 runsc + 配置 containerd。用 `eks-aks-runsc-installer.yaml`(DaemonSet)在节点上安装 runsc 二进制并写入 `/etc/containerd/config.toml` 的 `runsc` runtime handler,然后滚动重启 containerd。
- **验收命令**(在目标集群):
  - `kubectl get runtimeclass gvisor` → 存在
  - 在装了 runsc 的节点上 `runsc --version` → 有输出
  - 起一个 `runtimeClassName: gvisor` 的探针 Pod,`kubectl get pod <p> -o jsonpath='{.spec.runtimeClassName}'` == `gvisor` 且 Pod `Ready`

- [ ] **Step 2: 提供自装平台的 runsc DaemonSet**

`infra/gvisor/eks-aks-runsc-installer.yaml`:一个 DaemonSet,选择 sandbox node pool 的节点(用 nodeSelector/taints),initContainer 下载校验 runsc(建议固定版本 + sha512 校验,呼应 spec 的供应链原则),把 runsc 装到节点、patch containerd 配置。清单顶部注释写明:**必须固定 runsc 版本与校验和,并按节点 OS/containerd 版本调整路径**。

（此清单是模板,需按目标集群 containerd 版本与节点 OS 适配;不在本仓库 CI 执行。）

- [ ] **Step 3: 提交**

```bash
git add infra/gvisor/README.md infra/gvisor/eks-aks-runsc-installer.yaml
git commit -m "docs(sandbox): add gvisor node runsc install guide and DaemonSet template"
```

---

## Task 3: 集群侧 effective 验证(Pod 实际由 runsc 运行)

**目标:** 新增「effective」证据——从真实创建的 Pod 读回 `runtime_class_name` 并断言 == 期望值,区别于现有的「declared」。数据来源:`session.metadata["runtime_class_name"]`,由 `kubernetes.py:799-801` 从真实 Pod spec 读回,经 HTTP `SandboxSession.model_validate`(`http.py:56/291`,自动保留 metadata)透传到 verify 侧。

**Files:**
- Modify: `apps/api/src/taroai/deployment_evidence.py:31`(`SandboxLifecycleVerificationResult`)
- Modify: `apps/api/src/taroai/sandbox/lifecycle_verification.py`
- Test: `tests/api/test_sandbox_lifecycle_runtime_effective.py`

- [ ] **Step 1: 先读现有结构,确认接入点**

Run: `python -m pytest tests/api/test_kubernetes_sandbox_verification.py -q`（先确认现状全绿,作为回归基线）
读 `deployment_evidence.py` 的 `SandboxLifecycleVerificationResult` 字段风格、`lifecycle_verification.py:109` 的 `verify_sandbox_lifecycle` 主流程(它在何处创建 session、如何拿到返回的 `SandboxSession`),以及 `SandboxLifecycleVerificationConfig` 定义(用于加期望值字段)。`tests/api/test_kubernetes_sandbox_verification.py:27` 的 `RecordingKubernetesSandboxAdapter` 已能在 create 返回 `metadata.runtime_class_name`(`:50` 为 `"gvisor"`,`:820` 有 `"runc"` 反例),测试直接复用它。

- [ ] **Step 2: 写失败测试**

```python
# tests/api/test_sandbox_lifecycle_runtime_effective.py
from taroai.sandbox.lifecycle_verification import (
    sandbox_runtime_isolation_effective,
)


def test_effective_true_when_pod_runtime_class_matches_expected():
    session_metadata = {"runtime_class_name": "gvisor"}
    assert sandbox_runtime_isolation_effective(session_metadata, "gvisor") is True


def test_effective_false_when_pod_runtime_class_is_runc():
    session_metadata = {"runtime_class_name": "runc"}
    assert sandbox_runtime_isolation_effective(session_metadata, "gvisor") is False


def test_effective_false_when_pod_runtime_class_absent():
    assert sandbox_runtime_isolation_effective({}, "gvisor") is False


def test_effective_false_when_expected_blank():
    # 期望值必须显式配置,空期望不得判为通过(避免静默放行)
    assert sandbox_runtime_isolation_effective({"runtime_class_name": ""}, "") is False
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/api/test_sandbox_lifecycle_runtime_effective.py -v`
Expected: FAIL(`ImportError: cannot import name 'sandbox_runtime_isolation_effective'`)

- [ ] **Step 4: 实现纯函数**

在 `lifecycle_verification.py` 顶层新增(放在 `sandbox_lifecycle_capabilities_result` 附近,保持同文件工具函数聚集):

```python
def sandbox_runtime_isolation_effective(
    session_metadata: dict[str, object] | None,
    expected_runtime_class_name: str,
) -> bool:
    expected = expected_runtime_class_name.strip()
    if not expected:
        return False
    metadata = session_metadata or {}
    actual = metadata.get("runtime_class_name")
    return isinstance(actual, str) and actual.strip() == expected
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/api/test_sandbox_lifecycle_runtime_effective.py -v`
Expected: PASS

- [ ] **Step 6: 把 effective 字段加入 result 模型**

在 `deployment_evidence.py` 的 `SandboxLifecycleVerificationResult` 增加(默认 False，向后兼容):

```python
    runtime_isolation_effective: bool = False
    observed_runtime_class_name: str = ""
```

- [ ] **Step 7: 在 verify 主流程填充 effective(用创建返回的真实 session)**

注意路由:`verify_sandbox_lifecycle`(`:109`)里创建返回的变量名是 `session`(`:144`);而 `SandboxLifecycleVerificationResult` 是在 helper `sandbox_lifecycle_verification_result(...)`(`:359-446`)里构造的,不是主函数直接构造。做法:① 给 `SandboxLifecycleVerificationConfig` 增字段 `expected_runtime_class_name: str = "gvisor"`;② 给 helper `sandbox_lifecycle_verification_result` 增两个形参 `runtime_isolation_effective: bool = False`、`observed_runtime_class_name: str = ""`,在其构造 result 处透传;③ 在主 return 点(`:292` 附近)计算并传入:

```python
    observed_runtime_class_name = str(session.metadata.get("runtime_class_name", ""))
    runtime_isolation_effective = sandbox_runtime_isolation_effective(
        session.metadata,
        config.expected_runtime_class_name,
    )
    # 传入 sandbox_lifecycle_verification_result(..., runtime_isolation_effective=..., observed_runtime_class_name=...)
```

创建失败的早返回(`:158`)不传这两个值,依赖模型默认(False/"")即可。**Prior art:** `kubernetes_verification.py:393-406` 已有 adapter 级 actual-vs-expected `runtime_class_name` 比较,可镜像其精确匹配逻辑(不同 verifier,非重复)。

- [ ] **Step 8: 并入 passed 条件(带开关,保持向后兼容)**

现有 `sandbox_lifecycle_verification_passed`（`lifecycle_verification.py:625`）已有 `auth_challenge_required` 开关的先例。仿此加 `runtime_isolation_effective_required: bool = False` 形参,并在 return 链尾加:

```python
        and (
            not runtime_isolation_effective_required
            or result.runtime_isolation_effective
        )
```

私有安装/发布 gate 处(调用方)在 K8s+gVisor 强制场景传 `runtime_isolation_effective_required=True`;默认 False 使现有非 gVisor 契约测试不被破坏。

- [ ] **Step 9: 主流程断言(优先纯函数,主流程一条聚焦断言)**

核心逻辑已被 Step 2-5 的纯函数测试覆盖(hermetic、干净)。主流程再补一条聚焦断言即可:`RecordingKubernetesSandboxAdapter` 定义在 `test_kubernetes_sandbox_verification.py`(import 复用或复制最小版),它 create 返回 `metadata.runtime_class_name="gvisor"`;驱动 `verify_sandbox_lifecycle` 断言 `result.runtime_isolation_effective is True`、`observed_runtime_class_name == "gvisor"`。**注意非 hermetic**:完整 verify 会尝试打 `localhost:8002`(snapshot/file-read scope 探测),这些会 fail-fast 到 False 而不影响 effective 断言;因此只断言 effective 相关字段,不要依赖这些副作用字段。再加一条:期望 `"gvisor"` 但 adapter 返回 `"runc"` 时,`sandbox_lifecycle_verification_passed(result, runtime_isolation_effective_required=True) is False`。

- [ ] **Step 10: 跑测试确认通过**

Run: `python -m pytest tests/api/test_sandbox_lifecycle_runtime_effective.py tests/api/test_kubernetes_sandbox_verification.py -v`
Expected: PASS（含既有验证回归全绿）

- [ ] **Step 11: 提交**

```bash
git add apps/api/src/taroai/deployment_evidence.py \
        apps/api/src/taroai/sandbox/lifecycle_verification.py \
        tests/api/test_sandbox_lifecycle_runtime_effective.py
git commit -m "feat(sandbox): verify effective gvisor runtime from real pod, not just declared"
```

---

## Task 4: 收敛 adapter 代码默认为 fail-safe(决策点)

> **决策点:** controller 已在启动时强制 gVisor(`controller_service.py:99-112`),故运行时不存在降级。但 `KubernetesSandboxAdapter` 与 controller settings 的**类默认**是 `runtime_class_name=""`/`runtime_class_required=False`——脱离 controller 直接构造(测试、未来新调用路径)会静默退到无 runtime 隔离。本 task 把默认收敛为 fail-safe。**推荐执行;若团队倾向零测试改动,可改用「保守替代」(见末尾)。**

**Files:**
- Modify: `apps/api/src/taroai/sandbox/kubernetes.py:48-49`
- Modify: `apps/api/src/taroai/sandbox/controller_service.py:73-74`
- Modify: `tests/api/test_sandbox_kubernetes.py`(修复受影响用例)

- [ ] **Step 1: 写失败测试(断言新默认)**

在 `tests/api/test_sandbox_kubernetes.py` 增:

```python
def test_kubernetes_adapter_defaults_are_failsafe():
    from taroai.sandbox.kubernetes import KubernetesSandboxAdapter

    adapter = KubernetesSandboxAdapter()
    assert adapter.runtime_class_name == "gvisor"
    assert adapter.runtime_class_required is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/api/test_sandbox_kubernetes.py::test_kubernetes_adapter_defaults_are_failsafe -v`
Expected: FAIL（默认为 `""`/`False`）

- [ ] **Step 3: 收敛默认**

- `kubernetes.py:48-49`:
  ```python
      runtime_class_name: str = "gvisor"
      runtime_class_required: bool = True
  ```
- `controller_service.py:73-74`:
  ```python
      kubernetes_runtime_class_name: str = "gvisor"
      kubernetes_runtime_class_required: bool = True
  ```

- [ ] **Step 4: 跑单测确认新默认通过**

Run: `python -m pytest tests/api/test_sandbox_kubernetes.py::test_kubernetes_adapter_defaults_are_failsafe -v`
Expected: PASS

- [ ] **Step 5: 跑全量 sandbox 测试,定位回归**

Run: `python -m pytest tests/api/test_sandbox_kubernetes.py tests/api/test_kubernetes_sandbox_verification.py -v`
Expected: 真正会 break 的是**默认构造 adapter**(不传 runtime_class)的用例——其生成的 Pod manifest 现在带 `runtimeClassName: gvisor`,断言旧 manifest(无 runtimeClassName 或期望 runc)的用例会失败。已显式传 `runtime_class_required=True, runtime_class_name="gvisor"` 的用例(断言 `runtime_isolation is True` 的)不受影响。逐一根据用例本意处理(下一步)。

- [ ] **Step 6: 修复回归(显式化意图,而非放宽默认)**

对每个失败用例:若它本意就是「无 runtime 隔离」的负例,显式传 `runtime_class_required=False, runtime_class_name=""`(把意图写清楚);若只是没传参而默认变了,补齐或依赖新默认。**不得为让测试通过而回退默认**。

- [ ] **Step 7: 跑全量确认绿**

Run: `python -m pytest tests/ -q`
Expected: PASS（全绿）

- [ ] **Step 8: 提交**

```bash
git add apps/api/src/taroai/sandbox/kubernetes.py \
        apps/api/src/taroai/sandbox/controller_service.py \
        tests/api/test_sandbox_kubernetes.py
git commit -m "refactor(sandbox): make kubernetes adapter runtime-class default fail-safe"
```

**保守替代(仅当放弃 Step 3 改默认时):** 不改默认值,改为在 `KubernetesSandboxAdapter.create` 中当 `runtime_class_required is False` 时记一条显式 warning 日志(而非静默),并在 `get_capabilities` 文档串注明「默认不强制,生产必须经 controller 配置」。测试改动最小,但保护更弱。

---

## Verification（全部完成后)

```bash
# 单元 / 清单层
python -m pytest tests/infra/test_gvisor_runtimeclass_manifest.py -q
python -m pytest tests/api/test_sandbox_lifecycle_runtime_effective.py -q
python -m pytest tests/ -q

# 清单层(装了工具则跑)
kubectl kustomize infra/k8s | grep -A3 "kind: RuntimeClass"
helm template infra/helm/taroai | grep -A3 "kind: RuntimeClass"
```

**目标集群侧(需真实 K8s + gVisor 节点,属 Task 2 验收):**
1. `kubectl get runtimeclass gvisor` 存在
2. 起一个 sandbox session,`kubectl get pod <p> -o jsonpath='{.spec.runtimeClassName}'` == `gvisor` 且 Pod `Ready`
3. 带 `runtime_isolation_effective_required=True` 跑 `verify_sandbox_lifecycle`,`sandbox_lifecycle_verification_passed(...)` 为真

**最终预期:** 集群有 runsc `RuntimeClass`,节点装了 runsc,verify 能证明 Pod **实际由 runsc 运行**(而非仅配置声明),且 adapter 默认 fail-safe。内核边界达到 ChatGPT 同款用户态内核。

---

## 风险与开放问题

- **节点安装不可本地测**:Task 2 依赖目标集群/节点 OS,`eks-aks-runsc-installer.yaml` 是需适配的模板;GKE 建议直接用 GKE Sandbox node pool。
- **gVisor 兼容性**:部分 native 库 / GPU / syscall 不受支持,需在标准沙盒镜像(spec P1-4)上跑兼容性基准。
- **Task 4 测试面**:33 处构造 adapter,改默认会触及负例用例,需逐个显式化意图。
- **effective 证据强度**:本 plan 以「真实 Pod 的 `runtimeClassName` + Pod Ready」为 effective 证据(节点无 runsc 时 Pod 无法 Ready → create 超时,故 Ready+gvisor 已强含"runsc 生效")。如需更强,可在沙盒内追加 gVisor 运行时指纹探测(如读 `/proc/version`),列为后续增强,不在本 plan。
