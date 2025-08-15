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
        for (const country of show.querySelectorAll(".show-country")) {
            ro.push({ cc: country.dataset.code, ro: country.dataset.index });
        }
        ro.sort((a, b) => a.ro - b.ro);
        data[show.dataset.name] = ro.map(e => e.cc);
    }
    const res = await fetch(window.location.pathname, {
        method: "POST",
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })

    const body = await res.json();
    if (!res.ok) {
        error.classList.remove("hidden");
        error.textContent = body.error;
    } else {
        error.classList.add("hidden");
    }
}

class Entry {
    /** @type {string} */
    code;
    /** @type {number} */
    submitter;
    /** @type {number} */
    pot;
    /** @type {Element} */
    element;

    /**
     * @param {Element} element
     * @param {number} i
     */
    constructor(element, i) {
        this.code = element.dataset.code;
        this.submitter = parseInt(element.dataset.submitter);
        this.pot = i;
        this.element = element;
    }
}

/**
 * Validates that the allocation problem has a feasible solution
 * @param {Entry[][]} pots - Array of pots, each containing entries
 * @param {Object<string, {limit: number, entries: Entry[]}>} shows - Show configurations
 * @throws {Error} If validation fails
 */
function validateAllocation(pots, shows) {
    const showNames = Object.keys(shows);
    if (showNames.length < 2) {
        throw new Error('Need at least 2 shows for multi-draw allocation');
    }

    const totalEntries = pots.reduce((sum, pot) => sum + pot.length, 0);
    const totalCapacity = showNames.reduce((sum, name) => sum + shows[name].limit, 0);

    if (totalEntries > totalCapacity) {
        throw new Error(`Allocation impossible: ${totalEntries} entries exceed ${totalCapacity} available slots`);
    }

    // Check submitter constraints
    const entriesPerSubmitter = new Map();
    for (const pot of pots) {
        for (const entry of pot) {
            entriesPerSubmitter.set(entry.submitter, (entriesPerSubmitter.get(entry.submitter) ?? 0) + 1);
        }
    }

    for (const [submitter, count] of entriesPerSubmitter) {
        if (count > showNames.length) {
            throw new Error(`Submitter ${submitter} has ${count} entries but only ${showNames.length} shows available`);
        }
    }
}

/**
 * Assigns entries from a single pot to available shows using bipartite matching
 * @param {Entry[]} pot - Entries to assign (will be modified)
 * @param {Array<{name: string, limit: number, entries: Entry[], submitters: Set<number>}>} liveShows - Shows with capacity
 * @param {Xoshiro256StarStar} rng - Random number generator
 * @returns {boolean} True if assignment succeeded
 */
function assignPotToShows(pot, liveShows, rng) {
    const showCount = liveShows.length;
    const entryCount = pot.length;

    // Build adjacency lists (show -> compatible entries)
    const adjacency = Array.from({ length: showCount }, () => []);
    for (let showIndex = 0; showIndex < showCount; ++showIndex) {
        const show = liveShows[showIndex];
        for (let entryIndex = 0; entryIndex < entryCount; ++entryIndex) {
            if (!show.submitters.has(pot[entryIndex].submitter)) {
                adjacency[showIndex].push(entryIndex);
            }
        }
    }

    // Find maximum bipartite matching (entry -> show)
    const matchEntry = Array(entryCount).fill(-1);
    const seen = Array(entryCount);

    /**
     * DFS to find augmenting path
     * @param {number} showIndex
     * @returns {boolean}
     */
    function findAugmentingPath(showIndex) {
        for (const entryIndex of adjacency[showIndex]) {
            if (seen[entryIndex]) continue;
            seen[entryIndex] = true;

            if (matchEntry[entryIndex] === -1 || findAugmentingPath(matchEntry[entryIndex])) {
                matchEntry[entryIndex] = showIndex;
                return true;
            }
        }
        return false;
    }

    // Try to match each show
    for (let showIndex = 0; showIndex < showCount; ++showIndex) {
        seen.fill(false);
        findAugmentingPath(showIndex);
    }

    // Apply the matching and collect assigned indices
    const assignedIndices = [];
    for (let entryIndex = 0; entryIndex < entryCount; ++entryIndex) {
        const showIndex = matchEntry[entryIndex];
        if (showIndex !== -1) {
            const show = liveShows[showIndex];
            const entry = pot[entryIndex];
            show.entries.push(entry);
            show.submitters.add(entry.submitter);
            assignedIndices.push(entryIndex);
        }
    }

    // Remove assigned entries efficiently
    const remainingEntries = pot.filter((_, index) => !assignedIndices.includes(index));
    pot.length = 0;
    pot.push(...remainingEntries);

    return true;
}

