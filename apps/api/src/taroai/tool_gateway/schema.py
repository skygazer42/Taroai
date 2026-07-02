from typing import Any

from pydantic import BaseModel, Field


class JsonSchemaValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class JsonSchemaValidator(BaseModel):
    json_schema: dict[str, Any] = Field(default_factory=dict)

    def validate(self, value: Any) -> JsonSchemaValidationResult:
        errors: list[str] = []
        self._validate_value(value, self.json_schema or {}, "$", errors)
        return JsonSchemaValidationResult(valid=errors == [], errors=errors)

    def _validate_value(
        self,
        value: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        expected_type = schema.get("type")
        if expected_type is not None and not self._matches_type(value, expected_type):
            errors.append(f"{path} expected {expected_type}")
            return

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path} must be one of {schema['enum']}")

        if isinstance(value, dict):
            self._validate_object(value, schema, path, errors)
        if isinstance(value, list):
            self._validate_array(value, schema, path, errors)
        if isinstance(value, str):
            self._validate_string(value, schema, path, errors)
        if self._is_number(value):
            self._validate_number(value, schema, path, errors)

    def _validate_object(
        self,
        value: dict[str, Any],
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key} is required")

        properties = schema.get("properties", {})
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, dict):
                self._validate_value(value[key], property_schema, f"{path}.{key}", errors)

        if schema.get("additionalProperties") is False:
            additional = sorted(set(value) - set(properties))
            for key in additional:
                errors.append(f"{path}.{key} is not allowed")

    def _validate_array(
        self,
        value: list[Any],
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            errors.append(f"{path} must contain at least {min_items} items")
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            errors.append(f"{path} must contain at most {max_items} items")

        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                self._validate_value(item, item_schema, f"{path}[{index}]", errors)

    def _validate_string(
        self,
        value: str,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path} must contain at least {min_length} characters")
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            errors.append(f"{path} must contain at most {max_length} characters")

    def _validate_number(
        self,
        value: int | float,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{path} must be at least {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            errors.append(f"{path} must be at most {maximum}")

    def _matches_type(self, value: Any, expected_type: Any) -> bool:
        if isinstance(expected_type, list):
            return any(self._matches_type(value, item) for item in expected_type)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return self._is_number(value)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "null":
            return value is None
        return True

    def _is_number(self, value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
