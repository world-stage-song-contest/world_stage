let voteOrder = []
let votes = {}
let data = []
let points = []
let associations = {}

async function loadVotes() {
    const show = document.querySelector("#show").innerText;
    const res = await fetch(`/results/${show}/scoreboard/votes`);
    const json = await res.json();
    points = json.points;
    points.sort((a, b) => a - b);
    voteOrder = json.vote_order;
    for (const song of json.songs) {
        data.push(song);
    }
    data.sort((a, b) => a.ro - b.ro);
    votes = json.results;
    associations = json.associations;
}

function makeRow(code, name, subtitle, id) {
    const container = document.createElement("div");
    container.classList.add("element");
    container.dataset.country = name;
    container.dataset.id = id;

    const flagContainer = document.createElement("div");
    flagContainer.classList.add("flag-container");
    container.appendChild(flagContainer);

    const flagEl = document.createElement("img");
    flagEl.classList.add("flag");
    flagEl.src = `/static/flags/square/${code.toLowerCase()}.svg`;
    flagContainer.appendChild(flagEl);

    const nameContainer = document.createElement("div");
    nameContainer.classList.add("name-container");
    container.appendChild(nameContainer);

    const nameEl = document.createElement("div");
    nameEl.classList.add("name");
    nameEl.innerText = name;
    nameContainer.appendChild(nameEl);

    const subtitleEl = document.createElement("div");
    subtitleEl.classList.add("subtitle");
    subtitleEl.innerText = subtitle;
    nameContainer.appendChild(subtitleEl);

    const currentEl = document.createElement("div");
    currentEl.classList.add("current-points", "number");
    currentEl.innerText = "";
    container.appendChild(currentEl);

    const totalEl = document.createElement("div");
    totalEl.classList.add("total-points", "number");
    totalEl.innerText = "0";
    container.appendChild(totalEl);

    return [nameEl, currentEl, totalEl, container];
}

function makeVotingCard(from, code, country) {
    const container = document.createElement("div");
    container.classList.add("voting-card", "unloaded");

    const flagEl = document.createElement("img");
    flagEl.classList.add("voting-card-flag");
    flagEl.src = `/static/flags/rect/${code.toLowerCase()}.svg`;
    flagEl.alt = from;
    container.appendChild(flagEl);

    const wrapperEl = document.createElement("div");
    wrapperEl.classList.add("voting-card-user-wrapper");
    container.appendChild(wrapperEl);

    const nameEl = document.createElement("span");
    nameEl.classList.add("voting-card-name");
    nameEl.innerText = from;
    wrapperEl.appendChild(nameEl);

    const countryEl = document.createElement("span");
    countryEl.classList.add("voting-card-country");
    countryEl.innerText = `From ${country}`;
    wrapperEl.appendChild(countryEl);

    return container;
}

function indexToPts(i) {
    return points[i];
}

function ptsToIndex(pt) {
    return points.indexOf(pt);
}

class Country {
    constructor(name, subtitle, ro, id, country, code) {
        this.rollback = [];
        this.ro = ro;
        this.name = name;
        this.subtitle = subtitle;
        this.id = id;
        this.country = country;
        this.code = code;
        this.win = true;
        this.votes = new Array(Math.max(...points) + 1).fill(0);
        [this.nameEl, this.currentEl, this.totalEl, this.element] = makeRow(code, name, subtitle, id);
    }
    
    get points() {
        return this.votes.reduce(
            (a, v, i) => a + v * i,
            0
        );
    }
    
    setPosition(i, lim) {
        const col = Math.floor(i / lim);
        const row = i - lim * col;

        this.element.style.top = `${45 * row}px`;
        this.element.style.left = `${505 * col}px`;
    }
    
    vote(pt) {
        this.votes[pt]++;
        this.refresh(pt);
    }
    
    refresh(pt, setClass) {
        this.totalEl.innerText = this.points;
        if (pt == null) {
            this.currentEl.innerText = "";
            this.element.classList.remove("active");
            this.currentEl.classList.remove("visible");
        } else {
            this.currentEl.innerText = pt;
            this.currentEl.classList.add("visible");
            if (setClass == undefined || setClass) this.element.classList.add("active");
        }
    }
    
    compare(other) {
        function compareArrays(arr1, arr2) {
            for (let i = arr1.length - 1; i <= 0; i++) {
                const el1 = arr1[i];
                const el2 = arr2[i];
                
                const d = el1 - el2;
                if (d != 0) return d;
            }
            
            return 0;
        }
        
        const ptsDiff = this.points - other.points;
        const vtsDiff = compareArrays(this.votes, other.votes);
        
        if (ptsDiff != 0) return ptsDiff;
        if (vtsDiff != 0) return ptsDiff;
        return this.ro - other.ro;
    }

