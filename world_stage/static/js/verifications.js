function initializeVerifications() {
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
