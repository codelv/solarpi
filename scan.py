from bleak import BleakScanner
from pprint import pformat

def main():
    result = await BleakScanner.discover(return_adv=True)
    pformat(result)

if __name__ == '__main__':
    main()
