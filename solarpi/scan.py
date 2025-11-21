import asyncio
from pprint import pformat

from bleak import BleakScanner


async def main():
    result = await BleakScanner.discover(return_adv=True)
    pformat(result)


if __name__ == "__main__":
    asyncio.run(main())
