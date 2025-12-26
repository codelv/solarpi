import os
import asyncio
import dataclasses
import logging
import subprocess
import sys
from time import time
from typing import Optional

import aiosqlite
from bleak import BleakClient, BleakError, BleakGATTCharacteristic, BleakScanner

from . import config
from .db import State
from .utils import is_bt_addr, add_lecrc16, check_modbus_crc16, unpack_u16, unpack_u32

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

#ITNCG3_UUID =

ITNCG3_DATA_SERVICE_UUID = "00002b00-0000-1000-8000-00805f9b34fb"
ITNCG3_DATA_NOTIFY_UUID = "00002b10-0000-1000-8000-00805f9b34fb"
ITNCG3_DATA_WRITE_UUID = "00002b11-0000-1000-8000-00805f9b34fb"

DB = None
BT_LOCK = asyncio.Lock()
ERROR_COUNT = 0
SOLAR_CHARGER_ERROR_COUNT = 0
BATTERY_MONITOR: Optional[BleakClient] = None
SOLAR_CHARGER: Optional[BleakClient] = None
SOLAR_CHARGER_TYPE: str = "helios"

class BatteryMonitor:

    @staticmethod
    def is_cmd(c: int):
        return c >= 0xA0

    RECORDED_VOLTAGE = 0xA0
    RECORDED_CHARGE_CURRENT = 0xA1
    RECORDED_DISCHARGE_CURRENT = 0xA2
    RECORDED_DATA_START = 0xAA

    BATTERY_CAPACITY = 0xB0
    OVER_TEMP_PROTECTION = 0xB1
    VOLTAGE_ALIGN = 0xB2
    CURRENT_ALIGN = 0xB3
    TEMP_ENABLED = 0xB4
    LAN_STATUS = 0xB7
    LIVE_DATA_START = 0xBB
    VOLTAGE = 0xC0
    CURRENT = 0xC1
    ITEM2 = 0xC2
    ITEM3 = 0xC3
    LAN_ADDR = 0xC4
    OVER_VOLTAGE_PROTECTION_STATUS = 0xC5

    STATUS = 0xD0
    IS_CHARGING = 0xD1
    REMAINING_AH = 0xD2
    TOTAL_DISCHARGE_ENERGY = 0xD3
    TOTAL_CHARGE_ENERGY = 0xD4

    RECORD_PROGRESS_IN_MINS = 0xD5
    REMAINING_TIME_IN_MINS = 0xD5
    POWER = 0xD8
    TEMP_DATA = 0xD9  # in C

    CONFIG = 0xE0
    VERSION_INFO = 0xE2
    LOW_TEMP_PROTECTION_LEVEL = 0xE3
    DATA_END = 0xEE

    IS_RECORDING = 0xF1
    RECORDED_DATA_START_DATE = 0xF2
    RECORDED_DATA_START_TIME = 0xF3
    RECORDED_DATA_INDEX = 0xF4
    PASSWORD = 0xF6
    IS_TEMP_IN_F = 0xF7



@dataclasses.dataclass
class SolarCharger:
    client: BleakClient

    async def after_connect(self):
        """ Invoked after bluetooth connects. """
        raise NotImplementedError()

    async def refresh_data(self):
        """ Invoked in a loop to refresh data. """
        raise NotImplementedError()


