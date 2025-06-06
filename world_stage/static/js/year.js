/**
 * @param {HTMLInputElement} cb 
 */
function hideNonPlaceholders(cb) {
    if (cb.checked) {
        document.querySelectorAll('tr.finalised').forEach(el => el.classList.add('collapsed'));
    } else {
        document.querySelectorAll('tr.finalised').forEach(el => el.classList.remove('collapsed'));
    }
}

/**
 * 
 * @param {HTMLTableRowElement} row 
 */
function revealRow(row) {
    row.classList.remove("unrevealed");
}