import sys
import struct
import asyncio
import logging
import aiosqlite
import dataclasses
from typing import ClassVar, Optional
from time import time
from logging.handlers import RotatingFileHandler

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
BATTERY_MONITOR_REFRESH = bytearray([0xBB, 0x9A, 0xA9, 0x0C, 0xEE])

DB = None
BT_LOCK = asyncio.Lock()
# 54:14:A7:53:14:E9 BTG964
BATTERY_MONITOR_DEVICE = None
# C8:47:80:0D:2C:6A ChargePro
SOLAR_CHARGER_DEVICE = None


@dataclasses.dataclass
class State:
    timestamp: int = 0
    battery_voltage: float = 0
    battery_current: float = 0
    battery_is_charging: int = 0
    battery_is_temp_in_f: ClassVar[bool] = True
    battery_ah: float = 0
    battery_temp: float = 0
    battery_total_charge_energy: float = 0
    battery_total_discharge_energy: float = 0

    solar_panel_voltage: float = 0
    charger_voltage: float = 0
    charger_current: float = 0
    charger_temp: float = 0
    charger_total_energy: float = 0
    charger_status: int = 0
    room_temp: float = 0

    _instance: ClassVar["State"] = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def columns(self):
        return tuple(f.name for f in dataclasses.fields(self.__class__))

    def values(self):
        return dataclasses.astuple(self)

    def update_timestamp(self):
        self.timestamp = int(time())
        log.debug(f"State updated {self}")

    def insert_values_sql(self):
        return f"INSERT INTO solar VALUES {self.values()};"

    @classmethod
    def create_table_sql(cls):
        columns = []
        for i, f in enumerate(dataclasses.fields(cls)):
            column = f"{f.name}"
            if f.type is float:
                column += " REAL"
            elif f.type is int:
                column += " INTEGER"
            elif f.type is str:
                column += " TEXT"
            if i == 0:
                column += " PRIMARY KEY"
            column += " NOT NULL"
            columns.append(column)
        return f"CREATE TABLE IF NOT EXISTS solar({', '.join(columns)});"


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
    global BT_LOCK
    async with BT_LOCK:
        result = await BleakScanner.discover(return_adv=True)
    return web.Response(text=str(result))


@routes.get("/model/{address}/")
async def model(request):
    address = request.match_info["address"]
    print(f"Connecting to '{address}'")
    async with BT_LOCK:
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
                async with BT_LOCK:
                    result = await BleakScanner.discover(return_adv=True)
                for device, data in result.values():
                    log.info(f"  - {device} {data}")

                for device, data in result.values():
                    if BATTERY_MONITOR_DEVICE is None and (
                        #BATTERY_MONITOR_DATA_SERVICE_UUID in data.service_uuids
                        device.name == "BTG964"
                    ):
                        BATTERY_MONITOR_DEVICE = device
                        log.info(f"Found battery monitor: {device}")
                    elif SOLAR_CHARGER_DEVICE is None and (
                        # SOLAR_CHARGER_DATA_SERVICE_UUID in data.service_uuids
                        device.name == "ChargePro"
                    ):
                        SOLAR_CHARGER_DEVICE = device
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
    global BATTERY_MONITOR_DEVICE
    client: Optional[BleakClient] = None
    while True:
        try:
            if BATTERY_MONITOR_DEVICE is None:
                await asyncio.sleep(1)
                continue
            log.info(f"Connecting to battery monitor: {BATTERY_MONITOR_DEVICE}")
            client = BleakClient(BATTERY_MONITOR_DEVICE, timeout=30)
            async with BT_LOCK:
                await client.connect()

            model_number = await client.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
            log.info(f"Battery monitor model: {model_number}")

            read_buffer = bytearray()

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
            async with BT_LOCK:
                await client.start_notify(BATTERY_MONITOR_DATA_CHARACTERISTIC_UUID, on_battery_monitor_data)
                await client.write_gatt_char(BATTERY_MONITOR_CONF_CHARACTERISTIC_UUID, BATTERY_MONITOR_REFRESH)
            while True:
                await asyncio.sleep(60)
        except Exception as e:
            log.error("Error in monitor_battery:")
            log.exception(e)
            if client is not None:
                async with BT_LOCK:
                    await client.disconnect()
                client = None
            BATTERY_MONITOR_DEVICE = None
            await asyncio.sleep(1)


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
    global SOLAR_CHARGER_DEVICE
    client: Optional[BleakClient] = None
    while True:
        try:
            if SOLAR_CHARGER_DEVICE is None:
                await asyncio.sleep(1)
                continue
            log.info(f"Connecting to solar charger: {SOLAR_CHARGER_DEVICE}")
            client = BleakClient(SOLAR_CHARGER_DEVICE, timeout=30)
            async with BT_LOCK:
                await client.connect()
            model_number = await client.read_gatt_char(DEVICE_MODEL_CHARACTERISTIC_UUID)
            log.info(f"Solar charger model: {model_number}")
            last_sent = None
            def callback(sender: BleakGATTCharacteristic, data: bytearray):
                log.debug(f" solar charger data: {sender}: {data.hex()}")
                decode_solar_charger_data(data)
                last_sent = None
            async with BT_LOCK:
                await client.start_notify(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, callback)
            while True:
                now = time()
                if last_sent is None or (now - last_sent) > 5:
                    # If we get no response or a reply
                    last_sent = now
                    async with BT_LOCK:
                        await client.write_gatt_char(SOLAR_CHARGER_DATA_CHARACTERISTIC_UUID, SOLAR_CHARGER_HOME_DATA)
                await asyncio.sleep(1)
        except Exception as e:
            log.error("Error in monitor_charger:")
            log.exception(e)
            if client is not None:
                async with BT_LOCK:
                    await client.disconnect()
                client = None
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
            if DB and (SOLAR_CHARGER_DEVICE or BATTERY_MONITOR_DEVICE):
                if state.timestamp != last_timestamp:
                    last_timestamp = state.timestamp
                    cmd = state.insert_values_sql()
                    log.debug(f"SQL: {cmd}")
                    await DB.execute(cmd)
                    await DB.commit()
        except Exception as e:
            log.error("Error in snapshot_task:")
            log.exception(e)
        await asyncio.sleep(1)


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
            RotatingFileHandler("solarpi.log", maxBytes=5*1024*1000, backupCount=3),
            logging.StreamHandler(sys.stdout)
        ]
    )
    log.setLevel(logging.DEBUG)
    web.run_app(app, port=5000)

if __name__ == '__main__':
    main()



