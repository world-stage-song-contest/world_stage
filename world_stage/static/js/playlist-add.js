(function () {
    function appendSongCard(song, listId) {
        const list = document.getElementById(listId);
        if (!list || list.querySelector(`[data-song-id="${song.id}"]`)) return;

        const card = document.createElement('div');
        card.className = 'predictions-item';
        card.draggable = true;
        card.dataset.songId = song.id;

        const handle = document.createElement('span');
        handle.className = 'drag-handle';
        handle.setAttribute('aria-hidden', 'true');
        handle.textContent = '⠿';
        card.appendChild(handle);

        const position = document.createElement('span');
        position.className = 'position-label';
        position.textContent = list.querySelectorAll('.predictions-item').length + 1;
        card.appendChild(position);

        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'song_id';
        input.value = song.id;
        card.appendChild(input);

        const details = document.createElement('div');
        details.className = 'song-details';
        const flag = document.createElement('img');
        flag.src = window.flagStaticUrl(song.cc, 42);
        flag.width = 42;
        flag.height = 28;
        flag.className = 'flag flag-image';
        flag.draggable = false;
        details.appendChild(flag);

        const text = document.createElement('div');
        text.className = 'song-text';
        const title = document.createElement('a');
        title.className = 'song-title';
        title.href = song.details_url;
        title.textContent = song.title;
        text.appendChild(title);
        const meta = document.createElement('span');
        meta.className = 'song-meta';
        meta.textContent = `${song.artist} · ${song.country} · ${song.year}`;
        text.appendChild(meta);
        details.appendChild(text);
        card.appendChild(details);

        const removeFormId = `remove-song-${song.id}`;
        const remove = document.createElement('button');
        remove.type = 'submit';
        remove.setAttribute('form', removeFormId);
        remove.textContent = 'Remove';
        card.appendChild(remove);
        list.appendChild(card);

        const form = document.createElement('form');
        form.id = removeFormId;
        form.method = 'post';
        form.action = song.remove_url;
        document.getElementById('remove-song-forms').appendChild(form);

        list.hidden = false;
        document.getElementById('playlist-submit-row').hidden = false;
        document.getElementById('playlist-empty-message').hidden = true;
        document.getElementById('play-playlist-link').hidden = false;
    }

    function initialise() {
        document.querySelectorAll('[data-playlist-add]').forEach(function (button) {
            button.addEventListener('click', async function () {
                const playlistId = button.dataset.playlistId;
                const url = button.dataset.urlTemplate.replace('/0/', '/' + playlistId + '/');
                const body = new FormData();
                body.append('song_id', button.dataset.songId);

                button.disabled = true;
                const originalLabel = button.textContent;
                button.textContent = 'Adding…';
                try {
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: { 'Accept': 'application/json' },
                        body: body,
                    });
                    const data = await response.json();
                    if (!response.ok) {
                        throw new Error(data.error || 'Could not add song');
                    }
                    if (data.result.added && button.dataset.targetList) {
                        appendSongCard(data.result.song, button.dataset.targetList);
                    }
                    button.textContent = 'Added';
                    button.dataset.added = 'true';
                } catch (error) {
                    button.disabled = false;
                    button.textContent = originalLabel;
                    window.alert(error.message || 'Could not add song');
                }
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialise);
    } else {
        initialise();
    }
}());
