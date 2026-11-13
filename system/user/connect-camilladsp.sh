#!/bin/bash
#
# Jeep audio router: everything goes through CamillaDSP.
#
#   jeep_carplay  (stereo, system default sink)  -> camilladsp-capture inputs 1-2
#   jeep_stems    (8 ch: drums, bass, vocals, other, each L/R; the Music
#                  page's stem mixer places them in the cabin)
#                                                 -> camilladsp-capture inputs 3-10
#                  (only when the active config has the 10 stem inputs; the
#                  older 8-input presets keep inputs 3-8 unused)
#
# LIVI / CarPlay (and anything else) simply plays to the default sink, so it
# no longer matters what LIVI names its audio stream. The sinks and links are
# persistent; this loop only repairs them after PipeWire or CamillaDSP restarts.

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

CARPLAY_SINK="jeep_carplay"
STEMS_SINK="jeep_stems"
CAMILLA_NODE="camilladsp-capture"

CARPLAY_LINKS=(
    "monitor_FL:input_1"
    "monitor_FR:input_2"
)

STEMS_LINKS=(
    "monitor_AUX0:input_3"
    "monitor_AUX1:input_4"
    "monitor_AUX2:input_5"
    "monitor_AUX3:input_6"
    "monitor_AUX4:input_7"
    "monitor_AUX5:input_8"
    "monitor_AUX6:input_9"
    "monitor_AUX7:input_10"
)

sink_exists() {
    pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -qx "$1"
}

ensure_sinks() {
    if ! sink_exists "$CARPLAY_SINK"; then
        pactl load-module module-null-sink \
            sink_name="$CARPLAY_SINK" \
            channels=2 channel_map=front-left,front-right \
            rate=48000 \
            sink_properties="device.description='CarPlay-to-CamillaDSP'" \
            >/dev/null && echo "Created sink $CARPLAY_SINK"
        pactl set-sink-volume "$CARPLAY_SINK" 100% 2>/dev/null
    fi

    if ! sink_exists "$STEMS_SINK"; then
        pactl load-module module-null-sink \
            sink_name="$STEMS_SINK" \
            channels=8 channel_map=aux0,aux1,aux2,aux3,aux4,aux5,aux6,aux7 \
            rate=48000 \
            sink_properties="device.description='Stems-to-CamillaDSP'" \
            >/dev/null && echo "Created sink $STEMS_SINK"
        pactl set-sink-volume "$STEMS_SINK" 100% 2>/dev/null
    fi

    # Keep CarPlay as the default so LIVI never falls back to HDMI
    if [ "$(pactl get-default-sink 2>/dev/null)" != "$CARPLAY_SINK" ]; then
        pactl set-default-sink "$CARPLAY_SINK" && echo "Default sink -> $CARPLAY_SINK"
    fi
}

ensure_links() {
    local sink="$1"; shift

    for pair in "$@"; do
        local src="$sink:${pair%%:*}"
        local dst="$CAMILLA_NODE:${pair##*:}"

        if ! grep -qx "$src -> $dst" <<<"$LINKS"; then
            pw-link "$src" "$dst" 2>/dev/null && echo "Linked $src -> $dst"
        fi
    done
}

# Wanted links as "src -> dst" lines, for the ports that exist right now
wanted_links() {
    local sink="$1"; shift
    for pair in "$@"; do
        echo "$sink:${pair%%:*} -> $CAMILLA_NODE:${pair##*:}"
    done
}

# Cheap steady state: one "pw-link -il" call every 2 s. It lists every
# input port (linked or not) with the links into it. ("pw-link -l" only
# lists ports that already have a link, so after a fresh boot it never
# showed CamillaDSP's inputs and nothing got linked.) Sinks are re-checked
# every 30 s, and straight away whenever a link is missing; repair
# commands only run when something is missing.
tick=0
while true; do
    if [ $((tick % 15)) -eq 0 ] && pactl info >/dev/null 2>&1; then
        ensure_sinks
    fi
    tick=$((tick + 1))

    SNAPSHOT=$(pw-link -il 2>/dev/null)
    if grep -qx "$CAMILLA_NODE:input_1" <<<"$SNAPSHOT"; then
        # "src -> dst" for every link into an input port
        LINKS=$(awk '
            /^[^ \t]/ { port = $0; next }
            /\|<-/   { sub(/^[ \t]*\|<-[ \t]*/, ""); print $0 " -> " port }
        ' <<<"$SNAPSHOT")

    WANTED=$(wanted_links "$CARPLAY_SINK" "${CARPLAY_LINKS[@]}")
        if grep -qx "$CAMILLA_NODE:input_10" <<<"$SNAPSHOT"; then
            WANTED+=$'\n'$(wanted_links "$STEMS_SINK" "${STEMS_LINKS[@]}")
        fi

        if grep -qvxF -f <(printf '%s\n' "$LINKS") <<<"$WANTED"; then
            # Something is missing: make sure the sinks exist, then relink
            pactl info >/dev/null 2>&1 && ensure_sinks
            ensure_links "$CARPLAY_SINK" "${CARPLAY_LINKS[@]}"
            if grep -qx "$CAMILLA_NODE:input_10" <<<"$SNAPSHOT"; then
                ensure_links "$STEMS_SINK" "${STEMS_LINKS[@]}"
            fi
        fi
    fi

    sleep 2
done
