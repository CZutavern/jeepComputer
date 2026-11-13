# Qt Wayland compositor shell libraries

The PySide6 6.11.2 wheel ships the QtWayland.Compositor QML plugins but not
these four libraries they link against:

    libQt6WaylandCompositorXdgShell.so.6
    libQt6WaylandCompositorWLShell.so.6
    libQt6WaylandCompositorIviapplication.so.6
    libQt6WaylandCompositorPresentationTime.so.6

They come from the official Qt 6.11.2 linux_gcc_arm64 build (the same build
PySide6 bundles; libQt6WaylandCompositor.so.6 is byte-identical). Fetched with:

    pip install aqtinstall
    aqt install-qt linux_arm64 desktop 6.11.2 linux_gcc_arm64 \
        -m qtwaylandcompositor --archives qtwayland --external 7z

Use with LD_LIBRARY_PATH=<this dir>/lib. If PySide6 is upgraded, fetch the
matching Qt version again.
