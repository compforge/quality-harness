"""Private, stdlib-only wire codec, also shipped as source to Pod Python.

Every cell is tagged so SQL text cannot be mistaken for a serialized native value.
Keep this module independent of package imports: Pods need only Python and PyMySQL.
"""

from base64 import b64decode, b64encode
from datetime import date, datetime, time, timedelta
from decimal import Decimal


def encode_value(value: object) -> list[object]:
    if value is None or isinstance(value, (bool, int, float, str)):
        return ["scalar", value]
    if isinstance(value, bytes):
        return ["bytes", b64encode(value).decode("ascii")]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat()]
    if isinstance(value, timedelta):
        return ["timedelta", [value.days, value.seconds, value.microseconds]]
    raise TypeError(f"Unsupported MySQL value type: {type(value).__name__}")


def decode_value(cell: list[object]) -> object:
    kind, value = cell
    if kind == "scalar":
        return value
    if kind == "bytes":
        return b64decode(value)
    if kind == "decimal":
        return Decimal(value)
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "time":
        return time.fromisoformat(value)
    if kind == "timedelta":
        return timedelta(*value)
    raise ValueError("Unknown MySQL wire value type")
