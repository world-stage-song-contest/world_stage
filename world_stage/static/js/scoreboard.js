let voteOrder = []
let votes = {}
let data = []
let points = []
let maxPoints = 0;
let associations = {}
let userSongs = {}
// In specials, country alone doesn't uniquely identify an entry (a
// country can submit multiple songs), so the scoreboard shows the song
// title in place of the country name. Set by ``onLoad``.
let isSpecial = false;
// {song_id: penalty} — populated from the server. Songs absent from the
// map have no penalty.
let penalties = {}

/**
 * Run-generation counter. Bumped by ``reset()``; long-running async
 * functions (vote loop, sortCountries, animatePoints, applyPenaltyStage)
 * capture the generation at start and bail out as soon as they notice
 * it's been incremented. This stops a half-finished previous run from
 * mutating the new run's state (the module-level ``countries`` /
 * ``ro``) or animating now-detached DOM nodes.
 */
let runGen = 0;

/** ``await sleep(ms)`` — promise-wrapped setTimeout. */
function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
}

function toggleHeader() {
    const header = document.querySelector("header");
    header.classList.toggle("hidden");
}

async function loadVotes(year, show) {
    const res = await fetch(window.location.pathname + '/votes');
    const json = await res.json();
    points = json.points;
    userSongs = json.user_songs;
    points.sort((a, b) => a - b);
    maxPoints = points[points.length - 1];
    voteOrder = json.vote_order;
    for (const song of json.songs) {
        data.push(song);
    }
    data.sort((a, b) => a.vote_data.ro - b.vote_data.ro);
    votes = json.results;
    associations = json.associations;
    penalties = json.penalties || {};
}

function makeRow(country) {
    function makePointDisplay(padding, className) {
        const el = document.createElement("div");
        // Start with .zero so the initial "0" is dimmed like the
        // original per-digit zero-value treatment.
        el.classList.add(className, "number", "point-display", "zero");
        el.dataset.pad = String(padding);
        // CSS uses ``data-ghost`` to render the dim "all-segments" LCD
        // backdrop via a ::before pseudo-element. The cell shows the
        // absolute value of the score; negativity is conveyed via the
        // ``negative`` class so the digits change colour instead.
        el.dataset.ghost = "8".repeat(padding);
        el.dataset.value = "0";
        el.textContent = "0".padStart(padding, " ");
        return el;
    }

    const superContainer = document.createElement("div");
    superContainer.classList.add("element", "inactive");
    //superContainer.classList.add(`background-${country.bg}`, `text-${country.text}`, `foreground-${country.fg1}`, `foreground2-${country.fg2}`);
    superContainer.dataset.country = country.name;
    superContainer.dataset.id = country.id;

    const container = document.createElement("div");
    container.classList.add("inner-container");
    superContainer.appendChild(container);

    const overlayEl = document.createElement("div");
    overlayEl.classList.add("element-overlay");
    container.appendChild(overlayEl);

    const placeEl = document.createElement("div");
    placeEl.classList.add("element-place", "number");
    placeEl.textContent = "";
    container.appendChild(placeEl);

    const flagContainer = document.createElement("div");
    flagContainer.classList.add("flag-container");
    container.appendChild(flagContainer);

    const flagEl = document.createElement("img");
    flagEl.classList.add("flag");
    flagEl.src = window.flagStaticUrl(country.code, 40, "square");
    flagEl.alt = country.name;
    flagContainer.appendChild(flagEl);

    const flagOverlayEl = document.createElement("div");
    flagOverlayEl.classList.add("flag-overlay");
    flagContainer.appendChild(flagOverlayEl);

    const nameContainer = document.createElement("div");
    nameContainer.classList.add("name-container");
    container.appendChild(nameContainer);

    const nameEl = document.createElement("div");
    nameEl.classList.add("name");
    nameEl.textContent = country.name;
    nameContainer.appendChild(nameEl);

    /*
    const subtitleContainer = document.createElement("div");
    subtitleContainer.classList.add("subtitle");
    nameContainer.appendChild(subtitleContainer);

    const titleEl = document.createElement("span");
    titleEl.classList.add("title");
    titleEl.textContent = country.title;
    subtitleContainer.appendChild(titleEl);

    const byNode = document.createTextNode(" by ");
    subtitleContainer.appendChild(byNode);

    const artistEl = document.createElement("span");
    artistEl.classList.add("artist");
    artistEl.textContent = country.artist;
    subtitleContainer.appendChild(artistEl);
    */

    const currentlyVotingEl = document.createElement("div");
    currentlyVotingEl.classList.add("currently-voting");
    container.appendChild(currentlyVotingEl);

    const currentEl = makePointDisplay(2, "current-points");
    container.appendChild(currentEl);

    const totalEl = makePointDisplay(3, "total-points");
    container.appendChild(totalEl);

    return [nameEl, currentEl, totalEl, superContainer, currentlyVotingEl];
}