    setCanWin(leftVotes, leaderPts) {
        return;
        if (!this.win) return;
        if (this.points + leftVotes * 12 <= leaderPts) {
            this.win = false;
            this.element.classList.add("no-win");
        }
    }

    setWinner() {
        this.element.classList.add("winner");
        this.element.classList.remove("no-win");
    }
    
    toString() {
        return `Country { name = ${this.name}, ro = ${this.ro}, votes = ${this.votes} }`;
    }
}

function findInsertIndex(element, array) {
    for (const [i, el] of array.entries()) {
        const res = el.compare(element);
        if (res <= 0) return i;
    }
    
    return array.length - 1;
}

function moveElement(array, fromIndex, toIndex) {
    if (fromIndex >= 0 && fromIndex < array.length && toIndex >= 0 && toIndex < array.length) {
        const element = array.splice(fromIndex, 1)[0];
        array.splice(toIndex, 0, element);
    }
    return array;
}

let countries = {};
let ro = [];
let perColumn = 0;

function populate() {
    const cnt = data.length;
    perColumn = cnt / 2;
    for (const c of data) {
        const country = new Country(c.country, `${c.title} – ${c.artist}`, c.ro, c.id, c.country, c.code);
        countries[c.id] = country;
        ro.push(country);

        country.setPosition(c.ro - 1, perColumn);
        
        document.querySelector("#container").appendChild(country.element);
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

async function vote() {
    console.log("Voting");
    const voterCount = voteOrder.length;

    let countriesVoted = 0;
    
    for (const from of voteOrder) {
        let revealedPts = points.length - 1;
        const vts = votes[from];
        console.log(vts)

        let nickname = from;
        let country = null;
        let code = null;
        const assoc = associations[from];
        console.log(assoc)
        if (assoc) {
            nickname = assoc.nickname || from;
            country = assoc.country;
            code = assoc.code;
        }
        
        let voted = [];

        const card = makeVotingCard(nickname, code, country);
        document.querySelector("#from").appendChild(card);

        while (paused) {
            await new Promise(r => setTimeout(r, 100));
        }

        await new Promise(r => setTimeout(r, 50));
        card.classList.remove("unloaded");
        await new Promise(r => setTimeout(r, 2000));

        for (const pt of points) {
            const to = vts[pt];
            console.log(`Voted ${pt} for ${to}`);
            while (paused) {
                if (isReset) {
                    isReset = false;
                    return;
                }

                await new Promise(r => setTimeout(r, 100));
            }
            
            const country = countries[to]
            country.vote(pt);
            voted.push(country);
            
            const newIndex = findInsertIndex(country, ro);
            const oldIndex = ro.indexOf(country);

            let moveBack = [];
            
            if (oldIndex != newIndex) {
                moveBack = ro.slice(newIndex, oldIndex);
                for (const [off, el] of moveBack.entries()) {
                    el.element.classList.add("other-moving");
                    el.setPosition(newIndex + off + 1, perColumn);
                }
                
                country.setPosition(newIndex, perColumn);
                country.element.classList.add("main-moving");
                
                moveElement(ro, oldIndex, newIndex);
            }
            
            if (revealedPts == 3 || revealedPts == 1) {
                await new Promise(r => setTimeout(r, delay * 2));
            } else if (revealedPts == 2) {
                await new Promise(r => setTimeout(r, delay));
            }

            revealedPts--;

            country.element.classList.remove("main-moving");
            for (const el of moveBack) {
                el.element.classList.remove("other-moving");
            }
        }

        await new Promise(r => setTimeout(r, delay * 2.5));
        card.classList.add("unloaded2");
        await new Promise(r => setTimeout(r, delay * 2.5));

        countriesVoted++;
        const leader = ro[0];
        
        for (const c of ro) {
            c.refresh();
            c.setCanWin(voterCount - countriesVoted, leader.points);
        }

        card.remove();
    
        await new Promise(r => setTimeout(r, delay));
    }

    ro[0].setWinner();
    for (const [i, c] of ro.entries()) {
        c.refresh(i + 1, false);
        await new Promise(r => setTimeout(r, 100));
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
    console.log("Resetting");
    isReset = true;
    paused = true;

    document.querySelector("#from").innerHTML = "";
    
    depopulate();
    populate();
    await vote();
}

async function onLoad() {
    if (loaded) return;
    loaded = true;

    await loadVotes();

    document.querySelector("#reset").onclick = async () => {
        await reset();
    }

    populate();
    await vote();
}