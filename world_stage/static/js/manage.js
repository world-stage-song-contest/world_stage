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
    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}

async function closeVoting(showId) {
    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'close_voting'
    };

    const data = await fetchHelper(url, body);
    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}

async function closePredictions(showId) {
    const url = window.location.href + `/${showId}`;
    const body = { 'action': 'close_predictions' };
    const data = await fetchHelper(url, body);
    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}

async function openPredictions(showId) {
    const url = window.location.href + `/${showId}`;
    const body = { 'action': 'open_predictions' };
    const data = await fetchHelper(url, body);
    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}

async function changeShowStatus(el, showId) {
    const select = document.getElementById(el.dataset.select);
    if (!select) {
        const msg = `Select element with ID ${selectId} not found.`;
        setError(msg);
        console.error(msg);
        return;
    }

    const url = window.location.href + `/${showId}`;
    const body = {
        'action': 'set_status',
        'status': select.value
    };

    const data = await fetchHelper(url, body);
    if (data.error) {
        setError(data.error);
    } else {
        location.reload();
    }
}

async function sendDiscordNotification(button, showId) {
    const label = button.textContent;
    button.disabled = true;
    const data = await fetchHelper(window.location.href + `/${showId}`, {
        'action': 'send_discord_notification'
    });
    if (data.error) {
        setError(data.error);
        button.disabled = false;
        return;
    }
    setError(null);
    button.textContent = 'Sent';
    setTimeout(() => {
        button.textContent = label;
        button.disabled = false;
    }, 2000);
}

async function sendRunningOrderNotification(button, showId) {
    const label = button.textContent;
    button.disabled = true;
    const data = await fetchHelper(window.location.href + `/${showId}`, {
        'action': 'send_running_order_notification'
    });
    if (data.error) {
        setError(data.error);
        button.disabled = false;
        return;
    }
    setError(null);
    button.textContent = 'Sent';
    setTimeout(() => {
        button.textContent = label;
        button.disabled = false;
    }, 2000);
}

async function sendFinalResultsNotification(button, showId) {
    const label = button.textContent;
    button.disabled = true;
    const data = await fetchHelper(window.location.href + `/${showId}`, {
        'action': 'send_final_results_notification'
    });
    if (data.error) {
        setError(data.error);
        button.disabled = false;
        return;
    }
    setError(null);
    button.textContent = 'Sent';
    setTimeout(() => {
        button.textContent = label;
        button.disabled = false;
    }, 2000);
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

async function changeSubmissionsOpen() {
    const checkbox = document.getElementById('submissions_open');
    if (!checkbox) {
        const msg = `Checkbox with ID submissions_open not found.`;
        setError(msg);
        console.error(msg);
        return;
    }
    const data = await fetchHelper(window.location.href, {
        'action': 'set_submissions_open',
        'submissions_open': checkbox.checked
    });
    setError(data.error);
}

async function changeScoreboardStyle() {
    const select = document.getElementById('scoreboard_style');
    if (!select) {
        setError('Scoreboard style selector not found.');
        return;
    }
    const data = await fetchHelper(window.location.href, {
        'action': 'set_scoreboard_style',
        'scoreboard_style': select.value || null
    });
    setError(data.error);
}

async function deletePlaceholders(button) {
    if (!confirm('Are you sure you want to delete all placeholders from this year?')) return;
    button.disabled = true;
    try {
        const data = await fetchHelper(window.location.href, { action: 'delete_placeholders' });
        if (data.error) {
            setError(data.error);
        } else {
            location.reload();
        }
    } catch (error) {
        setError(`Failed to delete placeholders: ${error.message}`);
    } finally {
        button.disabled = false;
    }
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
