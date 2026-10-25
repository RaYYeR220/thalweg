"""Small DynamoDB helpers."""

from __future__ import annotations

from botocore.exceptions import ClientError

from . import aws, jsonx


def is_conditional(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def update_fields(
    table_name: str,
    key: dict,
    fields: dict,
    *,
    remove: tuple[str, ...] = (),
    condition: str | None = None,
    names: dict | None = None,
    values: dict | None = None,
) -> dict:
    """SET every non-None field (floats become Decimal), REMOVE `remove`, optional condition.

    Returns the item's new attributes. Raises ClientError (check `is_conditional`).
    """
    fields = jsonx.to_ddb(fields)
    attr_names = dict(names or {})
    attr_values = dict(jsonx.to_ddb(values or {}))
    sets, rems = [], []
    for i, (k, v) in enumerate(fields.items()):
        attr_names[f"#s{i}"] = k
        attr_values[f":s{i}"] = v
        sets.append(f"#s{i} = :s{i}")
    for i, k in enumerate(remove):
        attr_names[f"#r{i}"] = k
        rems.append(f"#r{i}")
    expr = []
    if sets:
        expr.append("SET " + ", ".join(sets))
    if rems:
        expr.append("REMOVE " + ", ".join(rems))
    kwargs = {"Key": key, "UpdateExpression": " ".join(expr), "ReturnValues": "ALL_NEW"}
    if attr_names:
        kwargs["ExpressionAttributeNames"] = attr_names
    if attr_values:
        kwargs["ExpressionAttributeValues"] = attr_values
    if condition:
        kwargs["ConditionExpression"] = condition
    return aws.table(table_name).update_item(**kwargs).get("Attributes", {})
