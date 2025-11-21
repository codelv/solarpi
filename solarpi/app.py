import os
import sys
import struct
import asyncio
import logging
import json
import aiosqlite
import dataclasses
from datetime import datetime, date, time, timedelta
from typing import Any, ClassVar, Optional
from logging.handlers import RotatingFileHandler

from aiohttp import web
from jinja2 import Environment, PackageLoader, select_autoescape

from . import config
from .db import State

env = Environment(
    loader=PackageLoader("solarpi"),
    autoescape=select_autoescape()
)
log = logging.getLogger("solarpi")


routes = web.RouteTableDef()
routes.static('/static', os.path.join(os.path.dirname(__file__), 'static'))


ChartData = dict[str, Any]
ChartDef = dict[str, Any]

async def load_energy_chart(d: date) -> ChartDef:
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
                if last_state:
                    # These values should never go down, if they do assume that device was reset and cleared the reading
                    if state.charger_total_energy < last_state.charger_total_energy:
                        solar_energy_output.append(state.charger_total_energy)
                    else:
                        solar_energy_output.append(state.charger_total_energy - last_state.charger_total_energy)
                    if state.battery_total_discharge_energy < last_state.battery_total_discharge_energy:
                        battery_discharge_energy.append(state.battery_total_discharge_energy)
                    else:
                        battery_discharge_energy.append(state.battery_total_discharge_energy - last_state.battery_total_discharge_energy)
                    if state.battery_total_charge_energy < last_state.battery_total_charge_energy:
                        battery_charge_energy.append(state.battery_total_charge_energy)
                    else:
                        battery_charge_energy.append(state.battery_total_charge_energy - last_state.battery_total_charge_energy)
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


async def load_time_based_charts(
    start_timestamp: int,
    end_timestamp: Optional[int] = None
) -> tuple[Optional[State], dict[str, ChartData]]:
    if end_timestamp is None:
        end_timestamp = int(datetime.now().timestamp())

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
            },
            {
                "label": "Charger Power (W)",
                "data": charger_power,
            },
            {
                "label": "Inverter Power (W)",
                "data": inverter_power,
            }
        ]
    }

    soc_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery State of Charge (Ah)",
                "data": battery_soc,
            },
        ]
    }

    temp_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Charger Temp (°C)",
                "data": charger_temp,
            },
            {
                "label": "Battery Temp (°C)",
                "data": battery_temp,
            },
            {
                "label": "Room Temp (°C)",
                "data": room_temp,
            },
        ]
    }

    #data = []
    state: Optional[State] = None
    async with DB.execute((
        "SELECT * FROM solar "
        "WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC LIMIT 86400"
    ), (start_timestamp, end_timestamp)) as cursor:
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
    return state, {
        "power": power_chart_data,
        "soc": soc_chart_data,
        "temp": temp_chart_data,
        "voltages": voltages_chart_data,
    }

@routes.get("/api/sidebar/")
async def api_sidebar(request: web.Request):
    template = env.get_template("sidebar.html")
    async with DB.execute("SELECT * FROM solar ORDER BY timestamp DESC LIMIT 1") as cursor:
        async for row in cursor:
            state = State(*row)
    content = template.render(state=state or State())
    return web.Response(text=content, content_type="text/html")


@routes.get(r"/api/charts/{t:\d+}/")
async def api_charts(request: web.Request):
    t = int(request.match_info['t'])
    state, data = await load_time_based_charts(t)
    if not state:
        return web.json_response({})
    return web.json_response(data)


def line_chart(data: ChartData) -> ChartDef:
     return {
        "type": 'line',
        "data": data,
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
            "datasets": {
                "line": {
                    "pointRadius": 0
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
            }
        }
    }

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
            assert 0 < p <= 1440
    except Exception as e:
        log.warning(f"Invalid time peroid: {e}")

    if p is None:
        start_timestamp = datetime.combine(d.date(), time(0, 0)).timestamp()
        end_timestamp = datetime.combine(d.date(), time(23, 59, 59)).timestamp()

    else:
        start_timestamp = (d - timedelta(minutes=p)).timestamp()
        end_timestamp = d.timestamp()

    state, data = await load_time_based_charts(start_timestamp, end_timestamp)
    energy_chart = await load_energy_chart(d.date())
    content = template.render(
        power_chart=json.dumps(line_chart(data['power'])),
        voltages_chart=json.dumps(line_chart(data['voltages'])),
        soc_chart=json.dumps(line_chart(data['soc'])),
        temp_chart=json.dumps(line_chart(data['temp'])),
        energy_chart=json.dumps(energy_chart),
        selected_peroid=p,
        selected_date=d.date(),
        is_today=d.date()==datetime.now().date(),
        state=state or State()
    )
    return web.Response(text=content, content_type="text/html")


def validate_settings(data, errors) -> Optional[dict[str, Any]]:
    try:
        battery_capacity = int(data['battery_capacity'])
        assert battery_capacity > 0
        return {
            "battery_capacity": battery_capacity
        }
    except Exception as e:
        errors["battery_capacity"] = "Battery capacity must be a non-zero number"
        log.exception(e)
        return None


@routes.get("/settings/")
@routes.post("/settings/")
async def settings_page(request: web.Request):
    template = env.get_template("settings.html")
    battery_capacity = State.battery_capacity
    errors = {}
    if request.method == "POST":
        data = await request.post()
        if cleaned_data := validate_settings(data, errors):
            battery_capacity = cleaned_data['battery_capacity']
            config.save(battery_capacity=battery_capacity)
            return web.HTTPFound(location="/")
    content = template.render(battery_capacity=battery_capacity, errors=errors)
    return web.Response(text=content, content_type="text/html")


async def on_startup(app):
    global DB
    config.load()
    log.info("Connecting to db...")
    DB = await aiosqlite.connect("solarpi.db")


async def on_cleanup(app):
    config.save()
    global DB
    if DB is not None:
        await DB.close()
        DB = None


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



