let voteOrder = []
let votes = {}
let data = []
let points = []
let maxPoints = 0;
let associations = {}
let userSongs = {}

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
}

function makeRow(country) {
    function makeElementDigits(el, className, start, padWith) {
        const digits = String(start).padStart(el.dataset.pad, padWith);
        for (const d of digits) {
            const digitEl = document.createElement("span");
            digitEl.classList.add(className);
            if (d == 0) {
                digitEl.classList.add("zero-value");
            }
            digitEl.textContent = d;
            el.appendChild(digitEl);
        }
    }

    function makePointDisplay(padding, className) {
        const currentEl = document.createElement("div");
        currentEl.classList.add(className, "number", "point-display");

        const bgEl = document.createElement("div");
        bgEl.classList.add("background-digits", "digits-container");
        bgEl.dataset.pad = padding;
        makeElementDigits(bgEl, "background-digit", 8, "8");
        currentEl.appendChild(bgEl);

        const fgEl = document.createElement("div");
        fgEl.classList.add("foreground-digits", "digits-container");
        fgEl.dataset.pad = padding;
        makeElementDigits(fgEl, "foreground-digit", 0, "0");
        currentEl.appendChild(fgEl);
        //makeElementDigits(currentEl, "foreground-digit");

        return [currentEl, fgEl];
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
    flagEl.src = `/flag/${country.code}.svg?t=square&s=40`;
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

    const [currentContEl, currentEl] = makePointDisplay(2, "current-points");
    container.appendChild(currentContEl);

    const [totalContEl, totalEl] = makePointDisplay(3, "total-points");
    container.appendChild(totalContEl);

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

function makeVotingCard(from, code, country) {
    code = code || "XXX";
    country = country || "Somewhere";

    const container = document.createElement("div");
    container.classList.add("voting-card", "unloaded");

    const flagEl = document.createElement("img");
    flagEl.classList.add("voting-card-flag");
    flagEl.src = `/flag/${code}.svg?t=rect&s=96`;
    flagEl.alt = from;
    container.appendChild(flagEl);

    const wrapperEl = document.createElement("div");
    wrapperEl.classList.add("voting-card-user-wrapper");
    container.appendChild(wrapperEl);

    const nameEl = document.createElement("span");
    nameEl.classList.add("voting-card-name");
    nameEl.textContent = from;
    wrapperEl.appendChild(nameEl);

    const countryEl = document.createElement("span");
    countryEl.classList.add("voting-card-country");
    countryEl.textContent = `From ${country}`;
    wrapperEl.appendChild(countryEl);

    return container;
}

function setDigitClasses(el, value, setZero) {
    if (setZero && (value == 0 || value == ' ')) {
        el.classList.add("zero-value");
        el.classList.remove("nonzero-value");
    } else {
        el.classList.remove("zero-value");
        el.classList.add("nonzero-value");
    }
}

/**
 * @param {HTMLElement} el
 * @param {number} value
 */
function setDigitValue(el, value) {
    el.textContent = value;
}


/**
 * @param {HTMLElement} el
 * @param {string} value
 */
function setElementText(el, value) {
    const digits = String(value).padStart(el.dataset.pad, " ");
    for (let i = 0; i < el.children.length; i++) {
        const digitEl = el.children[i];
        const char = digits[i];
        setDigitClasses(digitEl, char, true);
        setDigitValue(digitEl, char);
    }
}

/**
 * @param {HTMLElement} el
 * @param {number} value
 */
function setElementValue(el, value, setZero) {
    const digits = String(value).padStart(el.dataset.pad, "0");
    let leadingZero = true;
    for (let i = 0; i < el.children.length; i++) {
        const digitEl = el.children[i];
        const d = digits[i];

        const isZero = leadingZero && d == '0';
        if (d != '0') {
            leadingZero = false;
        }
        setDigitClasses(digitEl, d, setZero && isZero);
        setDigitValue(digitEl, d);
    }
}

function prepareElement(el, value, setZero) {
    const digits = String(value).padStart(el.dataset.pad, "0");
    let leadingZero = true;
    for (let i = 0; i < el.children.length; i++) {
        const digitEl = el.children[i];
        const d = digits[i];

        const isZero = leadingZero && d == '0';
        if (d != '0') {
            leadingZero = false;
        }
        setDigitClasses(digitEl, d, setZero && isZero);
    }
}

const duration = 1250;

/**
 * @param {HTMLElement} element
 * @param {number} end
 */
function animatePoints(element, end, setZero) {
    end = +end;
    //prepareElement(element, end, setZero);
    let start = parseInt(element.textContent) || 0;
    const direction = end > start ? 1 : -1;
    const stepDuration = duration / (maxPoints - 1);
    let lastTime = performance.now();

    function update(now) {
        const elapsed = now - lastTime;

        if (elapsed >= stepDuration) {
            lastTime = now;

            if (start !== end) {
                start += direction;
                setElementValue(element, start, setZero);
            }
        }

        if (start !== end) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

/**
 * @param {HTMLElement} element
 * @param {number} end
 */
function animateDigit(element, end, allowCountDown = false, setZero = false) {
    end = +end;
    let start = parseInt(element.textContent) || 0;
    let direction;
    if (allowCountDown) {
        direction = end > start ? 1 : -1;
    } else {
        direction = 1;
    }
    const stepDuration = duration / (maxPoints - 1);
    let lastTime = performance.now();

    function update(now) {
        const elapsed = now - lastTime;

        if (elapsed >= stepDuration) {
            lastTime = now;

            if (start !== end) {
                start += direction;
                start %= 10;
                setDigitValue(element, start, setZero);
            }
        }

        if (start !== end) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

/**
 * @param {HTMLElement} element
 * @param {number} end
 */
function animatePointsSeparately(element, end, allowCountDown = true) {
    const padding = +element.dataset.pad;
    const str = String(end).padStart(padding);

    element.querySelectorAll('.scoreboard-digit').forEach((el, i) => {
        const d = str[i];
        animateDigit(el, d, allowCountDown);
    });
}

function countDownPointValue(pt, animateEachDigit = false, allowCountDown = true, setZero) {
    const el = document.querySelector(`.points-value[data-value="${pt}"]`);
    el.classList.add("spent");
    if (animateEachDigit) {
        animatePointsSeparately(el, 0, allowCountDown);
    } else {
        animatePoints(el, 0);
    }
}

function resetPointValues(animateEachDigit = false, allowCountDown = true) {
    const els = document.querySelectorAll(".points-value");
    for (const el of els) {
        el.classList.remove("spent");
        if (animateEachDigit) {
            animatePointsSeparately(el, +el.dataset.value, allowCountDown);
        } else {
            animatePoints(el, +el.dataset.value);
        }
    }
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
        this.code = data.cc || "XXX";
        this.bg = data.bg;
        this.fg1 = data.fg1;
        this.fg2 = data.fg2;
        this.text = data.text;
        this.win = true;
        this.votes = new Proxy({}, {
            get: (target, name) => name in target ? target[name] : 0
        });
        [this.nameEl, this.currentEl, this.totalEl, this.element, this.currentlyVotingEl] = makeRow(this);
    }

    get points() {
        return Object.entries(this.votes).reduce(
            (a, v) => a + v[0] * v[1],
            0
        );
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
        //countDownPointValue(pt);
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
            setTimeout(() => {
                setElementValue(this.currentEl, 0, true);
            }, 1100);
            this.setInactive();
        } else {
            animatePoints(this.totalEl, this.points, true);
            animatePoints(this.currentEl, pt, true);
            this.setActive();
            if (pt == points[points.length - 1]) {
                this.element.classList.add("received-gold");
            } else if (pt == points[points.length - 2]) {
                this.element.classList.add("received-silver");
            }
            else if (pt == points[points.length - 3]) {
                this.element.classList.add("received-bronze");
            } else {
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
        animatePoints(this.currentEl, place, true);
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
            allKeys.sort((a, b) => a - b);
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
     * @param {number} leftVotes
     * @param {number} leaderPts
     */
    setCanWin(leftVotes, leaderPts) {
        if (!this.win) return;
        const left = this.points + leftVotes * Math.max(points);
        if (left <= leaderPts) {
            debugger;
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
            name: c.country.name,
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
    await new Promise(r => setTimeout(r, delay * 2.5));

    ro.sort((a, b) => b.compare(a));
    for (const [i, c] of ro.entries()) {
        setTimeout(() => {
            c.element.classList.remove("main-moving");
        }, delay * 2.5);
        c.setPosition(i, perColumn);
    }

    await new Promise(r => setTimeout(r, delay * 2.5));
}

async function vote() {
    const juryCounter = document.querySelector("#jury-count");
    const juryBar = document.querySelector("#jury-bar");
    const fromJury = document.querySelector("#from");

    let juryCount = 0;
    const voterCount = voteOrder.length;
    const pointsImmediate = points.slice(0, points.length - 3);
    const pointsDelayed = points.slice(points.length - 3);

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

        const card = makeVotingCard(nickname, code, country);
        fromJury.appendChild(card);

        while (paused) {
            await new Promise(r => setTimeout(r, 100));
        }

        juryCounter.textContent = juryCount;
        juryBar.classList.add("animating");
        setTimeout(() => {
            juryBar.classList.remove("animating");
        }, 2100);
        juryBar.style.width = `${(juryCount / voterCount) * 100}%`;

        await new Promise(r => setTimeout(r, 50));
        card.classList.remove("unloaded");
        await new Promise(r => setTimeout(r, 2000));

        const ownCountry = ro.filter(c => c.code == code);
        if (ownCountry.length > 0) {
            ownCountry[0].setOwnEntry();
        }
        if (entries) {
            for (const entry of entries) {
                const country = countries[entry];
                if (country) {
                    country.setOwnEntry();
                }
            }
            await new Promise(r => setTimeout(r, 500));
        }

        for (const pt of pointsImmediate) {
            while (paused) {
                if (isReset) {
                    isReset = false;
                    return;
                }

                await new Promise(r => setTimeout(r, 100));
            }

            const to = vts[pt];
            const country = countries[to];

            country.vote(pt);
        }

        await sortCountries();

        for (const pt of pointsDelayed) {
            while (paused) {
                if (isReset) {
                    isReset = false;
                    return;
                }

                await new Promise(r => setTimeout(r, 100));
            }

            const to = vts[pt];
            const country = countries[to];

            country.vote(pt);

            await sortCountries();
        }

        card.classList.add("unloaded2");
        await new Promise(r => setTimeout(r, delay * 2.5));

        const leader = ro[0];

        if (juryCount != voterCount) {
            for (const c of ro) {
                c.refresh();
                c.setCanWin(voterCount - juryCount, leader.points);
            }
        }

        await new Promise(r => setTimeout(r, 100));

        card.remove();

        await new Promise(r => setTimeout(r, delay));
    }

    juryBar.style.width = "100%";
    ro[0].setWinner();
    for (const [i, c] of ro.entries()) {
        c.setPlace(i + 1);
    }
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
    isReset = true;
    paused = true;

    document.querySelector("#from").innerHTML = "";

    depopulate();
    populate();
    await vote();
}

async function onLoad(year, show) {
    if (loaded) return;
    loaded = true;

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