class HeliosSolarCharger(SolarCharger):
    """ Reads data from the Helios M4860N and M48100F """
    last_sent: Optional[time] = None

    async def after_connect(self):
        model_number = await self.client.read_gatt_char(
            DEVICE_MODEL_CHARACTERISTIC_UUID
        )
        log.info(f"Helios charger model: {model_number}")
        await self.client.start_notify(
            SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, self.on_data
        )

    async def refresh_data(self):
        await asyncio.sleep(2)
        now = time()
        if self.last_sent is None or (now - self.last_sent) > 5:
            # If we get no response or a reply
            self.last_sent = now
            async with BT_LOCK:
                await self.client.write_gatt_char(
                    SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID,
                    SOLAR_CHARGER_HOME_DATA,
                    response=False,
                )

    def on_data(self, sender: BleakGATTCharacteristic, data: bytearray):
        log.debug(f"Helios solar charger data: {data.hex()}")
        state = State.instance()
        if (
            len(data) == 43
            and data[0] == 0x01
            and data[1] == 0x03
            and data[2] == 0x26
        ):
            # Home data
            state.charger_voltage = int(data[5:7].hex(), base=16) / 10
            state.charger_current = int(data[7:9].hex(), base=16) / 100
            state.charger_temp = data[11]
            t = data[12]
            state.battery_temp = t if t < 128 else (128 - t)
            state.solar_panel_voltage = int(data[19:21].hex(), base=16) / 10
            if pv := state.solar_panel_voltage:
                # TODO: Can it actually read the PV input current
                eff = 0.95
                state.solar_panel_current = round(eff*state.charger_voltage/pv*state.charger_current, 2)
            else:
                state.solar_panel_current = 0
            # today_peak_power = int(packet[21:23].hex(), base=16) # This can be calculated
            # today_charge_energy = int(packet[23:25].hex(), base=16) # These are wrong anyways
            state.charger_total_energy = int(data[33:37].hex(), base=16)
            state.charger_status = data[28]
            state.update_timestamp()
        else:
            pass
        self.last_sent = None # Clear to reset


