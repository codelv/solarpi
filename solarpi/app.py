import json
import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from typing import Any, Optional, cast

import aiosqlite
from aiohttp import web
from jinja2 import Environment, PackageLoader, select_autoescape

from . import config
from .db import State
from .utils import is_bt_addr

env = Environment(loader=PackageLoader("solarpi"), autoescape=select_autoescape())
log = logging.getLogger("solarpi")

routes = web.RouteTableDef()
routes.static("/static", os.path.join(os.path.dirname(__file__), "static"))

DB: Optional[aiosqlite.Connection] = None
ChartData = dict[str, Any]
ChartDef = dict[str, Any]
FormData = dict[str, Any]
FormErrors = dict[str, str]


async def load_energy_chart(d: date) -> ChartDef:
    assert DB is not None
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
        async with DB.execute(
            (
                "SELECT * FROM solar "
                "WHERE timestamp <= ? AND charger_total_energy > 0 AND battery_total_charge_energy > 0 AND battery_total_discharge_energy > 0 "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            (d.timestamp(),),
        ) as cursor:
            state: Optional[State] = None
            async for row in cursor:
                state = State(*row)
                if last_state:
                    # These values should never go down, if they do assume that device was reset and cleared the reading
                    if state.charger_total_energy < last_state.charger_total_energy:
                        solar_energy_output.append(state.charger_total_energy)
                    else:
                        solar_energy_output.append(
                            state.charger_total_energy - last_state.charger_total_energy
                        )
                    if (
                        state.battery_total_discharge_energy
                        < last_state.battery_total_discharge_energy
                    ):
                        battery_discharge_energy.append(
                            state.battery_total_discharge_energy
                        )
                    else:
                        battery_discharge_energy.append(
                            state.battery_total_discharge_energy
                            - last_state.battery_total_discharge_energy
                        )
                    if (
                        state.battery_total_charge_energy
                        < last_state.battery_total_charge_energy
                    ):
                        battery_charge_energy.append(state.battery_total_charge_energy)
                    else:
                        battery_charge_energy.append(
                            state.battery_total_charge_energy
                            - last_state.battery_total_charge_energy
                        )
                    inverter_energy.append(
                        solar_energy_output[-1]
                        - battery_charge_energy[-1]
                        + battery_discharge_energy[-1]
                    )
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
        ],
    }
    return {
        "type": "bar",
        "data": energy_chart_data,
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "scales": {
                "y": {
                    "beginAtZero": True,
                }
            },
        },
    }


