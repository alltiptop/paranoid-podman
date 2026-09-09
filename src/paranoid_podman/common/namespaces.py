"""Validate keep-id mappings without accepting host or joined namespaces."""

import re


def is_keep_id(value: object) -> bool:
    if value == "keep-id":
        return True
    if not isinstance(value, str) or not value.startswith("keep-id:"):
        return False
    seen = set()
    for option in value[len("keep-id:") :].split(","):
        match = re.fullmatch(r"(uid|gid|size)=([0-9]{1,10})", option)
        if not match or match.group(1) in seen:
            return False
        name, number = match.groups()
        if not (1 if name == "size" else 0) <= int(number) <= 4_294_967_294:
            return False
        seen.add(name)
    return True