/**
 * Assigns leftover entries to remaining show slots
 * @param {Entry[]} leftovers - Remaining entries to assign
 * @param {Array<{name: string, limit: number, entries: Entry[], submitters: Set<number>}>} showStates - All shows
 * @param {Xoshiro256StarStar} rng - Random number generator
 */
function assignLeftovers(leftovers, showStates, rng) {
    // Build list of available slots with show references
    const availableSlots = [];
    for (const show of showStates) {
        const remainingCapacity = show.limit - show.entries.length;
        for (let i = 0; i < remainingCapacity; i++) {
            availableSlots.push(show);
        }
    }

    const slotCount = availableSlots.length;
    const leftoverCount = leftovers.length;

    if (leftoverCount > slotCount) {
        throw new Error(`Cannot assign ${leftoverCount} leftovers to ${slotCount} available slots`);
    }

    // Bipartite matching for leftovers
    const slotToEntry = Array(slotCount).fill(-1);
    const seen = Array(slotCount);

    /**
     * DFS to find augmenting path for leftover assignment
     * @param {number} entryIndex
     * @returns {boolean}
     */
    function findAugmentingPath(entryIndex) {
        const submitter = leftovers[entryIndex].submitter;

        for (let slotIndex = 0; slotIndex < slotCount; ++slotIndex) {
            if (seen[slotIndex]) continue;

            const show = availableSlots[slotIndex];
            if (show.submitters.has(submitter)) continue;

            seen[slotIndex] = true;

            if (slotToEntry[slotIndex] === -1 || findAugmentingPath(slotToEntry[slotIndex])) {
                slotToEntry[slotIndex] = entryIndex;
                return true;
            }
        }
        return false;
    }

    // Match each leftover entry
    for (let entryIndex = 0; entryIndex < leftoverCount; ++entryIndex) {
        seen.fill(false);
        if (!findAugmentingPath(entryIndex)) {
            throw new Error(`Cannot place entry from submitter ${leftovers[entryIndex].submitter}: no compatible shows available`);
        }
    }

    // Apply the matching
    for (let slotIndex = 0; slotIndex < slotCount; ++slotIndex) {
        const entryIndex = slotToEntry[slotIndex];
        if (entryIndex !== -1) {
            const show = availableSlots[slotIndex];
            const entry = leftovers[entryIndex];
            show.entries.push(entry);
            show.submitters.add(entry.submitter);
        }
    }
}

/**
 * Main allocation algorithm - distributes entries from pots into shows
 * @param {Entry[][]} pots - Array of pots containing entries
 * @param {Object<string, {limit: number, entries: Entry[]}>} shows - Show configurations
 * @param {Xoshiro256StarStar} rng - Random number generator
 * @throws {Error} If allocation fails
 */
function drawIntoShows(pots, shows, rng) {
    if (!rng?.next) {
        throw new Error('Valid RNG with next(limit) method required');
    }

    validateAllocation(pots, shows);

    // Prepare show states with tracking
    const showNames = Object.keys(shows);
    const showStates = showNames.map(name => ({
        name,
        limit: shows[name].limit,
        entries: shows[name].entries,
        submitters: new Set(shows[name].entries.map(e => e.submitter)),
    }));

    // Shuffle each pot for randomization
    pots.forEach(rng.shuffle);

    // Phase 1: Assign complete pots to shows
    while (true) {
        const liveShows = showStates.filter(s => s.entries.length < s.limit);
        if (!liveShows.length) break;

        // Check if we have enough entries in each pot
        if (!pots.every(pot => pot.length >= liveShows.length)) break;

        // Randomize show order for fairness
        rng.shuffle(liveShows);

        for (const pot of pots) {
            if (!assignPotToShows(pot, liveShows, rng)) {
                throw new Error('Pot assignment failed: conflicting submitter constraints');
            }
        }
    }

    // Phase 2: Handle remaining entries
    const leftovers = rng.shuffle(pots.flat());
    if (leftovers.length > 0) {
        assignLeftovers(leftovers, showStates, rng);
    }

    // Verify final state
    for (const show of showStates) {
        if (show.entries.length !== show.limit) {
            throw new Error(`Show ${show.name} has ${show.entries.length} entries but needs ${show.limit}`);
        }
    }
}

