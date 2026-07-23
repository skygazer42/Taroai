import pytest

from taroai.tool_gateway import ToolExecutionError, ToolGateway, ToolGatewayRequest
from taroai.ui_render import register_ui_render_tool_handler


class EventStore:
    def __init__(self):
        self.run = object()
        self.events = []

    def get_run(self, tenant_id, run_id):
        assert (tenant_id, run_id) == ("tenant_1", "run_1")
        return self.run

    def append_run_event(self, run, event_type, payload):
        assert run is self.run
        self.events.append((event_type, payload))


def test_ui_render_tool_emits_a_safe_structured_block():
    store = EventStore()
    gateway = ToolGateway()
    register_ui_render_tool_handler(gateway, store)
    assert list(gateway.policies["ui.render"].input_schema["properties"])[:2] == [
        "content",
        "title",
    ]
    request = ToolGatewayRequest(
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        user_id="user_1",
        run_id="run_1",
        step_id="step:summary",
        tool_name="ui.render",
        tool_input={
            "title": "Run result",
            "description": "Execution summary",
            "content": "| Answer | Status |\n| --- | --- |\n| 42<br>exact | Verified |",
        },
    )

    result = gateway.execute_request(request)

    assert result.output["block_id"] == "ui_step-summary"
    assert result.output["intro"] == "Run result"
    assert store.events == [
        (
            "ui_render",
            {
                "blockId": "ui_step-summary",
                "title": "Run result",
                "spec": {
                    "root": "card",
                    "elements": {
                        "card": {
                            "type": "Card",
                            "props": {
                                "title": "Run result",
                                "description": "Execution summary",
                            },
                            "children": ["content"],
                        },
                        "content": {
                            "type": "Text",
                            "props": {
                                "text": "| Answer | Status |\n| --- | --- |\n| 42 exact | Verified |"
                            },
                        },
                    },
                },
            },
        )
    ]

    request.tool_input["content"] = "   "
    with pytest.raises(ToolExecutionError, match="must not be empty"):
        gateway.execute_request(request)
