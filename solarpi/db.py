import logging
import dataclasses
from time import time
from typing import ClassVar

log = logging.getLogger("solarpi")

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
