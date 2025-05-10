let voteOrder = []
let votes = {}
let data = []
let points = []
let associations = {}
let userSongs = {}

async function loadVotes() {
    const show = document.querySelector("#show").innerText;
    const searchParams = new URL(window.location.toString()).search;
    const res = await fetch(`/results/${show}/scoreboard/votes` + searchParams);
    const json = await res.json();
    points = json.points;
    userSongs = json.user_songs;
    points.sort((a, b) => a - b);
    voteOrder = json.vote_order;
    for (const song of json.songs) {
        data.push(song);
    }
    data.sort((a, b) => a.ro - b.ro);
    votes = json.results;
    associations = json.associations;
}

function makeRow(code, name, artist, title, id) {
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

    const subtitleContainer = document.createElement("div");
    subtitleContainer.classList.add("subtitle");
    nameContainer.appendChild(subtitleContainer);

    const titleEl = document.createElement("span");
    titleEl.classList.add("title");
    titleEl.innerText = title;
    subtitleContainer.appendChild(titleEl);

    const byNode = document.createTextNode(" by ");
    subtitleContainer.appendChild(byNode);

    const artistEl = document.createElement("span");
    artistEl.classList.add("artist");
    artistEl.innerText = artist;
    subtitleContainer.appendChild(artistEl);

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
    code = code || "XRW";
    country = country || "Rest of the World";

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
    constructor(index, name, artist, title, ro, id, country, code) {
        this.rollback = [];
        this.index = index;
        this.ro = ro;
        this.name = name;
        this.artist = artist;
        this.title = title;
        this.id = id;
        this.country = country;
        this.code = code;
        this.win = true;
        this.votes = new Array(Math.max(...points) + 1).fill(0);
        [this.nameEl, this.currentEl, this.totalEl, this.element] = makeRow(code, name, artist, title, id);
    }
    
    get points() {
        return this.votes.reduce(
            (a, v, i) => a + v * i,
            0
        );
    }

    get voters() {
        return this.votes.reduce((a, v) => a + v, 0);
    }
    
    setPosition(i, lim) {
        const col = Math.floor(i / lim);
        const row = i - lim * col;

        this.index = i;

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
            this.element.classList.remove("own-entry");
        } else {
            this.currentEl.innerText = pt;
            this.currentEl.classList.add("visible");
            this.element.classList.add( "main-moving");
            if (setClass == undefined || setClass) this.element.classList.add("active");
        }
    }

    finalise() {
        this.element.classList.remove("main-moving");
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
        const votersDiff = this.voters - other.voters;
        const vtsDiff = compareArrays(this.votes, other.votes);
        
        if (ptsDiff != 0) return ptsDiff;
        if (votersDiff != 0) return votersDiff;
        if (vtsDiff != 0) return vtsDiff;
        return other.ro - this.ro;
    }

    setCanWin(leftVotes, leaderPts) {
        /*
        if (!this.win) return;
        if (this.points + leftVotes * 12 <= leaderPts) {
            this.win = false;
            this.element.classList.add("no-win");
        }*/
    }

    setWinner() {
        this.element.classList.add("winner");
        this.element.classList.remove("no-win");
    }
    
    toString() {
        return `Country { name = ${this.name}, ro = ${this.ro}, votes = ${this.votes} }`;
    }
}

let countries = {};
let ro = [];
let perColumn = 0;

function populate() {
    const cnt = data.length;
    perColumn = Math.ceil(cnt / 2);
    for (const [i, c] of data.entries()) {
        const country = new Country(i, c.country, c.artist, c.title, c.ro, c.id, c.country, c.code);
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

async function sortCountries() {
    ro.sort((a, b) => b.compare(a));
    for (const [i, c] of ro.entries()) {
        c.setPosition(i, perColumn);
    }
    await new Promise(r => setTimeout(r, delay * 2));
    for (const c of ro) {
        c.finalise();
    }
}

async function vote() {
    const voterCount = voteOrder.length;
    const pointsImmediate = points.slice(0, points.length - 3);
    const pointsDelayed = points.slice(points.length - 3);

    let countriesVoted = 0;
    
    for (const from of voteOrder) {
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
        
        let voted = [];

        const card = makeVotingCard(nickname, code, country);
        document.querySelector("#from").appendChild(card);

        while (paused) {
            await new Promise(r => setTimeout(r, 100));
        }

        await new Promise(r => setTimeout(r, 50));
        card.classList.remove("unloaded");
        await new Promise(r => setTimeout(r, 2000));

        if (entries) {
            for (const entry of entries) {
                const country = countries[entry];
                if (country) {
                    country.element.classList.add("own-entry");
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
            voted.push(country);
        }

        await new Promise(r => setTimeout(r, delay * 2.5));
        sortCountries();
        await new Promise(r => setTimeout(r, delay * 2.5));

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
            voted.push(country);

            await new Promise(r => setTimeout(r, delay * 2.5));
            sortCountries();
            await new Promise(r => setTimeout(r, delay * 2.5));
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
        
        await new Promise(r => setTimeout(r, 100));

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