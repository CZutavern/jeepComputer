"""
Music: the stem mixer. Place drums / bass / vocals / other anywhere in the
cabin, live while the song plays; CarPlay can be muted separately.

Stem songs live in ~/Music/Stems (see audio/stem_player.py); anything else
that plays into the jeep_stems sink (8 ch) is placed the same way.

Library: Artists > Albums > Songs. Tap a song to play it (the rest of its
album carries on after it); + adds a song to Up Next, which plays before
the album carries on (like Apple Music). QUEUE shows what's coming.
"""

import json
import math
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy,
                               QSlider, QVBoxLayout, QWidget)

from audio import stems as S
from audio.camilla_client import CamillaClient
from audio.stem_player import StemPlayer, clean_name, find_songs
from dashboard.pages.base import DashboardPage
from dashboard.theme import COLORS, make_font
from dashboard.widgets.controls import BUTTON_STYLE, Panel, button, caption, text_label
from dashboard.widgets.stem_stage import StemStage

LIVI_MEDIA_FILE = Path.home() / ".config/LIVI/mediaData.json"
SYNC_STATUS = Path.home() / ".cache/jeep_computer/stems_sync.json"

LIST_STYLE = f"""
    QListWidget {{ background: transparent; border: none; color: {COLORS["text"]}; outline: none; }}
    QListWidget::item {{ padding: 0px 16px; margin-bottom: 8px; border-radius: 12px;
        border: 1px solid {COLORS["border"]}; background: {COLORS["background_top"]}; }}
    QListWidget::item:selected {{ background: {COLORS["accent_dark"]}; border: 1px solid {COLORS["accent"]};
        color: {COLORS["text"]}; }}
    QListWidget::item:disabled {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: {COLORS["border_bright"]}; border-radius: 4px; min-height: 40px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""

SLIDER_STYLE = f"""
    QSlider::groove:horizontal {{ height: 10px; border-radius: 5px; background: {COLORS["border"]}; }}
    QSlider::sub-page:horizontal {{ border-radius: 5px; background: {COLORS["accent"]}; }}
    QSlider::handle:horizontal {{ width: 34px; height: 34px; margin: -12px 0; border-radius: 17px;
        background: {COLORS["text"]}; border: 3px solid {COLORS["accent"]}; }}
