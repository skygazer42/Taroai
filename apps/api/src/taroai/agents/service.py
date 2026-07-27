import json
from typing import Any

from taroai.agents.models import (
    AgentDefinition,
    AgentDefinitionCreate,
    AgentDraft,
    AgentInvocation,
    AgentRunRequest,
    AgentVersion,
    AgentVersionSpec,
)
from taroai.domain import (
    ChatMessageCreate,
    ChatMessageDispatchStatus,
    ChatThreadCreate,
    RunCreate,
    RunMode,
    RunStatus,
    ResourceReference,
    new_id,
    utc_now,
)
from taroai.store import NotFoundError
from taroai.support.redaction import redact_text_entry


class AgentRegistryService:
    def __init__(
        self,
        *,
        registry: Any,
        store: Any,
        storage_catalog: Any | None = None,
        browser_profile_service: Any | None = None,
        agent_engine_registry: Any | None = None,
        coding_workspace_registry: Any | None = None,
        evaluation_service: Any | None = None,
        evaluation_repository: Any | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.storage_catalog = storage_catalog
        self.browser_profile_service = browser_profile_service
        self.agent_engine_registry = agent_engine_registry
        self.coding_workspace_registry = coding_workspace_registry
        self.evaluation_service = evaluation_service
        self.evaluation_repository = evaluation_repository

    def create(
        self,
        tenant_id: str,
        user_id: str,
        payload: AgentDefinitionCreate,
        source_run_id: str | None = None,
    ):
        now = utc_now()
        definition = AgentDefinition(
            id=new_id("agent"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            description=payload.description,
            app_kind=payload.app_kind,
            write_autonomy=payload.write_autonomy,
            latest_version=1,
            created_by_user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        version = AgentVersion(
            id=new_id("agent_version"),
            tenant_id=tenant_id,
            workspace_id=payload.workspace_id,
            agent_id=definition.id,
            version=1,
            spec=payload.version,
            created_by_user_id=user_id,
            created_at=now,
        )
        created = self.registry.create(definition, version)
        self.store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=definition.workspace_id,
            user_id=user_id,
            run_id=source_run_id,
            event_type="agent.created",
            metadata={
                "agent_id": definition.id,
                "version": version.version,
                "app_kind": definition.app_kind,
            },
        )
        return created

    def update_definition(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        *,
        source_run_id: str | None = None,
        **changes: Any,
    ) -> AgentDefinition:
        updated = self.registry.update_definition(tenant_id, agent_id, **changes)
        self.store.record_audit_event(
            tenant_id=tenant_id,
            workspace_id=updated.workspace_id,
            user_id=user_id,
            run_id=source_run_id,
            event_type="agent.updated",
            metadata={"agent_id": agent_id, "fields": sorted(changes)},
        )
        return updated

    def extract(
        self,
        tenant_id: str,
        thread_id: str,
        name: str | None = None,
        *,
        compile_playbook: bool = False,
    ) -> AgentDraft:
        thread = self.store.get_chat_thread(tenant_id, thread_id)
        runs = [
            run
            for run in self.store.list_runs(tenant_id, thread.workspace_id)
            if run.thread_id == thread_id and run.status == RunStatus.SUCCEEDED
        ]
        if not runs:
            raise ValueError("A successful Thread Run is required to extract an Agent")
        source_run = sorted(runs, key=lambda item: (item.updated_at, item.id))[-1]
        messages = self.store.list_chat_messages(tenant_id, thread_id)
        user_messages = [
            message.content for message in messages if message.role.value == "user"
        ]
        resource_refs = [
            ref.model_dump(mode="json")
            for message in messages
            for ref in message.resource_refs
        ]
        reference_file_ids = list(
            dict.fromkeys(
                str(item.get("id"))
                for item in resource_refs
                if item.get("type") == "file" and item.get("id")
            )
        )
        runtime_state = self._runtime_snapshot(tenant_id, source_run.id)
        used_skills = runtime_state.get("runtime_metadata", {}).get("used_skills", [])
        skill_bindings = {
            str(item.get("id") or item.get("skill_id")): dict(item)
            for item in resource_refs
            if item.get("type") == "skill" and (item.get("id") or item.get("skill_id"))
        }
        for item in used_skills:
            skill_id = item.get("skill_id") or item.get("id")
            if not skill_id:
                continue
            skill_bindings[str(skill_id)] = {
                "id": str(skill_id),
                "version": item.get("version"),
                "package_digest": item.get("package_digest"),
                "source_digest": item.get("source_digest"),
            }
        draft = AgentDraft(
            workspace_id=thread.workspace_id,
            name=name or thread.title or "Agent from conversation",
            description=f"Reusable Agent extracted from Thread {thread.id}",
            version=AgentVersionSpec(
                input_schema={
                    "type": "object",
                    "properties": {"request": {"type": "string"}},
                    "required": ["request"],
                },
                output_contract={"type": "string", "format": "markdown"},
                instructions=(
                    "Reproduce the successful workflow from this conversation.\n\n"
                    + "\n".join(f"- {item}" for item in user_messages[-8:])
                ),
                skill_bindings=list(skill_bindings.values()),
                connector_bindings=[
                    item for item in resource_refs if item.get("type") == "connector"
                ],
                knowledge_bindings=[
                    item for item in resource_refs if item.get("type") == "knowledge"
                ],
                reference_files=[
                    {"storage_object_id": item} for item in reference_file_ids
                ],
                model_policy={},
                runtime_snapshot={
                    **runtime_state.get("runtime_metadata", {}).get(
                        "runtime_snapshot", {}
                    ),
                    "source_run_id": source_run.id,
                    "checkpoint_sequence": runtime_state.get("checkpoint_sequence", 0),
                    "autonomy_mode": "workflow",
                },
                source_thread_id=thread.id,
                source_run_id=source_run.id,
                change_note="Extracted from a successful conversation",
            ),
            source_thread_id=thread.id,
            source_run_id=source_run.id,
        )
        if not compile_playbook:
            return draft

        playbook = self._compile_playbook(source_run)
        return draft.model_copy(
            update={
                "description": (
                    f"Deterministic Playbook compiled from Run {source_run.id}"
                ),
                "version": draft.version.model_copy(
                    update={
                        "input_schema": {"type": "object", "properties": {}},
                        "instructions": (
                            "Execute the reviewed deterministic Playbook exactly once. "
                            "The compiled action in the runtime snapshot is authoritative."
                        ),
                        "runtime_snapshot": {
                            **draft.version.runtime_snapshot,
                            "sandbox_enabled": True,
                            "compiled_playbook": playbook,
                        },
                        "change_note": (
                            "Compiled from one successful sandbox action; review required"
                        ),
                    }
                ),
            }
        )

    def _compile_playbook(self, source_run) -> dict[str, Any]:
        actions = self.store.list_agent_actions(source_run.tenant_id, source_run.id)
        if len(actions) != 1:
            raise ValueError(
                "A deterministic Playbook requires exactly one recorded action"
            )
        action = actions[0]
        observation = action.observation
        if action.status != "succeeded" or observation is None or not observation.success:
            raise ValueError("The Playbook source action must have succeeded")
        if action.decision.tool_name != "sandbox.command":
            raise ValueError(
                "Only a successful sandbox.command can be compiled in this version"
            )

        tool_input = {
            key: value
            for key, value in action.decision.tool_input.items()
            if key
            in {
                "command",
                "cwd",
                "timeout_seconds",
                "result_mode",
                "artifact_path",
                "artifact_paths",
            }
        }
        command = str(tool_input.get("command") or "").strip()
        if not command:
            raise ValueError("The Playbook source command is empty")
        _redacted, findings = redact_text_entry("compiled-playbook-command", command)
        if findings or action.decision.tool_input.get("env"):
            raise ValueError(
                "Commands containing credentials or environment values cannot be compiled"
            )

        artifact_paths = [
            str(path)
            for path in [
                tool_input.get("artifact_path"),
                *(tool_input.get("artifact_paths") or []),
            ]
            if path
        ]
        stdout = str(observation.output.get("stdout") or "").strip()
        if artifact_paths:
            result = {
                "mode": "artifacts",
                "response_text": (
                    "Playbook completed. Generated artifacts are available in this run."
                ),
            }
        elif (
            tool_input.get("result_mode") == "raw_stdout"
            and stdout
            and len(stdout) <= 4_000
        ):
            result = {"mode": "raw_stdout"}
        else:
            raise ValueError(
                "The source action must return short raw stdout or declare an artifact"
            )

        decision = action.decision.model_copy(
            update={
                "action_key": f"playbook:{action.id}",
                "rationale_summary": "Execute the reviewed compiled action",
                "tool_input": tool_input,
                "verification_required": False,
            }
        )
        return {
            "schema": "taroai.playbook.v1",
            "source_run_id": source_run.id,
            "source_action_id": action.id,
            "decision": decision.model_dump(mode="json"),
            "result": result,
        }

    def create_version(
        self, tenant_id: str, user_id: str, agent_id: str, spec: AgentVersionSpec
    ):
        definition = self.registry.get(tenant_id, agent_id)
        version = AgentVersion(
            id=new_id("agent_version"),
            tenant_id=tenant_id,
            workspace_id=definition.workspace_id,
            agent_id=agent_id,
            version=definition.latest_version + 1,
            spec=spec,
            created_by_user_id=user_id,
        )
        return self.registry.add_version(version)

    def restore_as_new(self, tenant_id: str, user_id: str, agent_id: str, version: int):
        source = self.registry.get_version(tenant_id, agent_id, version)
        spec = source.spec.model_copy(
            update={"change_note": f"Restored from version {version}"}, deep=True
        )
        return self.create_version(tenant_id, user_id, agent_id, spec)

    def publish(self, tenant_id: str, agent_id: str, version: int):
        target = self.registry.get_version(tenant_id, agent_id, version)
        for binding in target.spec.skill_bindings:
            if not (binding.get("id") or binding.get("skill_id")) or not binding.get(
                "version"
            ):
                raise ValueError(
                    "Published Agent skill bindings must pin id and version"
                )
        for reference in target.spec.reference_files:
            storage_object_id = reference.get("storage_object_id")
            if not storage_object_id:
                raise ValueError(
                    "Published Agent reference files must pin storage_object_id"
                )
            if self.storage_catalog is not None:
                storage_object = self.storage_catalog.get(tenant_id, storage_object_id)
                if storage_object.workspace_id != target.workspace_id:
                    raise ValueError(
                        "Published Agent reference file is not in the Agent workspace"
                    )
        for runtime_file in target.spec.runtime_snapshot.get("files", []):
            storage_object_id = runtime_file.get("storage_object_id")
            sandbox_path = str(runtime_file.get("sandbox_path") or "")
            if (
                not storage_object_id
                or not sandbox_path.startswith("/workspace/")
                or sandbox_path.startswith("/workspace/inputs/")
                or sandbox_path.startswith("/workspace/artifacts/")
                or ".." in sandbox_path.split("/")
            ):
                raise ValueError(
                    "Agent runtime snapshot files must pin storage and sandbox paths"
                )
            if self.storage_catalog is not None:
                storage_object = self.storage_catalog.get(tenant_id, storage_object_id)
                if storage_object.workspace_id != target.workspace_id:
                    raise ValueError(
                        "Agent runtime snapshot file is not in the Agent workspace"
                    )
        browser_profile_id = target.spec.runtime_snapshot.get("browser_profile_id")
        if browser_profile_id and self.browser_profile_service is not None:
            profile = self.browser_profile_service.get_profile(
                tenant_id, str(browser_profile_id)
            )
            if (
                profile.workspace_id != target.workspace_id
                or profile.status != "active"
            ):
                raise ValueError("Agent browser profile is not active in its workspace")
        engine_type = str(target.spec.runtime_snapshot.get("engine_type") or "native")
        connection_id = target.spec.runtime_snapshot.get("engine_connection_id")
        if engine_type not in {"native", "opencode", "codex", "claude"}:
            raise ValueError(
                "Agent runtime snapshot contains an unsupported Engine type"
            )
        if engine_type != "native" and not connection_id:
            raise ValueError("External Agent Engines require engine_connection_id")
        if connection_id and self.agent_engine_registry is not None:
            connection = self.agent_engine_registry.get_connection(
                tenant_id, str(connection_id)
            )
            if (
                connection.workspace_id != target.workspace_id
                or connection.status != "active"
                or connection.engine_type.value != engine_type
            ):
                raise ValueError(
                    "Agent Engine connection is not active for this Agent version"
                )
        repository_id = target.spec.runtime_snapshot.get("repository_id")
        if repository_id and self.coding_workspace_registry is not None:
            repository = self.coding_workspace_registry.get_repository(
                tenant_id, str(repository_id)
            )
            if (
                repository.workspace_id != target.workspace_id
                or repository.status != "active"
            ):
                raise ValueError(
                    "Agent repository binding is not active in its workspace"
                )
        model_policy = target.spec.model_policy
        if bool(model_policy.get("provider_id")) != bool(model_policy.get("model_id")):
            raise ValueError(
                "Agent model policy must pin provider_id and model_id together"
            )
        evaluation_suite_id = target.spec.runtime_snapshot.get("evaluation_suite_id")
        evaluation_suite_version = target.spec.runtime_snapshot.get(
            "evaluation_suite_version"
        )
        if bool(evaluation_suite_id) != bool(evaluation_suite_version):
            raise ValueError(
                "Agent evaluation binding must pin suite id and version together"
            )
        if evaluation_suite_id:
            if self.evaluation_service is None or self.evaluation_repository is None:
                raise ValueError("Agent evaluation service is not available")
            from taroai.evaluation import EvaluationTargetKind, canonical_digest

            target_digest = canonical_digest(target.spec.model_dump(mode="json"))
            matching_runs = [
                run
                for run in self.evaluation_repository.list_runs(tenant_id, agent_id)
                if run.target_kind == EvaluationTargetKind.AGENT
                and run.target_version == str(version)
                and run.target_digest == target_digest
                and run.suite_id == str(evaluation_suite_id)
                and run.suite_version == str(evaluation_suite_version)
            ]
            if not matching_runs:
                raise ValueError(
                    "Agent version must pass its pinned evaluation suite before publication"
                )
            latest_run = max(matching_runs, key=lambda item: item.completed_at)
            self.evaluation_service.assert_publishable(latest_run)
        return self.registry.publish(tenant_id, agent_id, version)

    def run(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        payload: AgentRunRequest,
    ) -> AgentInvocation:
        definition = self.registry.get(tenant_id, agent_id)
        version_number = payload.version or definition.published_version
        if version_number is None:
            raise ValueError("Agent must have a published version before it can run")
        version = self.registry.get_version(tenant_id, agent_id, version_number)
        if version.status != "published" and payload.version is None:
            raise ValueError("Agent version is not published")
        self._validate_input(version.spec.input_schema, payload.input)
        model_policy = version.spec.model_policy
        if bool(payload.provider_id) != bool(payload.model_id):
            raise ValueError(
                "Agent run model override requires provider_id and model_id"
            )
        provider_id = payload.provider_id or model_policy.get("provider_id")
        model_id = payload.model_id or model_policy.get("model_id")
        reasoning_effort = (
            payload.reasoning_effort
            if payload.reasoning_effort is not None
            else model_policy.get("reasoning_effort")
        )
        resource_refs = [
            ResourceReference(
                type="agent", id=definition.id, version=str(version.version)
            ),
            *self._resource_refs(version.spec),
        ]
        thread = self.store.create_chat_thread(
            tenant_id,
            user_id,
            ChatThreadCreate(
                workspace_id=definition.workspace_id,
                title=f"{definition.name} run",
                provider_id=provider_id,
                model_id=model_id,
                reasoning_effort=reasoning_effort,
                resource_refs=resource_refs,
            ),
        )
        execution_content = json.dumps(payload.input, ensure_ascii=False)
        single_value = next(iter(payload.input.values()), None)
        display_content = (
            single_value
            if len(payload.input) == 1 and isinstance(single_value, str)
            else execution_content
            if payload.input
            else f"Run {definition.name}"
        )
        message = self.store.append_chat_message(
            tenant_id,
            thread.id,
            user_id,
            ChatMessageCreate(
                content=display_content,
                execution_content=execution_content,
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                attachments=[
                    item["storage_object_id"]
                    for item in version.spec.reference_files
                    if item.get("storage_object_id")
                ],
                resource_refs=resource_refs,
            ),
        )
        run, _ = self.store.create_queued_thread_run_if_absent(
            tenant_id,
            user_id,
            RunCreate(
                workspace_id=definition.workspace_id,
                agent_id=definition.id,
                message=(
                    version.spec.instructions
                    + "\n\nStructured input:\n"
                    + execution_content
                ),
                attachments=[
                    item["storage_object_id"]
                    for item in version.spec.reference_files
                    if item.get("storage_object_id")
                ],
                thread_id=thread.id,
                trigger_message_id=message.id,
                provider_id=provider_id,
                model_id=model_id,
                reasoning_effort=reasoning_effort,
                mode=RunMode(
                    payload.mode
                    or (
                        RunMode.WORKFLOW.value
                        if definition.app_kind == "workflow"
                        else RunMode.AUTONOMOUS.value
                    )
                ),
                resource_refs=resource_refs,
            ),
        )
        return AgentInvocation(
            agent_id=definition.id,
            agent_version=version.version,
            thread_id=thread.id,
            message_id=message.id,
            run_id=run.id,
            events_url=f"/api/threads/{thread.id}/events",
        )

    def _validate_input(self, schema: dict[str, Any], value: dict[str, Any]) -> None:
        if schema.get("type") not in {None, "object"}:
            raise ValueError("Agent input schema root must be an object")
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"Missing required Agent input field: {required}")
        properties = schema.get("properties", {})
        python_types = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, item in value.items():
            expected_name = properties.get(key, {}).get("type")
            expected = python_types.get(expected_name)
            if expected is not None and not isinstance(item, expected):
                raise ValueError(f"Agent input field {key} must be {expected_name}")

    def _runtime_snapshot(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        try:
            return self.store.get_runtime_state(tenant_id, run_id).state_payload
        except NotFoundError:
            return {}

    def _resource_refs(self, spec: AgentVersionSpec) -> list[ResourceReference]:
        refs: list[ResourceReference] = []
        for kind, bindings in (
            ("skill", spec.skill_bindings),
            ("connector", spec.connector_bindings),
            ("knowledge", spec.knowledge_bindings),
        ):
            for binding in bindings:
                resource_id = binding.get("id") or binding.get(f"{kind}_id")
                if resource_id:
                    refs.append(
                        ResourceReference(
                            type=kind,
                            id=str(resource_id),
                            version=(
                                str(binding["version"])
                                if binding.get("version")
                                else None
                            ),
                        )
                    )
        return refs
