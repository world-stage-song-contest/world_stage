// ── State ────────────────────────────────────────────────────────────
let currentSongId = null;   // non-null when editing an existing song

// ── Lifecycle ────────────────────────────────────────────────────────

async function onLoad() {
    const languageCount = document.querySelectorAll('.language-select').length;
    document.getElementById('remove-language-button').disabled = languageCount == 1;
    const yearSelect = document.getElementById('year');
    const countrySelect = document.getElementById('country');
    const yearVal = parseInt(year, 10);
    const countryVal = country + '';
    if (yearVal) {
        yearSelect.value = yearVal;
        await populateCountries(yearSelect);
        countrySelect.value = countryVal.toUpperCase();
        await populateSongData();
    } else {
        yearSelect.value = '';
        clearFormFields();
    }

    const doesMatchCb = document.getElementById('does_match');
    if (!yearVal || !countryVal) {
        doesMatchCb.checked = true;
    }
    toggleTitleLanguageSelect(doesMatchCb);

    document.querySelectorAll('.time-input').forEach(el => {
        el.addEventListener('input', function (e) {
            let value = el.value.replace(/\D/g, '');

            if (value.length >= 2) {
                value = value.slice(0, 3);
                value = value.slice(0, 1) + ':' + value.slice(1);
            }

            el.value = value;
        });
    });

    // Intercept form submission
    document.getElementById('submit-song').addEventListener('submit', handleSubmit);
}

// ── Form submission via API ──────────────────────────────────────────

function collectFormData() {
    const form = document.forms.submit_song;
    const languages = [];
    for (const sel of document.querySelectorAll('.language-select')) {
        if (sel.value) languages.push(parseInt(sel.value, 10));
    }

    const data = {
        year: parseInt(form.year.value, 10),
        country: form.country.value,
        title: form.title.value || null,
        native_title: form.native_title.value || null,
        artist: form.artist.value || null,
        is_placeholder: form.is_placeholder.checked,
        is_translation: form.is_translation.checked,
        does_match: form.does_match.checked,
        video_link: form.video_link.value || null,
        snippet_start: form.snippet_start.value || null,
        snippet_end: form.snippet_end.value || null,
        translated_lyrics: form.translated_lyrics.value || null,
        romanized_lyrics: form.romanized_lyrics.value || null,
        native_lyrics: form.native_lyrics.value || null,
        notes: form.notes.value || null,
        sources: form.sources.value || null,
        languages: languages,
    };

    // Admin fields
    const forceSubmitter = document.getElementById('force_submitter');
    if (forceSubmitter && forceSubmitter.value !== 'none') {
        data.submitter_id = parseInt(forceSubmitter.value, 10);
    } else if (forceSubmitter && forceSubmitter.value === 'none') {
        data.submitter_id = null;
    }

    const adminApproved = document.getElementById('admin_approved');
    if (adminApproved) {
        data.admin_approved = adminApproved.checked;
    }

    // Cover art (admin)
    const posterLink = document.getElementById('poster_link');
    if (posterLink) {
        data.poster_link = posterLink.value || null;
    }

    return data;
}