function makePointsRow() {
    const row = document.querySelector("#points-row");

    for (const pt of points) {
        const container = document.createElement("div");
        container.classList.add("points-container");

        if (pt == points[points.length - 1]) {
            container.classList.add("gold");
        } else if (pt == points[points.length - 2]) {
            container.classList.add("silver");
        } else if (pt == points[points.length - 3]) {
            container.classList.add("bronze");
        }

        const ptEl = document.createElement("div");
        ptEl.classList.add("points-value", "number");
        const v = String(pt).padStart(2, "0");
        ptEl.textContent = v;
        ptEl.dataset.pad = "2";
        ptEl.dataset.value = pt;
        container.appendChild(ptEl);

        const overlayEl = document.createElement("div");
        overlayEl.classList.add("points-overlay");
        container.appendChild(overlayEl);

        row.insertBefore(container, row.firstChild);
    }

    return row;
}

function makeVotingCard(from, code, country, username = null) {
    code = code || "XX";

    const container = document.createElement("div");
    container.classList.add("voting-card", "unloaded");

    const flagEl = document.createElement("img");
    flagEl.classList.add("voting-card-flag");
    flagEl.src = window.flagStaticUrl(code, 96);
    flagEl.alt = from;
    container.appendChild(flagEl);

    const wrapperEl = document.createElement("div");
    wrapperEl.classList.add("voting-card-user-wrapper");
    container.appendChild(wrapperEl);

    const nameEl = document.createElement("span");
    nameEl.classList.add("voting-card-name");
    nameEl.textContent = from;
    wrapperEl.appendChild(nameEl);

    // Subtitle: "[username] from [country]" for real jurors, dropping
    // the username when it matches the displayed name. Synthetic cards
    // without a country (e.g. the penalty stage) skip the line entirely.
    if (country) {
        const countryEl = document.createElement("span");
        countryEl.classList.add("voting-card-country");
        if (username && username !== from) {
            countryEl.textContent = `${username} from ${country}`;
        } else {
            countryEl.textContent = `from ${country}`;
        }
        wrapperEl.appendChild(countryEl);
    }

    return container;
}

/**
 * Replace ``el``'s text with an arbitrary (non-numeric) string,
 * right-aligned within ``data-pad`` chars. Always clears the
 * ``negative`` class.
 */
function setElementText(el, value) {
    const pad = +el.dataset.pad || 0;
    el.classList.remove("negative", "zero");
    el.textContent = String(value).padStart(pad, " ");
}

/**
 * Update the numeric value displayed by ``el``. Only the absolute value
 * is rendered — negativity is conveyed via the ``negative`` class
 * (typically a red colour) so the digit count never has to grow to
 * accommodate a sign character.
 *
 * The numeric value is also stashed on ``data-value`` so
 * :func:`animatePoints` doesn't need to parse the formatted text back
 * out.
 */
