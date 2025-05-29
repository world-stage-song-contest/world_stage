function onLoad() {
    const yearSelect = document.getElementById('year');
    yearSelect.value = '';
    const languageCount = document.querySelectorAll('.language-select').length;
    document.getElementById('remove-language-button').disabled = languageCount == 1;
    const form = document.forms.submit_song;
    for (const element of form.elements) {
        if (element.tagName === 'SELECT' || element.tagName === 'TEXTAREA') {
            element.value = '';
        } else if (element.tagName === 'INPUT') {
            if (element.type === 'checkbox' || element.type === 'radio') {
                element.checked = false;
            } else {
                element.value = '';
            }
        }
    }

    const doesMatchCb = document.getElementById('does_match');
    doesMatchCb.checked = true;
    toggleTitleLanguageSelect(doesMatchCb);

    document.querySelectorAll('.time-input').forEach(el => {
        el.addEventListener('input', function (e) {
            let value = el.value.replace(/\D/g, '');

            if (value.length >= 3) {
                value = value.slice(0, 4);
                value = value.slice(0, 2) + ':' + value.slice(2);
            }

            console.log(value)
            el.value = value;
        });
    });
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
    newLanguageLabel.htmlFor = `language${n}`;
    newLanguageLabel.textContent = `Language ${parseInt(n) + 1}`;
    const newLanguageSelect = languageSelect.cloneNode(true);
    newLanguageSelect.name = `language${parseInt(n) + 1}`;
    newLanguageSelect.id = `language${parseInt(n) + 1}`;
    newLanguageSelect.dataset.n = parseInt(n) + 1;

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
    console.log(countriesData);
    const countries = countriesData.countries;
    clearCountriesSelect();
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
        return;
    }
    const songData = await fetchSongData(year, country);
    
    const languages = songData.languages;
    delete songData.languages;

    const form = document.forms.submit_song;
    for (const [key, value] of Object.entries(songData)) {
        if (key == "is_placeholder") continue;
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
    }

    console.log(languages);

    for (const [i, {id, name}] of languages.entries()) {
        if (i != 0) {
            addLanguageRow();
        }
        const languageSelect = document.querySelector(`#language${i + 1}`);
        languageSelect.value = id;
    }
}