/**
 * Updates show UI with assigned entries
 * @param {string} showName - Name of the show
 * @param {Entry[]} showEntries - Entries assigned to this show
 * @param {Xoshiro256StarStar} rng - Random number generator
 */
function setShowDraw(showName, showEntries, rng) {
    console.log(showEntries);
    const elements = [...document.querySelectorAll(`.show[data-name='${showName}'] .show-country`)];
    const entries = showEntries.slice();

    while (entries.length > 0) {
        const el = rng.pop(elements);
        const en = rng.pop(entries);

        el.dataset.code = en.code;
        el.dataset.pot = en.pot;
        el.querySelector('.flag').src = en.element.querySelector('.flag').src;
        el.querySelector('.country-name').textContent = en.element.querySelector('.country-name').textContent;
        en.element.dataset.show = showName;
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
            entries: []
        }
    });

    const pots = [...document.querySelectorAll(".pot")].map((e, i) =>
        [...e.querySelectorAll(".pot-item")].map(e => new Entry(e, i))
    );

    drawIntoShows(pots, showData, rng);

    // Sort entries by pot for display
    for (const show of Object.values(showData)) {
        show.entries.sort((a, b) => a.pot - b.pot);
    }

    for (const [showName, data] of Object.entries(showData)) {
        setShowDraw(showName, data.entries, rng);
    }

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
            entries: []
        }
    });

    if (Object.keys(showData).length !== 1) {
        throw new Error("Single-draw mode requires exactly one show");
    }

    const showName = Object.keys(showData)[0];
    const show = showData[showName];

    // Collect all entries from all pots
    const allEntries = [...document.querySelectorAll(".pot")].flatMap((e, i) =>
        [...e.querySelectorAll(".pot-item")].map(e => new Entry(e, i))
    );

    // Validate capacity
    if (allEntries.length > show.limit) {
        throw new Error(`Cannot fit ${allEntries.length} entries into ${show.limit} slots`);
    }

    // Check submitter constraints even in single-show mode
    const submitterCounts = new Map();
    for (const entry of allEntries) {
        submitterCounts.set(entry.submitter, (submitterCounts.get(entry.submitter) ?? 0) + 1);
    }

    show.entries = allEntries;
    setShowDraw(showName, show.entries, rng);
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
    const suitableSlot = currentShow.querySelector(`.show-country[data-code=${selected.dataset.code}]`);
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

    const [nextPot, looped] = nextSibling(currentPotElement);
    currentPotElement.classList.remove("active1");
    nextPot.classList.add("active1");

    if (looped) {
        const [nextShow,] = nextSibling(currentShowElement);
        currentShowElement.classList.remove("active1");
        nextShow.classList.add("active1");
    }

    clicked = false;
}

async function nextAll() {
    if (clicked) return;
    clicked = true;
    if (shows == null) drawShows();

    const currentShowElement = document.querySelector('.show.active1');
    const currentShow = currentShowElement.querySelector('.show-countries');

    const allCountries = [...document.querySelectorAll('.pot-item')];
    const eligibleCountries = [...document.querySelectorAll(`.pot-item[data-show='${currentShow.dataset.name}']`)];
    const selected = await selectCountryFromPot(allCountries, eligibleCountries);
    const showSlot = await getShowSlot(selected, currentShow);
    showSlot.classList.remove("transparent");

    await new Promise(r => setTimeout(r, 1000));

    selected.remove();
    showSlot.classList.remove("active2");

    if (currentShow.querySelectorAll('.empty').length == 0) {
        const [nextShow,] = nextSibling(currentShowElement);
        currentShowElement.classList.remove("active1");
        nextShow.classList.add("active1");
    }

    clicked = false;
}