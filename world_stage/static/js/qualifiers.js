// Each entry is uniquely keyed by ``<cc>-<entry_number>`` — country code
// alone isn't unique on specials, where the same country can submit
// several entries.
function entryKey(country) {
    return `${country.cc}-${country.entry_number ?? 1}`;
}

let revealOrder = {
    groups: [],

    data: function() {
        return this.groups.flatMap((group, index) =>
            group.entries.map(entry => [entry, index > 0, group.target_show_id])
        );
    },

    type: function(country) {
        const key = entryKey(country);
        const index = this.groups.findIndex(group =>
            group.entries.some(entry => entryKey(entry) === key)
        );
        if (index === 0) return "direct-to-final";
        if (index > 0) return "second-chance";
        return "non-qualifier";
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

    const group = revealOrder.groups.find(item => String(item.target_show_id) === String(type));
    if (!group) return;
    const arr = group.entries;
    const indexA = arr.findIndex(v => entryKey(v) === a);
    const indexB = arr.findIndex(v => entryKey(v) === b);
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
    isSpecial = !!json.is_special;
    allCountries = json.countries;
    revealOrder.groups = json.progressions.map(progression => ({
        ...progression,
        entries: json.reveal_order[String(progression.target_show_id)] || []
    }));
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
    const envelope = window.qualifiersTheme.createEnvelope(
        n,
        country,
        isSecondChance,
        {entryKey, entryLabel, isSpecial}
    );
    envelope.onclick = async () => {
        await putInPlace(envelope);
    };
    return envelope;
}

function createCountry(country, countryClass) {
    return window.qualifiersTheme.createCountry(
        country,
        countryClass,
        {entryKey, entryLabel, isSpecial}
    );
}

function createEnvelopes() {
    const wrapper = document.querySelector("#envelopes");
    for (const [groupIndex, group] of revealOrder.groups.entries()) {
        const container = document.createElement("div");
        container.dataset.targetShow = group.target_show_id;
        container.dataset.targetName = group.target_name;
        container.setAttribute("aria-label", group.target_name);
        for (const [index, country] of group.entries.entries()) {
            container.appendChild(createEnvelope(index + 1, country, groupIndex > 0));
        }
        wrapper.appendChild(container);
    }
}

function createRo() {
    console.log(revealOrder);
    const countries = document.querySelector("#results");
    const columnCount = window.qualifiersTheme.resultColumns || 2;
    const lim = Math.ceil(allCountries.length / columnCount);
    document.querySelector("#qualifier-reveal").style.setProperty(
        "--qualifier-column-count",
        String(columnCount)
    );
    document.querySelector("#qualifier-reveal").style.setProperty(
        "--qualifier-row-count",
        String(lim)
    );
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
    if (!window.qualifiersTheme) {
        throw new Error("Qualifier reveal theme was not loaded");
    }
    loaded = true;

    await loadVotes(year, show);
    createRo();
    createEnvelopes();
}

function toggleHeader() {
    const header = document.querySelector("header");
    header.classList.toggle("hidden");
    window.scrollTo({top: 0, left: 0});
}

async function save() {
    const progressions = Object.fromEntries(
        revealOrder.groups.map(group => [
            String(group.target_show_id),
            group.entries.map(entry => ({
                song_id: entry.id,
                is_special: entry.is_special === true
            }))
        ])
    );
    await fetch(window.location.pathname, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ action: "save", progressions })
    });
}
