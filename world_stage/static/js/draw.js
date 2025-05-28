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
function nextChildOrFirst(node, parent) {
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
 * @param {Element} pot 
 * @returns {Promise<Element>}
 */
async function selectRandomChild(pot) {
    let current = null;
    let totalCycles = lcg.next(40) + 15; // total "flashes"
    const minDelay = 10;
    const maxDelay = 175;

    for (let i = 0; i < totalCycles; i++) {
        const progress = i / totalCycles; // 0 to 1
        const eased = easeOutCubic(progress);
        const delay = minDelay + eased * (maxDelay - minDelay);

        if (current) current.classList.remove("active2");
        [current, _] = nextChildOrFirst(current, pot);
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

function setFirstActive() {
    document.querySelector(".show.active1").classList.remove("active1");
    const show = document.querySelector(".show");
    show.classList.add("active1");
    createRunningOrders(show);
}

let allDrawn = false;
let lockedIntoShow = true;
let finalised = false;
let runningOrders = [];

function createRunningOrders(show) {
    const songs = +show.dataset.songs;
    const ros = Array.from(Array(songs).keys());
    shuffle(ros);
    runningOrders = ros;
}

async function next() {
    if (clicked || allDrawn) return;
    clicked = true;

    const currentShowElement = document.querySelector(".show.active1");
    const currentShow = currentShowElement.querySelector(".show-countries");
    const currentPotElement = document.querySelector(".pot.active1");
    const currentPot = currentPotElement.querySelector(".pot-container");

    const country = await selectRandomChild(currentPot);
    country.classList.add("selected");

    const emptyCountry = currentShow.querySelector(".empty");

    const countryFlag = emptyCountry.querySelector(".flag");
    const countryFlagContainer = emptyCountry.querySelector(".flag-container");
    const countryName = emptyCountry.querySelector(".country-name");

    countryFlag.src = country.querySelector(".flag").src;
    countryName.textContent = country.querySelector(".country-name").textContent;
    countryFlagContainer.classList.remove("transparent");

    emptyCountry.dataset.code = country.dataset.code;
    emptyCountry.classList.remove("empty");
    countryName.classList.remove("transparent");

    //await new Promise(r => setTimeout(r, 1100));
    country.remove();

    currentPotElement.classList.remove("active1");
    const [nextPot, loopedAround] = getNextNonEmptyPot(currentPotElement);
    if (nextPot != null) {
        nextPot.classList.add("active1");
        currentShowElement.classList.remove("active1");
        const nextShow = getSuitableShow(currentShowElement, loopedAround);
        nextShow.classList.add("active1");
    }

    if (document.querySelectorAll(".pot-item").length == 0) {
        allDrawn = true;
        lockedIntoShow = true;
        setFirstActive();
    }

    clicked = false;
}

async function sortCountriesInShow() {
    if (clicked || lockedIntoShow) return;
    clicked = true;

    const currentShow = document.querySelector(".show.active1");
    const container = currentShow.querySelector(".show-countries");
    const lim = +container.dataset.limit;
    [...container.children].forEach((el) => {
        el.style.setProperty("--column", Math.floor(el.dataset.index / lim));
        el.style.setProperty("--row", el.dataset.index % lim);
    });
    await new Promise (r => setTimeout(r, 1500));
    
    currentShow.classList.remove("active1");
    const [nextShow, loopedAround] = nextChildOrFirst(currentShow);
    if (loopedAround) {
        finalised = true;
    } else {
        nextShow.classList.add("active1");
        createRunningOrders(nextShow);
    }
    clicked = false;
    lockedIntoShow = true;
}

async function makeRo() {
    if (clicked) return;
    clicked = true;
    const currentShow = document.querySelector(".show.active1");
    const songs = +currentShow.dataset.songs;
    const thisEl = currentShow.querySelector(".running-order.unfilled");

    if (currentShow.querySelectorAll(".running-order.unfilled").length == 0) {
        lockedIntoShow = false;
    }

    if (thisEl != null) {
        await animateNumberRoll(thisEl, runningOrders.pop(), songs, 500);

        thisEl.classList.remove("unfilled");
        thisEl.classList.add("filled");
    }

    clicked = false;
}

function shuffle(array) {
    for (let i = array.length - 1; i > 0; i--) {
        const j = lcg.next(i + 1);
        [array[i], array[j]] = [array[j], array[i]];
    }
    return array;
}

// Animate rolling and return a promise that resolves when done
function animateNumberRoll(element, finalNumber, maxNumber, delay = 0) {
    element.parentElement.dataset.index = finalNumber;
    finalNumber += 1;
    return new Promise((resolve) => {
        let current = 0;
        const duration = 1000;
        const steps = 20;
        const intervalTime = duration / steps;

        setTimeout(() => {
            let count = 0;
            const interval = setInterval(() => {
                count++;
                element.textContent = 1 + lcg.next(maxNumber);
                if (count >= steps) {
                    clearInterval(interval);
                    element.textContent = finalNumber;
                    resolve();
                }
            }, intervalTime);
        }, delay);
    });
}

function logDraw() {
    const data = {};
    for (const show of document.querySelectorAll('.show')) {
        const ro = [];
        for (const country of show.querySelectorAll(".show-country")) {
            ro.push({cc: country.dataset.code, ro: country.dataset.index});
        }
        ro.sort((a, b) => a.ro - b.ro);
        data[show.dataset.name] = ro.map(e => e.cc);
    }
    return data;
}

async function save() {
    if (!finalised) return;
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
    const res = await fetch(`/admin/draw/${year}`, {
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