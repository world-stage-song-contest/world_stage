let nTop = 0;
let nSecondChance = 0;
let nSpecial = 0;

// Each entry is uniquely keyed by ``<cc>-<entry_number>`` — country code
// alone isn't unique on specials, where the same country can submit
// several entries.
function entryKey(country) {
    return `${country.cc}-${country.entry_number ?? 1}`;
}

let revealOrder = {
    /** @type {Array<[object, boolean]>} */
    dtf: [],
    /** @type {Array<[object, boolean]>} */
    sc: [],

    data: function() {
        return this.dtf.concat(this.sc);
    },

    type: function(country) {
        const key = entryKey(country);
        if (this.dtf.some(v => entryKey(v[0]) === key)) {
            return "direct-to-final";
        } else if (this.sc.some(v => entryKey(v[0]) === key)) {
            return "second-chance";
        } else {
            return "non-qualifier";
        }
    }
}

function swapReveal(type, a, b) {
    function swapDom(a,b) {
        var aParent = a.parentNode;
        var bParent = b.parentNode;

        var aHolder = document.createElement("div");
        var bHolder = document.createElement("div");

        aParent.replaceChild(aHolder,a);
        bParent.replaceChild(bHolder,b);

        aParent.replaceChild(b,aHolder);
        bParent.replaceChild(a,bHolder);
    }

    const arr = revealOrder[type];
    const indexA = arr.findIndex(v => entryKey(v[0]) === a);
    const indexB = arr.findIndex(v => entryKey(v[0]) === b);
    console.log(`Swapping ${a} (${indexA}) with ${b} (${indexB})`);
    if (indexA === -1 || indexB === -1) return;
    const vA = arr[indexA];
    const vB = arr[indexB];
    arr[indexA] = vB;
    arr[indexB] = vA;

    const aEl = document.querySelector(`.envelope[data-id="${a}"]`);
    const bEl = document.querySelector(`.envelope[data-id="${b}"]`);
    console.log(aEl, bEl);
    if (aEl && bEl) {
        swapDom(aEl, bEl);
        const aNum = aEl.querySelector(".envelope-number");
        const bNum = bEl.querySelector(".envelope-number");
        aNum.textContent = indexB + 1;
        bNum.textContent = indexA + 1;
    }
}

let allCountries = {};
// Specials show the song title (multiple entries per country), regular
// years show the country name.
let isSpecial = false;

let clicked = false;

async function loadVotes(year, show) {
    const res = await fetch(window.location.pathname + '/votes');
    const json = await res.json();
    nTop = json.dtf;
    nSecondChance = json.sc;
    nSpecial = json.special;
    isSpecial = !!json.is_special;
    allCountries = json.countries;
    for (const country of json.reveal_order.dtf) {
        revealOrder.dtf.push([country, false]);
    }
    for (const country of json.reveal_order.sc) {
        revealOrder.sc.push([country, true]);
    }
}

/**
 * Label shown on the envelope card / reveal row. Specials use the song
 * title (the country can have multiple entries); regular years use the
 * country name.
 * @param {object} country
 * @returns {string}
 */
function entryLabel(country) {
    return isSpecial ? country.title : country.country;
}

/**
 * @param {Array} array
 * @returns {Array}
 * */
function shuffle(array) {
    for (let i = array.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [array[i], array[j]] = [array[j], array[i]];
    }
    return array;
}

/**
 * @param {Array} els
 * @returns {void}
 * */
function reveal(els) {
    for (const el of els) {
        el.classList.add("revealed");
    }
}

function isNumeric(string) {
    return !isNaN(string) && !isNaN(parseFloat(string));
}

async function dismissCountry(envelope) {
    envelope.onclick = null;

    const card = envelope.querySelector(".envelope-card");
    const top = envelope.querySelector(".envelope-top");
    //const ro = document.querySelector(".ro");

    card.classList.remove("grow");
    await new Promise(r => setTimeout(r, 500));

    card.classList.add("shrunk");
    card.classList.remove("move-up");
    await new Promise(r => setTimeout(r, 1000));

    top.classList.remove("flip", "bottom");
    await new Promise(r => setTimeout(r, 1500));

    envelope.classList.add("fade-out");
    //ro.classList.add("transparent");
    await new Promise(r => setTimeout(r, 1000));

    envelope.remove();

    clicked = false;
}

async function flipCard(envelope) {
    envelope.onclick = null;

    const card = envelope.querySelector(".envelope-card");
    const front = card.querySelector(".card-front");
    const back = card.querySelector(".card-back");

    front.classList.toggle("flipped");
    back.classList.toggle("flipped");
    await new Promise(r => setTimeout(r, 1000));

    const reveal = document.querySelector(`.country[data-id="${envelope.dataset.id}"]`);
    //const ro = reveal.querySelector(".reveal-ro");

    await new Promise(r => setTimeout(r, 100));

    reveal.classList.add("revealed");
    if (envelope.classList.contains("second-chance")) {
        reveal.classList.add("second-chance");
    }
    //ro.classList.remove("transparent");
    await new Promise(r => setTimeout(r, 1000));

    envelope.onclick = async () => {
        await dismissCountry(envelope);
    }
}

async function revealCard(envelope) {
    envelope.onclick = null;

    const card = envelope.querySelector(".envelope-card");
    const top = envelope.querySelector(".envelope-top");

    top.classList.add("flip");
    card.classList.remove("hidden");
    await new Promise(r => setTimeout(r, 1000));

    card.classList.remove("shrunk");
    card.classList.add("move-up");
    top.classList.add("bottom");
    await new Promise(r => setTimeout(r, 250));

    card.classList.add("grow");
    await new Promise(r => setTimeout(r, 1000));

    envelope.onclick = async () => {
        await flipCard(envelope);
    }
}

