function formatLocalDatetime(instant) {
    const months = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    const pad = value => String(value).padStart(2, '0');

    return `${pad(instant.getDate())} ${months[instant.getMonth()]} ${instant.getFullYear()}`
        + `, ${pad(instant.getHours())}:${pad(instant.getMinutes())}`;
}

function initializeLocalDatetimes() {
    for (const element of document.querySelectorAll('[data-local-datetime]')) {
        const instant = new Date(element.dateTime);
        if (!Number.isNaN(instant.getTime())) {
            element.textContent = formatLocalDatetime(instant);
        }
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeLocalDatetimes);
} else {
    initializeLocalDatetimes();
}
