/**
 * @param {HTMLSelectElement} select
 */
function updateFlag(select) {
    const newCc = select.selectedOptions[0].dataset.country;
    const flagUrl = window.flagStaticUrl(newCc, 30);
    const flagEl = document.getElementById(select.dataset.flag);
    flagEl.src = flagUrl;
}

/**
 * Refresh score options after the official ballot flag changes. The server
 * remains the source of truth for every ballot-entry rule.
 * @param {HTMLSelectElement} countrySelect
 */
async function updateVotingCountry(countrySelect) {
    updateFlag(countrySelect);
    const rulesUrl = countrySelect.dataset.rulesUrl;
    if (!rulesUrl || !countrySelect.value || countrySelect.value === 'XX') return;

    const url = new URL(rulesUrl, window.location.origin);
    url.searchParams.set('country', countrySelect.value);
    const response = await fetch(url, {headers: {'Accept': 'application/json'}});
    if (!response.ok) return;
    const payload = await response.json();
    const rules = payload.rules;
    document.querySelectorAll('[data-pool-song]').forEach((element) => {
        const rule = rules[element.dataset.poolSong];
        element.hidden = rule.kind === 'FORBIDDEN';
        if (element.tagName === 'INPUT') {
            element.disabled = element.hidden;
            element.readOnly = rule.kind === 'FORCED';
            element.max = rule.score_cap;
            if (element.disabled) element.value = 0;
            if (element.readOnly) element.value = rule.required_score;
        }
    });
    updatePoolRemaining();
    const forcedByScore = {};
    Object.entries(rules).forEach(([songId, rule]) => {
        if (rule.kind === 'FORCED') forcedByScore[String(rule.required_score)] = songId;
    });

    document.querySelectorAll('select[name^="pts-"]').forEach((select) => {
        const score = select.name.slice(4);
        const forcedSongId = forcedByScore[score];
        Array.from(select.options).forEach((option) => {
            if (!option.value) {
                option.disabled = Boolean(forcedSongId);
                option.hidden = Boolean(forcedSongId);
                return;
            }
            const rule = rules[option.dataset.songId];
            const allowed = forcedSongId
                ? option.value === forcedSongId
                : rule && rule.kind === 'NORMAL';
            option.disabled = !allowed;
            option.hidden = !allowed;
        });
        if (forcedSongId) {
            select.value = forcedSongId;
        } else if (select.selectedOptions[0]?.disabled) {
            select.value = '';
        }
        updateFlag(select);
    });
    updateRankedValidity();
}

function setVotingValidity(field, message) {
    const invalid = Boolean(message);
    field.setCustomValidity(message);
    field.setAttribute('aria-invalid', String(invalid));
    field.classList.toggle('invalid', invalid);
}

function updateRankedValidity() {
    const fields = [...document.querySelectorAll('select[name^="pts-"]')];
    const counts = new Map();
    fields.forEach((select) => {
        if (select.value) counts.set(select.value, (counts.get(select.value) || 0) + 1);
    });
    fields.forEach((select) => {
        const duplicate = (counts.get(select.value) || 0) > 1;
        setVotingValidity(select, duplicate ? 'Choose each song only once.' : '');
    });
}

function clearVotes() {
    document.querySelectorAll('input[name^="song-"]').forEach((input) => {
        if (!input.readOnly) input.value = 0;
    });
    updatePoolRemaining();
    document.querySelectorAll('select[name^="pts-"]').forEach((select) => {
        select.value = '';
        updateFlag(select);
    });
    updateRankedValidity();
}

function updatePoolRemaining() {
    const remaining = document.getElementById('pool-remaining');
    if (!remaining) return;
    const fields = [...document.querySelectorAll('input[name^="song-"]')];
    const used = fields.filter((input) => !input.disabled)
        .reduce((total, input) => total + (Number(input.value) || 0), 0);
    const pointsLeft = Number(remaining.dataset.total) - used;
    document.getElementById('pool-remaining-value').textContent = pointsLeft;
    fields.forEach((input) => {
        const score = Number(input.value) || 0;
        const minimum = Number(input.dataset.minScore);
        let message = '';
        if (!input.disabled && score !== 0) {
            if (score < minimum) message = `Give at least ${minimum} points or leave this song at zero.`;
            else if (pointsLeft < 0) message = 'You have used more points than available.';
        }
        setVotingValidity(input, message);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    updateRankedValidity();
    const label = document.getElementById('pool-remaining-label');
    if (!label) return;
    label.textContent = 'points remaining';
    updatePoolRemaining();
});
