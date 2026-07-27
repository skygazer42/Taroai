import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.domain import (
    ChatMessageCreate,
    ChatMessageDeliveryStatus,
    ChatMessageDispatchStatus,
    ChatMessageRole,
    ChatThreadCreate,
    ChatThreadStatus,
    Run,
    RunCreate,
    RunMode,
    RunStatus,
    utc_now,
)
WorkflowStatus = Literal[
    "awaiting_approval", "running", "paused", "succeeded", "failed", "cancelled"
]
WorkflowTaskStatus = Literal[
    "pending", "queued", "running", "succeeded", "failed", "cancelled", "blocked"
]


class WorkflowTaskSpec(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    preferred_tool: str | None = Field(default=None, alias="preferredTool")
    tool_input: dict[str, Any] = Field(default_factory=dict, alias="toolInput")
    tool_mode: Literal["read_only", "standard", "code"] = Field(
        default="standard", alias="toolMode"
    )
    model_hint: Literal["fast", "strong"] = Field(default="strong", alias="modelHint")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class WorkflowPhaseSpec(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    title: str = Field(min_length=1, max_length=200)
    tasks: list[WorkflowTaskSpec] = Field(min_length=1)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class WorkflowSpec(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    phases: list[WorkflowPhaseSpec] = Field(min_length=1)
    max_concurrency: int = Field(default=4, ge=1, le=8, alias="maxConcurrency")
    final_synthesis_prompt: str = Field(
        min_length=1, max_length=20_000, alias="finalSynthesisPrompt"
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def validate_graph(self):
        tasks = [task for phase in self.phases for task in phase.tasks]
        if len(tasks) > 100:
            raise ValueError("workflow cannot contain more than 100 tasks")
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow task ids must be unique")
        phase_ids = [phase.id for phase in self.phases]
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("workflow phase ids must be unique")
        known = set(ids)
        graph = {task.id: task.depends_on for task in tasks}
        for task_id, dependencies in graph.items():
            if task_id in dependencies:
                raise ValueError(f"workflow task cannot depend on itself: {task_id}")
            missing = set(dependencies) - known
            if missing:
                raise ValueError(
                    f"workflow task {task_id} has unknown dependencies: {sorted(missing)}"
                )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("workflow dependencies contain a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)
        return self

    def task(self, task_id: str) -> WorkflowTaskSpec:
        for phase in self.phases:
            for task in phase.tasks:
                if task.id == task_id:
                    return task
        raise KeyError(task_id)

    def phase_id_for(self, task_id: str) -> str:
        for phase in self.phases:
            if any(task.id == task_id for task in phase.tasks):
                return phase.id
        raise KeyError(task_id)


class WorkflowPreviewUpdate(BaseModel):
    spec: WorkflowSpec

    model_config = ConfigDict(extra="forbid")


class WorkflowRun(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    parent_run_id: str
    parent_thread_id: str | None = None
    user_id: str
    status: WorkflowStatus
    spec: WorkflowSpec
    approval_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class WorkflowTask(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    workflow_id: str
    task_id: str
    phase_id: str
    status: WorkflowTaskStatus = "pending"
    child_thread_id: str | None = None
    child_run_id: str | None = None
    summary: str = ""
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


def workflow_spec_from_plan(goal: str, plan: list[Any]) -> WorkflowSpec:
    phases: list[WorkflowPhaseSpec] = []
    phase_positions: dict[str, int] = {}
    previous_id: str | None = None
    for index, step in enumerate(plan):
        phase_id = str(getattr(step, "phase_id", None) or f"phase_{index + 1}")
        dependencies = getattr(step, "depends_on", None)
        if dependencies is None:
            dependencies = [previous_id] if previous_id else []
        task = WorkflowTaskSpec(
            id=step.id,
            title=step.title,
            description=step.title,
            depends_on=dependencies,
            preferred_tool=step.tool_name,
            tool_input=step.tool_input,
            tool_mode=getattr(step, "tool_mode", "standard"),
            model_hint=getattr(step, "model_hint", "strong"),
        )
        if phase_id not in phase_positions:
            phase_positions[phase_id] = len(phases)
            phases.append(
                WorkflowPhaseSpec(
                    id=phase_id,
                    title=str(getattr(step, "phase_title", None) or step.title),
                    tasks=[task],
                )
            )
        else:
            phases[phase_positions[phase_id]].tasks.append(task)
        previous_id = step.id
    title = (goal.strip().splitlines() or ["Workflow"])[0][:200]
    return WorkflowSpec(
        name=title,
        description=goal.strip()[:2000],
        phases=phases,
        final_synthesis_prompt=(
            "Synthesize the verified task results into one direct answer that satisfies "
            "the user's original request. Honor its requested scope, format, and brevity; "
            "when it says only or 只, return exactly the requested content. "
            "Do not add claims beyond the task evidence or "
            "infer the inverse or converse of a conditional rule. Do not mention internal "
            "workflow machinery."
        ),
    )


def workflow_goal(store: Any, run: Run) -> str:
    if run.agent_id is None and run.trigger_message_id is not None:
        return store.get_chat_message(run.tenant_id, run.trigger_message_id).content
    return run.message


class WorkflowCoordinator(BaseModel):
    store: Any
    runtime: Any

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def ready_runs(self, tenant_id: str, workflow_id: str) -> list[Run]:
        workflow = self.store.get_workflow(tenant_id, workflow_id)
        if workflow.status != "running":
            return []
        tasks = self.store.list_workflow_tasks(tenant_id, workflow_id)
        active = sum(task.status in {"queued", "running"} for task in tasks)
        slots = max(0, workflow.spec.max_concurrency - active)
        if not slots:
            return []
        succeeded = {task.task_id for task in tasks if task.status == "succeeded"}
        ready = [
            task
            for task in tasks
            if task.status == "pending"
            and set(workflow.spec.task(task.task_id).depends_on) <= succeeded
        ][:slots]
        # ponytail: 当前部署只有一个 Agent Worker；多 Worker 抢占时再把此筛选下沉为 SQL SKIP LOCKED。
        return [self._create_task_run(workflow, task, tasks) for task in ready]

    def mark_running(self, run: Run) -> None:
        task = self.store.get_workflow_task_for_child_run(run.tenant_id, run.id)
        if task is None or task.status != "queued":
            return
        updated = self.store.update_workflow_task(
            run.tenant_id, task.id, status="running"
        )
        self._task_event(updated)

    def complete_child(self, run: Run, state: Any) -> list[Run]:
        task = self.store.get_workflow_task_for_child_run(run.tenant_id, run.id)
        if task is None or task.status in {"succeeded", "failed", "cancelled"}:
            return []
        workflow = self.store.get_workflow(run.tenant_id, task.workflow_id)
        if run.status == RunStatus.SUCCEEDED:
            summary = self._task_summary(run, state)
            updated = self.store.update_workflow_task(
                run.tenant_id,
                task.id,
                status="succeeded",
                summary=summary,
                error=None,
                completed_at=utc_now(),
            )
            self._task_event(updated)
            tasks = self.store.list_workflow_tasks(run.tenant_id, workflow.id)
            if all(item.status == "succeeded" for item in tasks):
                if workflow.status != "paused":
                    self._synthesize(workflow, tasks)
                return []
            return self.ready_runs(run.tenant_id, workflow.id)

        status = "cancelled" if run.status == RunStatus.CANCELLED else "failed"
        updated = self.store.update_workflow_task(
            run.tenant_id,
            task.id,
            status=status,
            error=getattr(state, "failure_reason", None) or run.status.value,
            completed_at=utc_now(),
        )
        self._task_event(updated)
        if workflow.status not in {"cancelled", "paused"}:
            self._fail_workflow(workflow, updated.error or "workflow task failed")
        return []

    def retry_task(self, tenant_id: str, workflow_id: str, task_id: str) -> list[Run]:
        workflow = self.store.get_workflow(tenant_id, workflow_id)
        task = next(
            (
                item
                for item in self.store.list_workflow_tasks(tenant_id, workflow_id)
                if item.task_id == task_id
            ),
            None,
        )
        if task is None:
            raise ValueError(f"workflow task not found: {task_id}")
        if task.status not in {"failed", "cancelled", "blocked"}:
            raise ValueError("only failed, cancelled, or blocked workflow tasks can be retried")
        self.store.update_workflow_task(
            tenant_id,
            task.id,
            status="pending",
            child_thread_id=None,
            child_run_id=None,
            summary="",
            error=None,
            completed_at=None,
        )
        self.store.update_workflow(tenant_id, workflow_id, status="running")
        parent = self.store.update_run_status(
            tenant_id, workflow.parent_run_id, RunStatus.RUNNING
        )
        if workflow.approval_id:
            self.store.update_approval_execution(
                tenant_id,
                workflow.parent_run_id,
                workflow.approval_id,
                "applying",
            )
        self.store.append_run_event(
            parent,
            "workflow.resumed",
            {"workflowId": workflow.id, "taskId": task_id, "reason": "task_retry"},
        )
        return self.ready_runs(tenant_id, workflow_id)

    def pause(self, tenant_id: str, workflow_id: str) -> WorkflowRun:
        current = self.store.get_workflow(tenant_id, workflow_id)
        if current.status != "running":
            raise ValueError("only running workflows can be paused")
        workflow = self.store.update_workflow(tenant_id, workflow_id, status="paused")
        parent = self.store.update_run_status(
            tenant_id, workflow.parent_run_id, RunStatus.WAITING_FOR_USER
        )
        self.store.append_run_event(parent, "workflow.paused", {"workflowId": workflow.id})
        return workflow

    def resume(self, tenant_id: str, workflow_id: str) -> tuple[WorkflowRun, list[Run]]:
        workflow = self.store.get_workflow(tenant_id, workflow_id)
        if workflow.status != "paused":
            raise ValueError("only paused workflows can be resumed")
        workflow = self.store.update_workflow(tenant_id, workflow_id, status="running")
        parent = self.store.update_run_status(
            tenant_id, workflow.parent_run_id, RunStatus.RUNNING
        )
        self.store.append_run_event(parent, "workflow.resumed", {"workflowId": workflow.id})
        tasks = self.store.list_workflow_tasks(tenant_id, workflow.id)
        if tasks and all(task.status == "succeeded" for task in tasks):
            self._synthesize(workflow, tasks)
            return self.store.get_workflow(tenant_id, workflow.id), []
        return workflow, self.ready_runs(tenant_id, workflow_id)

    def cancel(self, tenant_id: str, workflow_id: str, user_id: str) -> WorkflowRun:
        workflow = self.store.get_workflow(tenant_id, workflow_id)
        if workflow.status == "cancelled":
            return workflow
        if workflow.status in {"succeeded", "failed"}:
            raise ValueError("completed workflows cannot be cancelled")
        for task in self.store.list_workflow_tasks(tenant_id, workflow_id):
            if task.child_run_id and task.status in {"queued", "running"}:
                child = self.store.get_run(tenant_id, task.child_run_id)
                if child.status not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                    RunStatus.TIMED_OUT,
                }:
                    self.store.cancel_run(tenant_id, child.id, user_id, "workflow_cancelled")
            if task.status in {"pending", "queued", "running", "blocked"}:
                self.store.update_workflow_task(
                    tenant_id, task.id, status="cancelled", completed_at=utc_now()
                )
        workflow = self.store.update_workflow(
            tenant_id, workflow_id, status="cancelled", completed_at=utc_now()
        )
        parent = self.store.get_run(tenant_id, workflow.parent_run_id)
        if parent.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }:
            parent = self.store.cancel_run(
                tenant_id, parent.id, user_id, "workflow_cancelled"
            )
        self.store.cancel_pending_approval_requests(tenant_id, parent.id, user_id)
        if workflow.approval_id:
            self.store.update_approval_execution(
                tenant_id,
                parent.id,
                workflow.approval_id,
                "superseded",
            )
        self.store.append_run_event(parent, "workflow.cancelled", {"workflowId": workflow.id})
        return workflow

    def _create_task_run(
        self,
        workflow: WorkflowRun,
        task: WorkflowTask,
        all_tasks: list[WorkflowTask],
    ) -> Run:
        spec = workflow.spec.task(task.task_id)
        parent = self.store.get_run(workflow.tenant_id, workflow.parent_run_id)
        goal = workflow_goal(self.store, parent)
        summaries = {
            item.task_id: item.summary
            for item in all_tasks
            if item.task_id in spec.depends_on and item.summary
        }
        prompt = (
            f'You are a worker in the approved workflow "{workflow.spec.name}".\n'
            "Execute only the task below. Do not solve the whole workflow.\n\n"
            f"Parent goal:\n{goal}\n\n"
            f"Task: {spec.title}\n{spec.description}\n\n"
            "Completed dependency task summaries:\n"
            f"{json.dumps(summaries, ensure_ascii=False)}\n\n"
            "Use only these summaries as upstream task results. Do not infer results from "
            "unlisted sibling tasks.\n\n"
            f"Tool policy: {spec.tool_mode}. Preferred tool: {spec.preferred_tool or 'none'}. "
            f"Suggested input: {json.dumps(spec.tool_input, ensure_ascii=False)}. "
            "Use available tools only when useful and verify the result. Return a concise "
            "handoff with the result, evidence, artifacts, blockers, and next-step notes; "
            "omit empty sections."
        )
        thread = self.store.create_chat_thread(
            workflow.tenant_id,
            workflow.user_id,
            ChatThreadCreate(
                workspace_id=workflow.workspace_id,
                title=f"{workflow.spec.name} · {spec.title}"[:200],
                provider_id=parent.provider_id,
                model_id=parent.model_id,
                reasoning_effort=parent.reasoning_effort,
            ),
        )
        self.store.update_chat_thread(
            workflow.tenant_id, thread.id, status=ChatThreadStatus.DELETED
        )
        message = self.store.append_chat_message(
            workflow.tenant_id,
            thread.id,
            workflow.user_id,
            ChatMessageCreate(
                content=spec.title,
                execution_content=prompt,
                kind="workflow_task",
                dispatch_status=ChatMessageDispatchStatus.INFLIGHT,
                resource_refs=parent.resource_refs,
            ),
        )
        run, _ = self.store.create_queued_thread_run_if_absent(
            workflow.tenant_id,
            workflow.user_id,
            RunCreate(
                workspace_id=workflow.workspace_id,
                agent_id=parent.agent_id,
                message=prompt,
                attachments=parent.attachments,
                mode=RunMode.AUTONOMOUS,
                thread_id=thread.id,
                trigger_message_id=message.id,
                provider_id=parent.provider_id,
                model_id=parent.model_id,
                reasoning_effort=parent.reasoning_effort,
                resource_refs=parent.resource_refs,
            ),
        )
        updated = self.store.update_workflow_task(
            workflow.tenant_id,
            task.id,
            status="queued",
            child_thread_id=thread.id,
            child_run_id=run.id,
            attempts=task.attempts + 1,
        )
        self._task_event(updated)
        return run

    def _task_event(self, task: WorkflowTask) -> None:
        workflow = self.store.get_workflow(task.tenant_id, task.workflow_id)
        parent = self.store.get_run(task.tenant_id, workflow.parent_run_id)
        self.store.append_run_event(
            parent,
            "workflow.task.updated",
            {
                "workflowId": workflow.id,
                "taskId": task.task_id,
                "phaseId": task.phase_id,
                "status": task.status,
                "attempts": task.attempts,
                "childRunId": task.child_run_id,
                "summary": task.summary,
                "error": task.error,
            },
        )
        phase_tasks = [
            item
            for item in self.store.list_workflow_tasks(task.tenant_id, workflow.id)
            if item.phase_id == task.phase_id
        ]
        statuses = {item.status for item in phase_tasks}
        phase_status = (
            "succeeded"
            if statuses == {"succeeded"}
            else "failed"
            if statuses & {"failed", "cancelled", "blocked"}
            else "running"
            if statuses & {"queued", "running", "succeeded"}
            else "pending"
        )
        self.store.append_run_event(
            parent,
            "workflow.phase.updated",
            {
                "workflowId": workflow.id,
                "phaseId": task.phase_id,
                "status": phase_status,
            },
        )

    def _task_summary(self, run: Run, state: Any) -> str:
        if summary := str(getattr(state, "final_response_text", None) or "").strip():
            return summary[:20_000]
        messages = self.store.list_chat_messages(run.tenant_id, run.thread_id)
        for message in reversed(messages):
            if message.role == ChatMessageRole.ASSISTANT and message.content.strip():
                return message.content.strip()[:20_000]
        return "Task completed successfully."

    def _synthesize(self, workflow: WorkflowRun, tasks: list[WorkflowTask]) -> None:
        # 延迟导入，避免 Store 导入持久化模型时反向加载 SQL Repository。
        from taroai.model_gateway import (
            ModelBudgetExceededError,
            ModelGatewayError,
            ModelGatewayRequest,
            ModelMessage,
        )

        parent = self.store.get_run(workflow.tenant_id, workflow.parent_run_id)
        goal = workflow_goal(self.store, parent)
        payload = [
            {
                "taskId": task.task_id,
                "title": workflow.spec.task(task.task_id).title,
                "summary": task.summary,
            }
            for task in tasks
        ]
        fallback = "\n\n".join(
            f"### {item['title']}\n{item['summary']}" for item in payload
        )
        request = ModelGatewayRequest(
            tenant_id=parent.tenant_id,
            workspace_id=parent.workspace_id,
            user_id=parent.user_id,
            run_id=parent.id,
            provider_id=parent.provider_id,
            model=parent.model_id,
            reasoning_effort=parent.reasoning_effort,
            messages=[
                ModelMessage(role="system", content=workflow.spec.final_synthesis_prompt),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {"goal": goal, "task_results": payload},
                        ensure_ascii=False,
                    ),
                ),
            ],
            metadata={"operation": "workflow_synthesize", "workflow_id": workflow.id},
        )
        chunks: list[str] = []
        try:
            for delta in self.runtime.model_gateway.stream_response(request):
                chunks.append(delta)
                self.store.append_run_event(parent, "assistant.delta", {"delta": delta})
        except (NotImplementedError, ModelGatewayError, ModelBudgetExceededError) as error:
            self.store.append_run_event(
                parent,
                "workflow.synthesis_fallback",
                {"reason": type(error).__name__},
            )
        response = "".join(chunks).strip() or fallback or "工作流已完成。"
        message = None
        if parent.thread_id:
            message = self.store.append_chat_message(
                parent.tenant_id,
                parent.thread_id,
                None,
                ChatMessageCreate(
                    role=ChatMessageRole.ASSISTANT,
                    content=response,
                    dispatch_status=ChatMessageDispatchStatus.COMPLETED,
                    delivery_status=ChatMessageDeliveryStatus.DELIVERED,
                ),
            )
        self.store.append_run_event(
            parent,
            "assistant.message.completed",
            {"message_id": message.id if message else None, "content": response},
        )
        if parent.trigger_message_id:
            self.store.update_chat_message(
                parent.tenant_id,
                parent.trigger_message_id,
                dispatch_status=ChatMessageDispatchStatus.COMPLETED,
                delivery_status=ChatMessageDeliveryStatus.DELIVERED,
            )
        self.store.update_workflow(
            parent.tenant_id, workflow.id, status="succeeded", completed_at=utc_now()
        )
        if workflow.approval_id:
            self.store.update_approval_execution(
                parent.tenant_id,
                parent.id,
                workflow.approval_id,
                "applied",
            )
        completed = self.store.update_run_status(
            parent.tenant_id, parent.id, RunStatus.SUCCEEDED, emit_status_event=False
        )
        self.store.append_run_event(
            completed,
            "workflow.completed",
            {"workflowId": workflow.id, "taskCount": len(tasks)},
        )
        self.store.append_run_event(completed, "run.succeeded", {"mode": "workflow"})
        state = self.runtime._load_state(parent.tenant_id, parent.id)
        state.status = RunStatus.SUCCEEDED
        state.final_response_text = response
        self.runtime._save_state(state)

    def _fail_workflow(self, workflow: WorkflowRun, error: str) -> None:
        for task in self.store.list_workflow_tasks(workflow.tenant_id, workflow.id):
            if task.child_run_id and task.status in {"queued", "running"}:
                child = self.store.get_run(workflow.tenant_id, task.child_run_id)
                if child.status not in {
                    RunStatus.SUCCEEDED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                    RunStatus.TIMED_OUT,
                }:
                    self.store.cancel_run(
                        workflow.tenant_id,
                        child.id,
                        workflow.user_id,
                        "workflow_sibling_failed",
                    )
                cancelled = self.store.update_workflow_task(
                    workflow.tenant_id,
                    task.id,
                    status="cancelled",
                    completed_at=utc_now(),
                )
                self._task_event(cancelled)
        self.store.update_workflow(
            workflow.tenant_id, workflow.id, status="failed", completed_at=utc_now()
        )
        if workflow.approval_id:
            self.store.update_approval_execution(
                workflow.tenant_id,
                workflow.parent_run_id,
                workflow.approval_id,
                "apply_failed",
                error,
            )
        parent = self.store.update_run_status(
            workflow.tenant_id,
            workflow.parent_run_id,
            RunStatus.FAILED,
            emit_status_event=False,
        )
        self.store.append_run_event(
            parent,
            "workflow.failed",
            {"workflowId": workflow.id, "error": error},
        )
        self.store.append_run_event(parent, "run.failed", {"reason": "workflow_task_failed"})
        if parent.trigger_message_id:
            self.store.update_chat_message(
                parent.tenant_id,
                parent.trigger_message_id,
                dispatch_status=ChatMessageDispatchStatus.FAILED,
                delivery_status=ChatMessageDeliveryStatus.FAILED,
            )
