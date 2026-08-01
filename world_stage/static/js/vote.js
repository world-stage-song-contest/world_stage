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
}

function clearVotes() {
    document.querySelectorAll('select[name^="pts-"]').forEach((select) => {
        select.value = '';
        select.classList.remove('invalid');
        select.classList.add('valid');
        updateFlag(select);
    });
}
