(function () {
    // Difference between the server clock and this client's clock, in
    // seconds. Refreshed on every /radio/now fetch so all clients
    // schedule song changes against the same (server) timeline.
    let clockSkew = 0;
    let current = null;
    let switchTimer = null;
    let countdownTimer = null;
    let tunedIn = false;

    const $ = (id) => document.getElementById(id);

    const player = videojs('radio-player');
    // The schedule is gapless (slot_end = slot_start + stored
    // duration), so a song normally ends right as the next one is
    // fetched. The countdown overlay is only worth showing when the
    // media ran out well before its stored duration says it should.
    player.on('ended', () => {
        if (current && current.slot_end - serverNow() > 2) showUpNext();
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
        let data;
        try {
            data = await fetchNow();
        } catch (e) {
            // Transient failure (or no songs yet): retry without
            // losing the beat — the schedule is recomputed on every
            // fetch, so a late retry still lands on the right song.
            $('radio-error').textContent = 'Lost the signal, retrying…';
            $('radio-error').style.display = 'block';
            clearTimeout(switchTimer);
            switchTimer = setTimeout(tune, 5000);
            return;
        }
        $('radio-error').style.display = 'none';
        hideUpNext();
        current = data;
        renderMeta(data);
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
