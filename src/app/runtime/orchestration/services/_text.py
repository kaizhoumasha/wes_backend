"""Runtime orchestration service 的共享文本规范化。"""


def normalize_required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def escape_key_part(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


__all__ = ["escape_key_part", "normalize_required_text"]
