To install on the pi


### Setup solarpi service

Install the service 
```
cp solarpi.service /etc/systemd/system/solarpi.service
systemctl daemon-reload
systemctl enable solarpi.service
systemctl start solarpi.service
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


