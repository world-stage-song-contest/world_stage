import {completeWsql} from './wsql-completion.mjs';

const searchType = document.getElementById('search-type');
const searchQuery = document.getElementById('search-query');
const searchSubmit = document.getElementById('search-submit');
const suggestions = document.getElementById('search-suggestions');
const autocomplete = document.getElementById('search-autocomplete');
const schemaElement = document.getElementById('search-schema');
const schema = JSON.parse(schemaElement.textContent);
const choiceFields = new Set(JSON.parse(schemaElement.dataset.choiceFields));
const choiceCache = new Map();
const pendingChoices = new Map();
const failedChoices = new Map();
let suggestionVersion = 0;
let completion = null;
let selected = -1;
let composing = false;

function closeSuggestions() {
    suggestionVersion++;
    completion = null;
    selected = -1;
    suggestions.hidden = true;
    suggestions.replaceChildren();
    searchQuery.removeAttribute('aria-activedescendant');
}

async function loadChoices(resultType, field) {
    if (!choiceFields.has(field) || typeof fetch !== 'function' ||
        typeof AbortController !== 'function') return;
    const key = `${resultType}:${field}`;
    if (choiceCache.get(resultType)?.[field] || Date.now() - (failedChoices.get(key) ?? 0) < 30000) return;
    const version = suggestionVersion;
    if (!pendingChoices.has(key)) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        const url = new URL(schemaElement.dataset.choicesUrl, location.href);
        url.search = new URLSearchParams({type: resultType, field});
        const request = fetch(url, {headers: {'Accept': 'application/json'}, signal: controller.signal})
            .then(response => {
                if (!response.ok) throw new Error('Could not load value suggestions');
                return response.json();
            }).then(({result}) => {
                if (!Array.isArray(result)) throw new Error('Invalid value suggestions');
                if (!choiceCache.has(resultType)) choiceCache.set(resultType, {});
                choiceCache.get(resultType)[field] = result;
            }).catch(() => failedChoices.set(key, Date.now()))
            .finally(() => {
                clearTimeout(timeout);
                pendingChoices.delete(key);
            });
        pendingChoices.set(key, request);
    }
    await pendingChoices.get(key);
    if (suggestionVersion === version && document.activeElement === searchQuery &&
        searchType.value === resultType && choiceCache.get(resultType)?.[field]) showSuggestions();
}

function updateSearchAvailability() {
    const disabled = searchType.value === '';
    searchQuery.disabled = disabled;
    searchSubmit.disabled = disabled;
    const saveButton = document.getElementById('save-query');
    if (saveButton) saveButton.disabled = disabled;
    closeSuggestions();
}

function acceptSuggestion(index) {
    if (!completion) return;
    const text = completion.suggestions[index];
    const prefix = /[A-Za-z_0-9]/.test(searchQuery.value[completion.start - 1] ?? '') ? ' ' : '';
    const suffix = /\s/.test(searchQuery.value[completion.end] ?? '') ? '' : ' ';
    searchQuery.setRangeText(prefix + text + suffix, completion.start, completion.end, 'end');
    closeSuggestions();
    searchQuery.focus();
    searchQuery.dispatchEvent(new Event('input', {bubbles: true}));
}

function showSuggestions() {
    closeSuggestions();
    if (!autocomplete.checked || composing || searchQuery.disabled ||
        searchQuery.selectionStart !== searchQuery.selectionEnd) return;
    completion = completeWsql(searchQuery.value, searchQuery.selectionStart, schema, searchType.value,
        choiceCache.get(searchType.value));
    if (completion.valueField) loadChoices(searchType.value, completion.valueField);
    if (!completion.suggestions.length) return;
    completion.suggestions.forEach((text, index) => {
        const option = document.createElement('div');
        option.id = `search-suggestion-${index}`;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        option.textContent = text;
        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => acceptSuggestion(index));
        suggestions.append(option);
    });
    suggestions.hidden = false;
}