async def load_peaks_chart(d: date) -> ChartDef:
    assert DB is not None
    dates = []
    peak_battery_charge_power = []
    peak_battery_discharge_power = []
    peak_battery_charge_current = []
    peak_battery_discharge_current = []
    peak_solar_power = []
    peak_inverter_power = []
    peak_inverter_current = []
    peak_charger_current = []

    start = datetime.combine(d, time(12, 0))
    for i in range(8, -1, -1):
        d = (start - timedelta(days=i)).date()
        st = datetime.combine(d, time(0, 0)).timestamp()
        et = datetime.combine(d, time(23, 59, 59)).timestamp()
        dates.append(d)

        async with DB.execute(
            (
                "SELECT MAX(charger_voltage * charger_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_solar_power.append(row[0])
            else:
                peak_solar_power.append(0)

        async with DB.execute(
            (
                "SELECT MAX(charger_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_charger_current.append(row[0])
            else:
                peak_charger_current.append(0)

        async with DB.execute(
            (
                "SELECT MAX(battery_voltage * battery_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? AND battery_is_charging = 1 ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_battery_charge_power.append(row[0])
            else:
                peak_battery_charge_power.append(0)

        async with DB.execute(
            (
                "SELECT MAX(battery_voltage * battery_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? AND battery_is_charging = 0 ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_battery_discharge_power.append(row[0])
            else:
                peak_battery_discharge_power.append(0)

        async with DB.execute(
            (
                "SELECT MAX(CASE battery_is_charging "
                "WHEN 1 THEN (charger_voltage * charger_current - battery_voltage * battery_current) "
                "ELSE (battery_voltage * battery_current + charger_voltage * charger_current) "
                "END) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_inverter_power.append(row[0])
            else:
                peak_inverter_power.append(0)

        async with DB.execute(
            (
                "SELECT MAX(CASE battery_is_charging "
                "WHEN 1 THEN (charger_current - battery_current) "
                "ELSE (battery_current + charger_current) "
                "END) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_inverter_current.append(row[0])
            else:
                peak_inverter_current.append(0)

        async with DB.execute(
            (
                "SELECT MAX(battery_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? AND battery_is_charging = 1 ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_battery_charge_current.append(row[0])
            else:
                peak_battery_charge_current.append(0)

        async with DB.execute(
            (
                "SELECT MAX(battery_current) FROM solar "
                "WHERE timestamp >= ? AND timestamp <= ? AND battery_is_charging = 0 ORDER BY timestamp DESC LIMIT 1"
            ),
            (st, et),
        ) as cursor:
            if row := await cursor.fetchone():
                peak_battery_discharge_current.append(row[0])
            else:
                peak_battery_discharge_current.append(0)

    peak_chart_data = {
        "labels": [str(d) for d in dates],
        "datasets": [
            {
                "label": "Peak solar power (W)",
                "data": peak_solar_power,
                "borderWidth": 1,
            },
            {
                "label": "Peak battery charge power (W)",
                "data": peak_battery_charge_power,
                "borderWidth": 1,
            },
            {
                "label": "Peak battery discharge power (W)",
                "data": peak_battery_discharge_power,
                "borderWidth": 1,
            },
            {
                "label": "Peak inverter power (W)",
                "data": peak_inverter_power,
                "borderWidth": 1,
            },
            {
                "label": "Peak charger current (A)",
                "data": peak_charger_current,
                "borderWidth": 1,
            },
            {
                "label": "Peak battery charge current (A)",
                "data": peak_battery_charge_current,
                "borderWidth": 1,
            },
            {
                "label": "Peak battery discharge current (A)",
                "data": peak_battery_discharge_current,
                "borderWidth": 1,
            },
            {
                "label": "Peak inverter current (A)",
                "data": peak_inverter_current,
                "borderWidth": 1,
            },
        ],
    }
    return {
        "type": "bar",
        "data": peak_chart_data,
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "scales": {
                "y": {
                    "beginAtZero": True,
                }
            },
        },
    }


async def load_time_based_charts(
    start_timestamp: int, end_timestamp: Optional[int] = None
) -> tuple[Optional[State], dict[str, ChartData]]:
    if end_timestamp is None:
        end_timestamp = int(datetime.now().timestamp())
    assert DB is not None
    timestamps: list[int] = []
    battery_voltage: list[float] = []
    battery_current: list[float] = []
    battery_power: list[float] = []
    charger_current: list[float] = []
    charger_voltage: list[float] = []
    charger_power: list[float] = []
    inverter_current: list[float] = []
    solar_voltage: list[float] = []
    solar_current: list[float] = []
    inverter_power: list[float] = []
    battery_soc: list[float] = []
    charger_temp: list[float] = []
    battery_temp: list[float] = []
    room_temp: list[float] = []

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
        ],
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
            },
        ],
    }

    soc_chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery State of Charge (Ah)",
                "data": battery_soc,
            },
        ],
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
        ],
    }

    # data = []
    state: Optional[State] = None
    async with DB.execute(
        (
            "SELECT * FROM solar "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp ASC LIMIT 86400"
        ),
        (start_timestamp, end_timestamp),
    ) as cursor:
        async for row in cursor:
            state = State(*row)
            timestamps.append(state.timestamp * 1000)
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
    assert DB is not None
    template = env.get_template("sidebar.html")
    async with DB.execute(
        "SELECT * FROM solar ORDER BY timestamp DESC LIMIT 1"
    ) as cursor:
        async for row in cursor:
            state = State(*row)
    content = template.render(state=state or State())
    return web.Response(text=content, content_type="text/html")


@routes.get(r"/api/charts/{t:\d+}/")
async def api_charts(request: web.Request):
    t = int(request.match_info["t"])
    state, data = await load_time_based_charts(t)
    if not state:
        return web.json_response({})
    return web.json_response(data)


def line_chart(data: ChartData) -> ChartDef:
    return {
        "type": "line",
        "data": data,
        "options": {
            "responsive": True,
            "normalized": True,
            "animation": False,
            "maintainAspectRatio": False,
            "spanGaps": True,
            "scales": {
                "x": {
                    "type": "time",
                }
            },
            "datasets": {"line": {"pointRadius": 0}},
            "plugins": {
                "legend": {
                    "position": "top",
                },
            },
        },
    }


@routes.get("/")
async def index(request: web.Request):
    template = env.get_template("index.html")

    d = datetime.now()
    try:
        if date_str := request.query.get("d", ""):
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
            d = datetime.combine(day, d.time())
    except Exception as e:
        log.warning(f"Invalid date query: {e}")

    p = None
    try:
        if peroid_str := request.query.get("p", ""):
            p = int(peroid_str)
            assert 0 < p <= 1440
    except Exception as e:
        log.warning(f"Invalid time peroid: {e}")

    if p is None:
        start_timestamp = int(datetime.combine(d.date(), time(0, 0)).timestamp())
        end_timestamp = int(datetime.combine(d.date(), time(23, 59, 59)).timestamp())

    else:
        start_timestamp = int((d - timedelta(minutes=p)).timestamp())
        end_timestamp = int(d.timestamp())

    state, data = await load_time_based_charts(start_timestamp, end_timestamp)
    energy_chart = await load_energy_chart(d.date())
    peaks_chart = await load_peaks_chart(d.date())

    soc_chart = line_chart(data["soc"])
    soc_chart["options"]["scales"]["y"] = {
        "beginAtZero": True,
        "min": 0,
        "max": State.battery_capacity,
    }

    content = template.render(
        power_chart=json.dumps(line_chart(data["power"])),
        voltages_chart=json.dumps(line_chart(data["voltages"])),
        soc_chart=json.dumps(soc_chart),
        temp_chart=json.dumps(line_chart(data["temp"])),
        energy_chart=json.dumps(energy_chart),
        peaks_chart=json.dumps(peaks_chart),
        selected_peroid=p,
        selected_date=d.date(),
        is_today=d.date() == datetime.now().date(),
        state=state or State(),
    )
    return web.Response(text=content, content_type="text/html")


def validate_settings(data: FormData, errors: FormErrors) -> Optional[FormData]:
    cleaned_data = {}
    try:
        battery_capacity = int(data["battery_capacity"])
        assert battery_capacity > 0
        cleaned_data["battery_capacity"] = battery_capacity
    except Exception as e:
        errors["battery_capacity"] = "Battery capacity must be a non-zero number"
        log.exception(e)
        return None

    try:
        if addr := data["battery_monitor_addr"]:
            assert is_bt_addr(addr)
            cleaned_data["battery_monitor_addr"] = addr
    except Exception as e:
        errors["battery_monitor_addr"] = (
            "Battery monitor address must be a bluetooth address"
        )
        log.exception(e)
        return None
    try:
        if addr := data["solar_charger_addr"]:
            assert is_bt_addr(addr)
            cleaned_data["solar_charger_addr"] = addr
    except Exception as e:
        errors["solar_charger_addr"] = (
            "Solar charger address must be a bluetooth address"
        )
        log.exception(e)
        return None

    return cleaned_data


@routes.get("/settings/")
@routes.post("/settings/")
async def settings_page(request: web.Request):
    template = env.get_template("settings.html")
    battery_capacity = State.battery_capacity
    battery_monitor_addr = config.Config.battery_monitor_addr
    solar_charger_addr = config.Config.solar_charger_addr
    errors: FormErrors = {}
    if request.method == "POST":
        data = cast(FormData, await request.post())
        if cleaned_data := validate_settings(data, errors):
            config.save(**cleaned_data)
            return web.HTTPFound(location="/")
    content = template.render(
        battery_capacity=battery_capacity,
        battery_monitor_addr=battery_monitor_addr,
        solar_charger_addr=solar_charger_addr,
        errors=errors,
    )
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
            logging.StreamHandler(sys.stdout),
        ],
    )
    log.setLevel(logging.DEBUG)
    web.run_app(app, port=5000)


if __name__ == "__main__":
    main()
