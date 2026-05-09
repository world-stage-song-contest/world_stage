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
        await populateSongData(entryNumber);
    } else {
        yearSelect.value = '';
        clearFormFields();
    }

    const doesMatchCb = document.getElementById('does_match');
    if (!yearVal || !countryVal) {
        doesMatchCb.checked = true;
    }
    toggleTitleLanguageSelect(doesMatchCb);

    document.querySelectorAll('.time-input').forEach(attachTimeInputHandler);

    // Seed a default 4/4 row when the user opens the time signatures
    // section for the first time. We don't do this for the initial
    // collapsed state so songs with no signatures never accidentally
    // submit one.
    const tsDetails = document.getElementById('time-signatures-details');
    if (tsDetails) {
        tsDetails.addEventListener('toggle', () => {
            if (!tsDetails.open) return;
            const container = document.getElementById('time-signature-rows');
            if (container && !container.firstElementChild) {
                addTimeSignatureRow();
            }
        });
    }

    // Same pattern for genres: opening the section adds an empty
    // select. Abandoned blank rows are dropped at submit time.
    const genresDetails = document.getElementById('genres-details');
    if (genresDetails) {
        genresDetails.addEventListener('toggle', () => {
            if (!genresDetails.open) return;
            const container = document.getElementById('genre-rows');
            if (container && !container.firstElementChild) {
                addGenreRow();
            }
        });
    }

    // Intercept form submission
    document.getElementById('submit-song').addEventListener('submit', handleSubmit);
}

function attachTimeInputHandler(el) {
    el.addEventListener('input', function () {
        let value = el.value.replace(/\D/g, '');

        if (value.length >= 2) {
            value = value.slice(0, 3);
            value = value.slice(0, 1) + ':' + value.slice(1);
        }

        el.value = value;
    });
}

function parseTimeStr(str) {
    if (!str) return 0;
    const parts = str.split(':');
    if (parts.length === 2) {
        const m = parseInt(parts[0], 10) || 0;
        const s = parseInt(parts[1], 10) || 0;
        return m * 60 + s;
    }
    return parseInt(str, 10) || 0;
}

