#!/bin/bash
#
# Wi-Fi keeper (jeep-wifi-keeper.service, runs as root).
#
# 1. Fast reconnect. The Jeep drives in and out of range of the house
#    Wi-Fi all the time. NetworkManager reconnects on its own, but while
#    disconnected it scans only every couple of minutes. This asks for a
#    scan every 15 s whenever wlan0 is disconnected (60 s when parked on
#    the UPS battery), and brings a saved network up itself if
#    NetworkManager hasn't started connecting.
#
# 2. Parked hotspot. When the Jeep is parked (weather-station / sleep-cycle
#    mode) and no saved network has been in range for 3 minutes, start the
#    "Jeep Computer" hotspot (NM profile jeep-hotspot) so a phone can open
#    the web page at http://10.42.0.1:8080. Every 15 minutes it drops for a
#    few seconds to look for a saved network (the router may just have been
#    down). Starting the Jeep takes it down at once: while driving the phone
#    is on the CarPlay dongle's Wi-Fi anyway. Disable with "enabled": false
#    in ~/.config/jeep_computer/hotspot.json.
#
# It never touches wlan0 while it is connected to a network, connecting,
# or unmanaged (e.g. if LIVI's own wireless CarPlay ever takes the radio).
#
# Testing without touching the connection (one pass, actions printed):
#   WIFI_KEEPER_DRY_RUN=1 WIFI_KEEPER_ONCE=1 [FAKE_STATE=30] [FAKE_PARKED=1] \
#   [FAKE_AWAY_S=200] [FAKE_HOTSPOT=1] [FAKE_HOTSPOT_AGE_S=1000] [FAKE_NO_KNOWN=1] \
#   bash tools/wifi_keeper.sh

IFACE=wlan0
UPS_STATUS=/run/jeep-ups/status.json
HOTSPOT=jeep-hotspot
HOTSPOT_CFG=${HOTSPOT_CFG:-"$HOME/.config/jeep_computer/hotspot.json"}
AWAY_BEFORE_HOTSPOT_S=180
HOTSPOT_RECHECK_S=900

DRY=${WIFI_KEEPER_DRY_RUN:-0}
ONCE=${WIFI_KEEPER_ONCE:-0}

log() { echo "$(date '+%F %T') $*"; }

act() {
    if [ "$DRY" = 1 ]; then
        echo "DRY RUN: $*"
    else
        "$@" > /dev/null 2>&1
    fi
}

state() {
    # "100 (connected)", "30 (disconnected)", "10 (unmanaged)", ...
    if [ -n "$FAKE_STATE" ]; then echo "$FAKE_STATE"; return; fi
    nmcli -t -g GENERAL.STATE device show "$IFACE" 2>/dev/null | cut -d' ' -f1
}

parked() {
    if [ -n "$FAKE_PARKED" ]; then [ "$FAKE_PARKED" = 1 ]; return; fi
    grep -Eq '"mode": "(station|duty)"' "$UPS_STATUS" 2>/dev/null
}

hotspot_enabled() { grep -q '"enabled": true' "$HOTSPOT_CFG" 2>/dev/null; }

hotspot_active() {
    if [ -n "$FAKE_HOTSPOT" ]; then [ "$FAKE_HOTSPOT" = 1 ]; return; fi
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$HOTSPOT"
}

interval() { if parked; then echo 60; else echo 15; fi; }

# Saved Wi-Fi client profiles that autoconnect, highest priority first
saved_profiles() {
    nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show 2>/dev/null |
        awk -F: '$2 == "802-11-wireless" && $3 == "yes" { print $4 "\t" $1 }' |
        sort -rn | cut -f2- | grep -vx "$HOTSPOT"
}

# Scan, then connect to the best saved network in range. 0 if one was found.
connect_known() {
    act nmcli device wifi rescan ifname "$IFACE"
    if [ "$FAKE_NO_KNOWN" = 1 ]; then log "(test) no saved network in range"; return 1; fi
    [ "$DRY" = 1 ] || sleep 5
    local visible
    visible=$(nmcli -t -f SSID device wifi list ifname "$IFACE" --rescan no 2>/dev/null)
    while IFS= read -r name; do
        [ -z "$name" ] && continue
        ssid=$(nmcli -g 802-11-wireless.ssid connection show "$name" 2>/dev/null)
        if [ -n "$ssid" ] && grep -Fxq "$ssid" <<< "$visible"; then
            log "$ssid in range, connecting"
            act nmcli --wait 30 connection up id "$name" ifname "$IFACE"
            return 0
        fi
    done <<< "$(saved_profiles)"
    return 1
}

log "Wi-Fi keeper watching $IFACE"
now() { date +%s; }
away_since=""
hotspot_since=""
[ -n "$FAKE_AWAY_S" ] && away_since=$(( $(now) - FAKE_AWAY_S ))
[ -n "$FAKE_HOTSPOT_AGE_S" ] && hotspot_since=$(( $(now) - FAKE_HOTSPOT_AGE_S ))

while true; do
    if hotspot_active; then
        hotspot_since=${hotspot_since:-$(now)}
        if ! parked || ! hotspot_enabled; then
            log "Jeep on (or hotspot disabled): hotspot off"
            act nmcli connection down id "$HOTSPOT"
            hotspot_since=""
            away_since=$(now)
        elif [ $(( $(now) - hotspot_since )) -ge $HOTSPOT_RECHECK_S ]; then
            log "hotspot: looking for a saved network"
            act nmcli connection down id "$HOTSPOT"
            [ "$DRY" = 1 ] || sleep 3
            if connect_known; then
                hotspot_since=""
                away_since=""
            else
                act nmcli connection up id "$HOTSPOT"
                hotspot_since=$(now)
            fi
        fi

    elif [ "$(state)" = "30" ]; then
        away_since=${away_since:-$(now)}
        if ! connect_known && parked && hotspot_enabled \
                && [ $(( $(now) - away_since )) -ge $AWAY_BEFORE_HOTSPOT_S ]; then
            log "parked with no saved network in range: starting the hotspot"
            act nmcli connection up id "$HOTSPOT"
            hotspot_since=$(now)
        fi

    else
        away_since=""
    fi

    [ "$ONCE" = 1 ] && break
    sleep "$(interval)"
done
