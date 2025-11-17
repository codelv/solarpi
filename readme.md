# SolarPI

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

This is done to avoid running the app as root.

```
apt install nginx
rm /etc/nginx/sites-enabled/default
cp solarpi.site /etc/nginx/sites-enabled/solarpi
service nginx restart
```

You should see the app. 


