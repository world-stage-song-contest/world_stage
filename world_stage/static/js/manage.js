async function fetchHelper(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
    });
    const data = await res.json();
    return data;
}

function setError(message) {
    if (message) {
        const error = document.getElementById('error');
        error.textContent = message;
        error.classList.remove('hidden');
    } else {
        const error = document.getElementById('error');
        error.textContent = '';
        error.classList.add('hidden');
    }
}

async function openVoting(showId) {
    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'open_voting'
    };

    const data = await fetchHelper(url, body);

    setError(data.error);
}

async function closeVoting(showId) {
    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'close_voting'
    };

    const data = await fetchHelper(url, body);

    setError(data.error);
}

async function changeAccessType(el, showId) {
    const select = document.getElementById(el.dataset.select);
    if (!select) {
        const msg = `Select element with ID ${selectId} not found.`;
        setError(msg);
        console.error(msg);
        return;
    }

    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'set_access_type',
        'access_type': select.value
    };

    const data = await fetchHelper(url, body);

    setError(data.error);
}

async function changeDate(el, showId) {
    const dateInput = document.getElementById(el.dataset.input);
    if (!dateInput) {
        const msg = `Input element with ID ${el.dataset.input} not found.`;
        setError(msg);
        console.error(msg);
        return;
    }
    const date = dateInput.value;
    if (!date) {
        setError('Date cannot be empty.');
        return;
    }
    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'change_date',
        'date': date
    };
    const data = await fetchHelper(url, body);
    setError(data.error);
}

async function changeYearStatus() {
    const select = document.getElementById('year_status');
    if (!select) {
        const msg = `Select element with ID year_status not found.`;
        setError(msg);
        console.error(msg);
        return;
    }
    const url = window.location.href;
    const body = {
        'action': 'change_year_status',
        'year_status': select.value
    };
    const data = await fetchHelper(url, body);
    setError(data.error);
}

const actionsWhitelist = ['approve', 'unapprove', 'annul_password'];
async function modifyUser(userId, action, extraData) {
    if (!actionsWhitelist.includes(action)) {
        setError('Invalid action specified.');
        return;
    }

    const url = window.location.href;
    const body = {
        'action': action,
        'user_id': userId,
        'extra_data': extraData || {}
    };

    const data = await fetchHelper(url, body);

    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}