class ITNCG3SolarCharger(SolarCharger):
    """ Reads data from the EPEVER IT-NC-G3 models """
    last_cmd: Optional[bytearray] = None
    pending_read: Optional[asyncio.Queue] = None

    # CMD Format is devId (u8), register, len (u8), crc (u16)
    READ_DEV_ID = add_lecrc16(bytearray([0xf8, 0x45, 0x00, 0x01, 0x01, 0xf8]))
    #READ_INFO = add_lecrc16(bytearray([0x01, 0xe3, ]))
    READ_CHARGER_STATUS = add_lecrc16(bytearray([0x01, 0x04, 0x31, 0x17, 0x00, 0x04]))
    READ_BATTERY_STATUS = add_lecrc16(bytearray([0x01, 0x04, 0x31, 0x10, 0x00, 0x05]))
    READ_PV1 = add_lecrc16(bytearray([0x01, 0x04, 0x31, 0x00, 0x00, 0x04]))
    READ_PV2 = add_lecrc16(bytearray([0x01, 0x04, 0x31, 0x08, 0x00, 0x04]))
    READ_PV_BUS = add_lecrc16(bytearray([0x01, 0x04, 0x31, 0x1e, 0x00, 0x04]))
    READ_ACCUMULATED_POWER = add_lecrc16(bytearray([0x01, 0x04, 0x33, 0x01, 0x00, 0x12]))
    #? = add_lecrc16(bytearray([0x01, 0x04, 0x32, 0x00, 0x00, 0x04]))
    #? = add_lecrc16(bytearray([0x01, 0x04, 0x32, 0x05, 0x00, 0x01]))
    #? = add_lecrc16(bytearray([0x01, 0x04, 0x32, 0x05, 0x00, 0x01]))
    #? = add_lecrc16(bytearray([0x01, 0x04, 0x34, 0x00, 0x00, 0x04]))
    #? = add_lecrc16(bytearray([0x01, 0x04, 0x34, 0x05, 0x00, 0x05]))

    def read_reg_msg(self, dev_id: int, reg: int, address: int, size: int) -> bytearray:
        """ Format is struct {
            dev_id: u8,
            register: u8,
            address: u16, // Big endian
            data_type: u16, // Big endian
            crc: u16, // Little endian
        }
        """
        assert dev_id >=0 and dev_id <= 0xff
        assert reg >=0 and reg <= 0xff
        assert address >=0 and address <= 0xffff
        assert size >=0 and size <= 0xffff
        data = bytearray([
            dev_id,
            reg,
            address >> 8 & 0xff,
            address & 0xff,
            size >> 8 & 0xff,
            size & 0xff,
        ])
        return add_lecrc16(data)

    async def after_connect(self):
        await self.client.start_notify(ITNCG3_DATA_NOTIFY_UUID, self.on_data)

    async def refresh_data(self):
        await asyncio.sleep(1)
        state = State.instance()
        if r := await self.query_charger_status():
            log.info(f"Charger: {r[0]}A {r[1]}°C Ambient {r[2]}°C")
            state.charge_current = r[0]
            state.charger_temp = r[1]
        if r := await self.query_battery_status():
            state.charger_voltage = r
            log.info(f"Battery: {r}V")
        if r := await self.query_pv_bus():
            state.solar_panel_voltage = r[0]
            state.solar_panel_current = r[1]
        if r := await self.query_pv1():
            log.info(f"PV1: {r[0]}V {r[1]}A {r[2]}W {r[3]}kWh")
        if r := await self.query_pv2():
            log.info(f"PV2: {r[0]}V {r[1]}A {r[2]}W {r[3]}kWh")
        if r := await self.query_accumulated_power():
            state.charger_total_energy = r[3]
            log.info(f"Generation Today: {r[0]}kWh Total: {r[3]}kWh")
        state.update_timestamp()

    async def query_charger_status(self) -> Optional[tuple[float, float, float]]:
        """ Request and decode charger current and temp readings """
        resp = await self.send(self.READ_CHARGER_STATUS)
        # 01, 04, 08, 006d, 09c4, 0002, 0832, f290
        if len(resp) != 13 or not check_modbus_crc16(resp):
            return None
        charge_current = unpack_u16(resp[3:5])/100
        controller_temp = unpack_u16(resp[5:7])/100
        # TODO: what are 7 and 8
        ambient_temp = unpack_u16(resp[9:11])/100
        return (charge_current, controller_temp, ambient_temp)

    async def query_battery_status(self) -> Optional[float]:
        """ Request and decode temp readings """
        resp = await self.send(self.READ_BATTERY_STATUS)
        # 01, 04, 0a, 05a5, 0000, 0000, 0000, 05a5, c511
        if len(resp) != 15 or not check_modbus_crc16(resp):
            return None
        bat_voltage = unpack_u16(resp[11:13])/100
        return bat_voltage

    async def query_pv1(self) -> Optional[tuple[float, float, float, float]]:
        """ Request and decode temp readings """
        resp = await self.send(self.READ_PV1)
        # 01, 04, 08, 0d04, 0000, 0000, 0000, a054
        if len(resp) != 13 or not check_modbus_crc16(resp):
            return None
        voltage = unpack_u16(resp[3:5])/100
        current = unpack_u16(resp[5:7])/100
        watts = unpack_u16(resp[7:9])/100
        kwh = unpack_u16(resp[9:11])/100
        return (voltage, current, watts, kwh)

    async def query_pv2(self) -> Optional[tuple[float, float, float, float]]:
        """ Request and decode temp readings """
        resp = await self.send(self.READ_PV2)
        # 01,04,08,0e2d,0043,0981,0000,4ef8
        if len(resp) != 13 or not check_modbus_crc16(resp):
            return None
        voltage = unpack_u16(resp[3:5])/100
        current =  unpack_u16(resp[5:7])/100
        watts = unpack_u16(resp[7:9])/100
        kwh = unpack_u16(resp[9:11])/100
        return (voltage, current, watts, kwh)

    async def query_pv_bus(self) -> Optional[tuple[float, float, float, float]]:
        """ Request and decode temp readings """
        resp = await self.send(self.READ_PV_BUS)
        # 01,04,08,0e2d,0043,0981,0000,4ef8
        if len(resp) != 13 or not check_modbus_crc16(resp):
            return None
        voltage = unpack_u16(resp[3:5])/100
        current = unpack_u16(resp[5:7])/100
        watts = unpack_u16(resp[7:9])/100
        kwh = unpack_u16(resp[9:11])/100
        return (voltage, current, watts, kwh)

    async def query_accumulated_power(self) -> Optional[tuple[float, float, float, float]]:
        """ Request stats on total power genration. """
        resp = await self.send(self.READ_ACCUMULATED_POWER)
        if len(resp) != 41 or not check_modbus_crc16(resp):
            return None
        # TODO: Unpack load output
        daily_generation = unpack_u32(resp[21:25])/100
        monthly_generation = unpack_u32(resp[25:29])/100
        annual_generation = unpack_u32(resp[29:33])/100
        total_generation = unpack_u32(resp[33:37])/100
        return (daily_generation, monthly_generation, annual_generation, total_generation)

    async def send_noreply(self, data: bytearray):
        self.last_cmd = data
        log.debug(f"ITNCG3 Write: {data.hex()}")
        await self.client.write_gatt_char(ITNCG3_DATA_WRITE_UUID, data, response=False)

    async def cancel_read(self):
        if queue := self.pending_read:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def send(self, data: bytearray) -> Optional[bytearray]:
        await self.cancel_read()
        queue = self.pending_read = asyncio.Queue(maxsize=1)
        await self.send_noreply(data)
        result = await queue.get()
        if self.pending_read is queue:
            self.pending_read = None
        return result

    def on_data(self, sender: BleakGATTCharacteristic, data: bytearray):
        log.debug(f"ITNCG3 Read: {data.hex()}")
        if queue := self.pending_read:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass


