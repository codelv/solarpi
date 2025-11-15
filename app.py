import sys
import struct
import asyncio
import logging
import aiosqlite
import dataclasses
from typing import ClassVar
from time import time

from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic
from aiohttp import web

log = logging.getLogger("solarpi")

routes = web.RouteTableDef()

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


DB = None
BATTERY_MONITOR_DEVICE = None
SOLAR_CHARGER_DEVICE = None


@dataclasses.dataclass
class State:
    timestamp: int = 0
    battery_voltage: float = 0
    battery_current: float = 0
    battery_ah: float = 0
    battery_temp: float = 0
    battery_total_charge_energy: float = 0
    battery_total_discharge_energy: float = 0

    solar_panel_voltage: float = 0
    charger_voltage: float = 0
    charger_current: float = 0
    charger_temp: float = 0
    charger_total_energy: float = 0

    _instance = ClassVar["State"]

    @classmethod
    def instance(cls):
        return cls._instance or cls()

    def columns(self):
        return tuple(f.name for f in dataclasses.fields(self.__class__))

    def values(self):
        return dataclasses.astuple(self)

    def update_timestamp(self):
        # Used to
        self.timestamp = int(time())

    def insert_values_sql(self):
        return f"INSERT INTO solar VALUES {self.values};"

    @classmethod
    def create_table_sql(cls):
        columns = []
        for i, f in enumerate(dataclasses.fields(cls)):
            column = f"{f.name}"
            if isinstance(f.type, float):
                column += " REAL"
            elif isinstance(f.type, int):
                column += " INTEGER"
            elif isinstance(f.type, str):
                column += " TEXT"
            elif isinstance(f.type, datetime):
                column += " INTEGER" # Store as timestamp
            if i == 0:
                column += " PRIMARY KEY"
            column += " NOT NULL"
            columns.append(column)
        return f"CREATE TABLE IF NOT EXISTS solar({', '.join(columns)});"


class BatteryMonitor:

    @classmethod
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


@routes.get("/")
async def handle(request):
    template = """
    <html>
        <head>SolarPI</head>
        <body>
            <a href="/scan/">Scan</a>
        </body>
    </html>
    """
    return web.Response(text=template, content_type="text/html")


@routes.get("/scan/")
async def scan(request):
    result = await BleakScanner.discover(return_adv=True)
    return web.Response(text=str(result))


@routes.get("/model/{address}/")
async def model(request):
    address = request.match_info["address"]
    print(f"Connecting to '{address}'")
    async with BleakClient(address) as client:
        model_number = await client.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
        return web.Response(text="Model Number: {0}".format("".join(map(chr, model_number))))


async def scan_devices():
    """ Scan for a battery monitor and charger

    """
    while True:
        try:
            global BATTERY_MONITOR_DEVICE
            global SOLAR_CHARGER_DEVICE
            if BATTERY_MONITOR_DEVICE is None or SOLAR_CHARGER_DEVICE is None:
                log.info("Scanning devices...")
                result = await BleakScanner.discover(return_adv=True)
                for device, data in result.values():
                    log.info(f"  - {device} {data}")

                for device, data in result.values():
                    if BATTERY_MONITOR_DEVICE is None and (
                        BATTERY_MONITOR_DATA_SERVICE_UUID in data.service_uuids
                        or device.name == "BTG964"
                    ):
                        BATTERY_MONITOR_DEVICE = device
                        log.info(f"Found battery monitor: {device}")
                    elif SOLAR_CHARGER_DEVICE is None and SOLAR_CHARGER_DATA_SERVICE_UUID in data.service_uuids:
                        SOLAR_CHARGER_DEVICE = device.address
                        log.info(f"Found solar charger: {device}")
        except Exception as e:
            log.error("Error in scan_devices:")
            log.exception(e)
        await asyncio.sleep(10)


def decode_battery_monitor_data(packet: bytearray):
    """ Decode the battery monitor data and update the global state """
    data = bytearray()
    state = State.instance()
    changed = False
    for c in packet[1:-1]:
        if BatteryMonitor.is_cmd(c) and data:
            if c == BatteryMonitor.VOLTAGE:
                state.battery_voltage = struct.unpack("<h", data) / 100
                changed = True
            elif c == BatteryMonitor.CURRENT:
                state.battery_current = struct.unpack("<h", data) / 100
                changed = True
            elif c == BatteryMonitor.TOTAL_CHARGE_ENERGY:
                state.battery_total_charge_energy = struct.unpack("<h", data) / 100
                changed = True
            elif BatteryMonitor.TOTAL_DISCHARGE_ENERGY:
                state.battery_total_discharge_energy = struct.unpack("<h", data) / 100
                changed = True
            data  = bytearray()
        else:
            data  += c

    if changed:
        state.update_timestamp()


