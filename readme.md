# SolarPI

Solarpi is a simple python app to do datalogging of the junctec battery monitor and helios solar chargers via bluetooth.

It's made for a raspberry pi zero 2w but should will work on any debian based system.

![solarpi-web](docs/solarpi-screenshot.png)

To view live data using an android phone see [https://github.com/codelv/solar](solar).

### About

The app is split into two services, `solarpi-monitor` and `solarpi-web`. 

The `solarpi-monitor` service connects to the battery monitor and charger via bluetooth, pulls/decodes the data, 
and saves it into an sqlite db.

The `solarpi-web` service provides a simple web application to view the data in the database.

## Install

Copy the solarpi folder/python module to /opt/solar-pi.

Install the following python dependencies with apt:

```
python3-bleak
python3-aiohttp
python3-aiosqlite
python3-jinja2
```


### Setup solarpi service

Install the service 
```
cp solarpi*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable solarpi-monitor.service
systemctl enable solarpi-web.service
systemctl start solarpi-monitor.service
systemctl start solarpi-web.service
```

### Setup nginx proxy

This is done so the app does not need to be running as root.

```
apt install nginx
rm /etc/nginx/sites-enabled/default
cp solarpi.site /etc/nginx/sites-enabled/solarpi
service nginx restart
```

## Usage

Go to the ip or hostname of the pi in your browser and you should see the web page. 

If you see a 502 Bad gateway page make sure the solarpi-web service is up and running. 

Check logs using `journalctl -f solarpi-web.service` and `journalctl -f solarpi-monitor.service`.