async def reset_bluetooth(timeout: int = 2):
    log.warning(f"Resetting bluetooth (timeout={timeout})")
    global SOLAR_CHARGER
    global BATTERY_MONITOR
    SOLAR_CHARGER = None
    BATTERY_MONITOR = None
    log.debug(bluetooth_power(False))
    await asyncio.sleep(timeout / 2)
    log.debug(bluetooth_power(True))
    await asyncio.sleep(timeout / 2)


def bluetooth_power(on: bool):
    cmd = "bluetoothctl power"
    if on:
        cmd += " on"
    else:
        cmd += " off"
    log.debug(cmd)
    return subprocess.check_output(cmd.split(" ")).decode()


def bluetooth_trust(addr: str):
    if not is_bt_addr(addr):
        raise ValueError("Must be a bluetooth address")
    cmd = f"bluetoothctl trust {addr}"
    log.debug(cmd)
    parts = cmd.split(" ")
    assert len(parts) == 3
    return subprocess.check_output(parts).decode()


def bluetooth_disconnect(addr: str):
    if not is_bt_addr(addr):
        raise ValueError("Must be a bluetooth address")
    cmd = f"bluetoothctl disconnect {addr}"
    log.debug(cmd)
    parts = cmd.split(" ")
    assert len(parts) == 3
    return subprocess.check_output(parts).decode()


def disconnected_callback(client: BleakClient):
    log.debug(f"{client} disconnected")


