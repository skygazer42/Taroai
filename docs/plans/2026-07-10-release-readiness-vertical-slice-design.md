# Taroai Release-Readiness Vertical Slice Design

**Date:** 2026-07-10
**Status:** Approved
**Approach:** Evidence-first vertical closure

## Objective

Move Taroai from an enterprise-agent beta architecture to a verifiable internal-alpha release without adding more horizontal control-plane modules. The release must prove a real model-backed run, establish measurable agent quality, provide a CREAO-styled Manus-class execution experience, and ship through a formal signed builder rather than a repository ZIP.

Production sandbox status remains `sandbox beta` until a separate real Kubernetes plus gVisor/Kata isolation gate passes.

## Current Evidence

- Docker Desktop Engine 27.5.1 is available with 12 CPUs and about 50 GB memory; the Compose configuration parses successfully.
- No Taroai Compose services are currently running.
- Docker Desktop Kubernetes is not enabled. The configured `docker-desktop` context refuses connections on port 6443.
- The current Python environment collects 429 tests and raises 72 collection errors because `boto3` is missing. This environment cannot support a full-suite pass claim.
- The API and packaged Web service are not running; only the standalone static Web preview is available.
- `.env` is tracked by Git and contains non-empty model provider configuration. The owner approved using it for synthetic development verification, but it must be untracked and its key rotated before formal release.
- The default sandbox provider is `local_process`. It intentionally does not claim production network, filesystem, or resource isolation.
- The Docker sandbox has useful container hardening, but the Compose controller receives the raw Docker socket. A compromised controller therefore has host-daemon authority and cannot be treated as a production isolation boundary.
- Kubernetes sandbox manifests and verification logic exist, but no live cluster evidence exists. Namespace/RBAC alignment and CNI enforcement still require real verification.
- Customer-feedback evaluation candidates exist, but there is no complete benchmark runner, versioned dataset, scorer contract, aggregate quality gate, or CI enforcement.
- The Web client exposes run evidence and operational panels, but uses Run-shaped state, polling and full-response SSE reads rather than persistent conversations and one resumable incremental stream.
- A clean source-package builder, Ed25519 signing, transfer evidence and verification already exist. The formal release path still lacks enforced CI, image-digest binding, SBOM, vulnerability/license reports and SLSA provenance.

## Product and Delivery Constraints

- Preserve the approved CREAO visual baseline.
- Add Manus-class interaction patterns: persistent conversations, live execution, a three-pane workbench, recoverable approvals, browser/terminal/artifact inspection, and clear failure states.
- Do not claim Docker or raw `docker.sock` as production sandbox isolation.
- Do not distribute a ZIP of the development repository.
- Do not treat unit mocks or synthetic in-memory adapters as runtime evidence.
- Do not expose model keys, prompts containing private customer data, or unredacted evidence in build artifacts.
- Prefer extending existing runtime, verification and release components over adding parallel frameworks.

## Chosen Architecture

The release is controlled by a sequence of blocking evidence gates:

1. **Hermetic build and test gate**
   - Build an isolated dependency environment from pinned application requirements.
   - Require zero pytest collection errors, zero test failures, static checks, migration checks and configuration validation.
   - Local workstation packages are not release evidence.

2. **Real Docker Compose runtime gate**
   - Run API, workers, Web, PostgreSQL, Redis, MinIO, browser controller and sandbox controller as real services.
   - Use the existing model provider configuration, with DeepSeek as the first strict-gate provider and an OpenAI-compatible provider as the portable second-provider contract.
   - Prove login, conversation turn, planning, tool use, sandbox execution, file creation, artifact promotion, preview/download and cleanup.
   - Save redacted, checksummed JSON evidence for the same run.

3. **Agent evaluation gate**
   - Add one bounded evaluation package containing dataset models, scorer contracts, a runner, result persistence and regression comparison.
   - Keep the initial dataset at 50 versioned synthetic tasks: file/sandbox, browser, multi-tool, approval/policy, and failure recovery, ten cases each.
   - Key results by dataset, agent, model, prompt, tool and policy versions.
   - Feed immutable terminal run traces into evaluation; evaluation never mutates production prompts or skills directly.

4. **CREAO plus Manus experience gate**
   - Introduce persistent `Conversation` and `Turn` records rather than reconstructing chats from Run status.
   - Use one ordered, resumable SSE stream for planning, tools, sandbox, browser, artifacts, approval and final assistant output.
   - Keep CREAO typography, palette and navigation while presenting simultaneous Conversation, Agent Timeline, and Browser/Terminal/Artifact panes at desktop widths.
   - Preserve complete functionality on narrow screens through explicit pane switching rather than hiding navigation or evidence.

5. **Formal supply-chain gate**
   - Build only from a clean, immutable source tag.
   - Produce digest-pinned OCI images, a Helm package and an air-gap bundle rather than a repository-source ZIP.
   - Generate a deployment manifest, SPDX or CycloneDX SBOM, vulnerability and license reports, SLSA provenance, archive/image checksums, Ed25519 signatures and transfer/install evidence.
   - Refuse release when any upstream evidence gate is absent or failed.

6. **Production sandbox gate**
   - Run separately on a real Kubernetes cluster with a required gVisor or Kata RuntimeClass.
   - Use a dedicated sandbox namespace, Pod Security Admission `restricted`, least-privilege RBAC, digest-only images, default-deny ingress/egress, service-account-token suppression and explicit resource quotas.
   - Prove CNI enforcement, tenant isolation, metadata and cluster-service denial, failure recovery and orphan-free cleanup.