"""


def fmt_time(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class SeekBar(QWidget):
    """Progress bar; tap to seek."""

    seek = Signal(float)            # fraction 0..1

    def __init__(self):
        super().__init__()
        self.fraction = 0.0
        self.enabled_ = False
        self.setFixedHeight(40)

    def set_fraction(self, fraction, enabled=True):
        self.fraction, self.enabled_ = max(0.0, min(1.0, fraction)), enabled
        self.update()

    def mousePressEvent(self, event):
        if self.enabled_:
            self.seek.emit(max(0.0, min(1.0, event.position().x() / max(1, self.width()))))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0, self.height() / 2 - 5, self.width(), 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["border"]))
        p.drawRoundedRect(track, 5, 5)
        if self.enabled_:
            p.setBrush(QColor(COLORS["accent"]))
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * self.fraction, 10), 5, 5)
            p.setBrush(QColor(COLORS["text"]))
            p.drawEllipse(QRectF(track.width() * self.fraction - 9, self.height() / 2 - 9, 18, 18))
        p.end()


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class ElidedLabel(QLabel):
    """One line, cut with "…" instead of widening the panel."""

    def __init__(self, text="", size=19, weight=QFont.DemiBold, color=None):
        super().__init__(text)
        self.setFont(make_font(size, weight))
        self.setStyleSheet(f"color: {color or COLORS['text']}; background: transparent;")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(10)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignVCenter), text)
        p.end()


class LibraryRow(QWidget):
    """A list row: title, detail on the right, optional + / × button. Taps
    outside the button go through to the list (the row ignores them)."""

    action = Signal()

    def __init__(self, title, detail="", action_text=None, title_color=None):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 8 if action_text else 16, 0)
        row.setSpacing(10)
        row.addWidget(ElidedLabel(title, 19, QFont.DemiBold, title_color), stretch=1)
        if detail:
            row.addWidget(text_label(detail, 14, QFont.DemiBold, COLORS["text_secondary"]))
        if action_text:
            b = button(action_text, height=46, size=20)
            b.setFixedWidth(56)
            b.clicked.connect(self.action.emit)
            row.addWidget(b)


class ParamSlider(QWidget):
    """Caption, value text and a slider (integer steps, mapped by `to_value`)."""

    moved = Signal(float)
    released = Signal()

    def __init__(self, label, minimum, maximum, fmt):
        super().__init__()
        self.fmt = fmt
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        row = QHBoxLayout()
        row.addWidget(text_label(label, 16, QFont.DemiBold, COLORS["text_secondary"]))
        row.addStretch(1)
        self.value_label = text_label("", 17, QFont.ExtraBold)
        row.addWidget(self.value_label)
        col.addLayout(row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setFixedHeight(44)
        self.slider.setStyleSheet(SLIDER_STYLE)
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(self.released.emit)
        col.addWidget(self.slider)

    def set(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value))
        self.slider.blockSignals(False)
        self.value_label.setText(self.fmt(value))

    def _changed(self, value):
        self.value_label.setText(self.fmt(value))
        self.moved.emit(float(value))
        if not self.slider.isSliderDown():      # tap/step: no release event follows
            self.released.emit()


class StemRow(QWidget):
    """Colour, name (tap to select), level, mute and solo."""

    selected = Signal(str)
    toggled = Signal(str, str, bool)     # stem, "mute"/"solo", on

    def __init__(self, name):
        super().__init__()
        self.name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        chip = QLabel()
        chip.setFixedSize(10, 44)
        chip.setStyleSheet(f"background: {S.STEM_COLORS[name]}; border-radius: 5px;")
        row.addWidget(chip)
        self.name_button = button(S.STEM_LABELS[name].upper(), height=48, size=16, checkable=True)
        self.name_button.clicked.connect(lambda: self.selected.emit(self.name))
        row.addWidget(self.name_button, stretch=3)
        self.level = text_label("0 dB", 15, QFont.Bold, COLORS["text_secondary"])
        self.level.setFixedWidth(64)
        self.level.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.level)
        self.mute = button("M", height=48, size=16, checkable=True)
        self.solo = button("S", height=48, size=16, checkable=True)
        for b, key in ((self.mute, "mute"), (self.solo, "solo")):
            b.setFixedWidth(56)
            b.toggled.connect(lambda on, k=key: self.toggled.emit(self.name, k, on))
            row.addWidget(b)

    def refresh(self, stem, is_selected):
        self.name_button.setChecked(is_selected)
        self.level.setText(f"{stem['level_db']:+.0f} dB")
        for b, key in ((self.mute, "mute"), (self.solo, "solo")):
            b.blockSignals(True)
            b.setChecked(bool(stem.get(key)))
            b.blockSignals(False)


class MusicPlayerPage(DashboardPage):

    def __init__(self):
        super().__init__("Music", "Stems placed in the cabin")

        self.state = S.live_state()
        self.renderer = S.live_renderer(CamillaClient)
        self.player = StemPlayer()
        self.now_title = None
        self.now_detail = "Tap a song"
        self.meter_client = None
        self.library = {}           # artist -> album -> [songs], sorted
        self.nav = ("artists",)     # ("albums", artist) / ("songs", artist, album)
        self.showing_queue = False
        self.up_next = []           # songs added with +, played first
        self.continuing = []        # the rest of the album a tapped song is from
        self.continuing_from = ""

        body = QHBoxLayout()
        body.setSpacing(self.SPACING)
        self.layout_.addLayout(body, stretch=1)

        body.addLayout(self.build_left(), stretch=5)
        self.stage = StemStage()
        self.stage.set_state(self.state)
        self.stage.changed.connect(self.on_stage_changed)
        self.stage.selected_changed.connect(self.select_stem)
        body.addWidget(self.stage, stretch=9)
        body.addWidget(self.build_right(), stretch=5)

        self.select_stem("vocals")
        self.renderer.push(self.state)           # make the DSP match the saved placement

        self.position_timer = QTimer(self)
        self.position_timer.timeout.connect(self.update_transport)
        self.position_timer.start(250)
        self.meter_timer = QTimer(self)
        self.meter_timer.timeout.connect(self.update_meters)
        self.carplay_timer = QTimer(self)
        self.carplay_timer.timeout.connect(self.update_carplay_media)
        self.carplay_timer.start(2000)

        self.load_songs()

    # ------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------

    def build_left(self):
        col = QVBoxLayout()
        col.setSpacing(self.SPACING)

        library = Panel()
        lib = QVBoxLayout(library)
        lib.setContentsMargins(20, 16, 20, 16)
        lib.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.back_button = button("‹", height=40, size=22)
        self.back_button.setFixedWidth(48)
        self.back_button.clicked.connect(self.nav_back)
        head.addWidget(self.back_button)
        self.library_caption = caption("Library")
        self.library_caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(self.library_caption, stretch=1)
        self.queue_button = button("QUEUE", height=40, size=13, checkable=True)
        self.queue_button.setFixedWidth(104)
        self.queue_button.toggled.connect(self.show_queue)
        head.addWidget(self.queue_button)
        self.sync_button = button("SYNC", height=40, size=13)
        self.sync_button.setFixedWidth(80)
        self.sync_button.clicked.connect(self.sync_now)
        head.addWidget(self.sync_button)
        lib.addLayout(head)
        self.songs = QListWidget()
        self.songs.setFont(make_font(19, QFont.DemiBold))
        self.songs.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.songs.setFocusPolicy(Qt.NoFocus)
        self.songs.setStyleSheet(LIST_STYLE)
        self.songs.itemClicked.connect(self.item_clicked)
        lib.addWidget(self.songs, stretch=1)
        self.empty_label = text_label("Send songs from the PC (Send to > Jeep Stems) or put stem folders "
                                      "in ~/Music/Stems", 16, QFont.DemiBold, COLORS["muted"])
        self.empty_label.setWordWrap(True)
        lib.addWidget(self.empty_label)
        self.sync_label = text_label("", 14, QFont.DemiBold, COLORS["text_secondary"])
        self.sync_label.setWordWrap(True)
        lib.addWidget(self.sync_label)
        self.sync_seen = None
        col.addWidget(library, stretch=3)

        transport = Panel()
        t = QVBoxLayout(transport)
        t.setContentsMargins(20, 16, 20, 18)
        t.setSpacing(8)
        t.addWidget(caption("Now playing"))
        self.title_label = text_label("Nothing playing", 24, QFont.ExtraBold)
        self.title_label.setWordWrap(True)
        t.addWidget(self.title_label)
        self.kind_label = text_label("Tap a song", 15, QFont.DemiBold, COLORS["text_secondary"])
        t.addWidget(self.kind_label)
        self.seekbar = SeekBar()
        self.seekbar.seek.connect(self.seek)
        t.addWidget(self.seekbar)
        self.time_label = text_label("", 15, QFont.Bold, COLORS["text_secondary"])
        t.addWidget(self.time_label)
        buttons = QHBoxLayout()
        self.play_button = button("PLAY", height=62, size=19)
        self.play_button.clicked.connect(self.play_pause)
        self.next_button = button("NEXT", height=62, size=19)
        self.next_button.clicked.connect(self.next_track)
        self.stop_button = button("STOP", height=62, size=19)
        self.stop_button.clicked.connect(self.stop_all)
        buttons.addWidget(self.play_button, stretch=2)
        buttons.addWidget(self.next_button, stretch=1)
        buttons.addWidget(self.stop_button, stretch=1)
        t.addLayout(buttons)
        self.carplay_label = text_label("", 14, QFont.DemiBold, COLORS["accent_bright"])
        self.carplay_label.setWordWrap(True)
        t.addWidget(self.carplay_label)
        col.addWidget(transport, stretch=2)
        return col

    def build_right(self):
        panel = Panel()
        col = QVBoxLayout(panel)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(8)
        col.addWidget(caption("Stems"))

        self.rows = {}
        for name in S.STEMS:
            row = StemRow(name)
            row.selected.connect(self.select_stem)
            row.toggled.connect(self.toggle_stem)
            self.rows[name] = row
            col.addWidget(row)

        col.addSpacing(6)
        self.detail_caption = caption("Vocals")
        col.addWidget(self.detail_caption)
        self.level = ParamSlider("Level", -24, 6, lambda v: f"{v:+.0f} dB")
        self.width_ = ParamSlider("Stereo width", 0, 100, lambda v: f"{v:.0f} %")
        self.spread = ParamSlider("Spread", 0, 100, lambda v: f"{v:.0f} %")
        self.sub = ParamSlider("To subs", 0, 100, lambda v: f"{v:.0f} %")
        for slider, key, scale in ((self.level, "level_db", 1), (self.width_, "width", 100),
                                   (self.spread, "spread", 100), (self.sub, "sub", 100)):
            slider.moved.connect(lambda v, k=key, sc=scale: self.set_param(k, v / sc))
            slider.released.connect(self.persist)
            col.addWidget(slider)

        col.addStretch(1)
        presets = QGridLayout()
        presets.setSpacing(8)
        for i, (key, preset) in enumerate(S.PRESETS.items()):
            b = button(preset["label"].upper(), height=42, size=13)
            b.clicked.connect(lambda _=False, k=key: self.apply_preset(k))
            presets.addWidget(b, 0, i)
        col.addLayout(presets)

        # CarPlay and the stems muted independently (volume keys still
        # set the overall level of both)
        mutes = QHBoxLayout()
        mutes.setSpacing(8)
        self.carplay_mute = button("CARPLAY ON", height=50, size=15, checkable=True)
        self.carplay_mute.toggled.connect(lambda on: self.set_mute("carplay_mute", on))
        self.stems_mute = button("STEMS ON", height=50, size=15, checkable=True)
        self.stems_mute.toggled.connect(lambda on: self.set_mute("stems_mute", on))
        # Which seat the placement is tuned for (see audio/stems.py SEATS)
        self.seat = button("", height=50, size=15)
        self.seat.clicked.connect(self.next_seat)
        mutes.addWidget(self.carplay_mute)
        mutes.addWidget(self.stems_mute)
        mutes.addWidget(self.seat)
        col.addLayout(mutes)
        self.refresh_mutes()

        self.master = ParamSlider("Stems master", -24, 6, lambda v: f"{v:+.0f} dB")
        self.master.moved.connect(lambda v: self.set_master(v))
        self.master.released.connect(self.persist)
        self.master.set(self.state.get("master_db", 0.0))
        col.addWidget(self.master)
        self.status_label = text_label("", 13, QFont.DemiBold, COLORS["muted"])
        self.status_label.setWordWrap(True)
        col.addWidget(self.status_label)
        return panel

    # ------------------------------------------------------------
    # Stem controls
    # ------------------------------------------------------------

    def select_stem(self, name):
        self.selected = name
        self.stage.set_selected(name)
        stem = self.state["stems"][name]
        self.detail_caption.setText(S.STEM_LABELS[name])
        self.level.set(stem["level_db"])
        self.width_.set(stem["width"] * 100)
        self.spread.set(stem["spread"] * 100)
        self.sub.set(stem.get("sub", 1.0) * 100)
        self.refresh_rows()

    def refresh_rows(self):
        for name, row in self.rows.items():
            row.refresh(self.state["stems"][name], name == self.selected)

    def set_param(self, key, value):
        self.state["stems"][self.selected][key] = value
        self.state["preset"] = "custom"
        self.stage.update()
        self.refresh_rows()
        self.renderer.push(self.state)

    def set_master(self, value):
        self.state["master_db"] = value
        self.renderer.push(self.state)

    def set_mute(self, key, on):
        self.state[key] = on
        self.refresh_mutes()
        self.persist()

    def next_seat(self):
        seats = list(S.SEATS)
        current = self.state.get("seat", "driver")
        self.state["seat"] = seats[(seats.index(current) + 1) % len(seats)] if current in seats else "driver"
        self.refresh_mutes()
        self.stage.update()
        self.persist()

    def refresh_mutes(self):
        self.seat.setText(f"FOR {self.state.get('seat', 'driver').upper()}")
        for b, key, name in ((self.carplay_mute, "carplay_mute", "CARPLAY"),
                             (self.stems_mute, "stems_mute", "STEMS")):
            muted = bool(self.state.get(key))
            b.blockSignals(True)
            b.setChecked(muted)
            b.blockSignals(False)
            b.setText(f"{name} MUTED" if muted else f"{name} ON")
            b.setStyleSheet(BUTTON_STYLE + (
                f"QPushButton:checked {{ background: {COLORS['critical']}; border-color: {COLORS['critical']}; }}"
                if muted else ""))

    def toggle_stem(self, name, key, on):
        self.state["stems"][name][key] = on
        self.stage.update()
        self.persist()

    def apply_preset(self, key):
        self.state["stems"] = json.loads(json.dumps(S.PRESETS[key]["stems"]))
        self.state["preset"] = key
        self.stage.set_state(self.state)
        self.select_stem(self.selected)
        self.persist()

    def on_stage_changed(self, finished):
        self.state["preset"] = "custom"
        if finished:
            self.persist()
        else:
            self.renderer.push(self.state)

    def persist(self):
        S.save_state(self.state)
        self.renderer.push(self.state, persist=True)

    # ------------------------------------------------------------
    # Library and playback
    # ------------------------------------------------------------

    def load_songs(self):
        self.song_objects = find_songs()
        self.library = {}
        for song in self.song_objects:
            self.library.setdefault(song.artist, {}).setdefault(song.album, []).append(song)
        # Queued songs: same files, fresh objects; drop any that are gone
        by_key = {song.key: song for song in self.song_objects}
        self.up_next = [by_key[s_.key] for s_ in self.up_next if s_.key in by_key]
        self.continuing = [by_key[s_.key] for s_ in self.continuing if s_.key in by_key]
        # Still there? (a sync can rename or remove folders)
        if self.nav[0] != "artists" and self.nav[1] not in self.library:
            self.nav = ("artists",)
        elif self.nav[0] == "songs" and self.nav[2] not in self.library[self.nav[1]]:
            self.nav = ("albums", self.nav[1])
        self.empty_label.setVisible(not self.song_objects)
        self.render_list()

    # Library navigation --------------------------------------------

    def render_list(self):
        self.songs.clear()
        self.queue_button.setText(f"QUEUE {len(self.up_next)}" if self.up_next else "QUEUE")
        if self.showing_queue:
            self.back_button.setVisible(False)
            self.render_queue()
            return
        self.back_button.setVisible(self.nav[0] != "artists")
        if self.nav[0] == "artists":
            total = len(self.song_objects)
            self.library_caption.setText(f"LIBRARY · {total} SONG{'' if total == 1 else 'S'}")
            for artist, albums in self.library.items():
                count = sum(len(songs) for songs in albums.values())
                self.add_row(("artist", artist), artist, f"{plural(count, 'song')} ›")
        elif self.nav[0] == "albums":
            artist = self.nav[1]
            self.library_caption.setText(artist.upper())
            for album, songs in self.library[artist].items():
                self.add_row(("album", artist, album), clean_name(album), f"{plural(len(songs), 'song')} ›")
        else:
            artist, album = self.nav[1], self.nav[2]
            self.library_caption.setText(clean_name(album).upper())
            songs = self.library[artist][album]
            self.add_row(("play_album", artist, album), "▶  Play album", color=COLORS["accent_bright"])
            self.add_row(("queue_album", artist, album), "+  Queue album", color=COLORS["accent_bright"])
            for song in songs:
                row = self.add_row(("song", song), song.name, action="+")
                row.action.connect(lambda s_=song: self.queue_songs([s_]))
                if self.player.song is not None and self.player.song.key == song.key \
                        and self.player.state != "stopped":
                    self.songs.setCurrentRow(self.songs.count() - 1)

    def add_row(self, data, title, detail="", action=None, color=None, header=False):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, data)
        item.setSizeHint(QSize(0, 40 if header else 62))
        if header:
            item.setFlags(Qt.NoItemFlags)
        self.songs.addItem(item)
        row = LibraryRow(title, detail, action, COLORS["text_secondary"] if header else color)
        self.songs.setItemWidget(item, row)
        return row

    def item_clicked(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        kind = data[0]
        if kind == "artist":
            albums = self.library.get(data[1], {})
            # One album: straight to its songs
            self.nav = ("songs", data[1], next(iter(albums))) if len(albums) == 1 else ("albums", data[1])
            self.render_list()
        elif kind == "album":
            self.nav = ("songs", data[1], data[2])
            self.render_list()
        elif kind == "play_album":
            songs = self.library[data[1]][data[2]]
            self.play_song(songs[0], songs[1:], data[2])
        elif kind == "queue_album":
            self.queue_songs(self.library[data[1]][data[2]])
        elif kind == "song":
            song = data[1]
            album = self.library.get(song.artist, {}).get(song.album, [])
            rest = album[album.index(song) + 1:] if song in album else []
            self.play_song(song, rest, song.album)
        elif kind == "up_next":
            song = self.up_next.pop(data[1])
            self.play_song(song, self.continuing, self.continuing_from)
        elif kind == "continuing":
            song = self.continuing[data[1]]
            self.play_song(song, self.continuing[data[1] + 1:], self.continuing_from)

    def nav_back(self):
        if self.nav[0] == "songs" and len(self.library.get(self.nav[1], {})) > 1:
            self.nav = ("albums", self.nav[1])
        else:
            self.nav = ("artists",)
        self.render_list()

    # Queue ------------------------------------------------------------

    def show_queue(self, on):
        self.showing_queue = on
        self.render_list()

    def render_queue(self):
        self.library_caption.setText("UP NEXT")
        if self.player.state != "stopped" and self.now_title:
            self.add_row(None, f"NOW PLAYING · {self.now_title}", header=True)
        if not self.up_next and not self.continuing:
            self.add_row(None, "Nothing queued. Tap + on a song to add it.", header=True)
        for i, song in enumerate(self.up_next):
            row = self.add_row(("up_next", i), song.name, clean_name(song.album) if song.album != "Songs" else "",
                               action="×")
            row.action.connect(lambda i=i: self.unqueue(i))
        if self.continuing:
            self.add_row(None, f"THEN · {clean_name(self.continuing_from).upper()}", header=True)
            for i, song in enumerate(self.continuing):
                self.add_row(("continuing", i), song.name)

    def queue_songs(self, songs):
        self.up_next.extend(songs)
        self.render_list()
        # A moment's confirmation, then back to what's playing
        self.kind_label.setText(f"Queued {plural(len(songs), 'song')}" if len(songs) > 1
                                else f"Queued: {songs[0].name}")
        QTimer.singleShot(3000, lambda: self.kind_label.setText(self.now_detail))

    def unqueue(self, index):
        if 0 <= index < len(self.up_next):
            del self.up_next[index]
        self.render_list()

    # Playback ----------------------------------------------------------

    def play_song(self, song, continuing=None, continuing_from=""):
        if continuing is not None:
            self.continuing = list(continuing)
            self.continuing_from = continuing_from
        self.player.stop()
        self.player.play(song)
        self.now_title = song.name
        self.title_label.setText(self.now_title)
        artist = "" if song.artist == "Unsorted" else song.artist
        self.now_detail = (" · ".join(x for x in (artist, clean_name(song.album)) if x and x != "Songs")
                           or "4 stems · placed in the cabin")
        self.kind_label.setText(self.now_detail)
        self.render_list()
        self.update_transport()

    def next_track(self):
        """The next queued song: Up Next first, then the rest of the album."""
        if self.up_next:
            self.play_song(self.up_next.pop(0))
        elif self.continuing:
            self.play_song(self.continuing[0], self.continuing[1:], self.continuing_from)
        else:
            self.stop_all()
            self.render_list()

    def play_pause(self):
        if self.player.state in ("playing", "paused"):
            self.player.toggle()
        elif self.up_next or self.continuing:
            self.next_track()
        self.update_transport()

    def stop_all(self):
        self.player.stop()
        self.now_title = None
        self.now_detail = "Tap a song"
        self.update_transport()

    def seek(self, fraction):
        if self.player.song is not None and self.player.state != "stopped":
            self.player.seek(fraction * self.player.song.duration)

    def update_transport(self):
        if self.player.poll():
            self.now_title = None
            self.next_track()           # song ended: the queue carries on
        playing_stems = self.player.state in ("playing", "paused")
        self.next_button.setEnabled(bool(self.up_next or self.continuing))
        if playing_stems:
            pos, dur = self.player.position(), self.player.song.duration or 1
            self.seekbar.set_fraction(pos / dur)
            self.time_label.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
            self.play_button.setText("PAUSE" if self.player.state == "playing" else "PLAY")
        else:
            self.seekbar.set_fraction(0, enabled=False)
            self.play_button.setText("PLAY")
            self.time_label.setText("")
            if self.now_title is None:
                external = self.external_input()
                self.title_label.setText("External stem input" if external else "Nothing playing")
                self.kind_label.setText("Placed in the cabin" if external else "Tap a song")
        status = {"ok": "", "offline": "CamillaDSP isn't reachable",
                  "no-stem-inputs": "The active DSP config has no stem inputs: run tools/enable_stems.py",
                  "idle": ""}.get(self.renderer.status, "")
        self.status_label.setText(status)

    # ------------------------------------------------------------
    # Levels (glow on the pucks) and CarPlay
    # ------------------------------------------------------------

    def external_input(self):
        return any(v > 0.05 for v in self.stage.levels.values())

    def update_meters(self):
        try:
            if self.meter_client is None:
                self.meter_client = CamillaClient()
                self.meter_client.connect()
            rms = self.meter_client.call("GetCaptureSignalRms") or []
        except Exception:
            self.meter_client = None
            return
        levels = {}
        for i, name in enumerate(S.STEMS):
            ch = S.FIRST_STEM_INPUT + 2 * i
            db = max(rms[ch], rms[ch + 1]) if len(rms) > ch + 1 else -100
            levels[name] = max(0.0, min(1.0, (db + 50) / 44))
        self.stage.set_levels(levels)

    def sync_now(self):
        """Pull new stems from the stem server now (jeep-stems-sync.service)."""
        QProcess.startDetached("systemctl", ["--user", "start", "--no-block", "jeep-stems-sync.service"])
        self.sync_label.setText("Syncing…")

    def update_sync_status(self):
        try:
            status = json.loads(SYNC_STATUS.read_text())
        except (OSError, ValueError):
            return
        if status.get("time") == self.sync_seen:
            return
        self.sync_seen = status.get("time")
        when = time.strftime("%-I:%M %p", time.localtime(status.get("time", 0)))
        self.sync_label.setText(f"{status.get('message', '')} · {when}")
        self.sync_label.setStyleSheet(f"color: {COLORS['text_secondary'] if status.get('ok') else COLORS['warning']};"
                                      " background: transparent;")
        if status.get("new"):
            self.load_songs()

    def update_carplay_media(self):
        self.update_sync_status()
        if not self.isVisible():
            return
        try:
            payload = json.loads(LIVI_MEDIA_FILE.read_text()).get("payload", {})
        except (OSError, ValueError):
            payload = {}
        media = payload.get("media") or {}
        song, artist = media.get("MediaSongName"), media.get("MediaArtistName")
        if payload.get("error") or not song or song == "-":
            self.carplay_label.setText("")
        else:
            self.carplay_label.setText("CarPlay: " + " — ".join(x for x in (song, artist) if x and x != "-"))

    def showEvent(self, event):
        super().showEvent(event)
        self.load_songs()
        self.meter_timer.start(120)
        self.update_carplay_media()

    def hideEvent(self, event):
        self.meter_timer.stop()
        self.stage.set_levels({name: 0.0 for name in S.STEMS})
