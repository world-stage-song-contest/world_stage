(function () {
    'use strict';

    const STALL_TIMEOUT_MS = 10000;

    function audioMimeType(type) {
        return type.startsWith('video/') ? `audio/${type.slice('video/'.length)}` : type;
    }

    function sourceFromPlayer(player) {
        const tech = player.tech && player.tech({ IWillNotUseThisInPlugins: true });
        const media = tech && tech.el && tech.el();
        const source = media && media.querySelector('source');
        const src = (source && source.src) || (media && media.currentSrc);
        if (!src) return null;
        return { src, type: (source && source.type) || '' };
    }

    function attach(player, options = {}) {
        const playerElement = player.el();
        const fallbackElement = document.createElement('div');
        fallbackElement.className = 'media-audio-fallback';
        fallbackElement.hidden = true;
        fallbackElement.style.width = '100%';

        const message = document.createElement('p');
        message.textContent = 'This browser cannot play the video track. Playing audio only.';
        fallbackElement.appendChild(message);

        const audio = document.createElement('audio');
        audio.controls = true;
        audio.preload = 'auto';
        audio.crossOrigin = 'anonymous';
        audio.style.width = '100%';
        fallbackElement.appendChild(audio);
        playerElement.insertAdjacentElement('afterend', fallbackElement);

        let active = false;
        let playRequested = false;
        let stallTimer = null;
        let probeVersion = 0;

        function clearStallTimer() {
            clearTimeout(stallTimer);
            stallTimer = null;
        }

        function getSource() {
            return (options.getSource && options.getSource()) || sourceFromPlayer(player);
        }

        function cancelProbe() {
            probeVersion += 1;
            audio.removeAttribute('src');
            audio.querySelectorAll('source').forEach(source => source.remove());
            audio.load();
        }

        function tryAudioFallback() {
            if (active || audio.src || audio.querySelector('source')) return;
            const source = getSource();
            if (!source || !source.src) return;

            clearStallTimer();
            const version = ++probeVersion;
            const startTime = options.getStartTime
                ? Number(options.getStartTime())
                : Number(player.currentTime());
            const playbackRate = Number(player.playbackRate());
            const shouldPlay = playRequested || !player.paused()
                || (options.shouldPlay && options.shouldPlay());

            const sourceElement = document.createElement('source');
            sourceElement.src = source.src;
            if (source.type) sourceElement.type = audioMimeType(source.type);
            audio.appendChild(sourceElement);

            audio.addEventListener('canplay', () => {
                if (version !== probeVersion || active) return;
                active = true;
                player.pause();
                playerElement.style.display = 'none';
                fallbackElement.hidden = false;
                if (Number.isFinite(startTime) && startTime > 0) {
                    try { audio.currentTime = startTime; } catch (error) { /* metadata is still settling */ }
                }
                if (Number.isFinite(playbackRate) && playbackRate > 0) {
                    audio.playbackRate = playbackRate;
                }
                if (typeof options.onActivate === 'function') options.onActivate(audio);
                if (shouldPlay) {
                    const promise = audio.play();
                    if (promise && typeof promise.catch === 'function') promise.catch(() => {});
                }
            }, { once: true });
            audio.load();
        }

        function armStallTimer() {
            if (active || stallTimer || (!playRequested && player.paused())) return;
            const initialTime = Number(player.currentTime());
            stallTimer = setTimeout(() => {
                stallTimer = null;
                const currentTime = Number(player.currentTime());
                if (!player.paused()
                        && (!Number.isFinite(initialTime)
                            || !Number.isFinite(currentTime)
                            || currentTime - initialTime < 0.1)) {
                    tryAudioFallback();
                }
            }, STALL_TIMEOUT_MS);
        }

        function reset() {
            clearStallTimer();
            if (active) audio.pause();
            active = false;
            playRequested = false;
            cancelProbe();
            fallbackElement.hidden = true;
            playerElement.style.display = '';
        }

        player.on('play', () => { playRequested = true; });
        player.on('playing', () => {
            playRequested = true;
            clearStallTimer();
            if (!active) cancelProbe();
        });
        player.on('pause', () => {
            if (!player.error()) playRequested = false;
            clearStallTimer();
        });
        player.on('waiting', armStallTimer);
        player.on('stalled', armStallTimer);
        player.on('error', tryAudioFallback);

        audio.addEventListener('play', () => { playRequested = true; });
        audio.addEventListener('pause', () => { playRequested = false; });
        if (typeof options.onEnded === 'function') {
            audio.addEventListener('ended', options.onEnded);
        }

        return {
            audio,
            reset,
            isActive: () => active,
            play: () => active ? audio.play() : player.play(),
            pause: () => active ? audio.pause() : player.pause(),
            currentTime(value) {
                if (value === undefined) return active ? audio.currentTime : player.currentTime();
                if (active) audio.currentTime = value;
                else player.currentTime(value);
            },
        };
    }

    window.WorldStageAudioFallback = { attach };
})();