async def monitor_battery():
    global BATTERY_MONITOR_DEVICE
    while True:
        try:
            if BATTERY_MONITOR_DEVICE is None:
                await asyncio.sleep(1)
                continue
            log.info(f"Connecting to battery monitor: {BATTERY_MONITOR_DEVICE}")
            async with BleakClient(BATTERY_MONITOR_DEVICE, timeout=30) as client:
                model_number = await client.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
                log.info(f"Battery monitor model: {model_number}")

                read_buffer = bytearray()

                def on_battery_monitor_data(sender: BleakGATTCharacteristic, data: bytearray):
                    log.debug(f" battery monitor data: {sender}: {data}")
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
                            read_buffer = read_buffer[end:] # Discard extra
                            break
                        elif (end < 0 and start < 0):
                            break # Need to read more
                        assert start >= 0 and end > start

                        packet = read_buffer[start:end]
                        read_buffer = read_buffer[end:]
                        decode_battery_monitor_data(packet)

                    if len(read_buffer) > 4095:
                        log.warning("battery monitor read buffer discarded")
                        read_buffer = bytearray()

                await client.start_notify(BATTERY_MONITOR_DATA_CHARACTERISTIC_UUID, on_battery_monitor_data)
                while True:
                    await asyncio.sleep(1000)
        except Exception as e:
            log.error("Error in monitor_battery:")
            log.exception(e)
            BATTERY_MONITOR_DEVICE = None
            await asyncio.sleep(1)


async def monitor_charger():
    global SOLAR_CHARGER_DEVICE
    while True:
        try:
            if SOLAR_CHARGER_DEVICE is None:
                await asyncio.sleep(1)
                continue
            log.info(f"Connecting to solar charger: {SOLAR_CHARGER_DEVICE}")
            async with BleakClient(SOLAR_CHARGER_DEVICE, timeout=30) as client:
                model_number = await client.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
                log.info(f"Solar charger model: {model_number}")
                pending_actions = []

                def callback(sender: BleakGATTCharacteristic, data: bytearray):
                    log.debug(f" solar charger data: {sender}: {data}")
                    pending_actions.append(SOLAR_CHARGER_HOME_DATA)
                await client.start_notify(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, callback)

                while True:
                    if pending_actions:
                        data = pending_actions.pop()
                        await client.write_gatt_char(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, data)
                    await asyncio.sleep(1)


        except Exception as e:
            log.error("Error in monitor_charger:")
            log.exception(e)
            SOLAR_CHARGER_DEVICE = None # Force re-scan
            await asyncio.sleep(1)


async def snapshot_task():
    global DB
    global SOLAR_CHARGER_DEVICE
    global BATTERY_MONITOR_DEVICE
    last_timestamp = 0
    state = State.instance()
    while True:
        try:
            if DB and SOLAR_CHARGER_DEVICE and BATTERY_MONITOR_DEVICE:
                if state.timestamp != last_timestamp:
                    last_timestamp = state.timestamp
                    await DB.execute(state.insert_values_sql())
        except Exception as e:
            log.error("Error in snapshot_task:")
            log.exception(e)
        await asyncio.sleep(1)


async def init_db():
    global DB
    DB = await aiosqlite.connect("solarpi.db")
    await DB.execute(State.create_table_sql())
    await DB.commit()

async def fini_db():
    global DB
    if DB is not None:
        await DB.close()
        DB = None


async def on_startup(app):
    await init_db()
    asyncio.create_task(scan_devices())
    asyncio.create_task(monitor_battery())
    asyncio.create_task(monitor_charger())
    asyncio.create_task(snapshot_task())


async def on_cleanup(app):
    await fini_db()


app = web.Application()
app.add_routes(routes)
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("solarpi.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    log.setLevel(logging.DEBUG)
    web.run_app(app, port=5000)

if __name__ == '__main__':
    main()



