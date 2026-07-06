// The following variables are provided in the html file this is included in
// multiDraw: boolean
// year: number

class Xoshiro256StarStar {
    constructor(seed) {
        function splitmix64(seed) {
            let z = BigInt.asUintN(64, BigInt(seed));
            return function () {
                z = (z + 0x9e3779b97f4a7c15n) & 0xffffffffffffffffn;
                let r = z;
                r = (r ^ (r >> 30n)) * 0xbf58476d1ce4e5b9n & 0xffffffffffffffffn;
                r = (r ^ (r >> 27n)) * 0x94d049bb133111ebn & 0xffffffffffffffffn;
                return r ^ (r >> 31n);
            };
        }

        const sm64 = splitmix64(BigInt(seed));
        this.s = [sm64(), sm64(), sm64(), sm64()];
    }

    /**
     * @returns {bigint}
     */
    next64 = () => {
        // xoshiro256** algorithm
        let [s0, s1, s2, s3] = this.s;
        const result = BigInt.asUintN(64, ((s1 * 5n) << 7n | 0n) * 9n);

        const t = BigInt.asUintN(64, s1 << 17n);

        s2 ^= s0;
        s3 ^= s1;
        s1 ^= s2;
        s0 ^= s3;

        s2 ^= t;
        s3 = (s3 << 45n | s3 >> (64n - 45n)) & 0xffffffffffffffffn;

        this.s = [s0, s1, s2, s3];
        return result & 0xffffffffffffffffn;
    }

    /**
     * @returns {number}
     */
    next32 = () => {
        return Number(this.next64() >> 32n) >>> 0;
    }

    /**
     * @returns {number}
     */
    nextFloat = () => {
        // Uses highest 53 bits for IEEE-754 double
        const v = this.next64() >> 11n;
        return Number(v) / 0x1_0000_0000_0000_00;
    }

    /**
     * @param {number} limit
     * @returns {number}
     */
    next = limit => {
        return this.next32() % limit;
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T[]}
     */
    shuffle = a => {
        for (let i = a.length - 1; i > 0; --i) {
            const j = this.next(i + 1);
            [a[i], a[j]] = [a[j], a[i]];
        }
        return a;
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T}
     */
    select = a => {
        return a[this.next(a.length)];
    }

    /**
     * @template T
     * @param {T[]} a
     * @returns {T}
     */
    pop = a => {
        const i = this.next(a.length);
        return a.splice(i, 1)[0];
    }
}

function toggleHeader() {
    const header = document.querySelector("header");
    header.classList.toggle("hidden");
}

/**
 * @param {number} t
 * @returns {number}
 */
function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}

async function save() {
    const error = document.querySelector(".error");
    const data = {};
    for (const show of document.querySelectorAll('.show')) {
        const ro = [];
        for (const slot of show.querySelectorAll(".show-country")) {
            // Skip empty slots (e.g. an aborted draw); only assigned slots
            // carry a song id.
            if (!slot.dataset.id) continue;
            ro.push({ id: parseInt(slot.dataset.id, 10), ro: parseInt(slot.dataset.index, 10) });
        }
        ro.sort((a, b) => a.ro - b.ro);
        data[show.dataset.name] = ro.map(e => e.id);
    }
    const res = await fetch(window.location.pathname, {
        method: "POST",
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })

    const body = res.status === 204 ? {} : await res.json();
    if (!res.ok) {
        error.classList.remove("hidden");
        error.textContent = body.error;
    } else {
        error.classList.add("hidden");
    }
}

let shows = null;
const rng = new Xoshiro256StarStar(year);

/**
 * Performs multi-show draw with constraint satisfaction
 */
function drawShowsMulti() {
    if (!multiDraw) {
        throw new Error("Cannot draw multiple shows in single-draw mode");
    }

    const showData = {};
    [...document.querySelectorAll(".show")].forEach(e => {
        showData[e.dataset.name] = {
            limit: parseInt(e.dataset.songs),
            entries: [...e.querySelectorAll(".show-country")].map(slot => ({
                id: slot.dataset.id,
                code: slot.dataset.code,
            })),
        }
    });

    shows = showData;
}

/**
 * Performs single-show draw without constraints
 */
function drawShowsSingle() {
    if (multiDraw) {
        throw new Error("Cannot draw single show in multi-draw mode");
    }

    const showData = {};
    [...document.querySelectorAll(".show")].forEach(e => {
        showData[e.dataset.name] = {
            limit: parseInt(e.dataset.songs),
            entries: [...e.querySelectorAll(".show-country")].map(slot => ({
                id: slot.dataset.id,
                code: slot.dataset.code,
            })),
        }
    });

    if (Object.keys(showData).length !== 1) {
        throw new Error("Single-draw mode requires exactly one show");
    }
    shows = showData;
}

/**
 * Main entry point for drawing shows
 */
function drawShows() {
    try {
        if (multiDraw) {
            drawShowsMulti();
        } else {
            drawShowsSingle();
        }
    } catch (error) {
        console.error('Draw failed:', error);
        // Reset state on failure
        shows = null;
        throw error;
    }
}

/**
 * @param {HTMLElement} element
 */
function nextSibling(element) {
    const v = element.nextElementSibling;
    if (v == null) {
        return [element.parentElement.firstElementChild, true];
    }
    return [v, false];
}

const minDelay = 10;
const maxDelay = 175;

/**
 * @param {HTMLElement} element
 * @param {HTMLElement[]} elements
 * @param {(HTMLElement, HTMLElement) => boolean} equal
 */