async function handleSubmit(e) {
    e.preventDefault();
    const clickedButton = e.submitter;
    const action = clickedButton ? clickedButton.value : 'submit';

    clearError();

    if (action === 'delete') {
        if (!currentSongId) {
            handleError('No song to delete');
            return;
        }
        if (!confirm('Are you sure you want to delete your song?')) return;

        try {
            const res = await fetch(`/api/song/${currentSongId}`, {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
            });
            if (res.status === 204) {
                showSuccess('Song deleted successfully.');
                currentSongId = null;
                clearFormFields();
                // Re-populate countries (available list may have changed)
                const yearSelect = document.getElementById('year');
                if (yearSelect.value) await populateCountries(yearSelect);
                return;
            }
            const data = await res.json();
            handleError(data.error?.description || 'Failed to delete song');
        } catch (err) {
            handleError(`Network error: ${err.message}`);
        }
        return;
    }

    // Submit (create or update)
    const body = collectFormData();

    try {
        let res;
        if (currentSongId) {
            // PATCH existing
            res = await fetch(`/api/song/${currentSongId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
        } else {
            // POST new
            res = await fetch('/api/song', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
        }

        const data = await res.json();

        if (res.ok) {
            const song = data.result;
            currentSongId = song.id;
            const artist = song.artist || '';
            const title = song.title || '';
            const verb = res.status === 201 ? 'submitted' : 'updated';
            showSuccess(`The song "${artist} — ${title}" has been ${verb} for ${body.year}.`);
            // Re-populate countries (own list may have changed)
            const yearSelect = document.getElementById('year');
            if (yearSelect.value) {
                const countryVal = document.getElementById('country').value;
                await populateCountries(yearSelect);
                document.getElementById('country').value = countryVal;
            }
        } else {
            handleError(data.error?.description || 'Submission failed');
        }
    } catch (err) {
        handleError(`Network error: ${err.message}`);
    }
}

// ── UI helpers ───────────────────────────────────────────────────────

function showSuccess(message) {
    const successEl = document.getElementById('success-message');
    successEl.textContent = message;
    successEl.classList.remove('hidden');
    // Auto-hide after a few seconds
    setTimeout(() => successEl.classList.add('hidden'), 8000);
}

function clearFormFields() {
    currentSongId = null;
    const form = document.forms.submit_song;
    for (const element of form.elements) {
        // Skip year, country, action buttons, and language management buttons
        if (element.name === 'year' || element.name === 'country' || element.name === 'action') continue;
        if (element.type === 'button' || element.type === 'submit') continue;

        if (element.tagName === 'SELECT') {
            element.value = '';
        } else if (element.tagName === 'TEXTAREA') {
            element.value = '';
        } else if (element.tagName === 'INPUT') {
            if (element.type === 'checkbox' || element.type === 'radio') {
                element.checked = false;
            } else {
                element.value = '';
            }
        }
    }

    // Reset to a single language row
    resetLanguageRows();

    // Reset does_match to checked (default)
    const doesMatchCb = document.getElementById('does_match');
    doesMatchCb.checked = true;
    toggleTitleLanguageSelect(doesMatchCb);

    // Reset force_submitter if present
    const forceSubmitter = document.getElementById('force_submitter');
    if (forceSubmitter) {
        forceSubmitter.value = 'none';
    }
}

function resetLanguageRows() {
    const insertBefore = document.querySelector('#language-insert-before');
    // Remove all language rows except the first one
    const labels = document.querySelectorAll('.language-label');
    const selects = document.querySelectorAll('.language-select');
    for (let i = labels.length - 1; i >= 1; i--) {
        labels[i].remove();
        selects[i].remove();
    }
    // Clear the first language select
    if (selects.length > 0) {
        selects[0].value = '';
    }
    document.getElementById('remove-language-button').disabled = true;
}

function toggleTitleLanguageSelect(checkbox) {
    for (const element of document.querySelectorAll('.hide-match')) {
        if (checkbox.checked) {
            element.classList.add('hidden');
        } else {
            element.classList.remove('hidden');
        }
    }
}

function handleError(error) {
    const successEl = document.getElementById('success-message');
    successEl.classList.add('hidden');
    const errorMessage = document.getElementById('error-message');
    errorMessage.textContent = error;
    for (const element of document.querySelectorAll('.hidable')) {
        element.classList.add('hidden');
    }
    errorMessage.classList.remove('hidden');
}

function clearError() {
    const successEl = document.getElementById('success-message');
    successEl.classList.add('hidden');
    const errorMessage = document.getElementById('error-message');
    errorMessage.textContent = '';
    errorMessage.classList.add('hidden');
    for (const element of document.querySelectorAll('.hidable')) {
        element.classList.remove('hidden');
    }
}

// ── Language rows ────────────────────────────────────────────────────

function addLanguageRow() {
    document.getElementById('remove-language-button').disabled = false;
    const insertBefore = document.querySelector('#language-insert-before');
    const languageSelect = insertBefore.previousElementSibling;
    const languageLabel = languageSelect.previousElementSibling;
    const newLanguageLabel = languageLabel.cloneNode(true);
    const n = newLanguageLabel.dataset.n;
    newLanguageLabel.dataset.n = parseInt(n) + 1;
    newLanguageLabel.htmlFor = `language${parseInt(n) + 1}`;
    newLanguageLabel.textContent = `Language ${parseInt(n) + 1}`;
    const newLanguageSelect = languageSelect.cloneNode(true);
    newLanguageSelect.name = 'language';
    newLanguageSelect.id = `language${parseInt(n) + 1}`;
    newLanguageSelect.dataset.n = parseInt(n) + 1;
    newLanguageSelect.value = '';

    insertBefore.parentNode.insertBefore(newLanguageLabel, insertBefore);
    insertBefore.parentNode.insertBefore(newLanguageSelect, insertBefore);
}

function removeLanguageRow() {
    const deleteBefore = document.querySelector('#language-insert-before');
    const languageSelect = deleteBefore.previousElementSibling;
    const languageLabel = languageSelect.previousElementSibling;
    const n = languageLabel.dataset.n;
    if (n > 1) {
        languageLabel.remove();
        languageSelect.remove();
    }
    if (n == 2) {
        document.getElementById('remove-language-button').disabled = true;
    }
}

// ── Country / song data fetching ─────────────────────────────────────

async function fetchCountries(yearSelect) {
    const year = yearSelect.value;
    const url = `/member/submit/${year}`;
    const res = await fetch(url);
    const countries = await res.json();
    return countries;
}

function clearCountriesSelect() {
    const countrySelect = document.getElementById('country');
    for (const optgroup of countrySelect.querySelectorAll('optgroup')) {
        optgroup.innerHTML = '';
    }
}

async function populateCountries(yearSelect) {
    const countriesData = await fetchCountries(yearSelect);
    if (countriesData.error) {
        handleError(countriesData.error);
        return;
    } else {
        clearError();
    }

    const countries = countriesData.countries;
    clearCountriesSelect();
    const countrySelect = document.getElementById('country');
    countrySelect.value = '';
    clearFormFields();

    const ownGroup = document.getElementById('own-countries');
    const availableGroup = document.getElementById('available-countries');

    for (const {cc, name} of countries.own) {
        const option = document.createElement('option');
        option.value = cc;
        option.textContent = name;
        ownGroup.appendChild(option);
    }
    for (const {cc, name} of countries.placeholder) {
        const option = document.createElement('option');
        option.value = cc;
        option.textContent = name;
        availableGroup.appendChild(option);
    }
}

async function fetchSongData(year, country) {
    const url = `/member/submit/${year}/${country}`;
    const res = await fetch(url);
    const songData = await res.json();
    return songData;
}

async function populateSongData() {
    const yearSelect = document.getElementById('year');
    const countrySelect = document.getElementById('country');

    const year = yearSelect.value;
    const country = countrySelect.value;
    if (year === '' || country === '') {
        clearFormFields();
        return;
    }
    const songData = await fetchSongData(year, country);

    // Clear form before populating with new data
    clearFormFields();

    if (songData.error) {
        // No existing song — form is already cleared, ready for new entry
        return;
    }

    // Track the song ID for PATCH/DELETE
    currentSongId = songData.id || null;

    const languages = songData.languages;
    delete songData.languages;
    delete songData.id;

    const form = document.forms.submit_song;
    for (const [key, value] of Object.entries(songData)) {
        try {
            const forceSubmitter = document.getElementById('force_submitter');
            if (forceSubmitter && key == "user_id") {
                forceSubmitter.value = value;
                continue;
            }
            const newVal = value === null ? '' : value;
            const element = form.querySelector(`[name="${key}"]`);
            if (element) {
                if (element.tagName === 'SELECT') {
                    element.value = newVal;
                } else if (element.tagName === 'INPUT') {
                    if (element.type === 'checkbox') {
                        element.checked = newVal;
                    } else {
                        element.value = newVal;
                    }
                } else if (element.tagName === 'TEXTAREA') {
                    element.value = newVal;
                }
            }
        } catch (error) {
            console.error(`Error setting value for ${key}:`, error);
        }
    }

    // Reset language rows, then add the right number
    resetLanguageRows();
    for (const [i, {id, name}] of languages.entries()) {
        if (i != 0) {
            addLanguageRow();
        }
        const languageSelect = document.querySelector(`#language${i + 1}`);
        languageSelect.value = id;
    }

    // Update does_match visibility
    const doesMatchCb = document.getElementById('does_match');
    toggleTitleLanguageSelect(doesMatchCb);
}
