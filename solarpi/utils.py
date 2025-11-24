import re


def is_bt_addr(addr: str) -> bool:
    pattern = r"[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}"
    return re.match(pattern, addr, re.IGNORECASE) is not None
