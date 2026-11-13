# Stem mixer (Music page)

Four stems (drums, bass, vocals, other) are placed anywhere in the cabin by
dragging them on the Music page; placement is live while the song plays.

## Signal path

    stems -> jeep_stems (PipeWire, 8 ch) -> CamillaDSP inputs 3-10
          -> mixer (DBAP panning, audio/stems.py) -> crossovers, trims,
             time alignment, limiter -> outputs 1-8

CarPlay stays on inputs 1-2 and can be muted on its own (CARPLAY ON/MUTED on
the Music page). The old 5.1 music inputs are gone.

## Getting stems onto the Pi

**From the PC**: right-click songs > Send to > Jeep Stems. See
[stem-server.md](stem-server.md): the desktop splits them, the server holds
them until the Jeep is home.

**Files**: put each song in `~/Music/Stems/<Song name>/` with four files named
`drums`, `bass`, `vocals`, `other` (wav/flac/mp3/…; Demucs' output layout), or
drop Native Instruments `*.stem.mp4` files there. They appear in the library.

**Live from another program**: anything that plays 8-channel audio into the
`jeep_stems` sink is placed the same way. Channel order: drums L, drums R,
bass L, bass R, vocals L, vocals R, other L, other R (PipeWire AUX0-AUX7). For
example:

    ffmpeg -i drums.wav -i bass.wav -i vocals.wav -i other.wav -filter_complex \
      "join=inputs=4:channel_layout=7.1:map=0.FL-FL|0.FR-FR|1.FL-FC|1.FR-LFE|2.FL-BL|2.FR-BR|3.FL-SL|3.FR-SR" \
      -f f32le - | pw-cat --playback --raw --target jeep_stems --rate 48000 --channels 8 \
      --format f32 --channel-map AUX0,AUX1,AUX2,AUX3,AUX4,AUX5,AUX6,AUX7 -

## Library and queue

Music page, left: Artists > Albums > Songs (‹ goes back), from the folders
(Artist/Album/Song/, extra folders in between are fine), sorted by the
numbers in the names, which are hidden for display. Tap a song: it plays and
the rest of its album carries on after it. + adds a song to Up Next, which
plays before the album carries on; an album's "Queue album" adds all of it.
QUEUE shows what's next (tap to jump, × to remove); NEXT skips.

## Panning

DBAP within each frequency band's speakers: the four PRO-X6BMs (the only
speakers playing mids/highs, so they form the image) and the 2nd-row 8"
mid-bass separately; every stem also goes to the subs (bass management).
Per stem: level, stereo width, spread (source size), send to subs, mute, solo.
Presets: Stage, Surround, In the band, Wide.

**Seat** (the FOR DRIVER / PASSENGER / EVERYONE button): the DSP
time-aligns every speaker to the driver but has no level trims, so from the
driver's seat front L (0.66 m away) drowns out rear R (2.02 m). Tuned for a
seat, the DS18 gains make up each speaker's distance to it, so a stem sounds
where its puck is *from that seat*: modelled error over the cabin 49 -> 14
degrees (stems beside or behind the driver used to be heard in front).
EVERYONE is the plain, uncompensated mix. The lines from the selected puck
show what the chosen seat hears from each speaker.

**Levels match CarPlay:** the 8" pair sums in phase at 80-300 Hz, so its
gains add to one (not constant power, which played the low-mids 3 dB hot),
and every output gets CarPlay's level for it from the Audio page's Mixer tab
(the DS18s' -12.5 dB, the subs' +6 dB): tune a speaker for CarPlay and the
stems follow. Checked on real songs, the Stage preset matches CarPlay within
0.3 dB in every band. Each output in the Mixer tab also has a **Stems** row
(gain, INV, MUTE) to adjust the stems alone, on top of that.

Seat compensation makes up half of each speaker's distance to the seat
(3 dB per doubling, SEAT_COMPENSATION); full free-field compensation boosted
the far side too much.

Re-check after moving speakers or retuning: `python3 tools/stems_check.py
--balance` (the direction map, then the per-band comparison on your songs).
