# Taroai Helm Templates

This chart packages the backend control plane, workers, optional Sandbox Controller,
optional Playwright browser-controller, Web Workspace, migration
job, runtime configuration, service account, ingress, autoscaling hooks, and
network policy.

The chart does not create secret values by default. Set
`secrets.existingSecret` to a Kubernetes Secret that contains the keys declared
under `secrets.secretKeys`.

Enable `sandboxController.enabled` to deploy the packaged
`taroai-sandbox-controller` image and serve
`taroai.sandbox.controller_service:app` inside the cluster. The controller
receives only sandbox-controller configuration and
`TAROAI_SANDBOX_CONTROLLER_API_KEY`; it does not receive API, model, object
storage, bootstrap, or auth secrets. When
`TAROAI_SANDBOX_CONTROLLER_PROVIDER=kubernetes`, the chart also creates a
dedicated sandbox-controller ServiceAccount, a tokenless sandbox-runner
ServiceAccount for session Pods, and namespaced RBAC for Pods, Pods exec, and
NetworkPolicies. Set `TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ALLOWED_IMAGES` and,
for hardened clusters, `TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_REQUIRED=true`
with a non-empty `TAROAI_SANDBOX_CONTROLLER_KUBERNETES_RUNTIME_CLASS_NAME`.
Only enable `TAROAI_SANDBOX_CONTROLLER_KUBERNETES_ORPHAN_CLEANUP_ENABLED` when
the controller's active-session set is authoritative for the target namespace.
Keep `sandboxRuntimePolicy.enabled=true` for customer-operated Kubernetes
deployments unless the platform team applies equivalent controls separately.
The chart applies restricted Pod Security Admission labels to the release
namespace and creates sandbox runtime `ResourceQuota` and `LimitRange` resources
from `sandboxRuntimePolicy.resourceQuota` and
`sandboxRuntimePolicy.limitRange`. It also creates a default-deny
`NetworkPolicy` from `sandboxRuntimePolicy.networkPolicy` for Pods labeled
`app.kubernetes.io/name=taroai-sandbox-session`, giving Kubernetes sandbox Pods
a namespace-level isolation backstop before per-session policies are applied.

Enable `browserController.enabled` only when `TAROAI_BROWSER_PROVIDER=playwright`
and `TAROAI_BROWSER_CONTROLLER_BASE_URL` points at the in-cluster controller
service. Provide `TAROAI_BROWSER_CONTROLLER_API_KEY` through the runtime Secret
whenever browser-controller routes are shared beyond a single trusted namespace.

Enable `web.enabled` to deploy the static Web Workspace from the `taroai-web`
image. The web deployment serves only static assets, does not receive runtime
secrets, and is validated by the package/install checks as the customer-facing
workspace surface for local PoC and private package demos.

For `TAROAI_SANDBOX_PROVIDER=k8s` or `e2b`, point
`TAROAI_SANDBOX_CONTROLLER_BASE_URL` at the approved sandbox controller service
and provide a generated `TAROAI_SANDBOX_CONTROLLER_API_KEY` through the runtime
Secret for the HTTP sandbox controller adapter. The controller must expose
`GET /capabilities` and declare network isolation, filesystem isolation,
resource limits, destroy support, session TTL enforcement, maximum session TTL,
tenant session capacity, and run session capacity before private install
validation accepts the sandbox health evidence.

Full admin, SSO/MFA, skill marketplace, and browser live-view frontend surfaces
remain outside this chart slice.
