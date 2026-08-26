function localIsoTimestamp(instant) {
    const pad = value => String(value).padStart(2, '0');
    const timezoneName = new Intl.DateTimeFormat(undefined, {
        timeZoneName: 'short'
    }).formatToParts(instant).find(part => part.type === 'timeZoneName')?.value;

    return `${instant.getFullYear()}-${pad(instant.getMonth() + 1)}-${pad(instant.getDate())}`
        + ` ${pad(instant.getHours())}:${pad(instant.getMinutes())}`
        + (timezoneName ? ` ${timezoneName}` : '');
}

function updateLocalDatetime(input) {
    const output = document.querySelector(`[data-local-datetime-for="${input.id}"]`);
    if (!output) return;

    if (!input.value) {
        output.textContent = '—';
        return;
    }

    const instant = new Date(`${input.value}Z`);
    output.textContent = Number.isNaN(instant.getTime())
        ? 'Invalid date and time'
        : localIsoTimestamp(instant);
}

function initializeUtcDatetimes() {
    for (const input of document.querySelectorAll('[data-utc-datetime]')) {
        input.addEventListener('input', () => updateLocalDatetime(input));
        updateLocalDatetime(input);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeUtcDatetimes);
} else {
    initializeUtcDatetimes();
}
