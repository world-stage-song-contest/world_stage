(function () {
    'use strict';

    function post(path, body) {
        fetch(path, {
            method: 'POST',
            keepalive: true,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).catch(() => {});
    }

    function scrobbleThreshold(duration) {
        return Number.isFinite(duration) && duration > 30 ? Math.min(duration / 2, 240) : null;
    }

    class Tracker {
        constructor(enabled, countPlays = false) {
            this.enabled = !!enabled;
            this.countPlays = !!countPlays;
            this.playCounted = false;
            this.playTimer = null;
            this.hasEnded = false;
            this.song = null;
            this.durationProvider = null;
            this.heard = 0;
            this.playingSince = null;
            this.startedAt = null;
            this.nowPlayingSent = false;
            this.scrobbled = false;

            this.flush = this.flush.bind(this);
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) this.flush();
            });
            window.addEventListener('pagehide', this.flush);
        }

        setSong(song, durationProvider) {
            this.flush();
            clearTimeout(this.playTimer);
            this.song = song && Number.isInteger(song.id) ? song : null;
            this.durationProvider = durationProvider || null;
            this.heard = 0;
            this.playingSince = null;
            this.startedAt = null;
            this.nowPlayingSent = false;
            this.scrobbled = false;
            this.playCounted = false;
            this.hasEnded = false;
        }

        freeze() {
            if (this.playingSince !== null) {
                this.heard += Date.now() - this.playingSince;
                this.playingSince = null;
            }
        }

        heardMilliseconds() {
            return this.heard + (this.playingSince === null
                ? 0
                : Date.now() - this.playingSince);
        }

        heardSeconds() {
            return this.heardMilliseconds() / 1000;
        }

        duration() {
            const provided = this.durationProvider ? Number(this.durationProvider()) : NaN;
            if (Number.isFinite(provided) && provided > 0) return provided;
            const catalog = Number(this.song && this.song.duration);
            return Number.isFinite(catalog) && catalog > 0 ? catalog : null;
        }

        playing() {
            if (!this.song) return;
            if (this.hasEnded) this.setSong(this.song, this.durationProvider);
            if (this.playingSince === null) this.playingSince = Date.now();
            if (this.startedAt === null) this.startedAt = Math.floor(Date.now() / 1000);
            clearTimeout(this.playTimer);
            const threshold = scrobbleThreshold(this.duration());
            if (this.countPlays && !this.playCounted && threshold !== null) {
                const delay = Math.ceil(Math.max(0, threshold * 1000 - this.heardMilliseconds()));
                this.playTimer = setTimeout(this.flush, delay);
            }
            if (this.enabled && !this.nowPlayingSent) {
                this.nowPlayingSent = true;
                post('/scrobble/now-playing', { song_id: this.song.id });
            }
        }

        pause() {
            this.freeze();
            clearTimeout(this.playTimer);
            this.flush();
        }

        flush() {
            if (!this.song) return;
            const duration = this.duration();
            const threshold = scrobbleThreshold(duration);
            if (threshold === null || this.heardSeconds() < threshold) return;
            if (this.countPlays && !this.playCounted) {
                this.playCounted = true;
                clearTimeout(this.playTimer);
                post('/scrobble/play', {
                    song_id: this.song.id,
                    play_snapshot: this.song.play_snapshot,
                    radio_slot_id: this.song.radio_slot_id,
                    timestamp: this.startedAt,
                    heard_seconds: this.heardSeconds(),
                    duration,
                });
            }
            if (!this.enabled || this.scrobbled) return;
            this.scrobbled = true;
            post('/scrobble', {
                song_id: this.song.id,
                timestamp: this.startedAt || Math.floor(Date.now() / 1000),
            });
        }

        ended() {
            this.pause();
            this.hasEnded = true;
        }

        attachVideoJs(player) {
            player.on('playing', () => this.playing());
            player.on('pause', () => this.pause());
            player.on('waiting', () => this.pause());
            player.on('ended', () => this.ended());
        }

        attachMedia(media) {
            media.addEventListener('playing', () => this.playing());
            media.addEventListener('pause', () => this.pause());
            media.addEventListener('waiting', () => this.pause());
            media.addEventListener('ended', () => this.ended());
        }

        attachYouTube(iframe) {
            const initialise = () => {
                if (!window.YT || !window.YT.Player) return;
                const ytPlayer = new window.YT.Player(iframe, {
                    events: {
                        onStateChange: (event) => {
                            if (event.data === window.YT.PlayerState.PLAYING) this.playing();
                            else if (event.data === window.YT.PlayerState.ENDED) this.ended();
                            else if (event.data === window.YT.PlayerState.PAUSED
                                    || event.data === window.YT.PlayerState.BUFFERING) this.pause();
                        },
                    },
                });
                this.durationProvider = () => ytPlayer.getDuration();
            };

            if (window.YT && window.YT.Player) {
                initialise();
                return;
            }
            const previous = window.onYouTubeIframeAPIReady;
            window.onYouTubeIframeAPIReady = () => {
                if (typeof previous === 'function') previous();
                initialise();
            };
            if (!document.querySelector('script[src="https://www.youtube.com/iframe_api"]')) {
                const script = document.createElement('script');
                script.src = 'https://www.youtube.com/iframe_api';
                document.head.appendChild(script);
            }
        }
    }

    window.WorldStageScrobble = { Tracker, scrobbleThreshold };
})();
