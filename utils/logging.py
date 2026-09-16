"""Small JSON log formatter that never serializes passwords or file contents."""

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"time": datetime.now(timezone.utc).isoformat(),
                   "level": record.levelname, "event": record.getMessage()}
        for key in ("user_id", "document_id", "order_id", "reference"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(getattr(handler, "_cut_control_json", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._cut_control_json = True
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_event(event: str, **ids) -> None:
    logging.info(event, extra={key: value for key, value in ids.items()
                               if key in {"user_id", "document_id", "order_id", "reference"}})
