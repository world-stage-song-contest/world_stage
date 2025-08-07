class Xoshiro256ss {
    constructor(seed) {
        function splitmix64(seed) {
            let z = BigInt.asUintN(64, BigInt(seed));
            return function() {
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
     *
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
 *
 * @param {number} t
 * @returns {number}
 */
function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}

async function save() {
    if (!allDrawn) return;
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
    index;
    /** @type {Element} */
    element;

    /**
     * @param {Element} element
     * @param {number} i
     */
    constructor(element, i) {
        this.code = element.dataset.code;
        this.submitter = parseInt(element.dataset.submitter);
        this.index = i;
        this.element = element;
    }
}

/**
 * @param {Entry[][]} pots
 * @param {{string: {limit: number, entries: Entry[]}}} shows
 * @param {SFC32} lcg
 */
function drawIntoShows(pots, shows, lcg) {
  if (!lcg?.next) throw new Error('lcg.next(limit) is required');

  /* ---------- flatten input ---------- */
  const showNames = Object.keys(shows);
  if (showNames.length < 2) throw new Error('Need ≥ 2 shows');

  const entries = pots.flat();
  const N = entries.length;
  const K = showNames.length;

  /* ---------- quick impossibility checks ---------- */
  const capacity = showNames.reduce((t, s) => t + shows[s].limit, 0);
  if (N > capacity) throw new Error(`Impossible: ${N} entries > ${capacity} slots`);

  const perSubmitter = new Map();
  for (const e of entries)
    perSubmitter.set(e.submitter, (perSubmitter.get(e.submitter) ?? 0) + 1);
  for (const [s, c] of perSubmitter)
    if (c > K) throw new Error(`Submitter ${s} owns ${c} entries but only ${K} shows`);

  /* ---------- adjacency via submitter clash ---------- */
  const adj = Array.from({ length: N }, () => new Set());
  const bySubmitter = new Map();
  entries.forEach((e, i) => {
    const set = bySubmitter.get(e.submitter) ?? [];
    for (const j of set) { adj[i].add(j); adj[j].add(i); }
    set.push(i); bySubmitter.set(e.submitter, set);
  });

  const colourOf = Array(N).fill(-1);
  const satDeg   = Array(N).fill(0);
  const deg      = adj.map(s => s.size);
  const remaining = showNames.map(n => shows[n].limit);

  function pickVertex() {
    let best = -1, bestSat = -1, bestDeg = -1;
    for (let v = 0; v < N; ++v) if (colourOf[v] === -1) {
      if (satDeg[v] > bestSat ||
         (satDeg[v] === bestSat && deg[v] > bestDeg)) {
        best = v; bestSat = satDeg[v]; bestDeg = deg[v];
      }
    }
    return best;
  }

  function legalColours(v) {
    const banned = new Set();
    for (const u of adj[v]) {
      const c = colourOf[u];
      if (c !== -1) banned.add(c);
    }
    const order = [...Array(K).keys()];
    lcg.shuffle(order);
    return order.filter(c => !banned.has(c) && remaining[c] > 0);
  }

  function assign(vIdx) {
    if (vIdx === -1) return true;

    for (const c of legalColours(vIdx)) {
      colourOf[vIdx] = c; remaining[c]--;
      for (const u of adj[vIdx]) satDeg[u] = new Set(
        [...adj[u]].map(n => colourOf[n]).filter(x => x !== -1)).size;

      if (assign(pickVertex())) return true;

      colourOf[vIdx] = -1; remaining[c]++;
      for (const u of adj[vIdx]) satDeg[u] = new Set(
        [...adj[u]].map(n => colourOf[n]).filter(x => x !== -1)).size;
    }
    return false;
  }

  if (!assign(pickVertex()))
    throw new Error('Deadlock: global graph colouring infeasible');

  for (let i = 0; i < N; ++i) {
    const showIdx = colourOf[i];
    const showObj = shows[showNames[showIdx]];
    showObj.entries.push(entries[i]);
  }
}

/**
 *
 * @param {string} showName
 * @param {Entry[]} showEntries
 * @param {SFC32} lcg
 */
function setShowDraw(showName, showEntries, lcg) {
    console.log(showEntries);
    const elements = [...document.querySelectorAll(`.show[data-name='${showName}'] .show-country`)];
    const entries = showEntries.slice();

    while (entries.length > 0) {
        const el = lcg.pop(elements);
        const en = lcg.pop(entries);

        el.dataset.code = en.code;
        el.dataset.pot = en.pot;
        el.querySelector('.flag').src = en.element.querySelector('.flag').src;
        el.querySelector('.country-name').textContent = en.element.querySelector('.country-name').textContent;
        en.element.dataset.show = showName;
    }
}

let shows = null;

const lcg = new Xoshiro256ss(year);

function drawShowsMulti() {
    /**
     * @type {{string: {'limit': number, 'entries': Entry[]}}}
     */
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

    drawIntoShows(pots, showData, lcg)
    for (const show of Object.values(showData)) {
        show.entries.sort((a, b) => a[1] - b[1])
    }

    for (const [showName, data] of Object.entries(showData)) {
        setShowDraw(showName, data.entries, lcg);
    }
    shows = showData;
}

function drawShowsSingle() {
    /**
     * @type {{string: {'limit': number, 'entries': Entry[]}}}
     */
    const showData = {};
    [...document.querySelectorAll(".show")].forEach(e => {
        showData[e.dataset.name] = {
            limit: parseInt(e.dataset.songs),
            entries: []
        }
    });

    if (Object.keys(showData).length > 1) {
        throw new Error("Only drawing one show is supported");
    }

    const showName = Object.keys(showData)[0];

    const pots = [...document.querySelectorAll(".pot")].flatMap((e, i) =>
        [...e.querySelectorAll(".pot-item")].map(e => new Entry(e, i))
    );

    showData[showName].entries = pots;

    setShowDraw(showName, showData[showName].entries, lcg);

    shows = showData;
}


function drawShows() {
    if (multiDraw) {
        drawShowsMulti();
    } else {
        drawShowsSingle();
    }
}

/**
 * @param {Element} element
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
 *
 * @param {Element} element
 * @param {Element[]} elements
 * @param {(Element, Element) => boolean} equal
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

    let loops = ((lcg.next(5) + 1) % 3);
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
 * @param {Element[]} allCountries
 * @param {Element[]} eligibleCountries
 * @returns {Promise<Element>}
 */
async function selectCountryFromPot(allCountries, eligibleCountries) {
    const selectedCountry = lcg.select(eligibleCountries);

    await animateElementSelect(selectedCountry, allCountries, (a, b) => a.dataset.code == b.dataset.code);

    return selectedCountry;
}

/**
 * @param {Element} selected
 * @param {Element} currentShow
 * @returns {Promise<Element>}
 */
async function getShowSlot(selected, currentShow) {
    const allSlots = [...currentShow.querySelectorAll(".show-country.transparent")];
    const suitableSlot = currentShow.querySelector(`.show-country[data-code=${selected.dataset.code}]`);

    await animateElementSelect(suitableSlot, allSlots, (a, b) => a.dataset.code == b.dataset.code);

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