function formatTimeStr(seconds) {
    const s = Math.max(0, parseInt(seconds, 10) || 0);
    return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`;
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
        key_signatures: collectKeySignatures(),
        time_signatures: collectTimeSignatures(),
        subgenres: collectSubgenres(),
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
    // 'none' means "no override" — let the server apply its default
    // (the requester's own account for new songs, the existing submitter
    // for edits) rather than forcing submitter_id to null.
    const forceSubmitter = document.getElementById('force_submitter');
    if (forceSubmitter && forceSubmitter.value !== 'none') {
        data.submitter_id = parseInt(forceSubmitter.value, 10);
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
    const ksError = validateKeySignatures();
    if (ksError) {
        showValidationError(ksError);
        return;
    }
    const tsError = validateTimeSignatures();
    if (tsError) {
        showValidationError(tsError);
        return;
    }
    const body = collectFormData();

    try {
        let res;
        if (currentSongId) {
            // PUT existing (full replacement)
            res = await fetch(`/api/song/${currentSongId}`, {
                method: 'PUT',
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
            const cc = (song.country_id || '').toLowerCase();
            // Specials use /country/<cc>/<short_name>/<entry_number>,
            // regular years use /country/<cc>/<year>.
            let target;
            if (song.special_short_name) {
                target = `/country/${cc}/${song.special_short_name}/${song.entry_number}`;
            } else {
                target = `/country/${cc}/${song.year}`;
            }
            window.location.href = target;
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

    // Reset to a single empty key signature row. ``collectKeySignatures``
    // drops rows whose tonic, mode, and atonal flag are all empty, so a
    // pristine row submits as nothing.
    clearKeySignatureRows();
    ensureKeySignatureRows();

    // Time signatures start with no rows. Opening the section seeds a
    // default 4/4 row; pristine forms with the section folded won't
    // submit anything.
    clearTimeSignatureRows();

    // Genres also start empty. Opening the section seeds a single
    // empty select; abandoned blank rows are dropped on submit.
    clearGenreRows();

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

// ── Key signatures ───────────────────────────────────────────────────

function addKeySignatureRow(values) {
    const container = document.getElementById('key-signature-rows');
    const template = document.getElementById('key-signature-template');
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector('.key-signature-row');

    const tonicSel = row.querySelector('[data-ks="tonic"]');
    const tonicOther = row.querySelector('[data-ks="tonic-other"]');
    const modeSel = row.querySelector('[data-ks="mode"]');
    const modeOther = row.querySelector('[data-ks="mode-other"]');
    const atonalCb = row.querySelector('[data-ks="atonal"]');
    const microtonalCb = row.querySelector('[data-ks="microtonal"]');
    const startInput = row.querySelector('[data-ks="start"]');

    attachTimeInputHandler(startInput);
    tonicSel.addEventListener('change', () => toggleOther(tonicSel, tonicOther));
    modeSel.addEventListener('change', () => toggleOther(modeSel, modeOther));
    atonalCb.addEventListener('change', () => applyAtonal(row));

    if (values) {
        startInput.value = formatTimeStr(values.start_seconds);
        applyKeyValue(tonicSel, tonicOther, values.tonic);
        applyKeyValue(modeSel, modeOther, values.mode);
        // Atonal is the absence of both tonic and mode.
        atonalCb.checked = values.tonic == null && values.mode == null;
        microtonalCb.checked = !!values.microtonal;
        const notesInput = row.querySelector('[data-ks="notes"]');
        if (notesInput && values.notes != null) notesInput.value = values.notes;
        applyAtonal(row);
    }

    container.appendChild(fragment);
    document.getElementById('remove-key-signature-button').disabled = false;
}

function ensureKeySignatureRows() {
    const container = document.getElementById('key-signature-rows');
    if (container && !container.firstElementChild) {
        addKeySignatureRow();
    }
}

function applyKeyValue(select, otherInput, value) {
    if (value == null || value === '') {
        select.value = '';
        otherInput.value = '';
        otherInput.classList.add('hidden');
        return;
    }
    const match = Array.from(select.options).find(o => o.value === value);
    if (match) {
        select.value = value;
        otherInput.value = '';
        otherInput.classList.add('hidden');
    } else {
        select.value = '__other__';
        otherInput.value = value;
        otherInput.classList.remove('hidden');
    }
}

function toggleOther(select, otherInput) {
    if (select.value === '__other__') {
        otherInput.classList.remove('hidden');
    } else {
        otherInput.classList.add('hidden');
        otherInput.value = '';
    }
}

function applyAtonal(row) {
    const atonalCb = row.querySelector('[data-ks="atonal"]');
    if (atonalCb.checked) {
        row.classList.add('atonal');
    } else {
        row.classList.remove('atonal');
    }
}

function removeKeySignatureRow() {
    const container = document.getElementById('key-signature-rows');
    const last = container.lastElementChild;
    if (last) last.remove();
    if (!container.firstElementChild) {
        document.getElementById('remove-key-signature-button').disabled = true;
    }
}

function clearKeySignatureRows() {
    const container = document.getElementById('key-signature-rows');
    if (container) container.innerHTML = '';
    const btn = document.getElementById('remove-key-signature-button');
    if (btn) btn.disabled = true;
    const details = document.getElementById('key-signatures-details');
    if (details) details.open = false;
}

function readOtherOrSelect(row, name) {
    const sel = row.querySelector(`[data-ks="${name}"]`);
    if (!sel.value) return null;
    if (sel.value === '__other__') {
        const other = row.querySelector(`[data-ks="${name}-other"]`);
        const v = (other.value || '').trim();
        return v || null;
    }
    return sel.value;
}

// ── Genres / subgenres ───────────────────────────────────────────────

function addGenreRow(values) {
    const container = document.getElementById('genre-rows');
    const template = document.getElementById('genre-template');
    const fragment = template.content.cloneNode(true);
    const select = fragment.querySelector('.genre-select');
    if (values && values.id != null) {
        select.value = String(values.id);
    }
    container.appendChild(fragment);
    document.getElementById('remove-genre-button').disabled = false;
}

function removeGenreRow() {
    const container = document.getElementById('genre-rows');
    const last = container.lastElementChild;
    if (last) last.remove();
    if (!container.firstElementChild) {
        document.getElementById('remove-genre-button').disabled = true;
    }
}

function clearGenreRows() {
    const container = document.getElementById('genre-rows');
    if (container) container.innerHTML = '';
    const btn = document.getElementById('remove-genre-button');
    if (btn) btn.disabled = true;
}

function collectSubgenres() {
    const ids = [];
    for (const sel of document.querySelectorAll('.genre-select')) {
        if (sel.value) ids.push(parseInt(sel.value, 10));
    }
    return ids;
}

// ── Time signatures ──────────────────────────────────────────────────

function addTimeSignatureRow(values) {
    const container = document.getElementById('time-signature-rows');
    const template = document.getElementById('time-signature-template');
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector('.time-signature-row');

    const startInput = row.querySelector('[data-ts="start"]');
    const numInput = row.querySelector('[data-ts="numerator"]');
    const denomSel = row.querySelector('[data-ts="denominator"]');
    const mixedCb = row.querySelector('[data-ts="mixed"]');

    attachTimeInputHandler(startInput);
    mixedCb.addEventListener('change', () => applyMixedMeter(row));

    if (values) {
        startInput.value = formatTimeStr(values.start_seconds);
        if (values.numerator == null && values.denominator == null) {
            mixedCb.checked = true;
        } else {
            if (values.numerator != null) numInput.value = values.numerator;
            if (values.denominator != null) denomSel.value = String(values.denominator);
        }
        const notesInput = row.querySelector('[data-ts="notes"]');
        if (notesInput && values.notes != null) notesInput.value = values.notes;
        applyMixedMeter(row);
    }

    container.appendChild(fragment);
    document.getElementById('remove-time-signature-button').disabled = false;
}

function applyMixedMeter(row) {
    const mixedCb = row.querySelector('[data-ts="mixed"]');
    if (mixedCb.checked) {
        row.classList.add('mixed');
    } else {
        row.classList.remove('mixed');
    }
}

function removeTimeSignatureRow() {
    const container = document.getElementById('time-signature-rows');
    const last = container.lastElementChild;
    if (last) last.remove();
    if (!container.firstElementChild) {
        document.getElementById('remove-time-signature-button').disabled = true;
    }
}

function clearTimeSignatureRows() {
    const container = document.getElementById('time-signature-rows');
    if (container) container.innerHTML = '';
    const btn = document.getElementById('remove-time-signature-button');
    if (btn) btn.disabled = true;
}

function collectTimeSignatures() {
    const rows = document.querySelectorAll('.time-signature-row');
    const out = [];
    for (const row of rows) {
        const isMixed = row.querySelector('[data-ts="mixed"]').checked;
        const startSeconds = parseTimeStr(row.querySelector('[data-ts="start"]').value);
        const notesRaw = (row.querySelector('[data-ts="notes"]')?.value || '').trim();
        const notes = notesRaw || null;

        if (isMixed) {
            out.push({
                start_seconds: startSeconds,
                numerator: null,
                denominator: null,
                notes,
            });
            continue;
        }

        const numStr = (row.querySelector('[data-ts="numerator"]').value || '').trim();
        const denomStr = row.querySelector('[data-ts="denominator"]').value;

        if (numStr === '' || !denomStr) {
            // Partial row: keep it only if the user wrote a note, so a
            // standalone annotation isn't lost.
            if (notes) {
                out.push({
                    start_seconds: startSeconds,
                    numerator: null,
                    denominator: null,
                    notes,
                });
            }
            continue;
        }

        const numerator = parseInt(numStr, 10);
        const denominator = parseInt(denomStr, 10);
        if (!Number.isFinite(numerator) || numerator <= 0) continue;

        out.push({start_seconds: startSeconds, numerator, denominator, notes});
    }
    return out;
}

function validateTimeSignatures() {
    const rows = document.querySelectorAll('.time-signature-row');
    const seenStarts = new Set();
    for (const row of rows) {
        const isMixed = row.querySelector('[data-ts="mixed"]').checked;
        const numStr = (row.querySelector('[data-ts="numerator"]').value || '').trim();
        const denomStr = row.querySelector('[data-ts="denominator"]').value;
        const startSeconds = parseTimeStr(row.querySelector('[data-ts="start"]').value);
        const notes = (row.querySelector('[data-ts="notes"]')?.value || '').trim();

        // Match the skip rule in collectTimeSignatures: a partial row
        // is dropped unless it carries a standalone note.
        if (!isMixed && (numStr === '' || !denomStr) && !notes) continue;

        if (seenStarts.has(startSeconds)) {
            return `Two time signatures share the same start time (${formatTimeStr(startSeconds)}). Each time signature must start at a unique time.`;
        }
        seenStarts.add(startSeconds);
    }
    return null;
}

function validateKeySignatures() {
    const rows = document.querySelectorAll('.key-signature-row');
    const seenStarts = new Map();
    for (const row of rows) {
        const isAtonal = row.querySelector('[data-ks="atonal"]').checked;
        const isMicrotonal = row.querySelector('[data-ks="microtonal"]').checked;
        const startSeconds = parseTimeStr(row.querySelector('[data-ks="start"]').value);

        // Match the skip rule in collectKeySignatures: an untouched row
        // (no tonic, no mode, no atonal, no microtonal, no notes) is
        // silently dropped, so it shouldn't trigger duplicate-start
        // errors.
        let tonic = null, mode = null;
        if (!isAtonal) {
            tonic = readOtherOrSelect(row, 'tonic');
            mode = readOtherOrSelect(row, 'mode');
        }
        const notes = (row.querySelector('[data-ks="notes"]')?.value || '').trim();
        if (!isAtonal && !isMicrotonal && tonic === null && mode === null && !notes) continue;

        if (isAtonal && isMicrotonal) {
            return `A key signature at ${formatTimeStr(startSeconds)} cannot be both atonal and microtonal.`;
        }
        if (seenStarts.has(startSeconds)) {
            return `Two key signatures share the same start time (${formatTimeStr(startSeconds)}). Each key signature must start at a unique time.`;
        }
        seenStarts.set(startSeconds, true);
    }
    return null;
}

function showValidationError(message) {
    const successEl = document.getElementById('success-message');
    successEl.classList.add('hidden');
    const errorMessage = document.getElementById('error-message');
    errorMessage.textContent = message;
    errorMessage.classList.remove('hidden');
}

function collectKeySignatures() {
    const rows = document.querySelectorAll('.key-signature-row');
    const out = [];
    for (const row of rows) {
        const isAtonal = row.querySelector('[data-ks="atonal"]').checked;
        const isMicrotonal = row.querySelector('[data-ks="microtonal"]').checked;
        const startSeconds = parseTimeStr(row.querySelector('[data-ks="start"]').value);
        const notesRaw = (row.querySelector('[data-ks="notes"]')?.value || '').trim();
        const notes = notesRaw || null;
        let tonic = null;
        let mode = null;
        if (!isAtonal) {
            tonic = readOtherOrSelect(row, 'tonic');
            mode = readOtherOrSelect(row, 'mode');
            // A row with no tonic, mode, atonal flag, microtonal flag,
            // or notes is just an abandoned blank — drop it. Atonal
            // rows (both null), microtonal annotations, and standalone
            // notes are kept.
            if (tonic === null && mode === null && !isMicrotonal && notes === null) continue;
        }
        out.push({
            start_seconds: startSeconds,
            tonic,
            mode,
            microtonal: isMicrotonal,
            notes,
        });
    }
    return out;
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

async function fetchSongData(year, country, entryNumber) {
    let url = `/member/submit/${year}/${country}`;
    if (entryNumber) {
        url += `?entry_number=${encodeURIComponent(entryNumber)}`;
    }
    const res = await fetch(url);
    const songData = await res.json();
    return songData;
}

async function populateSongData(entryNumberOverride) {
    const yearSelect = document.getElementById('year');
    const countrySelect = document.getElementById('country');

    const year = yearSelect.value;
    const country = countrySelect.value;
    if (year === '' || country === '') {
        clearFormFields();
        return;
    }
    const songData = await fetchSongData(year, country, entryNumberOverride);

    // Clear form before populating with new data
    clearFormFields();

    if (songData.error) {
        // No existing song — form is already cleared, ready for new entry
        return;
    }

    // Track the song ID for PATCH/DELETE
    currentSongId = songData.id || null;

    const languages = songData.languages;
    const keySignatures = songData.key_signatures || [];
    const timeSignatures = songData.time_signatures || [];
    const subgenres = songData.subgenres || [];
    delete songData.languages;
    delete songData.key_signatures;
    delete songData.time_signatures;
    delete songData.subgenres;
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

    // Populate key signatures (if any). Always end with at least one
    // row visible so the affordance is discoverable; pristine rows are
    // filtered out at submit time.
    clearKeySignatureRows();
    for (const ks of keySignatures) {
        addKeySignatureRow(ks);
    }
    ensureKeySignatureRows();
    const details = document.getElementById('key-signatures-details');
    if (details) details.open = false;

    // Populate time signatures. Unlike key signatures, the section
    // stays empty when there's nothing saved — opening the <details>
    // seeds a default 4/4 row.
    clearTimeSignatureRows();
    for (const ts of timeSignatures) {
        addTimeSignatureRow(ts);
    }
    const tsDetails = document.getElementById('time-signatures-details');
    if (tsDetails) tsDetails.open = false;

    // Populate genres.
    clearGenreRows();
    for (const sg of subgenres) {
        addGenreRow(sg);
    }
    const genresDetails = document.getElementById('genres-details');
    if (genresDetails) genresDetails.open = false;

    // Update does_match visibility
    const doesMatchCb = document.getElementById('does_match');
    toggleTitleLanguageSelect(doesMatchCb);
}
