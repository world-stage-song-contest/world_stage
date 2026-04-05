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
}

function clearFormFields() {
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

function handleError(error) {
    const errorMessage = document.getElementById('error-message');
    errorMessage.textContent = error;
    for (const element of document.querySelectorAll('.hidable')) {
        element.classList.add('hidden');
    }
    errorMessage.classList.remove('hidden');
}

function clearError() {
    const errorMessage = document.getElementById('error-message');
    errorMessage.textContent = '';
    errorMessage.classList.add('hidden');
    for (const element of document.querySelectorAll('.hidable')) {
        element.classList.remove('hidden');
    }
}

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
    newLanguageSelect.name = `language${parseInt(n) + 1}`;
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

    const languages = songData.languages;
    delete songData.languages;

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
