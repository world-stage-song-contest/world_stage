class LCG {
  constructor(seed, modulus = 2 ** 31, multiplier = 1103515245, increment = 12345) {
    this.seed = seed;
    this.modulus = modulus;
    this.multiplier = multiplier;
    this.increment = increment;
  }

  next(limit) {
    this.seed = (this.multiplier * this.seed + this.increment) % this.modulus;
    if (limit) return this.seed % limit;
    else return this.seed;
  }

  nextFloat() {
    return this.next() / this.modulus;
  }
}

function simpleHash(num) {
  let hash = num;

  // Mix the bits with some bitwise and arithmetic operations
  hash = ((hash >> 16) ^ hash) * 0x45d9f3b;
  hash = ((hash >> 16) ^ hash) * 0x45d9f3b;
  hash = (hash >> 16) ^ hash;

  // Ensure it's a positive 32-bit integer
  return hash >>> 0;
}

const lcg = new LCG(year);

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

/**
 * @param {Element} node
 * @param {Element} parent
 */
function nextChildOrFirstHelper(node, parent) {
    if (node && node.nextElementSibling) {
        return [node.nextElementSibling, false];
    } else {
        if (!parent) {
            parent = node.parentElement;
        }

        return [parent.firstElementChild, true];
    }
}

/**
 * @param {Element} node
 * @param {Element} parent
 * @param {string} [skipClass=""]
 */
function nextChildOrFirst(node, parent, skipClass = "+") {
    let looped = false;
    let currentNode = node;
    while (true) {
        const [nextNode, loopedAround] = nextChildOrFirstHelper(currentNode, parent);
        currentNode = nextNode;
        looped = looped || loopedAround;
        if (currentNode == node) return [null, true];
        if (!currentNode.classList.contains(skipClass)) return [currentNode, looped];
    }
}

/**
 * @param {Element} pot
 * @returns {Promise<Element>}
 */
async function selectRandomChild(pot, skipClass = "q") {
    const children = Array.from(pot.querySelectorAll(`.item:not(.${skipClass})`));
    const elems = children.length;
    let current = null;
    let totalCycles = 1 //Math.floor(lcg.next(elems * 2.5) + elems * 1.5); // total "flashes"
    const minDelay = 10;
    const maxDelay = 175;

    for (let i = 0; i < totalCycles; i++) {
        const progress = i / totalCycles; // 0 to 1
        const eased = easeOutCubic(progress);
        const delay = minDelay + eased * (maxDelay - minDelay);

        if (current) current.classList.remove("active2");
        current = children[i % elems];
        current.classList.add("active2");

        await new Promise(r => setTimeout(r, delay));
    }

    return current;
}

let clicked = false;

function getNextNonEmptyPot(activePot) {
    let looped = false;
    let currentPot = activePot;
    while (true) {
        const [nextPot, loopedAround] = nextChildOrFirst(currentPot);
        currentPot = nextPot;
        looped = looped || loopedAround;
        if (currentPot == activePot) return [null, looped];
        if (currentPot.querySelectorAll(".pot-container .pot-item").length != 0) {
            return [nextPot, looped]
        }
    }
}

function getSuitableShow(activeShow, loopedAround) {
    let ret = activeShow;
    if (loopedAround || activeShow.querySelectorAll(".show-country.empty").length == 0) {
        ret = nextChildOrFirst(activeShow)[0];
    }
    return ret;
}

let allDrawn = false;

function transfer(dest, src) {
    const countryFlag = dest.querySelector(".flag");
    const countryFlagContainer = dest.querySelector(".flag-container");
    const countryName = dest.querySelector(".country-name");

    countryFlag.src = src.querySelector(".flag").src;
    countryName.textContent = src.querySelector(".country-name").textContent;
    countryFlagContainer.classList.remove("transparent");

    dest.dataset.code = src.dataset.code;
    dest.classList.remove("empty");
    countryName.classList.remove("transparent");
}

async function next(selectRandomFromPot = true) {
    if (clicked || allDrawn) return;
    clicked = true;

    const currentShowElement = document.querySelector(".show.active1");
    const currentShow = currentShowElement.querySelector(".show-countries");
    const currentPotElement = document.querySelector(".pot.active1");
    const currentPot = currentPotElement.querySelector(".pot-container");

    let country;
    if (selectRandomFromPot) {
        country = await selectRandomChild(currentPot);
    } else {
        country = currentPot.firstElementChild;
    }
    country.classList.add("selected");

    await new Promise(r => setTimeout(r, 500));

    const dest = await selectRandomChild(currentShow, "filled");
    dest.classList.add("selected");

    transfer(dest, country);

    await new Promise(r => setTimeout(r, 1100));
    country.remove();
    dest.classList.remove("selected", "active2");
    dest.classList.add("filled");

    if (selectRandomFromPot) {
        currentPotElement.classList.remove("active1");
        const [nextPot, loopedAround] = getNextNonEmptyPot(currentPotElement);
        if (nextPot != null) {
            nextPot.classList.add("active1");
            currentShowElement.classList.remove("active1");
            const nextShow = getSuitableShow(currentShowElement, loopedAround);
            nextShow.classList.add("active1");
        }
    }

    if (document.querySelectorAll(".pot-item").length == 0) {
        allDrawn = true;
    }

    clicked = false;
}

async function nextAll() {
    if (clicked || allDrawn) return;
    clicked = true;

    const currentShowElement = document.querySelector(".show.active1");
    const currentShow = currentShowElement.querySelector(".show-countries");
    const allPots = document.querySelector("#pots");

    const country = await selectRandomChild(allPots);
    country.classList.add("selected");

    await new Promise(r => setTimeout(r, 500));

    const dest = await selectRandomChild(currentShow, "filled");
    dest.classList.add("selected");

    transfer(dest, country);

    await new Promise(r => setTimeout(r, 1100));
    country.remove();
    dest.classList.remove("selected", "active2");
    dest.classList.add("filled");

    if (document.querySelectorAll(".pot-item").length == 0) {
        allDrawn = true;
        setFirstActive();
    }

    clicked = false;
}

async function save() {
    if (!allDrawn) return;
    const error = document.querySelector(".error");
    const data = {};
    for (const show of document.querySelectorAll('.show')) {
        const ro = [];
        for (const country of show.querySelectorAll(".show-country")) {
            ro.push({cc: country.dataset.code, ro: country.dataset.index});
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