const savedForm = document.getElementById('saved-search-form');
const dialog = document.getElementById('saved-search-dialog');

if (savedForm && dialog && typeof dialog.showModal === 'function' && typeof fetch === 'function' &&
    typeof AbortController === 'function') {
    const content = document.getElementById('saved-search-dialog-content');
    const selector = document.getElementById('saved-search');
    const query = document.getElementById('search-query');
    const type = document.getElementById('search-type');
    const saveButton = document.getElementById('save-query');
    const editingId = document.getElementById('editing-saved-id');
    let pending = null;

    function showError(message) {
        const error = document.createElement('p');
        error.className = 'error';
        error.setAttribute('role', 'alert');
        error.textContent = message;
        content.append(error);
    }

    function updateDefaults() {
        content.querySelectorAll('input[name^="default_enabled."]').forEach(checkbox => {
            const name = checkbox.name.slice('default_enabled.'.length);
            const input = checkbox.form.elements.namedItem('default.' + name);
            const nullDefault = checkbox.form.elements.namedItem('default_null.' + name);
            nullDefault.disabled = !checkbox.checked;
            input.disabled = !checkbox.checked || nullDefault.checked;
        });
    }

    async function send(url, data, method = 'POST') {
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        const previous = document.createDocumentFragment();
        previous.append(...content.childNodes);
        const heading = document.createElement('h2');
        heading.id = 'search-dialog-title';
        heading.textContent = 'Loading…';
        content.append(heading);
        if (!dialog.open) dialog.showModal();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const target = new URL(url, window.location.href);
            if (method === 'GET') target.search = new URLSearchParams(data).toString();
            const response = await fetch(target, {
                method, body: method === 'GET' ? undefined : data,
                headers: {'Accept': 'text/html', 'X-Search-Dialog': '1'}, signal: controller.signal,
            });
            if (pending !== controller) return;
            if (response.headers.get('Content-Type')?.includes('application/json')) {
                const result = await response.json();
                if (pending !== controller) return;
                if (!response.ok) throw new Error('The saved query could not be loaded.');
                if (result.saved) {
                    let option = [...selector.options].find(item => item.value === String(result.saved.id));
                    if (!option) {
                        option = new Option(result.saved.name, result.saved.id);
                        selector.add(option);
                    }
                    option.textContent = result.saved.name;
                    option.selected = true;
                    editingId.value = result.saved.id;
                } else if (typeof result.query === 'string') {
                    query.value = result.query;
                    type.value = result.type;
                    editingId.value = result.editingSavedId ?? '';
                    type.dispatchEvent(new Event('change', {bubbles: true}));
                    query.removeAttribute('aria-invalid');
                    query.removeAttribute('aria-describedby');
                    document.getElementById('search-feedback').replaceChildren();
                    query.dispatchEvent(new Event('savedqueryloaded', {bubbles: true}));
                } else throw new Error('The server returned an unexpected response.');
                dialog.close();
                query.focus();
            } else {
                const source = await response.text();
                if (pending !== controller) return;
                const page = new DOMParser().parseFromString(source, 'text/html');
                const form = page.querySelector('.search-parameters');
                if (!form) throw new Error('Could not load the saved query. Check that you are signed in.');
                content.replaceChildren(form);
                updateDefaults();
                content.querySelector('input:not([type="hidden"]), select, textarea')?.focus();
            }
        } catch {
            if (pending === controller) {
                content.replaceChildren(previous);
                showError('Could not complete this request. Close this window and try again.');
            }
        } finally {
            clearTimeout(timeout);
            if (pending === controller) pending = null;
        }
    }

    document.getElementById('close-saved-search-dialog').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        pending?.abort();
        pending = null;
    });
    content.addEventListener('change', updateDefaults);
    content.addEventListener('submit', event => {
        event.preventDefault();
        send(event.target.action, new FormData(event.target));
    });
    query.form.addEventListener('submit', event => {
        if (event.submitter !== saveButton) return;
        event.preventDefault();
        send(saveButton.formAction, new FormData(query.form));
    });
    savedForm.addEventListener('submit', event => {
        event.preventDefault();
        const action = event.submitter?.hasAttribute('formaction') ? event.submitter.formAction : savedForm.action;
        send(action, new FormData(savedForm), 'GET');
    });
}
