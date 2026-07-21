(function () {
    "use strict";

    function setupDateFields() {
        const form = document.querySelector("[data-message-search-form]");
        if (!form) {
            return;
        }

        const modeSelect = form.querySelector("[data-date-mode]");
        const singleDateFields = form.querySelectorAll("[data-date-single]");
        const singleDateInput = form.querySelector("#date");
        const singleDateLabel = form.querySelector("[data-date-label]");
        const dateRangeFields = form.querySelectorAll("[data-date-range]");
        const dateRangeInputs = form.querySelectorAll("#date_from, #date_to");

        if (!modeSelect || !singleDateInput || !singleDateLabel) {
            return;
        }

        const singleDateLabels = {
            exact: "On date",
            before: "Before date",
            after: "After date",
        };

        function updateDateFields() {
            const mode = modeSelect.value;
            const showSingleDate = Object.hasOwn(singleDateLabels, mode);
            const showDateRange = mode === "between";

            for (const field of singleDateFields) {
                field.hidden = !showSingleDate;
            }
            singleDateInput.disabled = !showSingleDate;
            singleDateInput.required = showSingleDate;
            if (showSingleDate) {
                singleDateLabel.textContent = singleDateLabels[mode];
            }

            for (const field of dateRangeFields) {
                field.hidden = !showDateRange;
            }
            for (const input of dateRangeInputs) {
                input.disabled = !showDateRange;
                input.required = showDateRange;
            }
        }

        modeSelect.addEventListener("change", updateDateFields);
        form.addEventListener("reset", function () {
            window.setTimeout(updateDateFields, 0);
        });
        updateDateFields();
    }

    function setupParticipantTransfer(transfer) {
        const available = transfer.querySelector("[data-participant-available]");
        const selected = transfer.querySelector("[data-participant-selected]");
        const addButton = transfer.querySelector("[data-participant-add]");
        const removeButton = transfer.querySelector("[data-participant-remove]");
        const inputs = transfer.querySelector("[data-participant-inputs]");
        const required = transfer.dataset.participantRequired !== "false";

        if (!available || !selected || !addButton || !removeButton || !inputs) {
            return;
        }

        function sortOptions(select) {
            const options = Array.from(select.options);
            options.sort(function (first, second) {
                return first.text.localeCompare(second.text, undefined, {sensitivity: "base"});
            });
            for (const option of options) {
                select.append(option);
            }
        }

        function updateButtons() {
            addButton.disabled = available.selectedOptions.length === 0;
            removeButton.disabled = selected.selectedOptions.length === 0;
        }

        function syncParticipants() {
            inputs.replaceChildren();
            for (const option of selected.options) {
                const input = document.createElement("input");
                input.type = "hidden";
                input.name = inputs.dataset.participantField || "participant_id";
                input.value = option.value;
                inputs.append(input);
            }
            selected.setCustomValidity(
                required && selected.options.length === 0
                    ? "Select at least one conversation participant."
                    : ""
            );
            updateButtons();
        }

        function moveOptions(source, destination) {
            const options = Array.from(source.selectedOptions);
            for (const option of options) {
                option.selected = false;
                destination.append(option);
            }
            sortOptions(destination);
            syncParticipants();
            destination.focus();
        }

        addButton.addEventListener("click", function () {
            moveOptions(available, selected);
        });
        removeButton.addEventListener("click", function () {
            moveOptions(selected, available);
        });
        available.addEventListener("change", updateButtons);
        selected.addEventListener("change", updateButtons);
        available.addEventListener("dblclick", function () {
            moveOptions(available, selected);
        });
        selected.addEventListener("dblclick", function () {
            moveOptions(selected, available);
        });
        syncParticipants();
    }

    setupDateFields();
    for (const transfer of document.querySelectorAll("[data-participant-transfer]")) {
        setupParticipantTransfer(transfer);
    }
}());
