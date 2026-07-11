# 正式 Release Builder

`build-release.ps1` 生成的是可交付 release bundle，不是 repo ZIP。默认不构建、不推送镜像，只生成可复现的镜像构建计划和完整安装资产。

## 1. 默认：生成构建计划

```powershell
.\scripts\release\build-release.ps1
```

版本默认读取 `infra/helm/taroai/Chart.yaml` 的 `appVersion`，commit 默认读取当前 `HEAD`。工作区有未提交内容时会拒绝构建；仅在明确接受不可复现交付时使用 `-AllowDirty`。

输出目录：

```text
dist/releases/taroai-<version>-<commit>/
```

## 2. 构建四个产品镜像

```powershell
.\scripts\release\build-release.ps1 `
  -ImageMode Build `
  -Registry registry.example.com/taroai
```

构建：

- `taroai-api`
- `taroai-web`
- `taroai-sandbox-controller`
- `taroai-browser-controller`

API 镜像同时作为 worker 运行。Builder 会记录本地内容 digest、构建命令、commit、版本和 provenance。默认依然不 push。

## 3. 显式推送 Registry

```powershell
.\scripts\release\build-release.ps1 `
  -ImageMode Build `
  -Registry registry.example.com/taroai `
  -Push
```

`-Push` 必须和 `-ImageMode Build` 一起使用。推送后 manifest 记录 registry digest。

## 4. Air-gap 包

```powershell
.\scripts\release\build-release.ps1 `
  -ImageMode Build `
  -Registry registry.example.com/taroai `
  -AirGap
```

Air-gap 输出为 `.tar.gz`，不是源码 ZIP。它包含四个产品镜像以及 PostgreSQL、Redis、MinIO 运行镜像归档、Helm/Compose、迁移、配置模板、校验/安装脚本、SBOM 状态和回滚元数据。

## 5. SBOM 和签名

环境中存在 `syft` 时，Build/Air-gap 模式自动生成 SPDX JSON。需要把 SBOM 作为硬门禁时传 `-RequireSbom`。

签名密钥不交给 builder。通过批准的 PowerShell hook 对 `SHA256SUMS` 做 KMS/HSM/cosign 签名：

```powershell
.\scripts\release\build-release.ps1 `
  -ImageMode Build `
  -SigningHook C:\secure\sign-taroai-release.ps1
```

hook 接收 `BundlePath`、`ManifestPath`、`ChecksumPath`，应在 bundle 根目录输出 `SHA256SUMS.sig` 或 `SHA256SUMS.sig.json`。

## 6. 校验和安装

```powershell
.\scripts\release\verify-release.ps1 `
  -BundlePath .\dist\releases\taroai-<version>-<commit>

.\scripts\release\install-release.ps1 `
  -BundlePath .\dist\releases\taroai-<version>-<commit> `
  -Mode Validate
```

安装脚本也会先执行 bundle 校验；Compose 需要外部安全路径中的 runtime env，Helm 需要外部 Secret 和 customer values。

## 7. 回滚链

把上一版 `release-manifest.json` 传入：

```powershell
.\scripts\release\build-release.ps1 `
  -PreviousReleaseManifest C:\releases\previous\release-manifest.json
```

生成的 `metadata/rollback.json` 固定上一版镜像/commit 和当前 migration boundary。数据库结构已越过不兼容迁移时必须恢复发布前备份，不自动执行破坏性 down migration。

## 安全约束

- 禁止 `.env`、Git 元数据、虚拟环境、缓存、测试、开发 secrets、私钥进入 bundle。
- 发现疑似 API key、JWT、私钥、带凭据 URL 时立即失败，只报告文件路径，不打印秘密值。
- 输出先在 staging 目录完成，成功后才原子移动到最终目录。
- 已存在输出默认拒绝覆盖；仅 `-Force` 删除同名目标 bundle。