async function animateElementSelect(element, elements, equal) {
    const nElems = elements.length;

    async function mainLoop(current, i, cycles) {
        const progress = i / cycles;
        const eased = easeOutCubic(progress);
        const delay = minDelay + eased * (maxDelay - minDelay);

        if (current) current.classList.remove("active2");
        current = elements[i % nElems];
        current.classList.add("active2");

        await new Promise(r => setTimeout(r, delay));

        return current;
    }

    const index = elements.indexOf(element);
    if (index == -1) {
        throw new Error("Element not part of elements");
    }

    let loops = ((rng.next(5) + 1) % 3);
    if (elements.length < 5) loops += 1;
    if (index < 5) loops += 1;

    const cycles = loops * elements.length;
    const totalCycles = cycles + index;

    let current = null;
    let i = 0;

    for (; i <= cycles; i++) {
        current = await mainLoop(current, i, totalCycles);
    }

    for (; !equal(element, current); i++) {
        current = await mainLoop(current, i, totalCycles);
    }
}

/**
 * @param {HTMLElement} a
 * @param {HTMLElement} b
 * @returns {boolean}
 */
function cmpItems(a, b) {
    // Prefer song id when available — for specials a country can have
    // multiple entries, so data-code isn't unique.
    if (a.dataset.id && b.dataset.id) {
        return a.dataset.id === b.dataset.id;
    }
    return a.dataset.code === b.dataset.code;
}

/**
 * @param {HTMLElement[]} allCountries
 * @param {HTMLElement[]} eligibleCountries
 * @returns {Promise<HTMLElement>}
 */
async function selectCountryFromPot(allCountries, eligibleCountries) {
    const selectedCountry = rng.select(eligibleCountries);
    await animateElementSelect(selectedCountry, allCountries, cmpItems);
    return selectedCountry;
}

/**
 * @param {HTMLElement} selected
 * @param {HTMLElement} currentShow
 * @returns {Promise<HTMLElement>}
 */
async function getShowSlot(selected, currentShow) {
    const allSlots = [...currentShow.querySelectorAll(".show-country.transparent")];
    // Match by song id when present (handles specials with duplicate
    // country codes), falling back to country code for legacy callers.
    const suitableSlot = selected.dataset.id
        ? currentShow.querySelector(`.show-country[data-id="${selected.dataset.id}"]`)
        : currentShow.querySelector(`.show-country[data-code="${selected.dataset.code}"]`);
    await animateElementSelect(suitableSlot, allSlots, cmpItems);
    return suitableSlot;
}

let clicked = false;

async function next() {
    if (clicked) return;
    clicked = true;
    if (shows == null) drawShows();

    const currentPotElement = document.querySelector('.pot.active1');
    const currentPot = currentPotElement.querySelector('.pot-container');
    const currentShowElement = document.querySelector('.show.active1');
    const currentShow = currentShowElement.querySelector('.show-countries');

    try {
        let selected;
        if (multiDraw) {
            const allCountries = [...currentPot.querySelectorAll('.pot-item')];
            const eligibleCountries = [...currentPot.querySelectorAll(`.pot-item[data-show='${currentShow.dataset.name}']`)];
            selected = await selectCountryFromPot(allCountries, eligibleCountries);
        } else {
            selected = document.querySelector(".pot-item:first-child");
        }
        const showSlot = await getShowSlot(selected, currentShow);
        showSlot.classList.remove("transparent");

        await new Promise(r => setTimeout(r, 1000));

        selected.remove();
        showSlot.classList.remove("active2");
    } catch (e) {
        console.log(e);
    }

    const [nextShow, looped] = nextSibling(currentShowElement);
    currentShowElement.classList.remove("active1");
    nextShow.classList.add("active1");
    // Bring the new active show into view if the #shows container has
    // overflowed off-screen — uses the container's `scroll-behavior:
    // smooth` so the scroll animates rather than jumping.
    nextShow.scrollIntoView({ block: "nearest", inline: "center" });

    if (looped) {
        const [nextPot,] = nextSibling(currentPotElement);
        currentPotElement.classList.remove("active1");
        nextPot.classList.add("active1");
    }

    if (currentPotElement.querySelectorAll('.item').length == 0) {
        currentPotElement.remove();
    }

    clicked = false;
}

/**
 * Size every stacked pot to the widest country card across all of them,
 * so one long name (e.g. "Bosnia and Herzegovina") doesn't leave a single
 * pot wider than the rest. Skips the specials / single-pot layout, which
 * uses its own responsive grid and hides the country name.
 */
function sizePotsToWidestCountry() {
    const potsEl = document.getElementById('pots');
    if (!potsEl || potsEl.classList.contains('with-titles')) return;

    // Clear any previous value first so a re-run (e.g. after web fonts
    // load) measures the natural content width rather than the uniform
    // width we last applied. Reading layout below forces the reflow.
    potsEl.style.removeProperty('--pot-tile-width');

    let max = 0;
    for (const item of potsEl.querySelectorAll('.pot:not(.pot-big) .pot-item')) {
        max = Math.max(max, item.getBoundingClientRect().width);
    }
    if (max > 0) {
        potsEl.style.setProperty('--pot-tile-width', `${Math.ceil(max)}px`);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sizePotsToWidestCountry);
} else {
    sizePotsToWidestCountry();
}
// Web fonts can change text width after first paint — re-measure once
// they're ready so the uniform width still fits the longest name.
if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(sizePotsToWidestCountry);
}
