function showMoveError(message) {
    const error = document.getElementById('error-message');
    error.textContent = message;
    error.classList.remove('hidden');
}

function clearMoveError() {
    const error = document.getElementById('error-message');
    error.textContent = '';
    error.classList.add('hidden');
}

function resetSelect(select, label) {
    select.replaceChildren(new Option(label, ''));
    select.disabled = true;
}

function resetDestination() {
    const yearSelect = document.getElementById('to_year');
    yearSelect.value = '';
    yearSelect.disabled = true;
    resetSelect(document.getElementById('to_country'), 'Select a country');
    const button = document.getElementById('move-button');
    button.disabled = true;
}

async function populateEntries() {
    clearMoveError();
    const year = document.getElementById('from_year').value;
    const entrySelect = document.getElementById('song_id');
    resetSelect(entrySelect, 'Select a country');
    resetDestination();
    if (!year) return;

    try {
        const response = await fetch(`/member/move/${year}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to load entries');
        for (const entry of data.entries) {
            entrySelect.add(new Option(entry.country, entry.id));
        }
        entrySelect.disabled = data.entries.length === 0;
        if (data.entries.length === 0) showMoveError(`You have no entries in ${year}.`);
    } catch (error) {
        showMoveError(error.message);
    }
}

function selectEntry() {
    clearMoveError();
    resetDestination();
    if (!document.getElementById('song_id').value) return;
    document.getElementById('to_year').disabled = false;
}

async function populateDestinationCountries() {
    clearMoveError();
    const year = document.getElementById('to_year').value;
    const songId = document.getElementById('song_id').value;
    const countrySelect = document.getElementById('to_country');
    resetSelect(countrySelect, 'Select a country');
    const button = document.getElementById('move-button');
    button.disabled = true;
    if (!year || !songId) return;

    try {
        const response = await fetch(
            `/member/move/destinations/${year}?song_id=${encodeURIComponent(songId)}`,
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to load countries');
        for (const country of data.countries) {
            const placeholder = country.replaces_placeholder ? ' (replace placeholder)' : '';
            countrySelect.add(new Option(`${country.name}${placeholder}`, country.cc));
        }
        countrySelect.disabled = data.countries.length === 0;
        if (data.countries.length === 0) showMoveError('There are no available countries in that year.');
    } catch (error) {
        showMoveError(error.message);
    }
}

function selectDestinationCountry() {
    const ready = !!document.getElementById('to_country').value;
    const button = document.getElementById('move-button');
    button.disabled = !ready;
}

async function submitMove(event) {
    event.preventDefault();
    clearMoveError();
    const form = document.forms.move_entry;
    const button = document.getElementById('move-button');
    button.disabled = true;

    try {
        const response = await fetch('/member/move', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                song_id: parseInt(form.song_id.value, 10),
                to_year: parseInt(form.to_year.value, 10),
                to_country: form.to_country.value,
            }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error?.description || 'Move failed');
        window.location.href = data.result.details_url;
    } catch (error) {
        button.disabled = false;
        showMoveError(error.message);
    }
}

function onLoad() {
    document.getElementById('from_year').addEventListener('change', populateEntries);
    document.getElementById('song_id').addEventListener('change', selectEntry);
    document.getElementById('to_year').addEventListener('change', populateDestinationCountries);
    document.getElementById('to_country').addEventListener('change', selectDestinationCountry);
    document.getElementById('move-entry').addEventListener('submit', submitMove);
}
