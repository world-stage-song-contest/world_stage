let voteOrder = []
let votes = {}
let data = []
let points = []
let maxPoints = 0;
let associations = {}
let userSongs = {}
// In specials and national finals, country alone doesn't uniquely identify
// an entry, so the scoreboard shows the song title in its place. Set by
// ``onLoad``.
let useSongTitleLabels = false;
// {song_id: penalty} — populated from the server. Songs absent from the
// map have no penalty.
let penalties = {}

const theme = window.scoreboardTheme;
if (!theme) {
    throw new Error("A scoreboard theme script must be loaded before scoreboard.js");
}

// Themes own markup and presentation. The core only relies on this rendering
// interface, keeping voting, score animation, sorting, and the two-column split
// independent of any stylesheet's selectors or geometry.
const requiredThemeMethods = [
    "createRow", "createPointsLegend", "createVotingCard", "renderNumber",
    "renderText", "positionRow", "setActive", "setInactive", "markReceived",
    "applyPenalty", "stopMoving", "markCannotWin", "markWinner", "markOwnEntry",
    "setFinalPlace", "showVotingCard", "hideVotingCard", "updateJuryProgress",
    "completeJuryProgress", "reset", "toggleHeader"
];
for (const method of requiredThemeMethods) {
    if (typeof theme[method] !== "function") {
        throw new Error(`The scoreboard theme is missing ${method}()`);
    }
}

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

function totalVoteCount(votes) {
    return Object.values(votes).reduce((total, count) => total + count, 0);
}

function comparePointCounts(first, second) {
    const allPoints = new Set([...Object.keys(first), ...Object.keys(second)]);
    const descendingPoints = [...allPoints].map(Number).sort((a, b) => b - a);
    for (const point of descendingPoints) {
        const difference = (first[point] || 0) - (second[point] || 0);
        if (difference !== 0) return difference;
    }
    return 0;
}

