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

    class Tracker {
        constructor(enabled) {
            this.enabled = !!enabled;
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
            this.song = song && Number.isInteger(song.id) ? song : null;
            this.durationProvider = durationProvider || null;
            this.heard = 0;
            this.playingSince = null;
            this.startedAt = null;
            this.nowPlayingSent = false;
            this.scrobbled = false;
        }

        freeze() {
            if (this.playingSince !== null) {
                this.heard += Date.now() / 1000 - this.playingSince;
                this.playingSince = null;
            }
        }

        heardSeconds() {
            return this.heard + (this.playingSince === null
                ? 0
                : Date.now() / 1000 - this.playingSince);
        }

        duration() {
            const provided = this.durationProvider ? Number(this.durationProvider()) : NaN;
            if (Number.isFinite(provided) && provided > 0) return provided;
            const catalog = Number(this.song && this.song.duration);
            return Number.isFinite(catalog) && catalog > 0 ? catalog : null;
        }

        playing() {
            if (!this.song) return;
            if (this.playingSince === null) this.playingSince = Date.now() / 1000;
            if (this.startedAt === null) this.startedAt = Math.floor(Date.now() / 1000);
            if (this.enabled && !this.nowPlayingSent) {
                this.nowPlayingSent = true;
                post('/scrobble/now-playing', { song_id: this.song.id });
            }
        }

        pause() {
            this.freeze();
        }

        flush() {
            if (!this.enabled || !this.song || this.scrobbled) return;
            const duration = this.duration();
            // AudioScrobbler eligibility: over 30 seconds long and heard
            // for half the track or four minutes, whichever comes first.
            if (!duration || duration <= 30) return;
            if (this.heardSeconds() < Math.min(duration / 2, 240)) return;
            this.scrobbled = true;
            post('/scrobble', {
                song_id: this.song.id,
                timestamp: this.startedAt || Math.floor(Date.now() / 1000),
            });
        }

        ended() {
            this.freeze();
            this.flush();
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

    window.WorldStageScrobble = { Tracker };
})();
