import os
import sys
import struct
import asyncio
import logging
import json
import aiosqlite
import dataclasses
from datetime import datetime, date, time, timedelta
from typing import ClassVar, Optional
from logging.handlers import RotatingFileHandler

from aiohttp import web
from jinja2 import Environment, PackageLoader, select_autoescape

from .db import State

env = Environment(
    loader=PackageLoader("solarpi"),
    autoescape=select_autoescape()
)
log = logging.getLogger("solarpi")


routes = web.RouteTableDef()
routes.static('/static', os.path.join(os.path.dirname(__file__), 'static'))


async def load_energy_chart(d: date):
    dates = []
    last_state: Optional[State] = None
    solar_energy_output = []
    battery_discharge_energy = []
    battery_charge_energy = []
    inverter_energy = []
    start = datetime.combine(d, time(23, 59, 59))
    for i in range(8, -1, -1):
        d = start - timedelta(days=i)
        # Exclude empty readings
        async with DB.execute((
            "SELECT * FROM solar "
            "WHERE timestamp <= ? AND charger_total_energy > 0 AND battery_total_charge_energy > 0 AND battery_total_discharge_energy > 0 "
            "ORDER BY timestamp DESC LIMIT 1"
        ), (d.timestamp(),)) as cursor:
            state: Optional[State] = None
            async for row in cursor:
                state = State(*row)
                log.warning(f"Date {d} {state}")
                if last_state:
                    solar_energy_output.append(max(0, state.charger_total_energy - last_state.charger_total_energy))
                    battery_discharge_energy.append(max(0, state.battery_total_discharge_energy - last_state.battery_total_discharge_energy))
                    battery_charge_energy.append(max(0, state.battery_total_charge_energy - last_state.battery_total_charge_energy))
                    inverter_energy.append(solar_energy_output[-1]-battery_charge_energy[-1]+battery_discharge_energy[-1])
                    dates.append(d.date())
                last_state = state

    energy_chart_data = {
        "labels": [str(d) for d in dates],
        "datasets": [
            {
                "label": "Solar energy output (kWh)",
                "data": solar_energy_output,
                "borderWidth": 1,
            },
            {
                "label": "Battery discharge energy (kWh)",
                "data": battery_discharge_energy,
                "borderWidth": 1,
            },
            {
                "label": "Battery charge energy (kWh)",
                "data": battery_charge_energy,
                "borderWidth": 1,
            },
            {
                "label": "Inverter/Load energy (kWh)",
                "data": inverter_energy,
                "borderWidth": 1,
            },
        ]
    }
    log.warning(energy_chart_data)
    return {
        "type": 'bar',
        "data": energy_chart_data,
        "options": {
             "scales": {
                 "y": {
                     "beginAtZero": True,
                 }
            },
        }
    }

@routes.get("/api/sidebar/")
async def sidebar(request: web.Request):
    template = env.get_template("sidebar.html")
    async with DB.execute("SELECT * FROM solar ORDER BY timestamp DESC LIMIT 1") as cursor:
        async for row in cursor:
            state = State(*row)
    content = template.render(state=state or State())
    return web.Response(text=content, content_type="text/html")