function toggleHeader() {
    theme.toggleHeader();
    // Header visibility changes the vertical space available to responsive
    // themes without producing a native resize event. Wait for the display
    // change to be laid out, then run the normal positioning pass.
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
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

const displayValues = new WeakMap();

function initialiseDisplay(element) {
    displayValues.set(element, 0);
    theme.renderNumber(element, 0);
}

function setElementText(element, value) {
    theme.renderText(element, value);
}

function setElementValue(element, value) {
    displayValues.set(element, value);
    theme.renderNumber(element, value);
}

const duration = 1250;

/**
 * Animate the integer value displayed by ``element`` from its current
 * value to ``end``, one step at a time. Values are kept in the core so a
 * theme can format its display without exposing that formatting back here.
 *
 * @param {HTMLElement} element
 * @param {number} end
 */
/**
 * Bump and return the per-element animation token. Any in-flight rAF
 * loop or deferred reset captured the previous token, so writing a new
 * one cancels them on their next tick — protecting us from two
 * animations racing on the same display (which used to leave
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
    let current = displayValues.get(element) || 0;
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
    // mutating now-detached elements (and worse, can
    // race with a fresh animation on the same DOM node).
    const gen = runGen;

    function update(now) {
        if (gen !== runGen) return;
        // A newer animation (or reset) on this element has superseded
        // us — bail before updating the display.
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
    code;
    /** @type {boolean} */
    win;
    /** @type {Object<number, number>} */
    votes;
    /** @type {Object} */
    view;

    constructor(data) {
        this.index = data.index;
        this.ro = data.ro;
        this.name = data.name;
        this.artist = data.artist;
        this.title = data.title;
        this.id = data.id;
        this.code = data.cc || "XX";
        this.win = true;
        this.penalty = 0;
        this.votes = new Proxy({}, {
            get: (target, name) => name in target ? target[name] : 0
        });
        this.view = theme.createRow(this);
        initialiseDisplay(this.view.current);
        initialiseDisplay(this.view.total);
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
     * is animated downwards and the theme is notified so it can render the
     * penalised state.
     * @param {number} amount
     */
    applyPenalty(amount) {
        this.penalty = (this.penalty || 0) + amount;
        this.setActive();
        theme.applyPenalty(this.view);
        animatePoints(this.view.total, this.points);
        animatePoints(this.view.current, -amount);
    }

    get voters() {
        return totalVoteCount(this.votes);
    }

    /**
     * @param {number} i
     * @param {number} lim
     */
    setPosition(i, lim) {
        this.index = i;
        theme.positionRow(this.view, i, lim);
    }

    /**
     * @param {number} pt
     */
    vote(pt) {
        this.votes[pt]++;
        this.refresh(pt);
    }

    setActive() {
        theme.setActive(this.view);
    }

    setInactive() {
        theme.setInactive(this.view);
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
            const token = bumpAnimToken(this.view.current);
            setTimeout(() => {
                if (gen !== runGen) return;
                if (token !== this.view.current._animToken) return;
                setElementValue(this.view.current, 0);
            }, 1100);
            this.setInactive();
        } else {
            animatePoints(this.view.total, this.points);
            animatePoints(this.view.current, pt);
            this.setActive();
            let rank = null;
            if (pt == points[points.length - 1]) {
                rank = 1;
            } else if (pt == points[points.length - 2]) {
                rank = 2;
            } else if (pt == points[points.length - 3]) {
                rank = 3;
            }
            theme.markReceived(this.view, rank);
        }
    }

    /**
     * @param {number} place
     */
    setPlace(place) {
        this.setActive();
        theme.setFinalPlace(this.view, place);
        animatePoints(this.view.current, place);
    }

    finalise() {
        theme.stopMoving(this.view);
    }

    /**
     * @param {Country} other
     * @returns {number}
     */
    compare(other) {
        const ptsDiff = this.points - other.points;
        if (ptsDiff != 0) return ptsDiff;

        const votersDiff = this.voters - other.voters;
        if (votersDiff != 0) return votersDiff;

        const vtsDiff = comparePointCounts(this.votes, other.votes);
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
            theme.markCannotWin(this.view);
        }
    }

    setWinner() {
        theme.markWinner(this.view);
    }

    setOwnEntry() {
        this.setActive();
        theme.markOwnEntry(this.view);
        setElementText(this.view.current, "()");
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
    perColumn = Math.ceil(cnt / 2);
    return perColumn;
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
            name: useSongTitleLabels ? c.title : c.country.name,
            cc: c.country.cc
        });
        countries[c.id] = country;
        ro.push(country);

        container.appendChild(country.view.element);

        country.setPosition(c.vote_data.ro - 1, perColumn);
    }
}

function depopulate() {
    for (const c of Object.values(countries)) {
        c.view.element.remove();
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
            theme.stopMoving(c.view);
        }, delay * 2.5);
        c.setPosition(i, perColumn);
    }

    await sleep(delay * 2.5);
}

async function vote() {
    const gen = runGen;
    const stale = () => gen !== runGen;

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

        const card = theme.createVotingCard({
            name: nickname,
            code,
            country,
            username: from
        });
        fromJury.appendChild(card);

        while (paused) {
            await sleep(100);
            if (stale()) return;
        }

        theme.updateJuryProgress(juryCount, voterCount);

        await sleep(50);
        if (stale()) return;
        theme.showVotingCard(card);
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

        theme.hideVotingCard(card);
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

    theme.completeJuryProgress();

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
    const card = theme.createVotingCard({name: "Penalties", penalty: true});
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
    theme.showVotingCard(card);
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

    theme.hideVotingCard(card);
    await sleep(delay * 2.5);
    if (stale()) return;
    card.remove();

    // Reset per-row state so the upcoming vote loop starts from a
    // clean slate (current score back to 0, rows inactive).
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

    theme.reset();

    depopulate();
    populate();
    await vote();
}

async function onLoad(year, show, songTitleLabels = false) {
    if (loaded) return;
    loaded = true;
    useSongTitleLabels = !!songTitleLabels;

    await loadVotes(year, show);

    window.addEventListener('resize', () => {
        setColumnLimit();
        for (const country of ro) {
            country.setPosition(country.index, perColumn);
        }
    }, true);
    setColumnLimit();

    document.querySelector("#total-juries").innerHTML = voteOrder.length;

    document.querySelector("#reset").onclick = async () => {
        await reset();
    }

    theme.createPointsLegend(points);
    populate();
    await vote();
}