async def scan_devices():
    """Scan for a battery monitor and charger"""
    global ERROR_COUNT
    scan_attempts = 0

    global SOLAR_CHARGER
    global SOLAR_CHARGER_TYPE
    global BATTERY_MONITOR

    for addr in (config.CONFIG.battery_monitor_addr, config.CONFIG.solar_charger_addr):
        if addr:
            try:
                log.debug(bluetooth_disconnect(addr))
            except Exception as e:
                log.warning(e)

    while True:
        try:
            if ERROR_COUNT >= 5:
                # It's possible for one device to get stuck in a error loop
                # where the device is found but for whatever reason it cannot connect or send data
                # If error count exceeds the limit do a full reset
                await reset_bluetooth(10)
                ERROR_COUNT = 0

            if BATTERY_MONITOR and SOLAR_CHARGER:
                continue  # Both are connected ok. Nothing to do!

            if "SOLARPI_NOBM" in os.environ and SOLAR_CHARGER:
                continue # Battery monitor disabled
            if "SOLARPI_NOSCC" in os.environ and BATTERY_MONITOR:
                continue # Solar charger disabled

            # Reload config in case addresses changed
            config.load()

            log.info("Scanning devices...")
            async with BT_LOCK:
                async with BleakScanner() as scanner:
                    await asyncio.sleep(10)
                    result = scanner.discovered_devices_and_advertisement_data
                    for device, data in result.values():
                        log.info(f" - {device}")
                        log.info(f"      {data}")
                    log.info("Scan complete!")
                    for device, data in result.values():
                        if BATTERY_MONITOR is None and (
                            (device.address == config.CONFIG.battery_monitor_addr)
                            or BATTERY_MONITOR_DATA_SERVICE_UUID in data.service_uuids
                        ):
                            log.info(f"Found battery monitor: {device}")
                            BATTERY_MONITOR = BleakClient(
                                device, disconnected_callback, timeout=30
                            )
                        elif SOLAR_CHARGER is None and (
                            (device.address == config.CONFIG.solar_charger_addr)
                            or (
                                SOLAR_CHARGER_DATA_SERVICE_UUID in data.service_uuids
                                # Battery monitor has both
                                and BATTERY_MONITOR_DATA_SERVICE_UUID
                                not in data.service_uuids
                            )
                            or ITNCG3_DATA_SERVICE_UUID in data.service_uuids
                        ):
                            log.info(f"Found solar charger: {device}")
                            if charger_type := config.CONFIG.solar_charger_type:
                                SOLAR_CHARGER_TYPE = charger_type
                            elif ITNCG3_DATA_SERVICE_UUID in data.service_uuids:
                                SOLAR_CHARGER_TYPE = "epever"
                            else:
                                SOLAR_CHARGER_TYPE = "helios"
                            SOLAR_CHARGER = BleakClient(
                                device, disconnected_callback, timeout=10
                            )
                    await asyncio.sleep(1)

            if BATTERY_MONITOR and SOLAR_CHARGER:
                scan_attempts = 0
                log.info("Both devices found")
                continue
            if not BATTERY_MONITOR:
                log.warning("Battery monitor not found")
            if not SOLAR_CHARGER:
                log.warning("Solar charger not found")

            # If there is a lot of failed attempts try resetting bluetooth
            # as it seems to get jacked up and cannot recover any other way
            if "SOLARPI_NORESET" in os.environ:
                continue
            scan_attempts += 1
            log.warning(f"Failed scan attempts {scan_attempts}")
            if scan_attempts >= 10:
                await reset_bluetooth(10)
                scan_attempts = 0

        except Exception as e:
            log.error("Error in scan_devices:")
            log.exception(e)
        finally:
            await asyncio.sleep(10)