try {
    autocomplete.checked = localStorage.getItem('wsql-autocomplete') !== 'off';
} catch {}
searchQuery.setAttribute('aria-autocomplete', autocomplete.checked ? 'list' : 'none');
searchQuery.setAttribute('aria-controls', 'search-suggestions');
document.getElementById('search-autocomplete-control').hidden = false;
autocomplete.addEventListener('change', () => {
    try {
        localStorage.setItem('wsql-autocomplete', autocomplete.checked ? 'on' : 'off');
    } catch {}
    searchQuery.setAttribute('aria-autocomplete', autocomplete.checked ? 'list' : 'none');
    closeSuggestions();
});
searchType.addEventListener('change', updateSearchAvailability);
window.addEventListener('pageshow', updateSearchAvailability);
searchQuery.addEventListener('input', showSuggestions);
searchQuery.addEventListener('click', showSuggestions);
searchQuery.addEventListener('blur', closeSuggestions);
searchQuery.addEventListener('select', () => {
    if (searchQuery.selectionStart !== searchQuery.selectionEnd) closeSuggestions();
});
searchQuery.addEventListener('compositionstart', () => {
    composing = true;
    closeSuggestions();
});
searchQuery.addEventListener('compositionend', () => {
    composing = false;
    showSuggestions();
});
searchQuery.addEventListener('keydown', event => {
    if (event.isComposing || composing) return;
    if (event.ctrlKey && event.code === 'Space') {
        event.preventDefault();
        showSuggestions();
        return;
    }
    if (suggestions.hidden) return;
    if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') event.preventDefault();
        closeSuggestions();
    } else if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) {
        closeSuggestions();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const count = completion.suggestions.length;
        selected = selected < 0 ? (event.key === 'ArrowDown' ? 0 : count - 1) :
            (selected + (event.key === 'ArrowDown' ? 1 : count - 1)) % count;
        [...suggestions.children].forEach((option, index) => {
            option.setAttribute('aria-selected', String(index === selected));
        });
        const option = suggestions.children[selected];
        searchQuery.setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Enter' && selected >= 0) {
        event.preventDefault();
        acceptSuggestion(selected);
    } else if (['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
        closeSuggestions();
    }
});
updateSearchAvailability();

const searchForm = searchQuery.form;
const searchPage = searchForm.closest('.page-search');
const searchStatus = document.getElementById('search-status');
const editingSavedId = document.getElementById('editing-saved-id');
let pendingSearch = null;

searchQuery.addEventListener('savedqueryloaded', () => {
    pendingSearch?.abort();
    pendingSearch = null;
    document.getElementById('search-results').replaceChildren();
    searchStatus.textContent = '';
    searchStatus.hidden = true;
    setSearchPending(false);
});

function updateQueryError() {
    const hasError = document.getElementById('search-error') !== null;
    if (hasError) {
        searchQuery.setAttribute('aria-invalid', 'true');
        searchQuery.setAttribute('aria-describedby', 'search-error');
    } else {
        searchQuery.removeAttribute('aria-invalid');
        searchQuery.removeAttribute('aria-describedby');
    }
}

function setSearchPending(pending) {
    document.getElementById('search-results').setAttribute('aria-busy', String(pending));
    searchForm.setAttribute('aria-busy', String(pending));
    searchPage.querySelectorAll('.search-pagination button').forEach(button => {
        button.disabled = pending;
    });
}

function showRequestFailure(data) {
    const feedback = document.getElementById('search-feedback');
    const message = document.createElement('p');
    message.className = 'error';
    message.setAttribute('role', 'alert');
    message.textContent = 'Could not load the search results. Try again or use a full-page search.';
    const fallback = document.createElement('button');
    fallback.type = 'button';
    fallback.textContent = 'Use full-page search';
    fallback.addEventListener('click', () => {
        const form = document.createElement('form');
        form.method = 'post';
        form.action = searchForm.action;
        form.hidden = true;
        for (const [name, value] of data) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = value;
            form.append(input);
        }
        searchPage.append(form);
        form.submit();
    });
    feedback.replaceChildren(message, fallback);
    updateQueryError();
}

