import os
import sys
import struct
import asyncio
import logging
import aiosqlite
import dataclasses
import subprocess
from typing import ClassVar, Optional
from time import time
from logging.handlers import RotatingFileHandler

from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic, BleakError

from . import config
from .db import State

log = logging.getLogger("solarpi")

DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
DEVICE_MODEL_CHARACTERISTIC_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
BATTERY_MONITOR_DATA_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
BATTERY_MONITOR_DATA_CHARACTERISTIC_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
BATTERY_MONITOR_CONF_CHARACTERISTIC_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
BATTERY_MONITOR_DATA_DESCRIPTOR_UUID = "00002902-0000-1000-8000-00805f9b34fb"
SOLAR_CHARGER_DATA_SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
SOLAR_CHARGER_DATA_DESCRIPTOR_UUID = "00002902-0000-1000-8000-00805f9b34fb"
SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
SOLAR_CHARGER_HOME_DATA = bytearray([0x01, 0x03, 0x01, 0x01, 0x00, 0x13, 0x54, 0x3B])
BATTERY_MONITOR_REFRESH = bytearray([0xBB, 0x9A, 0xA9, 0x0C, 0xEE])

DB = None
BT_LOCK = asyncio.Lock()
ERROR_COUNT = 0
SOLAR_CHARGER_ERROR_COUNT = 0
# 54:14:A7:53:14:E9 BTG964
BATTERY_MONITOR: Optional[BleakClient] = None
# C8:47:80:0D:2C:6A ChargePro
SOLAR_CHARGER: Optional[BleakClient] = None


class BatteryMonitor:

    @staticmethod
    def is_cmd(c: int):
        return c >= 0xa0


    RECORDED_VOLTAGE = 0xa0
    RECORDED_CHARGE_CURRENT = 0xa1
    RECORDED_DISCHARGE_CURRENT = 0xa2
    RECORDED_DATA_START = 0xaa

    BATTERY_CAPACITY = 0xb0
    OVER_TEMP_PROTECTION = 0xb1
    VOLTAGE_ALIGN = 0xb2
    CURRENT_ALIGN = 0xb3
    TEMP_ENABLED = 0xb4
    LAN_STATUS = 0xb7
    LIVE_DATA_START = 0xbb
    VOLTAGE = 0xc0
    CURRENT = 0xc1
    ITEM2 = 0xc2
    ITEM3 = 0xc3
    LAN_ADDR = 0xc4
    OVER_VOLTAGE_PROTECTION_STATUS = 0xc5

    STATUS = 0xd0
    IS_CHARGING = 0xd1
    REMAINING_AH = 0xd2
    TOTAL_DISCHARGE_ENERGY = 0xd3
    TOTAL_CHARGE_ENERGY = 0xd4

    RECORD_PROGRESS_IN_MINS = 0xd5
    REMAINING_TIME_IN_MINS = 0xd5
    POWER = 0xd8
    TEMP_DATA = 0xd9 # in C

    CONFIG = 0xe0
    VERSION_INFO = 0xe2
    LOW_TEMP_PROTECTION_LEVEL = 0xe3
    DATA_END = 0xee

    IS_RECORDING = 0xf1
    RECORDED_DATA_START_DATE = 0xf2
    RECORDED_DATA_START_TIME = 0xf3
    RECORDED_DATA_INDEX = 0xf4
    PASSWORD = 0xf6
    IS_TEMP_IN_F = 0xf7


async def reset_bluetooth(timeout: int = 2):
    log.warning(f"Resetting bluetooth (timeout={timeout})")
    log.debug(bluetooth_power(False))
    await asyncio.sleep(timeout/2)
    global SOLAR_CHARGER
    global BATTERY_MONITOR
    SOLAR_CHARGER = None
    BATTERY_MONITOR = None
    log.debug(bluetooth_power(True))
    await asyncio.sleep(timeout/2)


def bluetooth_power(on: bool):
    cmd = "bluetoothctl power"
    if on:
        cmd += " on"
    else:
        cmd += " off"
    log.debug(cmd)
    return subprocess.check_output(cmd.split(" ")).decode()


