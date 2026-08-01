function initializeAuditFilters() {
    const list = document.getElementById("audit-filter-list");
    if (!list || list.dataset.initialized) return;
    list.dataset.initialized = "true";

    const fieldConfig = JSON.parse(list.dataset.fields);
    const initialFilters = JSON.parse(list.dataset.filters);
    const fields = Object.keys(fieldConfig);
    const addButton = document.getElementById("add-audit-filter");
    const resetButton = document.getElementById("reset-audit-filters");
    const hiddenInput = document.getElementById("audit-filters-json");
    const form = list.closest("form");

    function option(value, label, selectedValue) {
        const element = document.createElement("option");
        element.value = String(value);
        element.textContent = label;
        element.selected = String(value) === String(selectedValue);
        return element;
    }

    function valueControl(
        field,
        boundary,
        value,
        operatorValue,
        caseSensitive = false,
        accentSensitive = false
    ) {
        const config = fieldConfig[field];
        const label = boundary === "from" ? "old" : "new";
        const controls = document.createDocumentFragment();
        let control;

        if (config.choices) {
            control = document.createElement("select");
            control.append(option("", `Any ${label} value`, value));
            for (const choice of config.choices) {
                control.append(option(choice.value, choice.label, value));
            }
        } else if (config.type === "boolean") {
            control = document.createElement("select");
            control.append(option("", `Any ${label} value`, value));
            control.append(option("true", "true", value));
            control.append(option("false", "false", value));
        } else {
            control = document.createElement("input");
            control.type = config.type === "numeric" ? "number" : "text";
            control.placeholder = boundary === "from" ? "From" : "To";
            if (control.type === "number") {
                control.step = "any";
                const operator = document.createElement("select");
                operator.className =
                    `audit-filter-${boundary}-operator audit-filter-numeric-operator`;
                operator.setAttribute("aria-label", `${label} value operator`);
                for (const [value, operatorLabel] of [
                    ["eq", "="],
                    ["ne", "≠"],
                    ["lt", "<"],
                    ["gt", ">"],
                    ["lte", "≤"],
                    ["gte", "≥"]
                ]) {
                    operator.append(
                        option(value, operatorLabel, operatorValue ?? "eq")
                    );
                }
                controls.append(operator);
            } else {
                const operator = document.createElement("select");
                operator.className =
                    `audit-filter-${boundary}-operator audit-filter-text-operator`;
                operator.setAttribute("aria-label", `${label} text operator`);
                for (const [value, operatorLabel] of [
                    ["eq", "equals"],
                    ["starts_with", "starts"],
                    ["ends_with", "ends"],
                    ["contains", "contains"]
                ]) {
                    operator.append(
                        option(value, operatorLabel, operatorValue ?? "eq")
                    );
                }
                controls.append(operator);
                const sensitivity = document.createElement("select");
                sensitivity.className =
                    `audit-filter-${boundary}-sensitivity audit-filter-sensitivity`;
                sensitivity.setAttribute(
                    "aria-label",
                    `${label} value case and accent sensitivity`
                );
                sensitivity.title =
                    "—: NOCASE + NOACCENT; CASE: case-sensitive; " +
                    "ACCENT: accent-sensitive; BOTH: case- and accent-sensitive";
                const sensitivityValue = caseSensitive
                    ? (accentSensitive ? "both" : "case")
                    : (accentSensitive ? "accent" : "default");
                sensitivity.append(option("default", "—", sensitivityValue));
                sensitivity.append(option("case", "CASE", sensitivityValue));
                sensitivity.append(option("accent", "ACCENT", sensitivityValue));
                sensitivity.append(option("both", "BOTH", sensitivityValue));
                controls.append(sensitivity);
            }
            control.value = value ?? "";
        }

        control.className = `audit-filter-${boundary}`;
        control.setAttribute(
            "aria-label",
            boundary === "from" ? "Old value" : "New value"
        );
        controls.append(control);
        return controls;
    }

    function renderValues(row, filter = {}) {
        const field = row.querySelector(".audit-filter-field").value;
        const fromContainer = row.querySelector(".audit-filter-from-container");
        const toContainer = row.querySelector(".audit-filter-to-container");
        fromContainer.replaceChildren(
            valueControl(
                field,
                "from",
                filter.from,
                filter.from_operator,
                filter.from_case_sensitive,
                filter.from_accent_sensitive
            )
        );
        toContainer.replaceChildren(
            valueControl(
                field,
                "to",
                filter.to,
                filter.to_operator,
                filter.to_case_sensitive,
                filter.to_accent_sensitive
            )
        );
    }

    function refreshConnectors() {
        const rows = Array.from(list.querySelectorAll(".audit-filter-row"));
        rows.forEach((row, index) => {
            const connector = row.querySelector(".audit-filter-join");
            const whereOption = connector.querySelector('option[value="where"]');
            if (index === 0) {
                if (!whereOption) {
                    const firstOption = option("where", "Where", "where");
                    firstOption.disabled = true;
                    connector.prepend(firstOption);
                }
                connector.value = "where";
                connector.disabled = true;
            } else {
                if (whereOption) whereOption.remove();
                connector.disabled = false;
                if (!["and", "or"].includes(connector.value)) connector.value = "and";
            }
        });
    }

    function addRow(filter = {}) {
        const row = document.createElement("div");
        row.className = "audit-filter-row";

        const connector = document.createElement("select");
        connector.className = "audit-filter-join";
        connector.setAttribute("aria-label", "Boolean connector");
        connector.append(option("and", "AND", filter.join));
        connector.append(option("or", "OR", filter.join));

        const negate = document.createElement("select");
        negate.className = "audit-filter-not";
        negate.setAttribute("aria-label", "Negate filter");
        negate.append(option("", "", filter.not ? "not" : ""));
        negate.append(option("not", "NOT", filter.not ? "not" : ""));

        const fieldSelect = document.createElement("select");
        fieldSelect.className = "audit-filter-field";
        fieldSelect.setAttribute("aria-label", "Field");
        for (const field of fields) {
            fieldSelect.append(option(field, field, filter.field));
        }

        const fromContainer = document.createElement("span");
        fromContainer.className = "audit-filter-from-container";
        const toContainer = document.createElement("span");
        toContainer.className = "audit-filter-to-container";
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.textContent = "Remove";
        removeButton.setAttribute("aria-label", "Remove filter");

        row.append(
            connector,
            negate,
            fieldSelect,
            fromContainer,
            toContainer,
            removeButton
        );
        list.append(row);
        renderValues(row, filter);
        refreshConnectors();

        fieldSelect.addEventListener("change", () => renderValues(row));
        removeButton.addEventListener("click", () => {
            row.remove();
            refreshConnectors();
        });
    }

    function typedValue(field, control) {
        const value = control.value;
        if (value === "") return undefined;
        if (fieldConfig[field].type === "boolean") return value === "true";
        if (fieldConfig[field].type === "numeric") return Number(value);
        return value;
    }

    addButton.addEventListener("click", () => addRow());
    resetButton.addEventListener("click", () => {
        window.location.assign(resetButton.dataset.url);
    });
    form.addEventListener("submit", () => {
        const filters = Array.from(list.querySelectorAll(".audit-filter-row")).map(
            (row, index) => {
                const field = row.querySelector(".audit-filter-field").value;
                const filter = {field};
                if (index > 0) {
                    filter.join = row.querySelector(".audit-filter-join").value;
                }
                if (row.querySelector(".audit-filter-not").value === "not") {
                    filter.not = true;
                }
                for (const boundary of ["from", "to"]) {
                    const control = row.querySelector(`.audit-filter-${boundary}`);
                    const value = typedValue(field, control);
                    if (value !== undefined) {
                        filter[boundary] = value;
                        const operator = row.querySelector(
                            `.audit-filter-${boundary}-operator`
                        );
                        if (operator) filter[`${boundary}_operator`] = operator.value;
                        const sensitivity = row.querySelector(
                            `.audit-filter-${boundary}-sensitivity`
                        );
                        if (sensitivity) {
                            filter[`${boundary}_case_sensitive`] =
                                ["case", "both"].includes(sensitivity.value);
                            filter[`${boundary}_accent_sensitive`] =
                                ["accent", "both"].includes(sensitivity.value);
                        }
                    }
                }
                return filter;
            }
        );
        hiddenInput.value = JSON.stringify(filters);
    });

    initialFilters.forEach(addRow);
}
