function openShowMedia(button) {
    const dialog = document.getElementById(button.dataset.dialog);
    dialog.querySelector('form').reset();
    dialog.showModal();
}
