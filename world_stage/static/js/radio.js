(function () {
    // Difference between the server clock and this client's clock, in
    // seconds. Refreshed on every /radio/now fetch so all clients
    // schedule song changes against the same (server) timeline.
    let clockSkew = 0;
    let current = null;
    let switchTimer = null;
    let countdownTimer = null;
    let tunedIn = false;

    // Scrobbling: only active for logged-in users who've linked an
    // account (the server sets this flag). When off, no events are sent.
    const scrobbleEnabled = !!window.SCROBBLE_ENABLED;
    let listenedSeconds = 0;     // actual playback time for `current`
    let lastTick = null;         // serverNow() at last accounting point, null while paused
    let nowPlayingSent = false;  // de-dupe now-playing per song
    let scrobbledSlot = null;    // slot_start already scrobbled (ended/timer race guard)

    const $ = (id) => document.getElementById(id);

    // No seek bar: the radio is live, so scrubbing within a track is
    // meaningless (the schedule always wins on the next tune()).
    const player = videojs('radio-player', {
        controlBar: { progressControl: false },
    });
    // The schedule is gapless (slot_end = slot_start + stored
    // duration), so a song normally ends right at its window boundary.
    // 'ended' — not a timer — is the primary switch trigger: media
    // events still fire in backgrounded mobile tabs, while setTimeout
    // is throttled to uselessness there. Without this, the radio would
    // fall silent after one song once the screen turns off.
    player.on('ended', () => {
        if (!current) return;
        if (current.slot_end - serverNow() > 2) {
            // The media ran out well before its stored duration says
            // it should; wait out the window with the countdown (the
            // timer fallback handles the switch).
            showUpNext();
            return;
        }
        tune();
    });

    function videoElement() {
        // The underlying HTMLVideoElement that video.js wraps.
        const tech = player.tech ? player.tech({ IWillNotUseThisInPlugins: true }) : null;
        if (tech && tech.el && tech.el()) return tech.el();
        // Defensive fallback if the internal API shape changes.
        return player.el().querySelector('video') || document.getElementById('radio-player');
    }

    function serverNow() {
        return Date.now() / 1000 + clockSkew;
    }

    async function fetchNow() {
        const res = await fetch('/radio/now');
        if (!res.ok) throw new Error('radio fetch failed: ' + res.status);
        const data = await res.json();
        clockSkew = data.server_time - Date.now() / 1000;
        return data;
    }

    // Count ACTUAL playback time toward the scrobble threshold — paused
    // time doesn't count, and each gap is capped so a throttled/
    // backgrounded tab can't inflate the total in one jump.
    player.on('timeupdate', () => {
        if (!current || player.paused()) { lastTick = null; return; }
        const t = serverNow();
        if (lastTick !== null) listenedSeconds += Math.max(0, Math.min(2, t - lastTick));
        lastTick = t;
    });
    player.on('play', () => { lastTick = serverNow(); });
    player.on('pause', () => { lastTick = null; });

    function postScrobble(path, slot) {
        const body = JSON.stringify({ song_id: slot.song.id, started_at: slot.slot_start });
        // Plain keepalive fetch, NOT navigator.sendBeacon: privacy/ad
        // blockers neutralise the Beacon API wholesale (it's the classic
        // telemetry transport), so beacons were silently dropped as
        // "blocked:other" and no scrobble ever reached the server. We
        // only fire during active page life — song start and boundary,
        // never on unload — so keepalive fetch is all we need.
        fetch(path, {
            method: 'POST',
            keepalive: true,
            headers: { 'Content-Type': 'application/json' },
            body,
        }).catch(() => {});
    }

    function maybeScrobble(slot) {
        // Scrobble the outgoing song if the user heard enough of it.
        // Last.fm rules: longer than 30s, and played for at least half
        // its length or 4 minutes, whichever comes first.
        if (!scrobbleEnabled || !slot) return;
        if (slot.slot_start === scrobbledSlot) return;
        const dur = slot.song.duration;
        if (!dur || dur <= 30) return;
        if (listenedSeconds < Math.min(dur / 2, 240)) return;
        scrobbledSlot = slot.slot_start;
        postScrobble('/radio/scrobble', slot);
    }

    function renderMeta(data) {
        const song = data.song;
        $('np-title').textContent = song.title || 'Untitled';
        $('np-artist').textContent = song.artist || '';
        const origin = $('np-origin');
        origin.textContent = '';
        if (song.year_id >= 0) {
            const link = document.createElement('a');
            link.href = '/country/' + song.cc + '/' + song.year_id;
            link.textContent = song.country + ' ' + song.year;
            origin.appendChild(link);
        } else {
            origin.textContent = song.country + ' ' + song.year;
        }
        $('now-playing').style.display = 'flex';
    }

    function resync() {
        // Jump back to the live position, e.g. after a pause from the
        // lock screen: a radio resumes at "now", not where it left off.
        if (current && serverNow() < current.slot_end) {
            player.currentTime(Math.max(0, serverNow() - current.slot_start));
            const p = player.play();
            if (p && typeof p.catch === 'function') p.catch(() => {});
        } else {
            tune();
        }
    }

    function updateMediaSession(data) {
        // Lock-screen / notification metadata and controls. Also what
        // makes mobile OSes treat the page as a proper audio app.
        if (!('mediaSession' in navigator)) return;
        const song = data.song;
        navigator.mediaSession.metadata = new MediaMetadata({
            title: song.title || 'Untitled',
            artist: song.artist || '',
            album: 'World Stage Radio',
            artwork: song.poster ? [{ src: song.poster }] : [],
        });
        navigator.mediaSession.setActionHandler('play', resync);
        navigator.mediaSession.setActionHandler('pause', () => player.pause());
        // It's live radio: no seeking, no track skipping.
        for (const action of ['seekbackward', 'seekforward', 'seekto',
                              'previoustrack', 'nexttrack']) {
            try {
                navigator.mediaSession.setActionHandler(action, null);
            } catch (e) { /* action not supported by this browser */ }
        }
        if (navigator.mediaSession.setPositionState) {
            try {
                navigator.mediaSession.setPositionState({
                    duration: song.duration,
                    position: Math.min(song.duration, Math.max(0, data.offset)),
                    playbackRate: 1,
                });
            } catch (e) { /* invalid state values; cosmetic only */ }
        }
    }

    function showUpNext() {
        $('up-next').style.display = 'flex';
        clearInterval(countdownTimer);
        const tick = () => {
            const left = Math.max(0, Math.round(current.slot_end - serverNow()));
            const m = Math.floor(left / 60);
            const s = String(left % 60).padStart(2, '0');
            $('up-next-countdown').textContent = m + ':' + s;
        };
        tick();
        countdownTimer = setInterval(tick, 1000);
    }

    function hideUpNext() {
        $('up-next').style.display = 'none';
        clearInterval(countdownTimer);
    }

    function playSong(song) {
        const videoEl = videoElement();

        // Chrome rejects sources whose URL ends in .mov when set via
        // `videoEl.src = '…'` — which is what video.js's `player.src()`
        // does internally. The same URL works fine as a `<source>`
        // child with an explicit `type` attribute, so the source is
        // swapped by hand (same workaround as the show play page).
        videoEl.removeAttribute('src');
        videoEl.querySelectorAll('source').forEach((s) => s.remove());
        const source = document.createElement('source');
        source.src = song.url;
        source.type = song.mime;
        videoEl.appendChild(source);

        // Poster via video.js so its overlay stays in sync. For
        // audio-only files the poster is the cover art and must stay
        // up during playback, not just before it.
        player.poster(song.poster || '');
        if (player.audioPosterMode) {
            player.audioPosterMode(song.mime.startsWith('audio'));
        }

        // Swap subtitles: drop the previous song's track, attach this
        // one's, and show it by default.
        const remote = player.remoteTextTracks();
        for (let i = remote.length - 1; i >= 0; i--) {
            player.removeRemoteTextTrack(remote[i]);
        }
        if (song.vtt) {
            const trackEl = player.addRemoteTextTrack({
                kind: 'subtitles',
                src: song.vtt,
                label: 'Subtitles',
                default: true,
            }, true);
            if (trackEl && trackEl.track) trackEl.track.mode = 'showing';
        }

        videoEl.load();
        // Seek only once the new media's metadata is in: a currentTime
        // set while the source is still loading is discarded, and a
        // play() issued before then is aborted by the load. The offset
        // is recomputed here so the seek lands where the radio is at
        // this instant, not where it was when /radio/now answered.
        player.one('loadedmetadata', () => {
            player.currentTime(Math.max(0, serverNow() - current.slot_start));
            const p = player.play();
            if (p && typeof p.catch === 'function') {
                p.catch(() => {
                    // Unmuted autoplay can be rejected if the tune-in
                    // gesture has gone stale; retry muted rather than
                    // stalling the radio.
                    player.muted(true);
                    const r = player.play();
                    if (r && typeof r.catch === 'function') r.catch(() => {});
                });
            }
        });
    }

    function scheduleSwitch() {
        clearTimeout(switchTimer);
        // Half a second past the boundary so the server is already in
        // the next slot when we ask.
        const ms = Math.max(1000, (current.slot_end - serverNow()) * 1000 + 500);
        switchTimer = setTimeout(tune, ms);
    }

    async function tune() {
        // 'ended' and the fallback timer can race within the handoff
        // window; whichever fires first owns the switch.
        clearTimeout(switchTimer);
        let data;
        try {
            data = await fetchNow();
        } catch (e) {
            // Transient failure (or no songs yet): retry without
            // losing the beat — the schedule is recomputed on every
            // fetch, so a late retry still lands on the right song.
            $('radio-error').textContent = 'Lost the signal, retrying…';
            $('radio-error').style.display = 'block';
            switchTimer = setTimeout(tune, 5000);
            return;
        }
        $('radio-error').style.display = 'none';
        if (current && data.slot_start === current.slot_start) {
            // 'ended' beat the server clock to the boundary by a hair
            // and the same window came back; ask again just past it.
            // Not a real switch — don't scrobble or reset counters.
            current = data;
            const ms = Math.max(250, (data.slot_end - serverNow()) * 1000 + 250);
            switchTimer = setTimeout(tune, ms);
            return;
        }
        maybeScrobble(current);  // the song being replaced, if heard enough
        hideUpNext();
        current = data;
        listenedSeconds = 0;
        lastTick = null;
        nowPlayingSent = false;
        if (scrobbleEnabled && tunedIn) {
            nowPlayingSent = true;
            postScrobble('/radio/now-playing', data);
        }
        renderMeta(data);
        updateMediaSession(data);
        player.ready(() => playSong(data.song));
        scheduleSwitch();
    }

    $('tune-in-button').addEventListener('click', () => {
        tunedIn = true;
        $('tune-in').style.display = 'none';
        tune();
    });

    // Background tabs throttle timers, so a suspended tab can wake up
    // mid-way through a later song. Re-tune immediately: the fetch
    // recomputes the schedule, so we land exactly where the radio is.
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && tunedIn && current && serverNow() > current.slot_end) {
            tune();
        }
    });
})();
