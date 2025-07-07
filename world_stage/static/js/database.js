async function submitQuery() {
    const query = document.getElementById("query").value;
    const resultTable = document.getElementById("result");
    const error = document.getElementById("error");

    resultTable.innerHTML = "<thead></thead><tbody></tbody>";
    error.textContent = "";

    const body = {
        'query': query
    };

    try {
        const response = await fetch(window.location.pathname, {
            method: "POST",
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        const thead = resultTable.querySelector("thead");
        const tbody = resultTable.querySelector("tbody");

        thead.innerHTML = "";
        tbody.innerHTML = "";

        const headers = data.headers || [];
        const rows = data.rows || [];

        const headerRow = document.createElement("tr");
        headers.forEach(header => {
            const th = document.createElement("th");
            th.textContent = header;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);

        rows.forEach(row => {
            const tr = document.createElement("tr");
            headers.forEach(header => {
                const td = document.createElement("td");
                td.innerText = row[header];
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    } catch (error) {
        error.textContent = `Error: ${error.message}`;
    }
}