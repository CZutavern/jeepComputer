import QtQuick
import QtQuick.Window
import QtWayland.Compositor
import QtWayland.Compositor.XdgShell

// Jeep Computer shell: a small full-screen Wayland compositor that hosts the
// dashboard and LIVI (CarPlay) side by side, like a car head unit's system UI.
//
//   z 0  dashboard   - fills the screen
//   z 1  CarPlay     - wherever the dashboard asks (carplayX/Y/W/H, carplayVisible).
//                      EVERY LIVI window lives here (it opens a second window
//                      when a phone connects), so they all hide with the page.
//   z 2  dialogs     - the dashboard's own extra windows (exit confirmation), centred
//
// jeep_shell.py sets dashboardPid and the carplay* properties from the
// dashboard's control-socket requests.
WaylandCompositor {
    id: comp
    socketName: "jeep-shell-0"

    // Set by jeep_shell.py
    property int dashboardPid: -1
    property int carplayX: 0
    property int carplayY: 60
    property int carplayW: 1920
    property int carplayH: 930
    property bool carplayVisible: false

    // Read by jeep_shell.py
    property bool carplayConnected: carplayWindows.count > 0
    // LIVI's video helper only exists while a phone is streaming
    property int helperCount: 0
    property bool carplayStreaming: helperCount > 0
    // Show LIVI's own UI (its settings) even without a phone; set by the dashboard
    property bool liviUi: false
    property bool carplayShown: carplayVisible && carplayConnected && (carplayStreaming || liviUi)
    property bool dashboardConnected: dashboardItem.shellSurface !== null

    // LIVI's main process. Windows from its other processes are helpers
    // (the GStreamer video host): shown, but taps go through them to the
    // main window, which is what forwards touches to the phone.
    property int carplayPid: -1

    // Newest full-size LIVI window: the one that gets sized to the slot
    property var carplayToplevel: null
    property var carplaySurface: null

    // What LIVI is actually drawing (for diagnostics / size enforcement)
    property size carplayBufferSize: carplaySurface ? carplaySurface.bufferSize : Qt.size(0, 0)
    property size carplayDestSize: carplaySurface ? carplaySurface.destinationSize : Qt.size(0, 0)

    // Volume pop-up, drawn by the shell so it shows over CarPlay too.
    // jeep_shell.py sets these and bumps osdSerial to show it.
    property real osdVolume: -40
    property string osdMessage: ""
    property bool osdOffline: false
    property int osdSerial: 0

    // Night dimming (0 = off .. ~0.6), set by the dashboard from the light sensor
    property real dimLevel: 0

    // Alert strip over CarPlay (the dashboard's banner is hidden under it)
    property string bannerLevel: ""
    property string bannerText: ""
    property string bannerDetail: ""
    property string bannerDismissed: ""
    onBannerTextChanged: if (bannerText === "") bannerDismissed = ""
    onOsdSerialChanged: {
        osd.opacity = 1
        osdTimer.restart()
    }

    // Tell LIVI its new size once geometry settles (not on every step
    // of a layout change)
    Timer {
        id: resizeTimer
        interval: 250
        onTriggered: comp.sendCarplaySize()
    }
    onCarplayWChanged: { sizeRetries = 0; resizeTimer.restart() }
    onCarplayHChanged: { sizeRetries = 0; resizeTimer.restart() }

    function sendCarplaySize() {
        if (carplayW <= 0 || carplayH <= 0)
            return
        if (carplayToplevel) {
            console.log("shell: CarPlay size -> " + carplayW + "x" + carplayH)
            carplayToplevel.sendMaximized(Qt.size(carplayW, carplayH))
        }
        // The video helper scales the phone's picture to its window size, so
        // it needs the slot size too (the phone doesn't always send full-size
        // video; 1280x768 would otherwise sit small in the corner)
        for (var i = 0; i < carplayWindows.count; i++) {
            var w = carplayWindows.get(i)
            if (w.helper && !w.transient)
                w.toplevel.sendMaximized(Qt.size(carplayW, carplayH))
        }
    }

    // If LIVI ends up drawing at a different size than its slot (e.g. it
    // missed a resize while the dashboard restarted), ask again. Retries
    // are rate limited by the timer and capped per slot size.
    property int sizeRetries: 0
    Timer {
        id: sizeCheck
        interval: 500
        repeat: true
        running: comp.carplayToplevel !== null
        onTriggered: {
            // LIVI's actual buffer. If it doesn't match the slot, its picture
            // is stale (Chromium would scale it via the viewporter = stretched)
            if (!comp.carplaySurface) return
            var w = comp.carplayBufferSize.width, h = comp.carplayBufferSize.height
            // 1x1 = LIVI's video host, which draws through a subsurface
            if (w <= 1 || h <= 1) return
            if (w === comp.carplayW && h === comp.carplayH) {
                comp.sizeRetries = 0
            } else if (comp.sizeRetries < 5) {
                comp.sizeRetries++
                console.log("shell: CarPlay drew " + w + "x" + h + ", wanted "
                            + comp.carplayW + "x" + comp.carplayH + " - resending")
                comp.sendCarplaySize()
            }
        }
    }

    function focusDashboard() {
        // Can run while the shell is tearing down, when items are gone
        try {
            if (dashboardItem.shellSurface)
                dashboardItem.takeFocus()
        } catch (e) {}
    }

    function classify(toplevel, xdgSurface) {
        var pid = xdgSurface.surface.client.processId

        if (pid === dashboardPid && dashboardItem.shellSurface === null) {
            dashboardItem.shellSurface = xdgSurface
            toplevel.sendFullscreen(Qt.size(win.width, win.height))
            dashboardItem.takeFocus()
            console.log("shell: dashboard connected (pid " + pid + ")")
        } else if (pid === dashboardPid) {
            dialogs.append({ shellSurface: xdgSurface })
            console.log("shell: dashboard dialog (pid " + pid + ")")
        } else {
            // LIVI (any of its processes). Windows with a parent are its
            // own dialogs: centred in the CarPlay area at their own size.
            if (carplayPid === -1 || carplayWindows.count === 0)
                carplayPid = pid
            var helper = pid !== carplayPid
            var transient = toplevel.parentToplevel !== null
            carplayWindows.append({ shellSurface: xdgSurface, toplevel: toplevel,
                                    transient: transient, helper: helper })
            if (helper)
                helperCount++
            console.log("shell: CarPlay window " + carplayWindows.count
                        + (helper ? " (video helper)" : transient ? " (dialog)" : "")
                        + " (pid " + pid + ")")
            if (!transient && !helper)
                useCarplayWindow(toplevel, xdgSurface)
            else if (helper && !transient)
                toplevel.sendMaximized(Qt.size(carplayW, carplayH))
        }
    }

    function useCarplayWindow(toplevel, xdgSurface) {
        carplayToplevel = toplevel
        carplaySurface = xdgSurface ? xdgSurface.surface : null
        sizeRetries = 0
        sendCarplaySize()
    }

    function removeCarplayWindow(xdgSurface) {
        for (var i = 0; i < carplayWindows.count; i++) {
            if (carplayWindows.get(i).shellSurface === xdgSurface) {
                if (carplayWindows.get(i).helper)
                    helperCount = Math.max(0, helperCount - 1)
                carplayWindows.remove(i)
                break
            }
        }
        // Fall back to the newest remaining full-size window
        for (var j = carplayWindows.count - 1; j >= 0; j--) {
            var w = carplayWindows.get(j)
            if (!w.transient && !w.helper) {
                useCarplayWindow(w.toplevel, w.shellSurface)
                console.log("shell: CarPlay window closed, " + carplayWindows.count + " left")
                return
            }
        }
        carplayToplevel = null
        carplaySurface = null
        console.log("shell: CarPlay disconnected")
    }

    WaylandOutput {
        sizeFollowsWindow: true

        window: Window {
            id: win
            title: "Jeep Computer"
            visibility: Window.FullScreen
            visible: true
            color: "#050a14"

            // Boot splash stays up until the dashboard draws over it
            Image {
                anchors.fill: parent
                source: Qt.resolvedUrl("../boot/splash.png")
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
            }

            ShellSurfaceItem {
                id: dashboardItem
                anchors.fill: parent
                z: 0
                // Fade in over the boot splash (and back after a crash restart)
                opacity: shellSurface !== null ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 450; easing.type: Easing.OutCubic } }
                onSurfaceDestroyed: {
                    shellSurface = null
                    console.log("shell: dashboard disconnected")
                }
            }

            Item {
                id: carplayFrame
                z: 1
                x: comp.carplayX
                y: comp.carplayY
                width: comp.carplayW
                height: comp.carplayH
                clip: true

                // Until a phone streams, the dashboard's "Starting CarPlay"
                // page shows instead of LIVI's own screen
                opacity: comp.carplayShown ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

                Rectangle { anchors.fill: parent; color: "#000000" }

                ListModel { id: carplayWindows }

                // All LIVI windows, newest on top. Real size, never
                // stretched: a size mismatch shows as a brief black margin
                // instead of a distorted picture.
                Repeater {
                    model: carplayWindows

                    ShellSurfaceItem {
                        shellSurface: model.shellSurface
                        z: index
                        x: model.transient ? Math.round((carplayFrame.width - width) / 2) : 0
                        y: model.transient ? Math.round((carplayFrame.height - height) / 2) : 0
                        // Helpers only draw; disabled items (and their video
                        // subsurfaces) pass touches to LIVI's main window
                        enabled: !model.helper
                        // LIVI's own screen (settings) requested: the video
                        // would sit on top of it, so hide it meanwhile
                        visible: !(model.helper && comp.liviUi)
                        autoCreatePopupItems: true
                        // Keep keyboard focus on the dashboard (W / S volume keys)
                        focusOnClick: false
                        onSurfaceDestroyed: comp.removeCarplayWindow(shellSurface)
                    }
                }
            }


            // Alert strip across the top of CarPlay. Tap a warning to hide it
            // (critical alerts switch the dashboard to Home by themselves).
            Item {
                id: carplayBanner
                z: 1.5
                x: comp.carplayX
                y: comp.carplayY
                width: comp.carplayW
                height: 64
                property color accent: comp.bannerLevel === "critical" ? "#ff4d61" : "#ffb42e"
                opacity: comp.carplayShown && comp.bannerText !== ""
                         && comp.bannerDismissed !== comp.bannerText ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 180 } }

                Rectangle {
                    anchors.fill: parent
                    color: "#f208101f"
                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: Qt.rgba(carplayBanner.accent.r, carplayBanner.accent.g, carplayBanner.accent.b, 0.35) }
                            GradientStop { position: 1; color: Qt.rgba(carplayBanner.accent.r, carplayBanner.accent.g, carplayBanner.accent.b, 0.06) }
                        }
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: carplayBanner.accent }
                }
                Rectangle {
                    x: 22; anchors.verticalCenter: parent.verticalCenter
                    width: 30; height: 30; radius: 15; color: carplayBanner.accent
                    Text { anchors.centerIn: parent; text: "!"; color: "#08101f"; font.pixelSize: 20; font.bold: true }
                }
                Text {
                    x: 66; anchors.verticalCenter: parent.verticalCenter
                    text: comp.bannerText.toUpperCase()
                    color: "#eef4ff"; font.family: "Nunito Sans"; font.pixelSize: 22
                    font.weight: Font.ExtraBold; font.letterSpacing: 1.5
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 24
                    anchors.verticalCenter: parent.verticalCenter
                    text: (comp.bannerDetail ? comp.bannerDetail.toUpperCase() : "")
                          + (comp.bannerLevel === "warning" ? (comp.bannerDetail ? "  ·  " : "") + "TAP TO DISMISS" : "")
                    color: "#8ea4c8"; font.family: "Nunito Sans"; font.pixelSize: 15
                    font.bold: true; font.letterSpacing: 2
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: if (comp.bannerLevel === "warning") comp.bannerDismissed = comp.bannerText
                }
            }

            // Night dimming over everything but the volume pop-up; no input
            Rectangle {
                anchors.fill: parent
                z: 2.5
                color: "#000000"
                opacity: comp.dimLevel
                visible: opacity > 0.005
                Behavior on opacity { NumberAnimation { duration: 1500; easing.type: Easing.InOutQuad } }
            }

            // Volume pop-up (above everything)
            Item {
                id: osd
                z: 3
                width: 560
                height: 170
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height - height - 140
                opacity: 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

                Timer { id: osdTimer; interval: 1500; onTriggered: osd.opacity = 0 }

                Rectangle {
                    anchors.fill: parent
                    radius: 22
                    color: "#f513223e"
                    border.color: "#2f8cff"
                    border.width: 2
                }

                Text {
                    x: 30; y: 22
                    text: "VOLUME"
                    color: "#8ea4c8"
                    font.family: "Nunito Sans"
                    font.pixelSize: 16
                    font.bold: true
                    font.letterSpacing: 3
                }

                Text {
                    x: 30; y: 48
                    text: comp.osdOffline ? (comp.osdMessage || "CAMILLADSP OFFLINE")
                                          : (comp.osdMessage || comp.osdVolume.toFixed(1) + " dB")
                    color: comp.osdOffline ? "#ff4d61" : "#eef4ff"
                    font.family: "Nunito Sans"
                    font.pixelSize: 46
                    font.weight: Font.ExtraBold
                }

                Rectangle {
                    id: osdTrack
                    x: 30; y: parent.height - 26 - height
                    width: parent.width - 60
                    height: 12
                    radius: 6
                    color: "#1d2d4a"

                    Rectangle {
                        height: parent.height
                        radius: 6
                        width: comp.osdOffline ? 0
                             : parent.width * Math.max(0, Math.min(1, (comp.osdVolume + 60) / 60))
                        Behavior on width { NumberAnimation { duration: 120 } }
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: "#1a4fa8" }
                            GradientStop { position: 1; color: "#6cb4ff" }
                        }
                    }
                }
            }

            Repeater {
                model: ListModel { id: dialogs }

                ShellSurfaceItem {
                    z: 2
                    shellSurface: modelData
                    autoCreatePopupItems: true
                    x: Math.round((win.width - width) / 2)
                    y: Math.round((win.height - height) / 2)
                    Component.onCompleted: takeFocus()
                    onSurfaceDestroyed: {
                        dialogs.remove(index)
                        // comp can already be gone while the shell tears down
                        if (typeof comp !== "undefined")
                            comp.focusDashboard()
                    }
                }
            }
        }
    }

    XdgShell {
        onToplevelCreated: (toplevel, xdgSurface) => comp.classify(toplevel, xdgSurface)
    }

    XdgDecorationManagerV1 {
        preferredMode: XdgToplevel.ServerSideDecoration
    }
}