## Runtime Data Flow

1. The client creates or resumes a `Conversation` and submits a persistent `Turn` with workspace, selected model, attachments and an idempotency key.
2. The API persists the Turn before creating and enqueueing a Run.
3. A worker produces strictly ordered events with stable sequence numbers while executing planning, tool, sandbox, browser, artifact and approval stages.
4. The client consumes one SSE connection, resumes with `Last-Event-ID`, and updates all three workbench panes from the same event stream.
5. The Tool Gateway performs permission and policy checks. High-risk or non-idempotent actions stop at an Approval boundary.
6. The Sandbox Controller executes the approved action. Files are promoted through Storage as checksum-addressed objects and then exposed as Artifacts.
7. A terminal Run trace is immutable input to the evaluation runner.
8. Test, runtime, evaluation, frontend and build reports are assembled into release evidence. The builder rejects incomplete or failing evidence.

## Failure and Recovery Semantics

- Model timeouts, rate limits and malformed structured output receive bounded retries only. Retry exhaustion produces an explicit terminal failure.
- Automatic tool retry is allowed only for idempotent operations. External writes require reconciliation or a new Approval.
- Event sequence plus `Last-Event-ID` prevents duplicate or lost UI events after reconnect.
- Approval records include tool, arguments or diff, scope, risk, estimated cost, expiry and an idempotent decision. Duplicate submissions produce one decision.
- Sandbox timeout triggers command termination and session destruction. An independent sweeper checks for residual containers, Pods, NetworkPolicies and volumes without requiring incoming traffic.
- Artifact upload is checksum-verified and atomically promoted. Partial output never appears as a successful artifact.
- Frontend cancellation, reconnection, permission failure and server error states remain actionable and recoverable.
- All logs and release evidence pass structured redaction before persistence or transfer.
- A failed required gate retains its evidence and blocks promotion; it is never rewritten as a warning-only success.

## Acceptance Gates

### Build and Test

- The hermetic environment installs every declared runtime and test dependency.
- Pytest completes with zero collection errors and zero failures.
- Static checks, schema checks, migrations and Compose configuration validation pass.

### Real Docker Closed Loop

- The strict Compose gate runs 50 synthetic standard tasks with a real model provider.
- PostgreSQL, Redis, MinIO, browser controller and sandbox controller are exercised rather than mocked.
- At least one canonical task proves login, persisted turn, planning, command execution, file creation, artifact promotion, preview/download and cleanup using a single run ID.
- Redacted evidence includes service readiness, event ordering, storage object checksums, cleanup results and release-gate status.

### Agent Quality

- Safety and required-approval adherence: 100%.
- Task success rate: at least 90%.
- Tool failure rate: at most 1%.
- Unexpected retry rate and human intervention rate: at most 5%, excluding cases explicitly designed to request approval.
- Cost and p95 latency regression: at most 10% against the approved baseline.
- Every task has a bounded step count and bounded retries; infinite loops are impossible by contract.

### Frontend Experience

- One conversation supports at least 50 continuous turns and survives refresh/reconnect.
- SSE resumes without missing or duplicate events.
- First execution event p95 is at most 1.5 seconds under the local acceptance profile.
- Cancel feedback appears within 500 ms.
- Desktop widths show all three execution panes simultaneously; 320-1440 px keeps every function reachable.
- WCAG 2.2 AA checks pass with no critical or serious automated accessibility findings.
- Desktop and mobile Playwright workflows pass against the real API.
- CREAO visual-regression verdict remains at least 90.

### Production Kubernetes Sandbox

- RuntimeClass is gVisor or Kata and cannot silently fall back.
- Runtime images are digest-pinned and satisfy the allowlist.
- Public network, DNS, cloud metadata, cluster services and cross-tenant access are actively denied and observed as denied.
- Fork bomb, memory, CPU, PID, disk-fill and command-timeout probes remain within limits.
- Controller restart, node loss and API disconnect tests recover or clean up without residual resources.

### Release Supply Chain

- A dirty tree, missing tag, missing evidence, mutable image tag or failed scan blocks release.
- Outputs include signed OCI images, Helm package, air-gap bundle, manifest, SBOM, scan reports, provenance and install evidence.
- `.env`, `.git`, IDE/cache files, tests, private keys and repository ZIPs are absent.
- Install, upgrade, rollback and restore validation pass before production promotion.

## Promotion Levels

- **Internal alpha / sandbox beta:** hermetic tests, real Docker closed loop, agent evaluation, frontend E2E and formal builder all pass.
- **Production candidate:** the real Kubernetes isolation gate also passes.
- **Production:** install, upgrade, rollback and restore drills pass against the candidate artifacts.

## Security Follow-up

The currently tracked `.env` may be used only for the approved synthetic development verification. Before any formal release:

1. Stop tracking `.env` and keep only `.env.example` in source control.
2. Rotate every key that has ever appeared in `.env` or Git history.
3. Supply release and runtime secrets only through local shell/CI secret stores or the deployment secret manager.

## Implementation Order

1. Repair the hermetic dependency and full-test gate.
2. Execute and harden the strict Docker Compose closed loop.
3. Implement the versioned 50-case evaluation gate.
4. Implement persistent conversations, resumable streaming and the three-pane frontend.
5. Upgrade the release builder and add mandatory CI evidence.
6. Execute the separate real Kubernetes production-sandbox qualification.
