#!/bin/bash
# Copy the Jeep Computer's config files that live outside this repo into
# system/, so they're version-controlled with the code. Run before committing.
cd "$(dirname "$0")"
copy() { mkdir -p "$(dirname "$2")"; cp "$1" "$2" 2>/dev/null || echo "missing: $1"; }

copy ~/.local/bin/connect-camilladsp.sh          user/connect-camilladsp.sh
copy ~/.config/systemd/user/camilladsp-routing.service user/camilladsp-routing.service
copy ~/.config/systemd/user/jeep-startup.service user/jeep-startup.service
copy ~/.config/systemd/user/jeep-dashboard.service user/jeep-dashboard.service
copy ~/.config/systemd/user/jeep-weather-station.service user/jeep-weather-station.service
copy ~/.config/systemd/user/jeep-backup.service user/jeep-backup.service
copy ~/.config/systemd/user/jeep-backup.timer user/jeep-backup.timer
copy ~/.config/systemd/user/jeep-web.service user/jeep-web.service
copy ~/.config/systemd/user/jeep-stems-sync.service user/jeep-stems-sync.service
copy ~/.config/systemd/user/jeep-stems-sync.timer user/jeep-stems-sync.timer
copy ~/.config/labwc/autostart                   user/labwc-autostart
copy ~/.config/labwc/rc.xml                      user/labwc-rc.xml
copy ~/.config/kanshi/config                     user/kanshi-config
copy ~/.config/wf-panel-pi/wf-panel-pi.ini       user/wf-panel-pi.ini
copy ~/.config/pcmanfm/default/desktop-items-HDMI-A-2.conf user/pcmanfm-desktop-items.conf
copy ~/.config/pcmanfm/default/pcmanfm.conf      user/pcmanfm.conf
copy ~/.config/libfm/libfm.conf                 user/libfm.conf
copy ~/.local/share/applications/jeep-computer.desktop user/jeep-computer.desktop
copy ~/.config/jeep_computer/amp.json            user/amp.json
copy ~/.config/jeep_computer/ups.json            user/ups.json
copy ~/.config/jeep_computer/weather.json        user/weather.json
copy ~/.config/jeep_computer/stems.json          user/stems.json
copy ~/.config/jeep_computer/display.json        user/display.json
copy ~/.config/jeep_computer/trail.json          user/trail.json
copy ~/.config/jeep_computer/tach.json           user/tach.json

copy ~/.config/wireplumber/wireplumber.conf.d/51-jeep-audio-devices.conf user/wireplumber-51-jeep-audio-devices.conf
copy /etc/systemd/system/jeep-ups.service        root/jeep-ups.service
copy /etc/systemd/system/camilladsp.service      root/camilladsp.service
copy /etc/systemd/system/camillagui.service      root/camillagui.service
copy /etc/systemd/system/jeep-wifi-keeper.service root/jeep-wifi-keeper.service
copy /etc/systemd/system/jeep-duty-check.service root/jeep-duty-check.service
copy /etc/systemd/system/lightdm.service.d/jeep-duty.conf root/lightdm-jeep-duty.conf
copy /etc/NetworkManager/dispatcher.d/90-jeep-home-sync root/nm-dispatcher-90-jeep-home-sync
copy /etc/systemd/journald.conf.d/jeep-persistent.conf root/journald-jeep-persistent.conf
cp -r /etc/systemd/system/camilladsp.service.d   root/ 2>/dev/null
copy /usr/lib/systemd/system-shutdown/jeep-ups-power-restored root/jeep-ups-power-restored
copy /etc/chromium.d/99-no-keyring           root/chromium-99-no-keyring
copy /boot/firmware/cmdline.txt                  root/cmdline.txt
copy /boot/firmware/config.txt                   root/config.txt

mkdir -p camilladsp
cp ~/CamillaDSP/*.yml camilladsp/ 2>/dev/null
rm -f camilladsp/statefile.yml
echo "collected into $(pwd)"
