import logging

OPERATIONAL_FIELDS = (
    "component",
    "operation",
    "entity_type",
    "entity_id",
    "session_id",
    "delivery_id",
    "event_type",
    "result",
    "error_category",
    "primary_error_category",
    "primary_provider",
    "fallback_provider",
    "latency_ms",
    "record_count",
)


class OperationalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        fields = []
        for key in OPERATIONAL_FIELDS:
            value = getattr(record, key, None)
            if value is None:
                continue
            safe_value = " ".join(str(value).split())[:120]
            fields.append(f"{key}={safe_value}")
        if not fields:
            return rendered
        return f"{rendered} | {' '.join(fields)}"


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        OperationalFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO), handlers=[handler]
    )
