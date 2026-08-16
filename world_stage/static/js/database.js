function databaseResultText(value) {
    if (value === null) return "NULL";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function formatUnicodeResultTable(headers, rows, numericHeaders = []) {
    if (!headers.length) return "";
    const numericColumns = new Set(numericHeaders);
    const linesFor = value => databaseResultText(value).replace(/\r\n?/g, "\n").replaceAll("\t", "    ").split("\n");
    const widthOf = value => Array.from(value).length;
    const cellLines = rows.map(row => headers.map(header => linesFor(row[header])));
    const widths = headers.map((header, column) => Math.max(
        widthOf(String(header)),
        ...cellLines.map(row => Math.max(...row[column].map(widthOf)))
    ));
    const fill = (character, count) => character.repeat(count + 2);
    const border = (left, middle, right, character) => left + widths.map(width => fill(character, width)).join(middle) + right;
    const pad = (value, width, rightAligned = false) => {
        const padding = " ".repeat(width - widthOf(value));
        return rightAligned ? padding + value : value + padding;
    };
    const output = [
        border("┌", "┬", "┐", "─"),
        "│ " + headers.map((header, column) => pad(String(header), widths[column])).join(" │ ") + " │",
        border("╞", "╪", "╡", "═")
    ];
    rows.forEach((row, rowIndex) => {
        const height = Math.max(...cellLines[rowIndex].map(lines => lines.length));
        for (let line = 0; line < height; line += 1) {
            const cells = headers.map((header, column) => {
                const value = cellLines[rowIndex][column][line] || "";
                const numeric = row[header] !== null && (typeof row[header] === "number" || numericColumns.has(header));
                return pad(value, widths[column], line === 0 && numeric);
            });
            output.push("│ " + cells.join(" │ ") + " │");
        }
    });
    output.push(border("└", "┴", "┘", "─"));
    return output.join("\n");
}

function initializeDatabaseWorkbench() {
    const root = document.getElementById("database-workbench");
    if (!root || root.dataset.initialized) return;
    root.dataset.initialized = "true";

    const api = root.dataset.apiRoot;
    const state = {
        schema: { objects: [], relationships: [] },
        objectMap: new Map(),
        operation: "select",
        savedQueries: [],
        disabledSavedTags: new Set(),
        editingSavedId: null,
        pendingExecution: null,
        pendingSave: null,
        explorer: null
    };
    let valueControlId = 0;
    const tabNames = new Set(["builder", "sql", "saved", "explorer"]);

    const byId = id => document.getElementById(id);
    const status = byId("db-global-status");
    const containers = {
        joins: byId("db-joins"),
        results: byId("db-result-columns"),
        groups: byId("db-group-fields"),
        aggregates: byId("db-aggregates"),
        values: byId("db-values"),
        filters: byId("db-filters"),
        having: byId("db-having"),
        order: byId("db-order"),
        sqlParameters: byId("db-sql-parameters")
    };

    function setStatus(message, kind = "") {
        status.textContent = message;
        status.className = `db-status ${kind}`;
    }

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json", ...(options.headers || {}) },
            ...options
        });
        if (!response.ok) {
            let message = `Request failed (${response.status})`;
            try { message = (await response.json()).error || message; } catch (_) { /* noop */ }
            throw new Error(message);
        }
        if (response.status === 204) return null;
        return response.json();
    }

    function makeOption(value, label, selected) {
        const item = document.createElement("option");
        item.value = value;
        item.textContent = label;
        item.selected = String(value) === String(selected);
        return item;
    }

    function fillSelect(select, choices, selected, placeholder = "") {
        const options = choices.map(choice => makeOption(choice.value, choice.label, selected));
        if (placeholder) options.unshift(makeOption("", placeholder, selected || ""));
        select.replaceChildren(...options);
    }

    function removeButton(row, onRemove = updatePreview) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Remove";
        button.addEventListener("click", () => { row.remove(); onRemove(); });
        return button;
    }

    function object(name) { return state.objectMap.get(name); }
    function baseTable() { return byId("db-base-table").value; }
    function baseAlias() { return byId("db-base-alias").value.trim() || baseTable(); }

    function joinedAliases() {
        const aliases = [{ alias: baseAlias(), table: baseTable() }];
        containers.joins.querySelectorAll(".join-row").forEach(row => {
            aliases.push({ alias: row.querySelector(".join-alias").value.trim(), table: row.querySelector(".join-table").value });
        });
        return aliases.filter(item => item.alias && object(item.table));
    }

    function columnChoices({ includeOutputs = false } = {}) {
        const choices = [];
        for (const item of joinedAliases()) {
            for (const column of object(item.table)?.columns || []) {
                choices.push({ value: `${item.alias}.${column.name}`, label: `${item.alias}.${column.name}` });
            }
        }
        if (includeOutputs) {
            containers.aggregates.querySelectorAll(".aggregate-row").forEach(row => {
                const alias = row.querySelector(".aggregate-alias").value.trim();
                if (alias) choices.push({ value: `@${alias}`, label: alias });
            });
        }
        return choices;
    }

    function parseColumn(value) {
        const dot = value.indexOf(".");
        return { kind: "column", table: value.slice(0, dot), column: value.slice(dot + 1) };
    }

    function metadataForColumn(value) {
        const ref = parseColumn(value);
        const alias = joinedAliases().find(item => item.alias === ref.table);
        return object(alias?.table)?.columns.find(column => column.name === ref.column);
    }

    function parameterTypeForColumn(value) {
        const type = (metadataForColumn(value)?.type || "text").toLowerCase();
        if (["smallint", "integer", "bigint"].includes(type)) return "integer";
        if (["numeric", "decimal", "real", "double precision"].includes(type)) return "number";
        if (type === "boolean") return "boolean";
        if (type === "date") return "date";
        if (type.includes("timestamp")) return "datetime";
        if (type === "text" || type.includes("character") || type === "citext") return "text";
        return "other";
    }

    function literalValue(raw, columnName, forceNumber = false) {
        const type = forceNumber ? "number" : parameterTypeForColumn(columnName);
        if (raw === "") return raw;
        if (type === "integer") {
            const value = Number.parseInt(raw, 10);
            return Number.isNaN(value) ? raw : value;
        }
        if (type === "number") {
            const value = Number(raw);
            return Number.isNaN(value) ? raw : value;
        }
        if (type === "boolean") return String(raw).toLowerCase() === "true";
        return raw;
    }

    function parameterName(raw) {
        const match = raw.trim().match(/^@([A-Za-z_][A-Za-z0-9_]*)$/);
        return match?.[1] || "";
    }

    function literalInputValue(raw) {
        return raw.replace(/\\([@\\])/g, "$1");
    }

    function builderInputValue(value) {
        if (value?.kind === "parameter") return `@${value.name || ""}`;
        const literal = value?.value ?? "";
        if (typeof literal !== "string") return literal;
        const escaped = literal.replaceAll("\\", "\\\\");
        return escaped.startsWith("@") ? `\\${escaped}` : escaped;
    }

    function valueCategory(columnName, forceNumber = false) {
        if (forceNumber) return "number";
        const metadata = metadataForColumn(columnName);
        if (metadata?.references) return "foreign-key";
        const type = (metadata?.type || "text").toLowerCase();
        if (["smallint", "integer", "bigint"].includes(type)) return "integer";
        if (["numeric", "decimal", "real", "double precision"].includes(type)) return "number";
        if (type === "boolean") return "boolean";
        if (type === "date") return "date";
        if (type.includes("timestamp")) return "datetime";
        return "text";
    }

    function operatorChoices(category, nullable = true) {
        const equality = [["eq", "is"], ["ne", "is not"]];
        const comparisons = [["lt", "less than"], ["lte", "at most"], ["gt", "greater than"], ["gte", "at least"]];
        const text = [
            ["contains", "contains"], ["starts_with", "starts with"], ["ends_with", "ends with"],
            ["like", "matches LIKE pattern"], ["ilike", "matches ILIKE pattern"]
        ];
        const empty = nullable ? [["is_null", "is null"], ["is_not_null", "is not null"]] : [];
        const choices = category === "text"
            ? [...equality, ...text, ...empty]
            : ["integer", "number", "date", "datetime"].includes(category)
                ? [...equality, ...comparisons, ...empty]
                : [...equality, ...empty];
        return choices.map(([value, label]) => ({ value, label }));
    }

    function configureOperator(select, columnName, forceNumber = false) {
        const current = select.value;
        const metadata = metadataForColumn(columnName);
        const choices = operatorChoices(valueCategory(columnName, forceNumber), metadata?.nullable !== false);
        fillSelect(select, choices, choices.some(item => item.value === current) ? current : "eq");
    }

    function validateTypedInput(input) {
        const raw = input.value;
        if (!raw || parameterName(raw)) { input.setCustomValidity(""); return true; }
        const value = literalInputValue(raw);
        const category = input.dataset.valueCategory || "text";
        let valid = true;
        if (category === "integer") valid = /^-?\d+$/.test(value);
        else if (category === "number") valid = /^-?(?:\d+\.?\d*|\.\d+)$/.test(value);
        else if (category === "boolean") valid = /^(?:true|false)$/i.test(value);
        else if (category === "date") valid = /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
        else if (category === "datetime") valid = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/.test(value);
        else if (category === "foreign-key" && input._foreignValues) valid = input._foreignValues.has(value);
        input.setCustomValidity(valid ? "" : input.dataset.validationMessage || "Enter a valid value for this column.");
        return valid;
    }

    function configureValueInput(input, columnName, forceNumber = false) {
        const metadata = metadataForColumn(columnName);
        const category = valueCategory(columnName, forceNumber);
        const key = `${columnName}:${category}`;
        input.dataset.valueCategory = category;
        input.inputMode = ["integer", "number"].includes(category) ? "decimal" : "text";
        input.placeholder = {
            integer: "Integer or @parameter", number: "Number or @parameter",
            boolean: "true, false, or @parameter", date: "YYYY-MM-DD or @parameter",
            datetime: "Date and time or @parameter", "foreign-key": "Choose a valid value or use @parameter",
            text: "Text or @parameter", other: "Value or @parameter"
        }[category];
        input.dataset.validationMessage = {
            integer: "Enter a whole number or an @parameter.", number: "Enter a number or an @parameter.",
            boolean: "Enter true, false, or a parameter.", date: "Enter a valid date in YYYY-MM-DD format or a parameter.",
            datetime: "Enter a valid date and time or a parameter.", "foreign-key": "Choose a value that exists in the referenced table."
        }[category] || "";
        if (input.dataset.valueKey === key) { validateTypedInput(input); return; }
        input.dataset.valueKey = key;
        input._valueList?.remove();
        input._valueList = null;
        input.removeAttribute("list");
        input._foreignValues = null;
        input._foreignTimer = null;
        input._foreignLoadVersion = (input._foreignLoadVersion || 0) + 1;
        if (category === "boolean") {
            const list = document.createElement("datalist");
            list.id = `db-value-options-${++valueControlId}`;
            list.append(makeOption("true", "true"), makeOption("false", "false"));
            root.append(list);
            input._valueList = list;
            input.setAttribute("list", list.id);
        } else if (category === "foreign-key" && metadata?.references) {
            const list = document.createElement("datalist");
            list.id = `db-value-options-${++valueControlId}`;
            root.append(list);
            input._valueList = list;
            input.setAttribute("list", list.id);
            input._foreignValues = new Set();
            const version = input._foreignLoadVersion;
            const loadOptions = async () => {
                if (parameterName(input.value) || version !== input._foreignLoadVersion) return;
                try {
                    const data = await requestJson(`${api}/options?table=${encodeURIComponent(metadata.references.table)}&column=${encodeURIComponent(metadata.references.column)}&q=${encodeURIComponent(input.value)}`);
                    if (version !== input._foreignLoadVersion) return;
                    list.replaceChildren();
                    data.options.forEach(item => {
                        input._foreignValues.add(String(item.value));
                        list.append(makeOption(item.value, `${item.label} (${item.value})`));
                    });
                    validateTypedInput(input);
                } catch (error) { setStatus(error.message, "error"); }
            };
            input.addEventListener("focus", loadOptions, { once: true });
            input.addEventListener("input", () => {
                clearTimeout(input._foreignTimer);
                input._foreignTimer = setTimeout(loadOptions, 180);
            });
            loadOptions();
        }
        validateTypedInput(input);
    }

    function refreshColumnSelects() {
        const choices = columnChoices();
        root.querySelectorAll("select.db-column-select").forEach(select => {
            const current = select.value;
            const available = select.classList.contains("aggregate-column") || select.classList.contains("summary-column")
                ? [{ value: "*", label: "All rows (*)" }, ...choices]
                : choices;
            fillSelect(select, available, current, "Select column…");
        });
        root.querySelectorAll("select.db-order-expression").forEach(select => {
            const current = select.value;
            fillSelect(select, columnChoices({ includeOutputs: true }), current, "Select column…");
        });
    }

    function addResult(value = "", alias = "") {
        const row = document.createElement("div");
        row.className = "db-row aggregate-row result-row";
        const column = document.createElement("select");
        column.className = "db-column-select result-column";
        fillSelect(column, columnChoices(), value, "Select column…");
        const aliasInput = document.createElement("input");
        aliasInput.className = "result-alias";
        aliasInput.placeholder = "Result name (optional)";
        aliasInput.value = alias;
        row.append(column, aliasInput, removeButton(row));
        containers.results.append(row);
        row.addEventListener("change", updatePreview);
        row.addEventListener("input", updatePreview);
    }

    function addGroup(value = "") {
        const row = document.createElement("div");
        row.className = "db-row aggregate-row group-row";
        const column = document.createElement("select");
        column.className = "db-column-select group-column";
        fillSelect(column, columnChoices(), value);
        row.append(column, removeButton(row));
        containers.groups.append(row);
        row.addEventListener("change", updatePreview);
    }

    function addAggregate(data = {}) {
        const row = document.createElement("div");
        row.className = "db-row aggregate-row";
        const fn = document.createElement("select");
        fn.className = "aggregate-function";
        fillSelect(fn, [
            ["count", "Count"], ["count_distinct", "Count distinct"], ["sum", "Sum"],
            ["avg", "Average"], ["min", "Minimum"], ["max", "Maximum"]
        ].map(([value, label]) => ({ value, label })), data.function || "count");
        const column = document.createElement("select");
        column.className = "db-column-select aggregate-column";
        fillSelect(column, [{ value: "*", label: "All rows (*)" }, ...columnChoices()], data.column || "*");
        const alias = document.createElement("input");
        alias.className = "aggregate-alias";
        alias.placeholder = "Result name";
        const existingAliases = new Set(Array.from(containers.aggregates.querySelectorAll(".aggregate-alias")).map(item => item.value.trim()));
        const aliasBase = data.function || "count";
        let suggestedAlias = aliasBase;
        for (let suffix = 2; existingAliases.has(suggestedAlias); suffix++) suggestedAlias = `${aliasBase}_${suffix}`;
        alias.value = data.alias || suggestedAlias;
        row.append(fn, column, alias, removeButton(row, () => { refreshColumnSelects(); updatePreview(); }));
        containers.aggregates.append(row);
        row.addEventListener("change", () => {
            if (fn.value !== "count" && column.value === "*") column.value = "";
            refreshColumnSelects();
            updatePreview();
        });
        row.addEventListener("input", () => { refreshColumnSelects(); updatePreview(); });
    }

    function addValue(data = {}) {
        const row = document.createElement("div");
        row.className = "db-row aggregate-row value-row";
        const column = document.createElement("select");
        column.className = "value-column";
        const obj = object(baseTable());
        fillSelect(column, (obj?.columns || []).map(item => ({ value: item.name, label: item.name })), data.column, "Select column…");
        const input = document.createElement("input");
        input.className = "value-input";
        input.value = builderInputValue(data.value);
        input.placeholder = "Value or @parameter";
        row.append(column, input, removeButton(row));
        containers.values.append(row);
        const refreshValue = () => { configureValueInput(input, `${baseAlias()}.${column.value}`); updatePreview(); };
        column.addEventListener("change", refreshValue);
        input.addEventListener("input", () => { validateTypedInput(input); updatePreview(); });
        row.addEventListener("input", updatePreview);
        row.addEventListener("change", updatePreview);
        refreshValue();
    }

    function addFilter(container, data = {}, summary = false) {
        const row = document.createElement("div");
        row.className = "db-row filter-row";
        const connector = document.createElement("select");
        connector.className = "filter-connector";
        fillSelect(connector, [{ value: "and", label: "AND" }, { value: "or", label: "OR" }], data.connector || "and");
        const field = document.createElement("select");
        field.className = summary ? "filter-field summary-field" : "filter-field db-column-select";
        const summaryChoices = columnChoices({ includeOutputs: true }).filter(item => item.value.startsWith("@"));
        fillSelect(field, summary ? summaryChoices : columnChoices(), data.field, summary ? "Select result…" : "Select column…");
        const expressionWrap = document.createElement("span");
        expressionWrap.className = "db-inline-fields summary-expression";
        const expressionMode = document.createElement("select");
        expressionMode.className = "summary-expression-mode";
        fillSelect(expressionMode, [
            { value: "field", label: "Result calculation" },
            { value: "aggregate", label: "Custom calculation" }
        ], data.aggregate ? "aggregate" : "field");
        const summaryFunction = document.createElement("select");
        summaryFunction.className = "summary-function";
        fillSelect(summaryFunction, [
            ["count", "Count"], ["count_distinct", "Count distinct"],
            ["sum", "Sum"], ["avg", "Average"], ["min", "Minimum"], ["max", "Maximum"]
        ].map(([value, label]) => ({ value, label })), data.aggregate?.function || "count");
        const summaryColumn = document.createElement("select");
        summaryColumn.className = "db-column-select summary-column";
        fillSelect(summaryColumn, [{ value: "*", label: "All rows (*)" }, ...columnChoices()], data.aggregate?.column || "*");
        expressionWrap.append(expressionMode, field, summaryFunction, summaryColumn);
        const operator = document.createElement("select");
        operator.className = "filter-operator";
        fillSelect(operator, operatorChoices("text"), data.operator || "eq");
        const valueWrap = document.createElement("span");
        valueWrap.className = "db-inline-fields";
        const input = document.createElement("input");
        input.className = "filter-value";
        input.value = builderInputValue(data.value);
        input.placeholder = "Value or @parameter";
        valueWrap.append(input);
        row.append(connector, summary ? expressionWrap : field, operator, valueWrap, removeButton(row));
        container.append(row);
        const refresh = () => {
            if (summary) {
                const aggregate = expressionMode.value === "aggregate";
                field.hidden = aggregate;
                summaryFunction.hidden = !aggregate;
                summaryColumn.hidden = !aggregate;
                if (summaryFunction.value !== "count" && summaryColumn.value === "*") summaryColumn.value = "";
            }
            const outputAggregate = summary && field.value.startsWith("@")
                ? Array.from(containers.aggregates.querySelectorAll(".aggregate-row")).find(item => item.querySelector(".aggregate-alias").value.trim() === field.value.slice(1))
                : null;
            const outputColumn = outputAggregate?.querySelector(".aggregate-column").value;
            const selectedColumn = summary && expressionMode.value === "aggregate"
                ? summaryColumn.value === "*" ? `${baseAlias()}.id` : summaryColumn.value
                : outputAggregate
                    ? outputColumn === "*" ? `${baseAlias()}.id` : outputColumn
                    : field.value;
            const selectedFunction = summary && expressionMode.value === "aggregate"
                ? summaryFunction.value
                : outputAggregate?.querySelector(".aggregate-function").value || "";
            const numeric = ["count", "count_distinct", "sum", "avg"].includes(selectedFunction);
            configureOperator(operator, selectedColumn, numeric);
            configureValueInput(input, selectedColumn, numeric);
            const singleOperand = ["is_null", "is_not_null"].includes(operator.value);
            input.disabled = singleOperand;
            valueWrap.classList.toggle("unused-value", singleOperand);
            if (singleOperand) {
                input.setCustomValidity("");
                input.placeholder = "No value needed";
            }
            updatePreview();
        };
        row.addEventListener("change", refresh);
        input.addEventListener("input", () => { validateTypedInput(input); updatePreview(); });
        refresh();
    }

    function addOrder(data = {}) {
        const row = document.createElement("div");
        row.className = "db-row order-row";
        const expression = document.createElement("select");
        expression.className = "db-order-expression";
        fillSelect(expression, columnChoices({ includeOutputs: true }), data.expression);
        const direction = document.createElement("select");
        direction.className = "order-direction";
        fillSelect(direction, [{ value: "asc", label: "Ascending" }, { value: "desc", label: "Descending" }], data.direction || "asc");
        row.append(expression, direction, removeButton(row));
        containers.order.append(row);
        row.addEventListener("change", updatePreview);
    }

    function candidateRelationships(targetTable) {
        const aliases = joinedAliases();
        const result = [];
        for (const relation of state.schema.relationships) {
            for (const item of aliases) {
                if (item.table === relation.source_table && targetTable === relation.target_table) {
                    result.push({ label: `${item.alias}.${relation.source_column} = {alias}.${relation.target_column}`, left: [item.alias, relation.source_column], right: ["{alias}", relation.target_column] });
                }
                if (item.table === relation.target_table && targetTable === relation.source_table) {
                    result.push({ label: `${item.alias}.${relation.target_column} = {alias}.${relation.source_column}`, left: [item.alias, relation.target_column], right: ["{alias}", relation.source_column] });
                }
            }
        }
        return result;
    }

    function refreshJoinRelationship(row, selected) {
        const table = row.querySelector(".join-table").value;
        const alias = row.querySelector(".join-alias").value.trim() || table;
        const relations = candidateRelationships(table);
        row._relationships = relations;
        const select = row.querySelector(".join-relationship");
        fillSelect(select, relations.map((item, index) => ({ value: String(index), label: item.label.replaceAll("{alias}", alias) })), selected || "0");
    }

    function addJoin(data = {}) {
        const row = document.createElement("div");
        row.className = "db-row join-row";
        const type = document.createElement("select");
        type.className = "join-type";
        fillSelect(type, [{ value: "inner", label: "Inner join" }, { value: "left", label: "Left join" }], data.type || "inner");
        const table = document.createElement("select");
        table.className = "join-table";
        let preferredTable = data.table;
        if (!preferredTable) {
            const currentTables = new Set(joinedAliases().map(item => item.table));
            const relation = state.schema.relationships.find(item =>
                (currentTables.has(item.source_table) && !currentTables.has(item.target_table))
                || (currentTables.has(item.target_table) && !currentTables.has(item.source_table))
            );
            if (relation) {
                preferredTable = currentTables.has(relation.source_table)
                    ? relation.target_table
                    : relation.source_table;
            }
        }
        fillSelect(table, state.schema.objects.map(item => ({ value: item.name, label: item.name })), preferredTable);
        const alias = document.createElement("input");
        alias.className = "join-alias";
        alias.placeholder = "Alias";
        alias.value = data.alias || table.value;
        const relation = document.createElement("select");
        relation.className = "join-relationship";
        row.append(type, table, alias, relation, removeButton(row, () => { refreshColumnSelects(); updatePreview(); }));
        containers.joins.append(row);
        refreshJoinRelationship(row);
        if (data.on?.[0]) {
            const target = data.on[0];
            const index = row._relationships.findIndex(item => item.left[0] === target.left.table && item.left[1] === target.left.column && item.right[1] === target.right.column);
            if (index >= 0) relation.value = String(index);
        }
        table.addEventListener("change", () => { alias.value = table.value; refreshJoinRelationship(row); refreshColumnSelects(); updatePreview(); });
        alias.addEventListener("input", () => { refreshJoinRelationship(row, relation.value); refreshColumnSelects(); updatePreview(); });
        row.addEventListener("change", updatePreview);
        refreshColumnSelects();
    }

    function filterDefinition(container, summary = false) {
        const conditions = [];
        container.querySelectorAll(".filter-row").forEach((row, index) => {
            const field = row.querySelector(".filter-field")?.value || "";
            const aggregateMode = summary && row.querySelector(".summary-expression-mode").value === "aggregate";
            const aggregateColumn = aggregateMode ? row.querySelector(".summary-column").value : "";
            if ((!aggregateMode && !field) || (aggregateMode && !aggregateColumn)) return;
            const raw = row.querySelector(".filter-value").value;
            const parameter = parameterName(raw);
            let left;
            let aggregateFunction = "";
            if (aggregateMode) {
                const fn = row.querySelector(".summary-function").value;
                aggregateFunction = fn;
                const ref = aggregateColumn === "*" ? { table: baseAlias(), column: "*" } : parseColumn(aggregateColumn);
                left = { kind: "aggregate", function: fn === "count_distinct" ? "count" : fn, distinct: fn === "count_distinct", table: ref.table, column: ref.column };
            } else {
                left = summary && field.startsWith("@")
                    ? { kind: "output", name: field.slice(1) }
                    : parseColumn(field);
            }
            if (summary && field.startsWith("@")) {
                const output = Array.from(containers.aggregates.querySelectorAll(".aggregate-row")).find(item => item.querySelector(".aggregate-alias").value.trim() === field.slice(1));
                aggregateFunction = output?.querySelector(".aggregate-function").value || "";
            }
            const numericSummary = ["count", "count_distinct", "sum", "avg"].includes(aggregateFunction);
            conditions.push({
                connector: index === 0 ? "and" : row.querySelector(".filter-connector").value,
                condition: {
                    left,
                    operator: row.querySelector(".filter-operator").value,
                    value: parameter
                        ? { kind: "parameter", name: parameter }
                        : { kind: "literal", value: literalValue(literalInputValue(raw), aggregateMode ? aggregateColumn : field, numericSummary) }
                }
            });
        });
        if (!conditions.length) return null;
        let result = conditions[0].condition;
        for (const item of conditions.slice(1)) result = { logic: item.connector, conditions: [result, item.condition] };
        return result;
    }

    function joinsDefinition() {
        return Array.from(containers.joins.querySelectorAll(".join-row")).map(row => {
            const table = row.querySelector(".join-table").value;
            const alias = row.querySelector(".join-alias").value.trim() || table;
            const relation = row._relationships?.[Number(row.querySelector(".join-relationship").value)];
            if (!relation) return null;
            return {
                type: row.querySelector(".join-type").value,
                table, alias,
                on: [{ operator: "eq", left: { kind: "column", table: relation.left[0], column: relation.left[1] }, right: { kind: "column", table: alias, column: relation.right[1] } }]
            };
        }).filter(Boolean);
    }

    function valueDefinition(row) {
        const raw = row.querySelector(".value-input").value;
        const parameter = parameterName(raw);
        const column = row.querySelector(".value-column").value;
        return {
            column,
            value: parameter
                ? { kind: "parameter", name: parameter }
                : { kind: "literal", value: literalValue(literalInputValue(raw), `${baseAlias()}.${column}`) }
        };
    }

    function buildDefinition() {
        const definition = { operation: state.operation, from: { table: baseTable(), alias: baseAlias() } };
        if (state.operation === "select") {
            definition.joins = joinsDefinition();
            if (byId("db-summary-enabled").checked) {
                const groups = Array.from(containers.groups.querySelectorAll(".group-column")).filter(item => item.value).map(item => parseColumn(item.value));
                const aggregateColumns = Array.from(containers.aggregates.querySelectorAll(".aggregate-row")).filter(row => row.querySelector(".aggregate-column").value).map(row => {
                    const fn = row.querySelector(".aggregate-function").value;
                    const selected = row.querySelector(".aggregate-column").value;
                    const ref = selected === "*" ? { table: baseAlias(), column: "*" } : parseColumn(selected);
                    return { kind: "aggregate", function: fn === "count_distinct" ? "count" : fn, distinct: fn === "count_distinct", table: ref.table, column: ref.column, alias: row.querySelector(".aggregate-alias").value.trim() };
                });
                definition.columns = [...groups, ...aggregateColumns];
                definition.group_by = groups;
                definition.having = filterDefinition(containers.having, true);
            } else {
                definition.columns = Array.from(containers.results.querySelectorAll(".result-row")).filter(row => row.querySelector(".result-column").value).map(row => ({ ...parseColumn(row.querySelector(".result-column").value), alias: row.querySelector(".result-alias").value.trim() || undefined }));
            }
            definition.where = filterDefinition(containers.filters);
            definition.order_by = Array.from(containers.order.querySelectorAll(".order-row")).filter(row => row.querySelector(".db-order-expression").value).map(row => {
                const value = row.querySelector(".db-order-expression").value;
                return { expression: value.startsWith("@") ? { kind: "output", name: value.slice(1) } : parseColumn(value), direction: row.querySelector(".order-direction").value };
            });
            if (byId("db-limit").value !== "") definition.limit = { kind: "literal", value: Number(byId("db-limit").value) };
            if (byId("db-offset").value !== "") definition.offset = { kind: "literal", value: Number(byId("db-offset").value) };
        } else {
            definition.values = Array.from(containers.values.querySelectorAll(".value-row")).map(valueDefinition);
            if (state.operation === "update") definition.where = filterDefinition(containers.filters);
        }
        return definition;
    }

    function parameterSchemaForBuilder() {
        const parameters = new Map();
        const register = (name, columnName, typeOverride = "") => {
            if (!name || parameters.has(name)) return;
            const meta = metadataForColumn(columnName);
            const parameter = { name, label: name.replaceAll("_", " "), type: typeOverride || parameterTypeForColumn(columnName), required: true };
            if (meta?.references) parameter.picker = { table: meta.references.table, column: meta.references.column };
            parameters.set(name, parameter);
        };
        root.querySelectorAll(".filter-row").forEach(row => {
            if (row.querySelector(".filter-value").disabled) return;
            const name = parameterName(row.querySelector(".filter-value").value);
            if (!name) return;
            const aggregateMode = row.querySelector(".summary-expression-mode")?.value === "aggregate";
            const field = aggregateMode
                ? row.querySelector(".summary-column").value
                : row.querySelector(".filter-field").value;
            if (aggregateMode) {
                const fn = row.querySelector(".summary-function").value;
                const numeric = ["count", "count_distinct", "sum", "avg"].includes(fn);
                register(name, field === "*" ? `${baseAlias()}.id` : field, numeric ? "number" : "");
                return;
            }
            if (!field.startsWith("@")) register(name, field);
            else {
                const alias = field.slice(1);
                const aggregate = Array.from(containers.aggregates.querySelectorAll(".aggregate-row")).find(item => item.querySelector(".aggregate-alias").value.trim() === alias);
                const column = aggregate?.querySelector(".aggregate-column").value;
                const fn = aggregate?.querySelector(".aggregate-function").value || "";
                const numeric = ["count", "count_distinct", "sum", "avg"].includes(fn);
                register(name, column === "*" ? `${baseAlias()}.id` : column, numeric ? "number" : "");
            }
        });
        containers.values.querySelectorAll(".value-row").forEach(row => {
            const name = parameterName(row.querySelector(".value-input").value);
            if (name) register(name, `${baseAlias()}.${row.querySelector(".value-column").value}`);
        });
        return Array.from(parameters.values());
    }

    function expressionSql(item) {
        if (item.kind === "output") return item.name;
        if (item.kind === "aggregate") return `${item.function.toUpperCase()}(${item.distinct ? "DISTINCT " : ""}${item.column === "*" ? "*" : `${item.table}.${item.column}`})`;
        return `${item.table}.${item.column}`;
    }

    function conditionSql(condition) {
        if (!condition) return "";
        if (condition.conditions) return `(${condition.conditions.map(conditionSql).join(` ${condition.logic.toUpperCase()} `)})`;
        const ops = { eq: "=", ne: "<>", lt: "<", lte: "<=", gt: ">", gte: ">=", like: "LIKE", ilike: "ILIKE", contains: "ILIKE", starts_with: "ILIKE", ends_with: "ILIKE", is_null: "IS NULL", is_not_null: "IS NOT NULL" };
        const left = expressionSql(condition.left);
        if (["is_null", "is_not_null"].includes(condition.operator)) return `${left} ${ops[condition.operator]}`;
        let value = condition.value.kind === "parameter" ? `@${condition.value.name}` : JSON.stringify(condition.value.value);
        if (condition.operator === "contains") value = `'%${condition.value.value}%'`;
        return `${left} ${ops[condition.operator]} ${value}`;
    }

    function definitionSql(definition) {
        const table = definition.from.table;
        const alias = definition.from.alias;
        if (!table) return "Choose a table or view to begin.";
        if (definition.operation === "insert") {
            return `INSERT INTO ${table} (${definition.values.map(item => item.column).join(", ")})\nVALUES (${definition.values.map(item => item.value.kind === "parameter" ? `@${item.value.name}` : JSON.stringify(item.value.value)).join(", ")})\nRETURNING *`;
        }
        if (definition.operation === "update") {
            const set = definition.values.map(item => `${item.column} = ${item.value.kind === "parameter" ? `@${item.value.name}` : JSON.stringify(item.value.value)}`).join(",\n    ");
            return `UPDATE ${table} AS ${alias}\nSET ${set}${definition.where ? `\nWHERE ${conditionSql(definition.where)}` : "\n-- Add a filter before this query can run"}\nRETURNING *`;
        }
        if (!definition.columns?.length) return "Add at least one result column.";
        const columns = definition.columns.map(item => `${expressionSql(item)}${item.alias ? ` AS ${item.alias}` : ""}`).join(",\n       ");
        const joins = (definition.joins || []).map(item => `${item.type.toUpperCase()} JOIN ${item.table} AS ${item.alias}\n  ON ${expressionSql(item.on[0].left)} = ${expressionSql(item.on[0].right)}`).join("\n");
        const parts = [`SELECT ${columns}`, `FROM ${table} AS ${alias}`];
        if (joins) parts.push(joins);
        if (definition.where) parts.push(`WHERE ${conditionSql(definition.where)}`);
        if (definition.group_by?.length) parts.push(`GROUP BY ${definition.group_by.map(expressionSql).join(", ")}`);
        if (definition.having) parts.push(`HAVING ${conditionSql(definition.having)}`);
        if (definition.order_by?.length) parts.push(`ORDER BY ${definition.order_by.map(item => `${expressionSql(item.expression)} ${item.direction.toUpperCase()}`).join(", ")}`);
        if (definition.limit) parts.push(`LIMIT ${definition.limit.value}`);
        if (definition.offset) parts.push(`OFFSET ${definition.offset.value}`);
        return parts.join("\n");
    }

    function updatePreview() {
        try { byId("db-query-preview").textContent = definitionSql(buildDefinition()); }
        catch (error) { byId("db-query-preview").textContent = `Complete the query to preview it.\n${error.message}`; }
    }

    function setOperation(operation) {
        state.operation = operation;
        root.querySelectorAll("[data-operation]").forEach(button => button.classList.toggle("active", button.dataset.operation === operation));
        const select = operation === "select";
        const update = operation === "update";
        byId("db-table-label").textContent = select ? "From" : update ? "" : "Into";
        byId("db-alias-field").hidden = operation === "insert";
        byId("db-joins-block").hidden = !select;
        byId("db-results-block").hidden = !select;
        byId("db-values-block").hidden = select;
        byId("db-sort-block").hidden = !select;
        byId("db-filters-block").hidden = operation === "insert";
        byId("db-having-block").hidden = !select || !byId("db-summary-enabled").checked;
        if (!select && !containers.values.children.length) addValue();
        if (update && !containers.filters.children.length) addFilter(containers.filters);
        updatePreview();
    }

    function resetBuilder(table = "") {
        const operation = state.operation;
        byId("db-base-table").value = object(table) ? table : "";
        byId("db-base-alias").value = byId("db-base-table").value;
        Object.values(containers).slice(0, 8).forEach(container => container.replaceChildren());
        byId("db-summary-enabled").checked = false;
        byId("db-limit").value = "";
        byId("db-offset").value = "";
        setOperation(operation);
        refreshColumnSelects();
        updatePreview();
    }

    function addSqlParameter(data = {}) {
        const row = document.createElement("div");
        row.className = "db-row parameter-row";
        const name = document.createElement("input"); name.className = "sql-param-name"; name.placeholder = "parameter_name"; name.value = data.name || "";
        const type = document.createElement("select"); type.className = "sql-param-type";
        fillSelect(type, ["text", "integer", "number", "boolean", "date", "datetime"].map(value => ({ value, label: value })), data.type || "text");
        const defaultValue = document.createElement("input"); defaultValue.className = "sql-param-default"; defaultValue.placeholder = "Default (optional)"; defaultValue.value = data.default ?? "";
        row.append(name, type, defaultValue, removeButton(row, () => {}));
        containers.sqlParameters.append(row);
    }

    function sqlParameterSchema() {
        return Array.from(containers.sqlParameters.querySelectorAll(".parameter-row")).map(row => ({
            name: row.querySelector(".sql-param-name").value.trim(),
            label: row.querySelector(".sql-param-name").value.trim().replaceAll("_", " "),
            type: row.querySelector(".sql-param-type").value,
            required: row.querySelector(".sql-param-default").value === "",
            ...(row.querySelector(".sql-param-default").value !== "" ? { default: row.querySelector(".sql-param-default").value } : {})
        })).filter(item => item.name);
    }

    function tabFromUrl() {
        const name = window.location.hash.slice(1);
        return tabNames.has(name) ? name : "builder";
    }

    function switchTab(name, updateUrl = true) {
        if (!tabNames.has(name)) name = "builder";
        root.querySelectorAll("[data-db-tab]").forEach(button => button.classList.toggle("active", button.dataset.dbTab === name));
        root.querySelectorAll("[data-db-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.dbPanel === name));
        if (updateUrl && window.location.hash !== `#${name}`) window.history.pushState(null, "", `#${name}`);
        if (name === "saved") loadSavedQueries();
        if (name === "explorer" && !state.explorer && state.schema.objects.length) renderSchemaExplorer();
    }

    async function beginExecution(payload, parameters, format, operation = payload.definition?.operation) {
        state.pendingExecution = { payload: { ...payload, result_format: format }, parameters, format, operation };
        if (!parameters.length) return executePending({});
        const dialog = byId("db-parameter-dialog");
        const fields = byId("db-parameter-fields");
        fields.replaceChildren();
        byId("db-parameter-description").textContent = "Fill in the values for this run.";
        for (const parameter of parameters) {
            const label = document.createElement("label");
            label.textContent = parameter.label || parameter.name;
            let input;
            if (parameter.picker?.table) {
                input = document.createElement("select");
                input.append(makeOption("", "Select…"));
                try {
                    const data = await requestJson(`${api}/options?table=${encodeURIComponent(parameter.picker.table)}&column=${encodeURIComponent(parameter.picker.column)}`);
                    data.options.forEach(item => input.append(makeOption(item.value, `${item.label} (${item.value})`, parameter.default)));
                } catch (error) { setStatus(error.message, "error"); }
            } else {
                input = document.createElement("input");
                input.type = { integer: "number", number: "number", date: "date", datetime: "datetime-local", boolean: "checkbox" }[parameter.type] || "text";
                if (parameter.default !== undefined) {
                    if (input.type === "checkbox") input.checked = Boolean(parameter.default);
                    else input.value = parameter.default;
                }
            }
            input.dataset.parameterName = parameter.name;
            input.required = parameter.required !== false && input.type !== "checkbox";
            label.append(input); fields.append(label);
        }
        dialog.showModal();
    }

    async function executePending(values) {
        const pending = state.pendingExecution;
        if (!pending) return;
        if (["insert", "update", "delete"].includes(pending.operation) && !window.confirm(`Run this ${pending.operation.toUpperCase()} query? A backup will be made first.`)) return;
        setStatus("Running query…");
        try {
            const response = await fetch(`${api}/execute`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...pending.payload, values }) });
            if (!response.ok) {
                let message = `Query failed (${response.status})`;
                try { message = (await response.json()).error || message; } catch (_) { /* noop */ }
                throw new Error(message);
            }
            if (pending.format === "csv") {
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a"); link.href = url; link.download = "query.csv"; link.click(); URL.revokeObjectURL(url);
                setStatus("CSV downloaded.", "success");
            } else {
                const data = await response.json();
                renderResults(data);
                setStatus(`Query returned ${data.row_count} row${data.row_count === 1 ? "" : "s"}.`, "success");
            }
        } catch (error) { setStatus(error.message, "error"); }
    }

    function renderResults(data) {
        const section = byId("db-results-section");
        const table = byId("db-results");
        const headRow = document.createElement("tr");
        data.headers.forEach(header => { const th = document.createElement("th"); th.textContent = header; headRow.append(th); });
        table.querySelector("thead").replaceChildren(headRow);
        const body = document.createDocumentFragment();
        data.rows.forEach(row => {
            const tr = document.createElement("tr");
            data.headers.forEach(header => { const td = document.createElement("td"); const value = row[header]; td.textContent = databaseResultText(value); tr.append(td); });
            body.append(tr);
        });
        table.querySelector("tbody").replaceChildren(body);
        byId("db-unicode-results").textContent = formatUnicodeResultTable(data.headers, data.rows, data.numeric_headers);
        byId("db-results-meta").textContent = `${data.row_count} rows · ${data.duration_ms} ms`;
        section.hidden = false;
        section.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function setResultView(view) {
        const unicode = view === "unicode";
        byId("db-html-results").hidden = unicode;
        byId("db-unicode-results").hidden = !unicode;
        byId("db-copy-results").hidden = !unicode;
        byId("db-results-table-view").classList.toggle("active", !unicode);
        byId("db-results-table-view").setAttribute("aria-pressed", String(!unicode));
        byId("db-results-unicode-view").classList.toggle("active", unicode);
        byId("db-results-unicode-view").setAttribute("aria-pressed", String(unicode));
    }

    async function copyUnicodeResults() {
        const text = byId("db-unicode-results").textContent;
        const button = byId("db-copy-results");
        try {
            if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
            await navigator.clipboard.writeText(text);
        } catch (_) {
            const field = document.createElement("textarea");
            field.value = text;
            field.style.position = "fixed";
            field.style.opacity = "0";
            document.body.append(field);
            field.select();
            document.execCommand("copy");
            field.remove();
        }
        button.textContent = "Copied!";
        window.setTimeout(() => { button.textContent = "Copy table"; }, 2000);
    }

    function builderInputsValid() {
        for (const input of root.querySelectorAll(".value-input, .filter-value")) {
            if (input.disabled || input.closest("[hidden]")) continue;
            validateTypedInput(input);
            if (!input.reportValidity()) return false;
        }
        return true;
    }

    function executionForBuilder(format) {
        if (!builderInputsValid()) return;
        const definition = buildDefinition();
        return beginExecution({ query_kind: "builder", definition, parameters: parameterSchemaForBuilder() }, parameterSchemaForBuilder(), format);
    }
    function executionForSql(format) {
        const parameters = sqlParameterSchema();
        const sqlText = byId("db-sql-text").value;
        const operation = /\bDELETE\b/i.test(sqlText) ? "delete" : (sqlText.match(/\b(SELECT|INSERT|UPDATE)\b/i)?.[1] || "").toLowerCase();
        return beginExecution({ query_kind: "sql", sql_text: sqlText, parameters }, parameters, format, operation);
    }

    function openSave(queryKind) {
        if (queryKind === "builder" && !builderInputsValid()) return;
        const payload = queryKind === "builder" ? { query_kind: "builder", definition: buildDefinition(), parameters: parameterSchemaForBuilder() } : { query_kind: "sql", sql_text: byId("db-sql-text").value, parameters: sqlParameterSchema() };
        state.pendingSave = payload;
        const existing = state.savedQueries.find(item => item.id === state.editingSavedId);
        byId("db-save-name").value = existing?.name || "";
        byId("db-save-description").value = existing?.description || "";
        byId("db-save-tags").value = (existing?.tags || []).join(", ");
        byId("db-save-dialog").showModal();
    }

    async function savePending() {
        if (!state.pendingSave) return;
        const tags = byId("db-save-tags").value.split(",").map(tag => tag.trim()).filter(Boolean);
        const payload = { ...state.pendingSave, name: byId("db-save-name").value, description: byId("db-save-description").value, tags };
        try {
            await requestJson(state.editingSavedId ? `${api}/saved-queries/${state.editingSavedId}` : `${api}/saved-queries`, { method: state.editingSavedId ? "PUT" : "POST", body: JSON.stringify(payload) });
            setStatus("Query saved.", "success"); state.editingSavedId = null; await loadSavedQueries();
        } catch (error) { setStatus(error.message, "error"); }
    }

    async function loadSavedQueries() {
        try {
            const data = await requestJson(`${api}/saved-queries`);
            state.savedQueries = data.queries;
            renderSavedQueries();
        } catch (error) { setStatus(error.message, "error"); }
    }

    function renderSavedQueries() {
        const tags = new Map();
        state.savedQueries.flatMap(item => item.tags || []).forEach(tag => {
            const key = tag.toLocaleLowerCase();
            if (!tags.has(key)) tags.set(key, tag);
        });
        const tagBar = byId("db-saved-tags"); tagBar.replaceChildren();
        Array.from(tags.entries()).sort((a, b) => a[1].localeCompare(b[1])).forEach(([key, tag]) => {
            const button = document.createElement("button");
            const enabled = !state.disabledSavedTags.has(key);
            button.type = "button"; button.className = `db-tag-filter${enabled ? " active" : ""}`;
            button.textContent = tag; button.setAttribute("aria-pressed", String(enabled));
            button.title = enabled ? `Hide queries tagged “${tag}”` : `Show queries tagged “${tag}”`;
            button.addEventListener("click", () => {
                if (enabled) state.disabledSavedTags.add(key); else state.disabledSavedTags.delete(key);
                renderSavedQueries();
            });
            tagBar.append(button);
        });
        const visibleQueries = state.savedQueries.filter(item =>
            !(item.tags || []).some(tag => state.disabledSavedTags.has(tag.toLocaleLowerCase()))
        );
        const tbody = byId("db-saved-queries"); tbody.replaceChildren();
        if (!visibleQueries.length) {
            const row = tbody.insertRow(); const cell = row.insertCell(); cell.colSpan = 7;
            cell.textContent = state.savedQueries.length ? "No saved queries match the enabled tags." : "No saved queries yet.";
            return;
        }
        visibleQueries.forEach((item, index) => {
                const row = tbody.insertRow();
                row.dataset.value = index;
                const name = row.insertCell(); name.dataset.value = item.name; name.innerHTML = `<strong></strong><br><small></small>`; name.querySelector("strong").textContent = item.name; name.querySelector("small").textContent = item.description;
                const tagsCell = row.insertCell(); tagsCell.dataset.value = (item.tags || []).join(", ");
                const queryTags = document.createElement("div"); queryTags.className = "db-query-tags"; tagsCell.append(queryTags);
                (item.tags || []).forEach(tag => { const chip = document.createElement("span"); chip.textContent = tag; queryTags.append(chip); });
                if (!(item.tags || []).length) queryTags.textContent = "—";
                row.insertCell().textContent = `${item.operation.toUpperCase()} · ${item.query_kind}`;
                row.insertCell().textContent = item.parameters.map(parameter => parameter.label || parameter.name).join(", ") || "—";
                row.insertCell().textContent = item.created_by_username || "Unknown";
                const updated = row.insertCell(); updated.textContent = new Date(item.updated_at).toLocaleString(); updated.dataset.value = String(new Date(item.updated_at).getTime());
                const actions = row.insertCell();
                const run = document.createElement("button"); run.type = "button"; run.textContent = item.operation === "select" ? "Run" : "Review & run"; run.addEventListener("click", () => beginExecution({ saved_query_id: item.id }, item.parameters, "screen", item.operation));
                const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Edit"; edit.addEventListener("click", () => editSaved(item));
                const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete"; remove.addEventListener("click", async () => { if (!window.confirm(`Delete saved query “${item.name}”?`)) return; try { await requestJson(`${api}/saved-queries/${item.id}`, { method: "DELETE" }); await loadSavedQueries(); } catch (error) { setStatus(error.message, "error"); } });
                actions.append(run, edit, remove);
            });
        const table = tbody.closest("table");
        const sortedHeader = table.querySelector("th[aria-sort]:not([aria-sort='none'])");
        if (sortedHeader && typeof sortTable === "function") {
            sortTable(sortedHeader, table, Array.from(sortedHeader.parentElement.cells).indexOf(sortedHeader), sortedHeader.ariaSort);
        }
    }

    function editSaved(item) {
        state.editingSavedId = item.id;
        if (item.query_kind === "sql") {
            byId("db-sql-text").value = item.sql_text;
            containers.sqlParameters.replaceChildren(); item.parameters.forEach(addSqlParameter); switchTab("sql");
        } else { loadDefinition(item.definition); switchTab("builder"); }
        state.pendingSave = item.query_kind === "sql" ? { query_kind: "sql", sql_text: item.sql_text, parameters: item.parameters } : { query_kind: "builder", definition: item.definition, parameters: item.parameters };
        byId("db-save-name").value = item.name; byId("db-save-description").value = item.description; byId("db-save-tags").value = (item.tags || []).join(", ");
    }

    function loadDefinition(definition) {
        byId("db-base-table").value = definition.from.table;
        byId("db-base-alias").value = definition.from.alias;
        containers.joins.replaceChildren(); (definition.joins || []).forEach(addJoin);
        containers.results.replaceChildren(); containers.groups.replaceChildren(); containers.aggregates.replaceChildren(); containers.values.replaceChildren(); containers.filters.replaceChildren(); containers.having.replaceChildren(); containers.order.replaceChildren();
        setOperation(definition.operation);
        const grouped = Boolean(definition.group_by?.length || definition.columns?.some(item => item.kind === "aggregate"));
        byId("db-summary-enabled").checked = grouped; toggleSummary();
        if (grouped) {
            (definition.group_by || []).forEach(item => addGroup(`${item.table}.${item.column}`));
            (definition.columns || []).filter(item => item.kind === "aggregate").forEach(item => addAggregate({ function: item.distinct ? "count_distinct" : item.function, column: item.column === "*" ? "*" : `${item.table}.${item.column}`, alias: item.alias }));
        } else (definition.columns || []).forEach(item => addResult(`${item.table}.${item.column}`, item.alias));
        (definition.values || []).forEach(addValue);
        loadFilterTree(definition.where, containers.filters, false);
        loadFilterTree(definition.having, containers.having, true);
        (definition.order_by || []).forEach(item => addOrder({ expression: item.expression.kind === "output" ? `@${item.expression.name}` : `${item.expression.table}.${item.expression.column}`, direction: item.direction }));
        byId("db-limit").value = definition.limit?.value ?? 100; byId("db-offset").value = definition.offset?.value ?? 0;
        refreshColumnSelects(); updatePreview();
    }

    function flattenConditions(tree, connector = "and", result = []) {
        if (!tree) return result;
        if (tree.conditions) tree.conditions.forEach((item, index) => flattenConditions(item, index ? tree.logic : connector, result));
        else result.push({ ...tree, connector });
        return result;
    }
    function loadFilterTree(tree, container, summary) {
        flattenConditions(tree).forEach(item => addFilter(container, {
            connector: item.connector,
            field: item.left.kind === "output" ? `@${item.left.name}` : item.left.kind === "column" ? `${item.left.table}.${item.left.column}` : "",
            aggregate: item.left.kind === "aggregate" ? {
                function: item.left.distinct ? "count_distinct" : item.left.function,
                column: item.left.column === "*" ? "*" : `${item.left.table}.${item.left.column}`
            } : null,
            operator: item.operator,
            value: item.value
        }, summary));
    }

    function toggleSummary() {
        const enabled = byId("db-summary-enabled").checked;
        byId("db-plain-results").hidden = enabled;
        byId("db-summary-results").hidden = !enabled;
        byId("db-having-block").hidden = !enabled || state.operation !== "select";
        if (enabled && !containers.groups.children.length) addGroup(columnChoices()[0]?.value);
        if (enabled && !containers.aggregates.children.length) addAggregate();
        refreshColumnSelects(); updatePreview();
    }

    function renderSchemaExplorer() {
        if (!window.d3) { setStatus("The schema graph library could not be loaded.", "error"); return; }
        const svg = d3.select(byId("db-schema-canvas")); svg.selectAll("*").remove();
        const nodes = state.schema.objects.map(item => {
            const longestLine = Math.max(item.name.length, ...item.columns.map(column => column.name.length + column.type.length + 7));
            return { ...item, w: Math.max(240, Math.min(440, 24 + longestLine * 6.2)), h: 38 + item.columns.length * 14 };
        });
        const nodeMap = new Map(nodes.map(item => [item.name, item]));
        const links = state.schema.relationships.filter(item => nodeMap.has(item.source_table) && nodeMap.has(item.target_table)).map((item, id) => ({ id, source: item.source_table, target: item.target_table, relation: item }));
        const adjacency = new Map(nodes.map(item => [item.name, new Set()]));
        links.forEach(link => {
            adjacency.get(link.source).add(link.target);
            adjacency.get(link.target).add(link.source);
        });
        const isolatedNodes = nodes.filter(item => adjacency.get(item.name).size === 0).sort((a, b) => a.name.localeCompare(b.name));
        const visited = new Set();
        const components = [];
        nodes.filter(item => adjacency.get(item.name).size).forEach(start => {
            if (visited.has(start.name)) return;
            const component = [];
            const queue = [start.name];
            visited.add(start.name);
            while (queue.length) {
                const name = queue.shift();
                component.push(nodeMap.get(name));
                adjacency.get(name).forEach(neighbor => {
                    if (!visited.has(neighbor)) { visited.add(neighbor); queue.push(neighbor); }
                });
            }
            components.push(component);
        });

        function layoutComponent(component) {
            const names = new Set(component.map(item => item.name));
            const rootNode = [...component].sort((a, b) => adjacency.get(b.name).size - adjacency.get(a.name).size || a.name.localeCompare(b.name))[0];
            const level = new Map([[rootNode.name, 0]]);
            const queue = [rootNode.name];
            while (queue.length) {
                const name = queue.shift();
                Array.from(adjacency.get(name)).filter(neighbor => names.has(neighbor)).sort((a, b) => adjacency.get(b).size - adjacency.get(a).size || a.localeCompare(b)).forEach(neighbor => {
                    if (!level.has(neighbor)) { level.set(neighbor, level.get(name) + 1); queue.push(neighbor); }
                });
            }
            const layers = [];
            component.forEach(item => {
                const index = level.get(item.name) || 0;
                if (!layers[index]) layers[index] = [];
                layers[index].push(item);
            });
            layers.forEach(layer => layer.sort((a, b) => a.name.localeCompare(b.name)));
            for (let pass = 0; pass < 4; pass++) {
                for (let index = 1; index < layers.length; index++) {
                    const previousOrder = new Map(layers[index - 1].map((item, position) => [item.name, position]));
                    layers[index].sort((a, b) => {
                        const barycenter = item => {
                            const positions = Array.from(adjacency.get(item.name)).filter(name => previousOrder.has(name)).map(name => previousOrder.get(name));
                            return positions.length ? positions.reduce((sum, value) => sum + value, 0) / positions.length : Number.MAX_SAFE_INTEGER;
                        };
                        return barycenter(a) - barycenter(b) || a.name.localeCompare(b.name);
                    });
                }
                for (let index = layers.length - 2; index >= 0; index--) {
                    const nextOrder = new Map(layers[index + 1].map((item, position) => [item.name, position]));
                    layers[index].sort((a, b) => {
                        const barycenter = item => {
                            const positions = Array.from(adjacency.get(item.name)).filter(name => nextOrder.has(name)).map(name => nextOrder.get(name));
                            return positions.length ? positions.reduce((sum, value) => sum + value, 0) / positions.length : Number.MAX_SAFE_INTEGER;
                        };
                        return barycenter(a) - barycenter(b) || a.name.localeCompare(b.name);
                    });
                }
            }
            const layerWidths = layers.map(layer => d3.sum(layer, item => item.w) + Math.max(0, layer.length - 1) * 42);
            const layerHeights = layers.map(layer => d3.max(layer, item => item.h));
            const componentWidth = d3.max(layerWidths);
            let y = 0;
            layers.forEach((layer, index) => {
                let x = (componentWidth - layerWidths[index]) / 2;
                layer.forEach(item => {
                    item.graphLayer = index;
                    item.x = x + item.w / 2;
                    item.y = y + layerHeights[index] / 2;
                    x += item.w + 42;
                });
                y += layerHeights[index] + 110;
            });
            return { nodes: component, width: componentWidth, height: y - 110 };
        }

        const layouts = components.map(layoutComponent).sort((a, b) => b.nodes.length - a.nodes.length);
        const packingWidth = 2800;
        let packX = 0;
        let packY = 0;
        let rowHeight = 0;
        layouts.forEach((layout, componentIndex) => {
            if (packX && packX + layout.width > packingWidth) {
                packX = 0;
                packY += rowHeight + 110;
                rowHeight = 0;
            }
            layout.nodes.forEach(item => { item.graphComponent = componentIndex; item.x += packX; item.y += packY; });
            packX += layout.width + 150;
            rowHeight = Math.max(rowHeight, layout.height);
        });
        const connectedBottom = layouts.length ? d3.max(layouts.flatMap(layout => layout.nodes), item => item.y + item.h / 2) : 0;
        let shelfX = 0;
        let shelfY = connectedBottom + (layouts.length ? 90 : 0);
        let shelfRowHeight = 0;
        isolatedNodes.forEach(item => {
            if (shelfX && shelfX + item.w > packingWidth) {
                shelfX = 0;
                shelfY += shelfRowHeight + 18;
                shelfRowHeight = 0;
            }
            item.x = shelfX + item.w / 2;
            item.y = shelfY + item.h / 2;
            item.graphComponent = -1;
            item.graphLayer = -1;
            shelfX += item.w + 18;
            shelfRowHeight = Math.max(shelfRowHeight, item.h);
        });
        links.forEach(link => { link.source = nodeMap.get(link.source); link.target = nodeMap.get(link.target); });
        const layer = svg.append("g");
        const regions = layouts.map((layout, index) => ({
            label: index === 0 ? "Main schema" : `Connected group ${index + 1}`,
            minX: d3.min(layout.nodes, item => item.x - item.w / 2) - 28,
            maxX: d3.max(layout.nodes, item => item.x + item.w / 2) + 28,
            minY: d3.min(layout.nodes, item => item.y - item.h / 2) - 38,
            maxY: d3.max(layout.nodes, item => item.y + item.h / 2) + 28
        }));
        if (isolatedNodes.length) regions.push({
            label: "No relationships",
            minX: d3.min(isolatedNodes, item => item.x - item.w / 2) - 28,
            maxX: d3.max(isolatedNodes, item => item.x + item.w / 2) + 28,
            minY: d3.min(isolatedNodes, item => item.y - item.h / 2) - 38,
            maxY: d3.max(isolatedNodes, item => item.y + item.h / 2) + 28
        });
        const regionGroups = layer.append("g").selectAll("g").data(regions).join("g").attr("class", "schema-region");
        regionGroups.append("rect").attr("x", item => item.minX).attr("y", item => item.minY).attr("width", item => item.maxX - item.minX).attr("height", item => item.maxY - item.minY);
        regionGroups.append("text").attr("x", item => item.minX + 10).attr("y", item => item.minY + 18).text(item => item.label);
        const markerId = "db-schema-arrow";
        svg.append("defs").append("marker").attr("id", markerId).attr("viewBox", "0 -5 10 10").attr("refX", 8).attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto").append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "context-stroke");
        const idOf = value => typeof value === "string" ? value : value.name;
        const layerBounds = new Map();
        nodes.filter(item => item.graphComponent >= 0).forEach(item => {
            const key = `${item.graphComponent}:${item.graphLayer}`;
            const bounds = layerBounds.get(key) || { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
            bounds.minX = Math.min(bounds.minX, item.x - item.w / 2);
            bounds.maxX = Math.max(bounds.maxX, item.x + item.w / 2);
            bounds.minY = Math.min(bounds.minY, item.y - item.h / 2);
            bounds.maxY = Math.max(bounds.maxY, item.y + item.h / 2);
            layerBounds.set(key, bounds);
        });
        const sideFor = (a, b) => Math.abs(b.x - a.x) > Math.abs(b.y - a.y) ? (b.x > a.x ? "right" : "left") : (b.y > a.y ? "bottom" : "top");
        const crossLayerGroups = new Map();
        const sameLayerGroups = new Map();
        links.forEach(link => {
            const sameComponent = link.source.graphComponent === link.target.graphComponent;
            const layerDifference = link.target.graphLayer - link.source.graphLayer;
            if (sameComponent && layerDifference) {
                link.sourceSide = layerDifference > 0 ? "bottom" : "top";
                link.targetSide = layerDifference > 0 ? "top" : "bottom";
                const lowerLayer = Math.min(link.source.graphLayer, link.target.graphLayer);
                const key = `${link.source.graphComponent}:${lowerLayer}`;
                if (!crossLayerGroups.has(key)) crossLayerGroups.set(key, []);
                crossLayerGroups.get(key).push(link);
            } else if (sameComponent) {
                link.sourceSide = "top";
                link.targetSide = "top";
                const key = `${link.source.graphComponent}:${link.source.graphLayer}`;
                if (!sameLayerGroups.has(key)) sameLayerGroups.set(key, []);
                sameLayerGroups.get(key).push(link);
            } else {
                link.sourceSide = sideFor(link.source, link.target);
                link.targetSide = sideFor(link.target, link.source);
            }
        });
        crossLayerGroups.forEach((group, key) => {
            const [component, lowerLayer] = key.split(":").map(Number);
            const lower = layerBounds.get(`${component}:${lowerLayer}`);
            const upper = layerBounds.get(`${component}:${lowerLayer + 1}`);
            const laneStart = lower.maxY + 18;
            const laneEnd = upper.minY - 18;
            group.sort((a, b) => ((a.source.x + a.target.x) / 2) - ((b.source.x + b.target.x) / 2));
            group.forEach((link, index) => {
                link.routeKind = "between-layers";
                link.routeLane = laneStart + (laneEnd - laneStart) * ((index + 1) / (group.length + 1));
            });
        });
        sameLayerGroups.forEach((group, key) => {
            const bounds = layerBounds.get(key);
            group.sort((a, b) => Math.abs(a.source.x - a.target.x) - Math.abs(b.source.x - b.target.x));
            group.forEach((link, index) => {
                link.routeKind = "outside-layer";
                link.routeLane = bounds.minY - 26 - index * 12;
            });
        });
        const portGroups = new Map();
        const addPort = (link, end) => {
            const node = link[end];
            const side = link[`${end}Side`];
            const key = `${node.name}:${side}`;
            const entries = portGroups.get(key) || [];
            entries.push({ link, end });
            portGroups.set(key, entries);
        };
        links.forEach(link => { addPort(link, "source"); addPort(link, "target"); });
        portGroups.forEach(entries => {
            entries.sort((a, b) => {
                const aOther = a.link[a.end === "source" ? "target" : "source"];
                const bOther = b.link[b.end === "source" ? "target" : "source"];
                const side = a.link[`${a.end}Side`];
                return ["left", "right"].includes(side) ? aOther.y - bOther.y : aOther.x - bOther.x;
            });
            entries.forEach((entry, index) => { entry.link[`${entry.end}Port`] = { index, count: entries.length }; });
        });
        const port = (node, side, slot = { index: 0, count: 1 }) => {
            const fraction = (slot.index + 1) / (slot.count + 1);
            if (side === "left") return [node.x - node.w / 2, node.y - node.h / 2 + node.h * fraction];
            if (side === "right") return [node.x + node.w / 2, node.y - node.h / 2 + node.h * fraction];
            if (side === "top") return [node.x - node.w / 2 + node.w * fraction, node.y - node.h / 2];
            return [node.x - node.w / 2 + node.w * fraction, node.y + node.h / 2];
        };
        const edgePath = link => {
            const start = port(link.source, link.sourceSide, link.sourcePort);
            const end = port(link.target, link.targetSide, link.targetPort);
            if (["between-layers", "outside-layer"].includes(link.routeKind)) return `M${start[0]},${start[1]}L${start[0]},${link.routeLane}L${end[0]},${link.routeLane}L${end[0]},${end[1]}`;
            if (["left", "right"].includes(link.sourceSide)) {
                const middle = (start[0] + end[0]) / 2;
                return `M${start[0]},${start[1]}L${middle},${start[1]}L${middle},${end[1]}L${end[0]},${end[1]}`;
            }
            const middle = (start[1] + end[1]) / 2;
            return `M${start[0]},${start[1]}L${start[0]},${middle}L${end[0]},${middle}L${end[0]},${end[1]}`;
        };
        const edges = layer.append("g").selectAll("path").data(links).join("path").attr("class", "schema-link").attr("d", edgePath).attr("marker-end", `url(#${markerId})`);
        const groups = layer.append("g").selectAll("g").data(nodes).join("g").attr("class", item => `schema-node ${item.kind}`).attr("transform", item => `translate(${item.x - item.w / 2},${item.y - item.h / 2})`);
        groups.append("rect").attr("width", item => item.w).attr("height", item => item.h);
        groups.append("text").attr("class", "title").attr("x", 8).attr("y", 17).text(item => item.name);
        groups.append("text").attr("class", "kind").attr("x", item => item.w - 8).attr("y", 17).attr("text-anchor", "end").text(item => item.kind);
        groups.each(function(item) { const group = d3.select(this); item.columns.forEach((column, index) => group.append("text").attr("class", "column").attr("x", 8).attr("y", 34 + index * 14).text(`${column.primary_key ? "PK  " : column.references ? "FK  " : "    "}${column.name} · ${column.type}`)); });
        function selectNode(item, center = false) {
            const neighbors = new Set([item.name]); links.forEach(link => { if (idOf(link.source) === item.name) neighbors.add(idOf(link.target)); if (idOf(link.target) === item.name) neighbors.add(idOf(link.source)); });
            groups.classed("selected", node => node.name === item.name).classed("dimmed", node => !neighbors.has(node.name));
            edges.classed("active", link => idOf(link.source) === item.name || idOf(link.target) === item.name).classed("dimmed", link => idOf(link.source) !== item.name && idOf(link.target) !== item.name);
            renderSchemaDetail(item);
            if (center) { const box = byId("db-schema-canvas").getBoundingClientRect(); svg.transition().duration(250).call(zoom.transform, d3.zoomIdentity.translate(box.width / 2 - item.x, box.height / 2 - item.y)); }
        }
        function clearSelection() {
            groups.classed("selected", false).classed("dimmed", false);
            edges.classed("active", false).classed("dimmed", false);
            const detail = byId("db-schema-detail");
            detail.replaceChildren();
            const message = document.createElement("p");
            message.textContent = "Select a table or view.";
            detail.append(message);
        }
        groups.on("click", (event, item) => { event.stopPropagation(); selectNode(item); }).call(d3.drag().on("start", event => event.sourceEvent.stopPropagation()).on("drag", (event, item) => { item.x = event.x; item.y = event.y; groups.filter(node => node === item).attr("transform", `translate(${item.x - item.w / 2},${item.y - item.h / 2})`); edges.attr("d", edgePath); }));
        let transform = d3.zoomIdentity;
        const zoom = d3.zoom().scaleExtent([.03, 2]).on("zoom", event => { transform = event.transform; layer.attr("transform", transform); byId("db-schema-zoom").textContent = `${Math.round(transform.k * 100)}%`; });
        svg.call(zoom);
        svg.on("click", event => { if (!event.defaultPrevented) clearSelection(); });
        function fit(items = nodes) { const box = byId("db-schema-canvas").getBoundingClientRect(); const minX = d3.min(items, item => item.x - item.w / 2) - 30, maxX = d3.max(items, item => item.x + item.w / 2) + 30, minY = d3.min(items, item => item.y - item.h / 2) - 30, maxY = d3.max(items, item => item.y + item.h / 2) + 30; const scale = Math.max(.03, Math.min(1, .9 / Math.max((maxX - minX) / box.width, (maxY - minY) / box.height))); svg.transition().duration(300).call(zoom.transform, d3.zoomIdentity.translate(box.width / 2 - scale * (minX + maxX) / 2, box.height / 2 - scale * (minY + maxY) / 2).scale(scale)); }
        byId("db-schema-fit").onclick = () => fit();
        byId("db-schema-search").oninput = event => { const query = event.target.value.trim().toLowerCase(); const matches = query ? nodes.filter(item => item.name.includes(query) || item.columns.some(column => column.name.includes(query))) : []; groups.classed("match", item => matches.includes(item)); if (matches.length === 1) selectNode(matches[0], true); else if (matches.length > 1) fit(matches); };
        state.explorer = { fit }; setTimeout(() => fit(), 0);
    }

    function renderSchemaDetail(item) {
        const detail = byId("db-schema-detail"); detail.replaceChildren();
        const title = document.createElement("h3"); title.textContent = item.name;
        const meta = document.createElement("p"); meta.textContent = `${item.kind} · ${item.columns.length} columns`;
        const table = document.createElement("table"); const body = document.createElement("tbody");
        item.columns.forEach(column => { const row = body.insertRow(); row.insertCell().textContent = column.primary_key ? "PK" : column.references ? "FK" : ""; row.insertCell().textContent = column.name; row.insertCell().textContent = column.type; }); table.append(body);
        const use = document.createElement("button"); use.type = "button"; use.textContent = "Use as base table"; use.addEventListener("click", () => { resetBuilder(item.name); switchTab("builder"); });
        detail.append(title, meta, table, use);
    }

    root.querySelectorAll("[data-db-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.dbTab)));
    window.addEventListener("hashchange", () => switchTab(tabFromUrl(), false));
    root.querySelectorAll("[data-operation]").forEach(button => button.addEventListener("click", () => setOperation(button.dataset.operation)));
    byId("db-base-table").addEventListener("change", () => resetBuilder(baseTable()));
    byId("db-base-alias").addEventListener("input", () => { refreshColumnSelects(); updatePreview(); });
    byId("db-summary-enabled").addEventListener("change", toggleSummary);
    byId("db-add-result").addEventListener("click", () => addResult());
    byId("db-add-group").addEventListener("click", () => addGroup());
    byId("db-add-aggregate").addEventListener("click", () => addAggregate());
    byId("db-add-value").addEventListener("click", () => addValue());
    byId("db-add-filter").addEventListener("click", () => addFilter(containers.filters));
    byId("db-add-having").addEventListener("click", () => addFilter(containers.having, {}, true));
    byId("db-add-order").addEventListener("click", () => addOrder());
    byId("db-add-join").addEventListener("click", () => addJoin());
    byId("db-add-sql-parameter").addEventListener("click", () => addSqlParameter());
    ["db-limit", "db-offset"].forEach(id => byId(id).addEventListener("input", updatePreview));
    byId("db-run-builder").addEventListener("click", () => executionForBuilder("screen"));
    byId("db-csv-builder").addEventListener("click", () => executionForBuilder("csv"));
    byId("db-run-sql").addEventListener("click", () => executionForSql("screen"));
    byId("db-csv-sql").addEventListener("click", () => executionForSql("csv"));
    byId("db-results-table-view").addEventListener("click", () => setResultView("table"));
    byId("db-results-unicode-view").addEventListener("click", () => setResultView("unicode"));
    byId("db-copy-results").addEventListener("click", copyUnicodeResults);
    byId("db-save-builder").addEventListener("click", () => openSave("builder"));
    byId("db-save-sql").addEventListener("click", () => openSave("sql"));
    byId("db-confirm-run").addEventListener("click", event => { event.preventDefault(); const fields = Array.from(byId("db-parameter-fields").querySelectorAll("[data-parameter-name]")); if (!fields.every(input => input.reportValidity())) return; const values = Object.fromEntries(fields.map(input => [input.dataset.parameterName, input.type === "checkbox" ? input.checked : input.value])); byId("db-parameter-dialog").close(); executePending(values); });
    byId("db-confirm-save").addEventListener("click", event => { event.preventDefault(); if (!byId("db-save-name").reportValidity()) return; byId("db-save-dialog").close(); savePending(); });

    const initialTab = tabFromUrl();
    window.history.replaceState(null, "", `#${initialTab}`);
    switchTab(initialTab, false);

    (async () => {
        try {
            setStatus("Loading schema…");
            state.schema = await requestJson(`${api}/schema`);
            state.objectMap = new Map(state.schema.objects.map(item => [item.name, item]));
            fillSelect(byId("db-base-table"), state.schema.objects.map(item => ({ value: item.name, label: `${item.name} (${item.kind})` })), "", "Select table or view…");
            resetBuilder();
            switchTab(tabFromUrl(), false);
            await loadSavedQueries();
            setStatus("Schema loaded.", "success");
        } catch (error) { setStatus(error.message, "error"); }
    })();
}
