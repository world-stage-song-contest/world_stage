function getCookieValue(name) {
    const cookies = document.cookie.split('; ');
    for (let cookie of cookies) {
        const [key, ...valParts] = cookie.split('=');
        if (key === name) {
            return valParts.join('=');
        }
    }
    return null;
}

function getKVCookieValue(cookie, key) {
    if (cookie) {
        const keyValuePairs = cookie.split(';');
        for (let pair of keyValuePairs) {
            const [k, v] = pair.split('=');
            if (k.trim() === key) {
                return decodeURIComponent(v);
            }
        }
    }
    return null;
}

function setTheme() {
    const preferences = getCookieValue('preferences');
    const theme = getKVCookieValue(preferences, 'theme');

    if (theme == 'dark') {
        document.documentElement.dataset.theme = 'dark';
    } else if (theme == 'light') {
        document.documentElement.dataset.theme = 'light';
    } else {
        delete document.documentElement.dataset.theme;
    }
}

/**
 * @param {HTMLElement} element
 */
function showTooltip(element) {
    const tooltipText = element.title;
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    tooltip.textContent = tooltipText;
    document.body.appendChild(tooltip);
    const rect = element.getBoundingClientRect();
    tooltip.style.left = `${rect.left + window.scrollX}px`;
    tooltip.style.top = `${rect.bottom + window.scrollY}px`;
    element.addEventListener('mouseleave', () => {
        document.body.removeChild(tooltip);
    });
    element.addEventListener('click', () => {
        document.body.removeChild(tooltip);
    });
    element.addEventListener('touchend', () => {
        document.body.removeChild(tooltip);
    });
}