def decode_battery_monitor_data(packet: bytearray):
    """Decode the battery monitor data and update the global state
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
            # log.debug(f"Decode '{hex(c)}' data {data.hex()}")
            if c == BatteryMonitor.VOLTAGE:
                state.battery_voltage = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.CURRENT:
                state.battery_current = int(data.hex()) / 100
                changed = True
            elif c == BatteryMonitor.TOTAL_CHARGE_ENERGY:
                old = state.battery_total_charge_energy
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
    while True:
        await asyncio.sleep(1)
        try:
            if BATTERY_MONITOR is None:
                continue
            read_buffer = bytearray()
            last_sent = None

            def on_battery_monitor_data(
                sender: BleakGATTCharacteristic, data: bytearray
            ):
                log.debug(f" battery monitor data: {data.hex()}")
                nonlocal last_sent
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
                    if end >= 0 and end <= start:
                        read_buffer = read_buffer[end + 1 :]  # Discard extra
                        break
                    elif end < 0 or start < 0:
                        break  # Need to read more
                    assert start >= 0 and end > start

                    packet = read_buffer[start:end]
                    read_buffer = read_buffer[end + 1 :]
                    decode_battery_monitor_data(packet)

                if len(read_buffer) > 512:
                    log.warning("battery monitor read buffer discarded")
                    read_buffer = bytearray()
                last_sent = time()  # Avoid doing a request again

            async with BT_LOCK:
                log.info("Connecting to battery monitor")
                await BATTERY_MONITOR.connect(pair=True, timeout=30)
                log.info("Battery monitor connected!")
                await asyncio.sleep(1)
                model_number = await BATTERY_MONITOR.read_gatt_char(
                    DEVICE_MODEL_CHARACTERISTIC_UUID
                )
                log.info(f"Battery monitor model: {model_number}")
                await BATTERY_MONITOR.start_notify(
                    BATTERY_MONITOR_DATA_CHARACTERISTIC_UUID, on_battery_monitor_data
                )
                await asyncio.sleep(0.5)
                await BATTERY_MONITOR.write_gatt_char(
                    BATTERY_MONITOR_CONF_CHARACTERISTIC_UUID,
                    BATTERY_MONITOR_REFRESH,
                )


            # Periodically poll to make sure it's not just sitting with no data coming in
            # DO NOT SEND immeidately or it screws up the connection
            last_sent = time()
            error_count = 0
            while BATTERY_MONITOR.is_connected:
                await asyncio.sleep(10)
                now = time()
                if last_sent is None or (now - last_sent) > 60:
                    try:
                        async with BT_LOCK:
                            await BATTERY_MONITOR.write_gatt_char(
                                BATTERY_MONITOR_CONF_CHARACTERISTIC_UUID,
                                BATTERY_MONITOR_REFRESH,
                            )
                        last_sent = now
                        error_count = 0
                    except BleakError as e:
                        log.warning(
                            f"Bleak error trying to refresh battery monitor data: {e}"
                        )
                        error_count += 1
                        last_sent = None
                        if error_count > 9:
                            raise

        except Exception as e:
            log.error("Error in monitor_battery:")
            log.exception(e)
            if BATTERY_MONITOR is not None and BATTERY_MONITOR.is_connected:
                await BATTERY_MONITOR.disconnect()
                # BATTERY_MONITOR = None
            ERROR_COUNT += 1
            log.debug(f"  error count {ERROR_COUNT}")
            await asyncio.sleep(5)



SOLAR_CHARGER_TYPES = {
    "helios": HeliosSolarCharger,
    "epever": ITNCG3SolarCharger,
}

async def monitor_charger():
    global ERROR_COUNT
    while True:
        await asyncio.sleep(1)
        try:
            if SOLAR_CHARGER is None:
                continue

            # Create the handler based on the charger type detected
            Charger = SOLAR_CHARGER_TYPES[SOLAR_CHARGER_TYPE]
            charger = Charger(SOLAR_CHARGER)

            async with BT_LOCK:
                log.info("Connecting to solar charger")
                await SOLAR_CHARGER.connect(timeout=10)
                log.info("Solar charger connected!")
                await charger.after_connect()

            # Periodically poll to make sure it's not just sitting with no data coming in
            while SOLAR_CHARGER.is_connected:
                await charger.refresh_data()
        except Exception as e:
            log.error("Error in monitor_charger:")
            log.exception(e)
            if SOLAR_CHARGER is not None and SOLAR_CHARGER.is_connected:
                async with BT_LOCK:
                    await SOLAR_CHARGER.disconnect()
                # SOLAR_CHARGER = None
            ERROR_COUNT += 1
            log.debug(f"  error count {ERROR_COUNT}")
            await asyncio.sleep(5)


async def snapshot_task():
    state = State.instance()
    last_timestamp = state.timestamp
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
    if "SOLARPI_NODB" in os.environ:
        log.warning("Skipping db setup due to SOLARPI_NODB env var")
        return
    global DB
    log.info("Connecting to db...")
    DB = await aiosqlite.connect("solarpi.db")
    log.info("Creating table...")
    cmd = State.create_table_sql()
    log.debug(f"SQL: {cmd}")
    await DB.execute(cmd)
    await DB.commit()
    log.info("Db initalized!")

    # Load previous state if available
    await State.load(DB)


async def fini_db():
    global DB
    if DB is not None:
        await DB.close()
        DB = None


async def cleanup_bt():
    if BATTERY_MONITOR is not None and BATTERY_MONITOR.is_connected:
        await BATTERY_MONITOR.disconnect()
    if SOLAR_CHARGER is not None and SOLAR_CHARGER.is_connected:
        await SOLAR_CHARGER.disconnect()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    log.setLevel(logging.DEBUG)
    try:
        config.load()
        bluetooth_power(True)
        await init_db()
        # await reset_bluetooth(5)
        await asyncio.gather(
            scan_devices(), monitor_battery(), monitor_charger(), snapshot_task()
        )
    finally:
        await fini_db()
        await cleanup_bt()


if __name__ == "__main__":
    asyncio.run(main())
