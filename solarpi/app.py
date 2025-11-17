import os
import sys
import struct
import asyncio
import logging
import json
import aiosqlite
import dataclasses
from datetime import datetime, time
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

@routes.get("/")
async def index(request):
    template = env.get_template("index.html")

    d = datetime.now().date()
    start_of_day = datetime.combine(d, time(0, 0))
    timestamps = []
    battery_voltages = []
    chart_data = {
        "labels": timestamps,
        "datasets": [
            {
                "label": "Battery Voltage",
                "data": battery_voltages,
                "pointRadius": 0,
                #"spanGaps": True
            }
        ]
    }
    #data = []
    current = None
    async with DB.execute("SELECT * FROM solar WHERE timestamp >= ? ORDER BY timestamp DESC", (start_of_day.timestamp(), )) as cursor:
        async for row in cursor:
            state = State(*row)
            if current is None:
                current = state
            timestamps.append(state.timestamp)
            battery_voltages.append(state.battery_voltage)

            #data.append(State(*row))

    chart = {
        "type": 'line',
        "data": chart_data,
        "options": {
            "responsive": True,
            #"normalized": True,
            "animation": False,
            "scales": {
                "x": {
                    "type": 'timeseries',
                }
            },
            "plugins": {
                "legend": {
                    "position": 'top',
                },
                "title": {
                    "display": True,
                    "text": 'Battery Voltage'
                }
            }
        }
    }

    content = template.render(chart_data=json.dumps(chart), current=current)
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