async function loadSearch(data, {remember = true, focusResults = false} = {}) {
    pendingSearch?.abort();
    const controller = new AbortController();
    pendingSearch = controller;
    const focused = document.activeElement;
    closeSuggestions();
    setSearchPending(true);
    searchStatus.hidden = false;
    searchStatus.textContent = 'Searching…';
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(searchForm.action, {
            method: 'POST',
            body: data,
            headers: {'Accept': 'text/html', 'X-Search-Fragment': '1'},
            signal: controller.signal,
        });
        const source = await response.text();
        if (pendingSearch !== controller) return;
        if (![200, 400, 413, 503].includes(response.status) ||
            !response.headers.get('Content-Type')?.includes('text/html')) throw new Error('Invalid response');
        const documentResult = new DOMParser().parseFromString(source, 'text/html');
        const feedback = documentResult.getElementById('search-feedback');
        const results = documentResult.getElementById('search-results');
        if (!feedback || !results) throw new Error('Missing search response');
        const keepFocus = document.activeElement !== focused && document.activeElement !== document.body;
        document.getElementById('search-feedback').replaceWith(feedback);
        document.getElementById('search-results').replaceWith(results);
        updateQueryError();
        searchStatus.textContent = response.ok ? '' : 'Check the search error.';
        searchStatus.hidden = response.ok;
        if (!keepFocus) {
            if (!response.ok) document.getElementById('search-error')?.focus();
            else if (focusResults) document.getElementById('search-results-heading')?.focus();
        }
        if (remember) {
            const state = {q: data.get('q'), type: data.get('type'), offset: data.get('offset'),
                editingSavedId: editingSavedId?.value ?? '', searched: true};
            try {
                history.pushState({...history.state, wsqlSearch: state}, '');
            } catch {}
        }
    } catch {
        if (pendingSearch !== controller) return;
        showRequestFailure(data);
        searchStatus.textContent = '';
        searchStatus.hidden = true;
    } finally {
        clearTimeout(timeout);
        if (pendingSearch === controller) {
            pendingSearch = null;
            setSearchPending(false);
        }
    }
}

if (typeof fetch === 'function' && typeof AbortController === 'function') {
    searchPage.addEventListener('submit', event => {
        if (event.submitter?.id === 'save-query') return;
        const form = event.target;
        if (!form.matches('.search-form, .search-pagination')) return;
        event.preventDefault();
        const data = new FormData(form);
        data.set('offset', event.submitter?.value || '0');
        loadSearch(data, {focusResults: true});
    });

    function restoreSearch(state) {
        pendingSearch?.abort();
        pendingSearch = null;
        searchQuery.value = state.q;
        searchType.value = state.type;
        if (editingSavedId) editingSavedId.value = state.editingSavedId ?? '';
        updateSearchAvailability();
        if (state.searched) {
            const data = new FormData();
            for (const name of ['q', 'type', 'offset']) data.set(name, state[name]);
            loadSearch(data, {remember: false});
        } else {
            document.getElementById('search-results').replaceChildren();
            document.getElementById('search-feedback').replaceChildren();
            searchStatus.textContent = '';
            searchStatus.hidden = true;
            updateQueryError();
            setSearchPending(false);
        }
    }

    const savedSearch = history.state?.wsqlSearch;
    if (savedSearch) restoreSearch(savedSearch);
    else {
        const results = document.getElementById('search-results');
        try {
            history.replaceState({...history.state, wsqlSearch: {
                q: searchQuery.value,
                type: searchType.value,
                editingSavedId: editingSavedId?.value ?? '',
                offset: results.dataset.offset,
                searched: results.dataset.searched === 'true',
            }}, '');
        } catch {}
    }
    window.addEventListener('popstate', event => {
        if (event.state?.wsqlSearch) restoreSearch(event.state.wsqlSearch);
    });
}