async function putInPlace(envelope) {
    if (clicked) return;
    clicked = true;
    envelope.onclick = null;

    const placeholder = document.createElement("div");
    placeholder.classList.add("pseudo-envelope");
    const envelopes = envelope.parentNode
    const reveal = document.querySelector("#envelope");
    //const ro = document.querySelector(".ro");

    envelope.classList.add("fade-out");
    await new Promise(r => setTimeout(r, 1000));

    envelopes.replaceChild(placeholder, envelope);
    reveal.appendChild(envelope);
    envelope.classList.add("ready");
    await new Promise(r => setTimeout(r, 100));

    envelope.classList.remove("fade-out");
    //ro.classList.remove("transparent");
    await new Promise(r => setTimeout(r, 1000));

    envelope.onclick = async () => {
        await revealCard(envelope);
    }
}

function createEnvelope(n, country, isSecondChance) {
    const label = entryLabel(country);
    const countryName = country.country;
    const code = country.cc;
    const id = country.id;

    function createCard() {
        const card = document.createElement("div");
        card.classList.add("envelope-card", "hidden", "shrunk");

        const back = document.createElement("div");
        back.classList.add("card-back");
        card.appendChild(back);

        const front = document.createElement("div");
        front.classList.add("card-front", "flipped");
        card.appendChild(front);

        const flag = document.createElement("img");
        flag.src = `/flag/${code}.svg?t=rect&s=54`;
        flag.classList.add("card-flag");
        flag.title = countryName;
        front.appendChild(flag);

        // Specials display the song title (multiple entries per country
        // would otherwise be indistinguishable); regular years display
        // the country name. The flag's hover tooltip always carries the
        // country name.
        const titleEl = document.createElement("h2");
        titleEl.textContent = label;
        titleEl.title = label;
        titleEl.classList.add(isSpecial ? "card-title" : "card-country");
        front.appendChild(titleEl);

        return card;
    }

    const envelope = document.createElement("div");
    envelope.classList.add("envelope", isSecondChance ? "second-chance" : "direct-to-final");
    // Composite key — uniquely identifies the entry on specials where a
    // country can have multiple submissions.
    envelope.dataset.id = entryKey(country);
    envelope.dataset.cc = code;
    envelope.dataset.song = id;

    envelope.onclick = async () => {
        await putInPlace(envelope);
    }

    function createEnvelopePart(name, width, height) {
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", `#${name}`);
        use.setAttribute("width", width);
        use.setAttribute("height", height);

        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.appendChild(use);
        svg.setAttribute("width", width);
        svg.setAttribute("height", height);

        const part = document.createElement("div");
        part.classList.add(name, "envelope-part");
        part.appendChild(svg);

        return part;
    }

    envelope.appendChild(createEnvelopePart("envelope-bg", 180, 120));
    envelope.appendChild(createEnvelopePart("envelope-bottom", 180, 120));
    envelope.appendChild(createEnvelopePart("envelope-top", 180, 60));

    const number = document.createElement("h2");
    number.textContent = n;
    number.classList.add("envelope-number");
    envelope.appendChild(number);

    envelope.appendChild(createCard());

    return envelope;
}

function createCountry(country, countryClass) {
    const countryEl = document.createElement("div");
    countryEl.classList.add("country", countryClass);
    countryEl.dataset.id = entryKey(country);
    countryEl.dataset.cc = country.cc;

    const flag = document.createElement("img");
    flag.classList.add("reveal-flag");
    flag.src = `/flag/${country.cc}.svg?t=square&s=24`;
    flag.title = country.country;
    countryEl.appendChild(flag);

    // Specials use the song title (a single country can have multiple
    // entries); regular years use the country name. ``title`` attribute
    // exposes the full text on hover when truncated.
    const label = entryLabel(country);
    const heading = document.createElement("h2");
    heading.classList.add(isSpecial ? "reveal-title" : "reveal-country");
    heading.textContent = label;
    heading.title = label;
    countryEl.appendChild(heading);

    /*
    const ro = document.createElement("h2");
    ro.classList.add("reveal-ro", "transparent");
    country.appendChild(ro);*/

    return countryEl;
}

function createEnvelopes() {
    const envelopesDtf = document.querySelector("#envelopes-dtf");
    const envelopesSc = document.querySelector("#envelopes-sc");
    let n = 1;
    for (const [country, isSecondChance] of revealOrder.data()) {
        const envelope = createEnvelope(n++ - isSecondChance * nTop, country, isSecondChance);
        if (isSecondChance) {
            envelopesSc.appendChild(envelope);
        } else {
            envelopesDtf.appendChild(envelope);
        }
    }
}

function createRo() {
    console.log(revealOrder);
    const countries = document.querySelector("#results");
    const lim = allCountries.length / 2;
    for (const [i, country] of allCountries.entries()) {
        const col = Math.floor(i / lim);
        const row = i - lim * col;

        let countryClass = revealOrder.type(country);

        const countryEl = createCountry(country, countryClass);
        countryEl.style.gridColumn = col + 1;
        countryEl.style.gridRow = row + 1;

        countries.appendChild(countryEl);
    }

}

let loaded = false;

async function onLoad(year, show) {
    if (loaded) return;
    loaded = true;

    await loadVotes(year, show);
    createRo();
    createEnvelopes();
}

function toggleHeader() {
    const header = document.querySelector("header");
    header.classList.toggle("hidden");
}

async function save() {
    const dtf = revealOrder.dtf.map(v => v[0].id);
    const sc = revealOrder.sc.map(v => v[0].id);
    await fetch(window.location.pathname, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ action: "save", dtf, sc })
    });
}