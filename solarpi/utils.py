import re
import struct

def is_bt_addr(addr: str) -> bool:
    pattern = r"[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}"
    return re.match(pattern, addr, re.IGNORECASE) is not None


def unpack_u16(data: bytearray) -> int:
    # Unpack big endian u16
    return struct.unpack(">H", data)[0]


def unpack_u32(data: bytearray) -> int:
    # Unpack big endian u32
    return struct.unpack(">I", data)[0]


def add_lecrc16(data: bytearray) -> bytearray:
    """ Create a copy of data with a little endian crc16 to the array"""
    # Add a crc to the buffer
    crc = modbus_crc16(data)
    copy = data[:]
    copy.append(crc & 0xFF)
    copy.append((crc >> 8) & 0xFF)
    return copy


def modbus_crc16(data: bytearray) -> int:
    """ Compute a crc16 """
    crc = 0xFFFF
    for it in data:
        crc ^= it
        for i in range(8):
            odd = crc & 1
            crc >>= 1
            if odd:
                crc ^= 0xA001
    return crc


def check_modbus_crc16(data: bytearray) -> bool:
    if len(data) < 2:
        return False
    exp_crc = modbus_crc16(data[:-2])
    actual_crc = data[-2] + (data[-1] << 8)
    return exp_crc == actual_crc