function setElementValue(el, value) {
    const pad = +el.dataset.pad || 0;
    el.dataset.value = String(value);
    el.classList.toggle("negative", value < 0);
    // Mark zero values so CSS can dim the cell — matches the original
    // per-digit zero-value treatment.
    el.classList.toggle("zero", value === 0);
    el.textContent = String(Math.abs(value)).padStart(pad, " ");
}

const duration = 1250;

/**
 * Animate the integer value displayed by ``element`` from its current
 * value to ``end``, one step at a time. The current value is read from
 * ``data-value`` so the formatted text (with a sign char and space
 * padding) doesn't need to be parsed back to a number.
 *
 * @param {HTMLElement} element
 * @param {number} end
 */
/**
 * Bump and return the per-element animation token. Any in-flight rAF
 * loop or deferred reset captured the previous token, so writing a new
 * one cancels them on their next tick — protecting us from two
 * animations racing on the same dataset.value (which used to leave
 * end-of-show rows stuck on the points-just-received or on 0 when the
 * deferred refresh(null) timeout fired after setPlace finished).
 */
function bumpAnimToken(element) {
    const next = (element._animToken || 0) + 1;
    element._animToken = next;
    return next;
}

function animatePoints(element, end) {
    end = +end;
    let current = parseInt(element.dataset.value, 10);
    if (Number.isNaN(current)) current = 0;
    // Bump the token even on the early-return path so any pending
    // deferred reset on this element is invalidated.
    const token = bumpAnimToken(element);
    if (current === end) {
        setElementValue(element, end);
        return;
    }
    const direction = end > current ? 1 : -1;
    const stepDuration = duration / Math.max(1, maxPoints - 1);
    let lastTime = performance.now();
    // Capture the run generation so we can abandon this rAF loop if a
    // reset has happened since we started — otherwise the loop keeps
    // mutating dataset.value on now-detached elements (and worse, can
    // race with a fresh animation on the same DOM node).
    const gen = runGen;

    function update(now) {
        if (gen !== runGen) return;
        // A newer animation (or reset) on this element has superseded
        // us — bail before mutating dataset.value.
        if (token !== element._animToken) return;
        if (now - lastTime >= stepDuration) {
            lastTime = now;
            current += direction;
            setElementValue(element, current);
        }
        if (current !== end) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

class Country {
    /** @type {number} */
    index;
    /** @type {number} */
    ro;
    /** @type {string} */
    name;
    /** @type {string} */
    artist;
    /** @type {string} */
    title;
    /** @type {number} */
    id;
    /** @type {string} */
    country;
    /** @type {string} */
    code;
    /** @type {boolean} */
    win;
    /** @type {Object<number, number>} */
    votes;
    /** @type {HTMLElement} */
    element;
    /** @type {HTMLElement} */
    nameEl;
    /** @type {HTMLElement} */
    currentEl;
    /** @type {HTMLElement} */
    totalEl;
    /** @type {HTMLElement} */
    currentlyVotingEl;
    /** @type {string} */
    bg;
    /** @type {string} */
    fg1;
    /** @type {string} */
    fg2;
    /** @type {string} */
    text;

    constructor(data) {
        this.index = data.index;
        this.ro = data.ro;
        this.name = data.name;
        this.artist = data.artist;
        this.title = data.title;
        this.id = data.id;
        this.code = data.cc || "XX";
        this.bg = data.bg;
        this.fg1 = data.fg1;
        this.fg2 = data.fg2;
        this.text = data.text;
        this.win = true;
        this.penalty = 0;
        this.votes = new Proxy({}, {
            get: (target, name) => name in target ? target[name] : 0
        });
        [this.nameEl, this.currentEl, this.totalEl, this.element, this.currentlyVotingEl] = makeRow(this);
    }

    get points() {
        const raw = Object.entries(this.votes).reduce(
            (a, v) => a + v[0] * v[1],
            0
        );
        // Don't floor at 0 — penalties can push the running total
        // negative early in the show, and ``setElementValue`` already
        // renders negative values (red, no minus sign in the LCD digits).
        return raw - (this.penalty || 0);
    }

    /**
     * Apply a penalty deduction at the end of voting. The total display
     * is animated downwards and the row is marked with the ``penalised``
     * class so it can be styled distinctly.
     * @param {number} amount
     */
    applyPenalty(amount) {
        this.penalty = (this.penalty || 0) + amount;
        this.setActive();
        this.element.classList.add("penalised");
        animatePoints(this.totalEl, this.points);
        animatePoints(this.currentEl, -amount);
    }

    get voters() {
        return Object.keys(this.votes).length;
    }

    /**
     * @param {number} i
     * @param {number} lim
     */
    setPosition(i, lim) {
        const col = Math.floor(i / lim);
        const row = i - lim * col;

        this.index = i;
        const elsz = this.element.getBoundingClientRect();
        const yoff = elsz.height + 5;
        const xoff = elsz.width + 5;

        this.element.style.top = `${yoff * row}px`;
        this.element.style.left = `${xoff * col}px`;
    }

    /**
     * @param {number} pt
     */
    vote(pt) {
        this.votes[pt]++;
        this.refresh(pt);
    }

    setActive() {
        this.element.classList.remove("inactive", "own-entry");
        this.currentEl.classList.add("visible");
        this.element.classList.add("main-moving", "active", "received-points");
    }

    setInactive() {
        this.currentEl.classList.remove("visible");
        this.element.classList.add("inactive");
        this.element.classList.remove("received-gold", "received-silver", "received-bronze", "received-points", "active", "own-entry");
    }

    /**
     * @param {number} pt
     */
    refresh(pt) {
        if (pt == null) {
            const gen = runGen;
            // Token-protect the deferred reset: if anything else
            // (e.g. setPlace → animatePoints) targets this element
            // before the timeout fires, that call bumps the token and
            // we skip the reset, leaving the newer animation's value
            // intact.
            const token = bumpAnimToken(this.currentEl);
            setTimeout(() => {
                if (gen !== runGen) return;
                if (token !== this.currentEl._animToken) return;
                setElementValue(this.currentEl, 0);
            }, 1100);
            this.setInactive();
        } else {
            animatePoints(this.totalEl, this.points);
            animatePoints(this.currentEl, pt);
            this.setActive();
            if (pt == points[points.length - 1]) {
                this.element.classList.add("received-gold");
            } else if (pt == points[points.length - 2]) {
                this.element.classList.add("received-silver");
            } else if (pt == points[points.length - 3]) {
                this.element.classList.add("received-bronze");
            }
        }
    }

    /**
     * @param {number} place
     */
    setPlace(place) {
        this.setActive();
        const parent = this.element.parentElement;
        parent.insertBefore(this.element, parent.childNodes[place]);
        animatePoints(this.currentEl, place);
    }

    finalise() {
        this.element.classList.remove("main-moving");
    }

    /**
     * @param {Country} other
     * @returns {number}
     */
    compare(other) {
        function compareDicts(dict1, dict2) {
            const keys1 = Object.keys(dict1);
            const keys2 = Object.keys(dict2);
            const allKeys = [...new Set([...keys1, ...keys2])];
            allKeys.sort((a, b) => b - a);
            for (const key of allKeys) {
                const val1 = dict1[key];
                const val2 = dict2[key];

                if (val1 !== val2) {
                    return val1 - val2;
                }
            }
            return 0;
        }

        const ptsDiff = this.points - other.points;
        if (ptsDiff != 0) return ptsDiff;

        const votersDiff = this.voters - other.voters;
        if (votersDiff != 0) return votersDiff;

        const vtsDiff = compareDicts(this.votes, other.votes);
        if (vtsDiff != 0) return vtsDiff;

        return other.ro - this.ro;
    }

    /**
     * Mark this country as no-longer-able-to-win once the maximum
     * remaining points it could pick up can't catch the leader.
     *
     * @param {number} leftVotes  How many voters have yet to cast a ballot.
     * @param {number} leaderPts  Current leader's score.
     */
    setCanWin(leftVotes, leaderPts) {
        if (!this.win) return;
        // ``Math.max(points)`` returned NaN — Math.max doesn't accept
        // arrays. Spread the array so we get the actual max point value.
        const left = this.points + leftVotes * Math.max(...points);
        if (left <= leaderPts) {
            this.win = false;
            this.element.classList.add("no-win");
        }
    }

    setWinner() {
        this.element.classList.add("winner");
        this.element.classList.remove("no-win", "own-entry", "active");
    }

    setOwnEntry() {
        this.setActive();
        this.element.classList.add("own-entry");
        setElementText(this.currentEl, "()");
    }

    toString() {
        return `Country { name = ${this.name}, ro = ${this.ro}, votes = ${this.votes} }`;
    }
}

let countries = {};
let ro = [];
let perColumn = 0;

function setColumnLimit() {
    const cnt = data.length;
    const style = window.getComputedStyle(document.body);
    const lim = style.getPropertyValue('--columns');
    perColumn = Math.ceil(cnt / lim);
    return [cnt, lim, perColumn]
}

function populate() {
    const container = document.querySelector("#container");
    for (const [i, c] of data.entries()) {
        const country = new Country({
            index: i,
            artist: c.artist,
            title: c.title,
            ro: c.vote_data.ro,
            id: c.id,
            name: isSpecial ? c.title : c.country.name,
            cc: c.country.cc,
            bg: c.country.bg,
            fg1: c.country.fg1,
            fg2: c.country.fg2,
            text: c.country.text
        });
        countries[c.id] = country;
        ro.push(country);

        container.appendChild(country.element);

        country.setPosition(c.vote_data.ro - 1, perColumn);
    }
}

function depopulate() {
    for (const c of Object.values(countries)) {
        c.element.remove();
    }

    countries = {};
    ro = [];
}

let paused = true;
let delay = 1000;
let isReset = false;
let isVoting = false;

async function sortCountries() {
    const gen = runGen;
    await sleep(delay * 2.5);
    if (gen !== runGen) return;

    ro.sort((a, b) => b.compare(a));
    for (const [i, c] of ro.entries()) {
        setTimeout(() => {
            if (gen !== runGen) return;
            c.element.classList.remove("main-moving");
        }, delay * 2.5);
        c.setPosition(i, perColumn);
    }

    await sleep(delay * 2.5);
}

async function vote() {
    const gen = runGen;
    const stale = () => gen !== runGen;

    const juryCounter = document.querySelector("#jury-count");
    const juryBar = document.querySelector("#jury-bar");
    const fromJury = document.querySelector("#from");

    let juryCount = 0;
    const voterCount = voteOrder.length;
    const pointsImmediate = points.slice(0, points.length - 3);
    const pointsDelayed = points.slice(points.length - 3);

    // Penalties are revealed up front so their effect is baked into the
    // running totals shown during voting (Country.points already
    // subtracts ``this.penalty`` from the raw vote total).
    await applyPenaltyStage();
    if (stale()) return;

    for (const from of voteOrder) {
        juryCount++;
        const vts = votes[from];
        const entries = userSongs[from] || [];

        let nickname = from;
        let country = null;
        let code = null;
        const assoc = associations[from];
        if (assoc) {
            nickname = assoc.nickname || from;
            country = assoc.country;
            code = assoc.code;
        }

        const card = makeVotingCard(nickname, code, country, from);
        fromJury.appendChild(card);

        while (paused) {
            await sleep(100);
            if (stale()) return;
        }

        juryCounter.textContent = juryCount;
        juryBar.classList.add("animating");
        setTimeout(() => {
            if (stale()) return;
            juryBar.classList.remove("animating");
        }, 2100);
        juryBar.style.width = `${(juryCount / voterCount) * 100}%`;

        await sleep(50);
        if (stale()) return;
        card.classList.remove("unloaded");
        await sleep(2000);
        if (stale()) return;

        for (const entry of entries) {
            const country = countries[entry];
            if (country) {
                country.setOwnEntry();
            }
        }
        if (entries.length) {
            await sleep(500);
            if (stale()) return;
        }

        for (const pt of pointsImmediate) {
            while (paused) {
                if (isReset) {
                    isReset = false;
                    return;
                }
                await sleep(100);
                if (stale()) return;
            }

            const country = countries[vts[pt]];
            country.vote(pt);
        }

        await sortCountries();
        if (stale()) return;

        for (const pt of pointsDelayed) {
            while (paused) {
                if (isReset) {
                    isReset = false;
                    return;
                }
                await sleep(100);
                if (stale()) return;
            }

            const country = countries[vts[pt]];
            country.vote(pt);

            await sortCountries();
            if (stale()) return;
        }

        card.classList.add("unloaded2");
        await sleep(delay * 2.5);
        if (stale()) return;

        const leader = ro[0];

        if (juryCount != voterCount) {
            for (const c of ro) {
                c.refresh();
                c.setCanWin(voterCount - juryCount, leader.points);
            }
        }

        await sleep(100);
        if (stale()) return;

        card.remove();

        await sleep(delay);
        if (stale()) return;
    }

    juryBar.style.width = "100%";

    ro[0].setWinner();
    for (const [i, c] of ro.entries()) {
        c.setPlace(i + 1);
    }
}

/**
 * Reveal admin-applied penalties up front so they're baked into every
 * row's running total before voting starts. All affected rows animate
 * simultaneously. Skipped entirely if no penalties exist.
 */
async function applyPenaltyStage() {
    const entries = Object.entries(penalties || {});
    if (entries.length === 0) return;

    const gen = runGen;
    const stale = () => gen !== runGen;

    const fromJury = document.querySelector("#from");
    const card = makeVotingCard("Penalties", "XX", null);
    card.classList.add("penalty-card");
    fromJury.appendChild(card);

    while (paused) {
        if (isReset) {
            isReset = false;
            return;
        }
        await sleep(100);
        if (stale()) return;
    }

    await sleep(50);
    if (stale()) return;
    card.classList.remove("unloaded");
    await sleep(2000);
    if (stale()) return;

    // Fire every row's penalty animation in parallel — they all share
    // the same animation duration so the visual lands on every row at
    // about the same moment.
    for (const [songId, amount] of entries) {
        const country = countries[songId];
        if (country) country.applyPenalty(+amount);
    }
    // Sort once, after every penalty has been registered. Penalised
    // rows can now have negative running totals so the order changes;
    // sortCountries' built-in pre-sort ``sleep(delay * 2.5)`` also
    // doubles as the wait for the penalty animation to land.
    await sortCountries();
    if (stale()) return;

    card.classList.add("unloaded2");
    await sleep(delay * 2.5);
    if (stale()) return;
    card.remove();

    // Reset per-row state so the upcoming vote loop starts from a
    // clean slate (currentEl back to 0, rows inactive).
    for (const c of ro) {
        c.refresh();
    }
    await sleep(delay);
}

function togglePause() {
    paused = !paused;
}

function speedUp() {
    delay = Math.max(100, delay - 100);
}

function speedDown() {
    delay += 100;
}

let loaded = false

async function reset() {
    // Bumping the generation invalidates every captured ``gen`` in the
    // previous run — its in-flight rAF loops and setTimeouts become
    // no-ops, and its async functions return at their next stale check
    // instead of mutating the freshly-populated state below.
    runGen++;
    isReset = true;
    paused = true;

    document.querySelector("#from").innerHTML = "";

    depopulate();
    populate();
    await vote();
}

async function onLoad(year, show, special = false) {
    if (loaded) return;
    loaded = true;
    isSpecial = !!special;

    await loadVotes(year, show);

    window.addEventListener('resize', () => {
        setColumnLimit();
    }, true);
    setColumnLimit();

    document.querySelector("#total-juries").innerHTML = voteOrder.length;

    document.querySelector("#reset").onclick = async () => {
        await reset();
    }

    makePointsRow();
    populate();
    await vote();
}
