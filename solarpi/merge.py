import os
import asyncio
import argparse
from typing import Optional
from time import time

import aiosqlite
from aiosqlite import Connection
from .db import State


MAX_LIMIT = 60*60*24



async def earliest_timestamp(db: Connection) -> int:
    async with db.execute("SELECT timestamp FROM solar ORDER BY timestamp ASC LIMIT 1") as cursor:
        async for row in cursor:
            return row[0]
    return None


async def row_by_timestamp_after(db: Connection, start: int, end: int) -> dict[int, State]:
    rows = {}
    async with db.execute("SELECT * FROM solar WHERE timestamp >= ? and timestamp < ? ORDER BY timestamp ASC", (start, end)) as cursor:
        async for row in cursor:
            state = State(*row)
            rows[state.timestamp] = state
    return rows


def merge_row(state1: Optional[State], state2: Optional[State]) -> Optional[State]:
    if state1 and state2:
        new_state = (
            max(a, b)
            for a, b in zip(state1.values(), state2.values())
        )
        return State(*new_state)
    elif state1:
        return state1
    elif state2:
        return state2
    return None


async def merge_dbs(in_db1: Connection, in_db2: Connection, out_db: Connection):
    await out_db.execute(State.create_table_sql())
    await out_db.commit()

    current_timestamp = int(time())
    in_db1_start = (await earliest_timestamp(in_db1)) or current_timestamp
    in_db2_start = (await earliest_timestamp(in_db2)) or current_timestamp
    initial_timestamp = min(in_db1_start, in_db2_start)
    limit = 10000
    rows = 0
    total_timestamps = current_timestamp - initial_timestamp
    for start in range(initial_timestamp, current_timestamp, limit):
        end = start + limit
        progress = round(100*(start - initial_timestamp) / total_timestamps, 2)
        print(f"Merging at from {start} to {end} ({progress}%)")
        rows1 = await row_by_timestamp_after(in_db1, start, end)
        rows2 = await row_by_timestamp_after(in_db2, start, end)
        new_rows = 0
        for t in range(start, end):
            if state := merge_row(rows1.get(t), rows2.get(t)):
                new_rows += 1
                await out_db.execute(state.insert_values_sql())
        if new_rows:
            rows += new_rows
            print(f"Added {new_rows} total {rows}")
            await out_db.commit()
    print(f"Merged {rows} rows")

async def main():
    parser = argparse.ArgumentParser(
        prog='solarpi-merge',
        description='Merge two solarpi databases'
    )
    parser.add_argument('src1')
    parser.add_argument('src2')
    parser.add_argument('dst')
    args = parser.parse_args()
    if os.path.exists(args.dst):
        raise ValueError("Dst path must not exist")


    async with aiosqlite.connect(args.src1) as in_db1:
        async with aiosqlite.connect(args.src2) as in_db2:
            async with aiosqlite.connect(args.dst) as out_db:
                await merge_dbs(in_db1, in_db2, out_db)



if __name__ == "__main__":
    asyncio.run(main())