@routes.get("/")
async def index(request: web.Request):
    template = env.get_template("index.html")

    d = datetime.now()
    try:
        if date_str := request.query.get("d", ''):
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
            d = datetime.combine(day, d.time())
    except Exception as e:
        log.warning(f"Invalid date query: {e}")

    p = None
    try:
        if peroid_str := request.query.get("p", ''):
            p = int(peroid_str)
            assert p in (1, 3, 6, 12, 24)
    except Exception as e:
        log.warning(f"Invalid time peroid: {e}")

    if p is None:
        start_timestamp = datetime.combine(d.date(), time(0, 0)).timestamp()
        end_timestamp = datetime.combine(d.date(), time(23, 59, 59)).timestamp()

    else:
        start_timestamp = (d - timedelta(hours=p)).timestamp()
        end_timestamp = d.timestamp()


    timestamps = []
    battery_voltage = []
    battery_current = []
    battery_power = []
    charger_current = []
    charger_voltage = []
    charger_power = []
    inverter_current = []
    solar_voltage = []
    solar_current = []
    inverter_power = []
    battery_soc = []
    charger_temp = []
    battery_temp = []
    room_temp = []
    voltages_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery Voltage (V)",
                "data": battery_voltage,
                "pointRadius": 0,
            },
            {
                "label": "Battery Current (A)",
                "data": battery_current,
                "pointRadius": 0,
            },
            {
                "label": "Solar Voltage (V)",
                "data": solar_voltage,
                "pointRadius": 0,
            },
            {
                "label": "Solar Current (A)",
                "data": solar_current,
                "pointRadius": 0,
            },
            {
                "label": "Charger Voltage (V)",
                "data": charger_voltage,
                "pointRadius": 0,
            },
            {
                "label": "Charger Current (A)",
                "data": charger_current,
                "pointRadius": 0,
            },
            {
                "label": "Inverter Current (A)",
                "data": inverter_current,
                "pointRadius": 0,
            },
        ]
    }

    power_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery Power (W)",
                "data": battery_power,
                "pointRadius": 0,
            },
            {
                "label": "Charger Power (W)",
                "data": charger_power,
                "pointRadius": 0,
            },
            {
                "label": "Inverter Power (W)",
                "data": inverter_power,
                "pointRadius": 0,
            }
        ]
    }

    soc_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery State of Charge (Ah)",
                "data": battery_soc,
                "pointRadius": 0,
            },
        ]
    }

    temp_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Charger Temp (°C)",
                "data": charger_temp,
                "pointRadius": 0,
            },
            {
                "label": "Battery Temp (°C)",
                "data": battery_temp,
                "pointRadius": 0,
            },
            {
                "label": "Room Temp (°C)",
                "data": room_temp,
                "pointRadius": 0,
            },
        ]
    }

    #data = []
    state: Optional[State] = None
    async with DB.execute("SELECT * FROM solar WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC", (start_timestamp, end_timestamp)) as cursor:
        async for row in cursor:
            state = State(*row)
            timestamps.append(state.timestamp*1000)
            battery_voltage.append(state.battery_voltage)
            if state.battery_is_charging:
                battery_current.append(state.battery_current)
            else:
                battery_current.append(-state.battery_current)

            battery_power.append(state.battery_power)
            battery_soc.append(state.battery_ah)
            charger_voltage.append(state.charger_voltage)
            charger_current.append(state.charger_current)
            charger_power.append(state.charger_power)
            solar_voltage.append(state.solar_panel_voltage)
            solar_current.append(state.solar_panel_current)
            inverter_current.append(state.inverter_current)
            inverter_power.append(state.inverter_power)

            charger_temp.append(state.charger_temp)
            battery_temp.append(state.battery_temp)
            room_temp.append(state.room_temp)



    power_chart = {
        "type": 'line',
        "data": power_chart_data,
        "options": {
            "responsive": True,
            "normalized": True,
            "animation": False,
            "spanGaps": True,
            "scales": {
                "x": {
                    "type": 'time',
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
            }
        }
    }

    voltages_chart = {
        "type": 'line',
        "data": voltages_chart_data,
        "options": {
            "responsive": True,
            "normalized": True,
            "animation": False,
            "spanGaps": True,
            "scales": {
                "x": {
                    "type": 'time',
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
            }
        }
    }

    soc_chart = {
        "type": 'line',
        "data": soc_chart_data,
        "options": {
            "responsive": True,
            "normalized": True,
            "animation": False,
            "spanGaps": True,
            "scales": {
                "x": {
                    "type": 'time',
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
            }
        }
    }

    temp_chart = {
        "type": 'line',
        "data": temp_chart_data,
        "options": {
            "responsive": True,
            "normalized": True,
            "animation": False,
            "spanGaps": True,
            "scales": {
                "x": {
                    "type": 'time',
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
            }
        }
    }

    energy_chart = await load_energy_chart(d.date())
    content = template.render(
        power_chart=json.dumps(power_chart),
        voltages_chart=json.dumps(voltages_chart),
        soc_chart=json.dumps(soc_chart),
        temp_chart=json.dumps(temp_chart),
        energy_chart=json.dumps(energy_chart),
        selected_peroid=p,
        selected_date=d.date(),
        state=state or State()
    )
    return web.Response(text=content, content_type="text/html")


async def init_db():
    global DB
    log.info("Connecting to db...")
    DB = await aiosqlite.connect("solarpi.db")

async def fini_db():
    global DB
    if DB is not None:
        await DB.close()
        DB = None


async def on_startup(app):
    await init_db()

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
            RotatingFileHandler("solarpi-web.log", maxBytes=5*1024*1000, backupCount=3),
            logging.StreamHandler(sys.stdout)
        ]
    )
    log.setLevel(logging.DEBUG)
    web.run_app(app, port=5000)

if __name__ == '__main__':
    main()



