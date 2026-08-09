function initializeVerifications() {
    const updateSourceButtons = () => {
        document.querySelectorAll('.verification-source-dialog-open').forEach((button) => {
            const preview = button.previousElementSibling;
            button.hidden = preview.scrollHeight <= preview.clientHeight + 1;
        });
    };
    const hidePlaceholders = document.getElementById('hide-verification-placeholders');
    const hideHistorical = document.getElementById('hide-verification-historical');
    const statusFilters = document.querySelectorAll('.verification-status-filter');
    const verificationRows = document.querySelectorAll('tr[data-verification-kind]');
    const filterEmpty = document.getElementById('verification-filter-empty');
    const filterStorageKeys = {
        placeholders: 'verifications.hidePlaceholders',
        historical: 'verifications.hideHistorical',
    };

    const loadFilterPreference = (key) => {
        try {
            return window.localStorage.getItem(key) === 'true';
        } catch (_error) {
            return false;
        }
    };
    const saveFilterPreference = (key, value) => {
        try {
            window.localStorage.setItem(key, String(value));
        } catch (_error) {
            // Filtering still works when storage is unavailable.
        }
    };
    const applyFilters = () => {
        const visibleStatuses = new Set(
            Array.from(statusFilters)
                .filter((checkbox) => checkbox.checked)
                .map((checkbox) => checkbox.value),
        );
        let visibleRows = 0;

        verificationRows.forEach((row) => {
            const kind = row.dataset.verificationKind;
            const hiddenByKind = (kind === 'placeholder' && hidePlaceholders.checked)
                || (kind === 'historical' && hideHistorical.checked);
            const hiddenByStatus = kind !== 'placeholder'
                && !visibleStatuses.has(row.dataset.verificationStatus);
            row.hidden = hiddenByKind || hiddenByStatus;
            if (!row.hidden) visibleRows += 1;
        });

        if (filterEmpty) filterEmpty.hidden = visibleRows !== 0;
        updateSourceButtons();
    };

    if (hidePlaceholders && hideHistorical) {
        hidePlaceholders.checked = loadFilterPreference(filterStorageKeys.placeholders);
        hideHistorical.checked = loadFilterPreference(filterStorageKeys.historical);
        hidePlaceholders.addEventListener('change', () => {
            saveFilterPreference(filterStorageKeys.placeholders, hidePlaceholders.checked);
            applyFilters();
        });
        hideHistorical.addEventListener('change', () => {
            saveFilterPreference(filterStorageKeys.historical, hideHistorical.checked);
            applyFilters();
        });
        statusFilters.forEach((checkbox) => {
            checkbox.addEventListener('change', applyFilters);
        });
        applyFilters();
    }

    updateSourceButtons();
    window.addEventListener('resize', updateSourceButtons);

    document.querySelectorAll('.verification-dialog').forEach((dialog) => {
        dialog.querySelector('.verification-dialog-close').addEventListener('click', () => {
            dialog.close();
        });
        dialog.addEventListener('click', (event) => {
            const bounds = dialog.getBoundingClientRect();
            const outside = event.clientX < bounds.left
                || event.clientX > bounds.right
                || event.clientY < bounds.top
                || event.clientY > bounds.bottom;
            if (outside) dialog.close();
        });
    });

    document.querySelectorAll('.verification-dialog-open').forEach((button) => {
        const dialog = document.getElementById(button.dataset.dialog);
        button.addEventListener('click', () => dialog.showModal());
    });

    document.querySelectorAll('.verification-status-form').forEach((form) => {
        const status = form.elements.status;
        const message = form.elements.message;
        const dialog = form.querySelector('.verification-status-dialog');
        const prompt = dialog.querySelector('.verification-dialog-prompt');

        form.addEventListener('submit', (event) => {
            const needsMessage = status.value === 'rejected' || status.value === 'more-info';
            if (!needsMessage || dialog.open) return;

            event.preventDefault();
            prompt.textContent = status.value === 'rejected'
                ? 'Tell the submitter why this song was rejected.'
                : 'Tell the submitter what additional information is needed.';
            message.required = true;
            dialog.showModal();
            message.focus();
        });

        dialog.addEventListener('close', () => {
            message.required = false;
        });
    });
}
