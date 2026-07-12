/**
 * @param {HTMLSelectElement} select
 */
function updateFlag(select) {
    const newCc = select.selectedOptions[0].dataset.country;
    const flagUrl = `/flag/${newCc}.svg?s=30`
    const flagEl = document.getElementById(select.dataset.flag);
    flagEl.src = flagUrl;
}

function clearVotes() {
    document.querySelectorAll('select[name^="pts-"]').forEach((select) => {
        select.value = '';
        select.classList.remove('invalid');
        select.classList.add('valid');
        updateFlag(select);
    });
}
