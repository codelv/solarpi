import dataclasses
import logging
from time import time
from typing import ClassVar, Optional

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
    battery_capacity: ClassVar[float] = 600
    battery_total_charge_energy: float = 0
    battery_total_discharge_energy: float = 0

    solar_panel_voltage: float = 0
    charger_voltage: float = 0
    charger_current: float = 0
    charger_temp: float = 0
    charger_total_energy: float = 0
    charger_status: int = 0
    room_temp: float = 0

    _instance: ClassVar[Optional["State"]] = None

    @property
    def solar_panel_current(self):
        if v := self.solar_panel_voltage:
            return round(self.charger_voltage / v * self.charger_current, 2)
        return 0

    @property
    def battery_power(self):
        sign = 1 if self.battery_is_charging else -1
        return round(self.battery_voltage * sign * self.battery_current, 2)

    @property
    def battery_percent(self):
        return round(100 * self.battery_ah / self.battery_capacity, 2)

    @property
    def charger_power(self):
        return round(self.charger_voltage * self.charger_current, 2)

    @property
    def inverter_voltage(self):
        return max(self.charger_voltage, self.battery_voltage)

    @property
    def inverter_current(self):
        if self.battery_is_charging:
            # If charger outputs 14A and battery is charging at 10A, inverter is using 4A
            return round(max(0, self.charger_current - self.battery_current), 2)
        # If charger outputs 4A and battery is discharging at 10A, inverter is using 14A
        return round(self.charger_current + self.battery_current, 2)

    @property
    def inverter_power(self):
        return round(self.inverter_voltage * self.inverter_current, 2)

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
