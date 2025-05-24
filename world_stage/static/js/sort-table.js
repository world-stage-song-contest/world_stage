function sortTable(clickedEl, tableEl, columnIndex, direction) {
    const tbody = tableEl.querySelector('tbody');
    const thead = tableEl.querySelector('thead');
    if (!tbody) return;

    const rows = Array.from(tbody.rows);
    const sortedHeader = thead.querySelector('th[aria-sort]');

    const condA = sortedHeader && sortedHeader != clickedEl;
    const condB = direction === 'none';

    if (condA) {
        sortedHeader.removeAttribute('aria-sort');
    }

    if (condA || condB) {
        rows.sort((a, b) => {
            const aIndex = parseInt(a.getAttribute('data-value'), 10);
            const bIndex = parseInt(b.getAttribute('data-value'), 10);
            return aIndex - bIndex;
        });
    }

    if (condB) {
        rows.forEach(row => tbody.appendChild(row));
        return;
    }

    const dirMultiplier = direction === 'ascending' ? 1 : -1;

    rows.sort((a, b) => {
        const aCell = a.cells[columnIndex];
        const bCell = b.cells[columnIndex];

        const aRaw = aCell?.getAttribute('data-value') ?? aCell?.textContent ?? '';
        const bRaw = bCell?.getAttribute('data-value') ?? bCell?.textContent ?? '';

        const aVal = aRaw.trim();
        const bVal = bRaw.trim();

        const aEmpty = aVal === '';
        const bEmpty = bVal === '';
        if (aEmpty && !bEmpty) return 1;
        if (!aEmpty && bEmpty) return -1;
        if (aEmpty && bEmpty) return 0;

        const aNum = parseFloat(aVal);
        const bNum = parseFloat(bVal);
        const aIsNum = !isNaN(aNum);
        const bIsNum = !isNaN(bNum);

        if (aIsNum && bIsNum) {
            return (aNum - bNum) * dirMultiplier;
        }

        return aVal.localeCompare(bVal, undefined, { numeric: true }) * dirMultiplier;
    });

    rows.forEach(row => tbody.appendChild(row));
}

function addTableListeners() {
    for (const table of document.querySelectorAll('table.sortable')) {
        const ths = table.querySelectorAll('th');
        ths.forEach((th, i) => {
            if (th.classList.contains('sortable')) {
                th.addEventListener('click', () => {
                    const currentSort = th.ariaSort;

                    let newSort = 'none';
                    if (currentSort == null || currentSort === 'none') {
                        newSort = 'ascending';
                    } else if (currentSort === 'ascending') {
                        newSort = 'descending';
                    } else if (currentSort === 'descending') {
                        newSort = 'none';
                    }
                    console.log(currentSort, newSort);
                    th.ariaSort = newSort;
                    sortTable(th, table, i, newSort);
                });
            }
        });
    }
}

window.addEventListener('load', addTableListeners)