async def scan_devices():
    """ Scan for a battery monitor and charger

    """
    global BATTERY_MONITOR
    global SOLAR_CHARGER
    global ERROR_COUNT
    scan_attempts = 0
    def on_detection_callback(device, data):
        global SOLAR_CHARGER
        global BATTERY_MONITOR
        if BATTERY_MONITOR is None and BATTERY_MONITOR_DATA_SERVICE_UUID in data.service_uuids:
            log.info(f"Found battery monitor: {device}")
            BATTERY_MONITOR = BleakClient(device, timeout=20)
        elif SOLAR_CHARGER is None and (
            SOLAR_CHARGER_DATA_SERVICE_UUID in data.service_uuids
            # Battery monitor has both
            and BATTERY_MONITOR_DATA_SERVICE_UUID not in data.service_uuids
        ):
            log.info(f"Found solar charger: {device}")
            SOLAR_CHARGER = BleakClient(device, timeout=20)

    scanner = BleakScanner(on_detection_callback)
    while True:
        try:
            if ERROR_COUNT >= 5:
                # It's possible for one device to get stuck in a error loop
                # where the device is found but for whatever reason it cannot connect or send data
                # If error count exceeds the limit do a full reset
                await reset_bluetooth(10)
                ERROR_COUNT = 0

            if BATTERY_MONITOR and SOLAR_CHARGER:
                continue # Both are connected ok. Nothing to do!

            log.info("Scanning devices...")
            async with BT_LOCK:
                async with scanner:
                    await asyncio.sleep(30)
                result = scanner.discovered_devices_and_advertisement_data
            for device, data in result.items():
                log.info(f" - {device} {data}")


            if BATTERY_MONITOR and SOLAR_CHARGER:
                scan_attempts = 0
                log.info("Both devices found")
                continue

            # If there is a lot of failed attempts try resetting bluetooth
            # as it seems to get jacked up and cannot recover any other way
            scan_attempts += 1
            log.error(f"Failed scan attempts {scan_attempts}")
            if scan_attempts >= 10:
                await reset_bluetooth(10)
                scan_attempts = 0

        except Exception as e:
            log.error("Error in scan_devices:")
            log.exception(e)
        finally:
            await asyncio.sleep(10)


def decode_battery_monitor_data(packet: bytearray):
    """ Decode the battery monitor data and update the global state
    bb324397d20542d347ee
    bb0530c1013886d840ee
    bb0550c1014410d866ee
    bb324395d20546d349ee
    bb324394d20550d358ee
    bb0540c1014148d803ee
    bb324392d20554d360ee
    bb0530c1013886d840ee
    """
    data = bytearray()
    state = State.instance()
    changed = False
    for c in packet[1:-1]:
        if BatteryMonitor.is_cmd(c) and data:
            #log.debug(f"Decode '{hex(c)}' data {data.hex()}")
            if c == BatteryMonitor.VOLTAGE:
                state.battery_voltage = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.CURRENT:
                state.battery_current = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.TOTAL_CHARGE_ENERGY:
                state.battery_total_charge_energy = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.TOTAL_DISCHARGE_ENERGY:
                state.battery_total_discharge_energy = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.REMAINING_AH:
                state.battery_ah = int(data.hex()) / 1000
                changed = True
            elif c == BatteryMonitor.IS_CHARGING:
                state.battery_is_charging = int(data.hex()) == 1
                changed = True
            elif c == BatteryMonitor.IS_TEMP_IN_F:
                state.battery_is_temp_in_f = int(data.hex()) == 1
                changed = True
            elif c == BatteryMonitor.TEMP_DATA:
                # Convert to C
                if state.battery_is_temp_in_f:
                    t = round((int(data.hex()) - 32 - 5) * 5.0 / 9.0, 1)
                else:
                    t = int(data.hex()) - 100
                state.room_temp = t
                changed = True
            data = bytearray()
        else:
            data.append(c)

    if changed:
        state.update_timestamp()


async def monitor_battery():
    global ERROR_COUNT
    global BATTERY_MONITOR
    while True:
        await asyncio.sleep(1)
        try:
            if BATTERY_MONITOR is None:
                continue

            read_buffer = bytearray()
            last_sent = None

            def on_battery_monitor_data(sender: BleakGATTCharacteristic, data: bytearray):
                log.debug(f" battery monitor data: {sender}: {data.hex()}")
                nonlocal read_buffer
                read_buffer += data
                while read_buffer:
                    start_live = read_buffer.find(BatteryMonitor.LIVE_DATA_START)
                    start_rec = read_buffer.find(BatteryMonitor.RECORDED_DATA_START)
                    if start_live >= 0 and start_rec >= 0:
                        start = min(start_live, start_rec)
                    elif start_live >= 0:
                        start = start_live
                    else:
                        start = start_rec
                    end = read_buffer.find(BatteryMonitor.DATA_END)
                    if (end >= 0 and end <= start):
                        read_buffer = read_buffer[end+1:] # Discard extra
                        break
                    elif (end < 0 or start < 0):
                        break # Need to read more
                    assert start >= 0 and end > start

                    packet = read_buffer[start:end]
                    read_buffer = read_buffer[end+1:]
                    decode_battery_monitor_data(packet)

                if len(read_buffer) > 512:
                    log.warning("battery monitor read buffer discarded")
                    read_buffer = bytearray()
                last_sent = None

            async with BT_LOCK:
                log.info("Connecting to battery monitor")
                await BATTERY_MONITOR.connect(timeout=30)
                await BATTERY_MONITOR.get_services()

                model_number = await BATTERY_MONITOR.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
                log.info(f"Battery monitor model: {model_number}")
                await BATTERY_MONITOR.start_notify(BATTERY_MONITOR_DATA_CHARACTERISTIC_UUID, on_battery_monitor_data)

            # Periodically poll to make sure it's not just sitting with no data coming in
            while BATTERY_MONITOR is not None:
                await asyncio.sleep(10)
                now = time()
                if last_sent is None or (now - last_sent) > 60:
                    last_sent = now
                    async with BT_LOCK:
                        await BATTERY_MONITOR.write_gatt_char(BATTERY_MONITOR_CONF_CHARACTERISTIC_UUID, BATTERY_MONITOR_REFRESH, response=False)


        except Exception as e:
            log.error("Error in monitor_battery:")
            log.exception(e)
            if BATTERY_MONITOR is not None and BATTERY_MONITOR.is_connected:
                async with BT_LOCK:
                    await BATTERY_MONITOR.disconnect()
                # BATTERY_MONITOR = None
            ERROR_COUNT += 1
            log.debug(f"  error count {ERROR_COUNT}")
            await asyncio.sleep(10)


def decode_solar_charger_data(packet):
    state = State.instance()
    if len(packet) == 43 and packet[0] == 0x01 and packet[1] == 0x03 and packet[2] == 0x26:
        # Home data
        state.charger_voltage = int(packet[5:7].hex(), base=16) / 10
        state.charger_current = int(packet[7:9].hex(), base=16) / 100
        state.charger_temp = packet[11]
        t = packet[12]
        state.battery_temp = t if t < 128 else (128-t)
        state.solar_panel_voltage = int(packet[19:21].hex(), base=16) / 10
        # today_peak_power = int(packet[21:23].hex(), base=16) # This can be calculated
        # today_charge_energy = int(packet[23:25].hex(), base=16) # These are wrong anyways
        state.charger_total_energy = int(packet[33:37].hex(), base=16)
        state.charger_status = packet[28]
        state.update_timestamp()
    else:
        pass


async def monitor_charger():
    global ERROR_COUNT
    global SOLAR_CHARGER
    while True:
        await asyncio.sleep(1)
        try:
            if SOLAR_CHARGER is None:
                continue

            last_sent = None
            def callback(sender: BleakGATTCharacteristic, data: bytearray):
                log.debug(f" solar charger data: {sender}: {data.hex()}")
                decode_solar_charger_data(data)
                last_sent = None

            async with BT_LOCK:
                #client = BleakClient(SOLAR_CHARGER_DEVICE, timeout=20)
                log.info("Connecting to solar charger")
                await SOLAR_CHARGER.connect(timeout=30)
                await SOLAR_CHARGER.get_services()
                model_number = await SOLAR_CHARGER.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
                log.info(f"Solar charger model: {model_number}")
                await SOLAR_CHARGER.start_notify(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, callback)

            # Periodically poll to make sure it's not just sitting with no data coming in
            while SOLAR_CHARGER is not None:
                await asyncio.sleep(1)
                now = time()
                if last_sent is None or (now - last_sent) > 5:
                    # If we get no response or a reply
                    last_sent = now
                    async with BT_LOCK:
                        await SOLAR_CHARGER.write_gatt_char(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, SOLAR_CHARGER_HOME_DATA, response=False)

        except Exception as e:
            log.error("Error in monitor_charger:")
            log.exception(e)
            if SOLAR_CHARGER is not None and SOLAR_CHARGER.is_connected:
                async with BT_LOCK:
                    await SOLAR_CHARGER.disconnect()
                # SOLAR_CHARGER = None
            ERROR_COUNT += 1
            log.debug(f"  error count {ERROR_COUNT}")
            await asyncio.sleep(30)


async def snapshot_task():
    global DB
    global SOLAR_CHARGER
    global BATTERY_MONITOR
    last_timestamp = 0
    state = State.instance()
    while True:
        await asyncio.sleep(1)
        try:
            if DB and (SOLAR_CHARGER or BATTERY_MONITOR):
                if state.timestamp != last_timestamp:
                    last_timestamp = state.timestamp
                    cmd = state.insert_values_sql()
                    log.debug(f"SQL: {cmd}")
                    await DB.execute(cmd)
                    await DB.commit()
        except Exception as e:
            log.error("Error in snapshot_task:")
            log.exception(e)



async def init_db():
    global DB
    log.info("Connecting to db...")
    DB = await aiosqlite.connect("solarpi.db")
    log.info("Creating table...")
    cmd = State.create_table_sql()
    log.debug(f"SQL: {cmd}")
    await DB.execute(cmd)
    await DB.commit()
    log.info("Db initalized!")


async def fini_db():
    global DB
    if DB is not None:
        await DB.close()
        DB = None


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler("solarpi-monitor.log", maxBytes=5*1024*1000, backupCount=3),
            logging.StreamHandler(sys.stdout)
        ]
    )
    log.setLevel(logging.DEBUG)
    try:
        config.load()
        await init_db()
        # await reset_bluetooth(5)
        await asyncio.gather(
            scan_devices(),
            monitor_battery(),
            monitor_charger(),
            snapshot_task()
        )
    finally:
        await fini_db()
        config.save()


if __name__ == '__main__':
    asyncio.run(main())




