(function () {
    "use strict";

    function makeScore(role) {
        const element = document.createElement("div");
        element.classList.add("sb97-score", `sb97-${role}`);
        element.dataset.role = role;
        return element;
    }

    function createRow(country) {
        const element = document.createElement("div");
        element.classList.add("sb97-row", "inactive");
        element.dataset.country = country.name;
        element.dataset.id = country.id;

        const flagFrame = document.createElement("div");
        flagFrame.classList.add("sb97-flag-frame");
        element.appendChild(flagFrame);

        const picture = document.createElement("picture");
        picture.classList.add("sb97-flag-picture");
        flagFrame.appendChild(picture);

        const smallFlagSource = document.createElement("source");
        const smallFlagCutoff = window.FLAG_SMALL_WIDTH_CUTOFF || 36;
        smallFlagSource.srcset = window.flagStaticUrl(
            country.code,
            smallFlagCutoff - 1
        );
        // The row's precise width is only known after it enters the layout.
        // ``positionRow`` enables this source when the rendered flag is small.
        smallFlagSource.media = "not all";
        picture.appendChild(smallFlagSource);

        const flag = document.createElement("img");
        flag.classList.add("sb97-flag", "flag-image");
        flag.src = window.flagStaticUrl(country.code, smallFlagCutoff + 1);
        flag.alt = country.name;
        flag.draggable = false;
        picture.appendChild(flag);

        const name = document.createElement("div");
        name.classList.add("sb97-name");
        name.textContent = country.name;
        element.appendChild(name);

        const current = makeScore("current");
        element.appendChild(current);

        const total = makeScore("total");
        element.appendChild(total);

        return {element, flagFrame, smallFlagSource, name, current, total};
    }

    function createPointsLegend() {
        document.querySelector("#points-row").replaceChildren();
    }

    function createVotingCard({name, code, country, username, penalty = false}) {
        const element = document.createElement("div");
        element.classList.add("sb97-card", "unloaded");
        if (penalty) element.classList.add("penalty-card");

        if (!penalty) {
            const flag = document.createElement("img");
            flag.classList.add("sb97-card-flag", "flag-image");
            flag.src = window.flagStaticUrl(code || "XX", 96);
            flag.alt = name;
            flag.draggable = false;
            element.appendChild(flag);
        }

        const copy = document.createElement("div");
        copy.classList.add("sb97-card-copy");
        element.appendChild(copy);

        const heading = document.createElement("strong");
        heading.classList.add("sb97-card-name");
        heading.textContent = name;
        copy.appendChild(heading);

        if (country) {
            const detail = document.createElement("span");
            detail.classList.add("sb97-card-detail");
            detail.textContent = username && username !== name
                ? `${username} · ${country}`
                : country;
            copy.appendChild(detail);
        }

        return element;
    }

    function renderNumber(element, value) {
        element.classList.toggle("negative", value < 0);
        element.classList.toggle("zero", value === 0);
        element.textContent = String(value);
    }

    function renderText(element, value) {
        element.classList.remove("negative", "zero");
        element.textContent = value === "()" ? "" : String(value);
    }

    function positionRow(view, position, perColumn) {
        const scoreboard = document.querySelector("#scoreboard");
        const container = document.querySelector("#container");
        const jury = document.querySelector("#jury-container");
        const scoreboardStyle = getComputedStyle(scoreboard);
        const juryStyle = getComputedStyle(jury);
        const bodyStyle = getComputedStyle(document.body);
        const pixels = (style, property) =>
            parseFloat(style.getPropertyValue(property)) || 0;
        const trailingHeight = jury.getBoundingClientRect().height
            + pixels(juryStyle, "margin-top")
            + pixels(juryStyle, "margin-bottom")
            + pixels(scoreboardStyle, "padding-bottom")
            + pixels(scoreboardStyle, "border-bottom-width")
            + pixels(scoreboardStyle, "margin-bottom")
            + pixels(bodyStyle, "padding-bottom")
            + 4;
        const availableHeight = window.innerHeight
            - container.getBoundingClientRect().top
            - trailingHeight;
        const rowHeight = Math.max(
            24,
            Math.min(56, Math.floor(availableHeight / perColumn) - 2)
        );
        scoreboard.style.setProperty("--sb97-row-height", `${rowHeight}px`);

        const column = Math.floor(position / perColumn);
        const row = position - perColumn * column;
        const size = view.element.getBoundingClientRect();
        const flagWidth = (size.height - 4) * 1.5 + 3;
        const renderedFlagWidth = flagWidth - 3;
        const useSmallFlag = renderedFlagWidth <= (window.FLAG_SMALL_WIDTH_CUTOFF || 36);
        view.smallFlagSource.media = useSmallFlag ? "all" : "not all";
        view.element.style.setProperty("--sb97-flag-frame-width", `${flagWidth}px`);
        container.style.height = `${(size.height + 2) * perColumn}px`;
        view.element.style.top = `${(size.height + 2) * row}px`;
        view.element.style.left = `${(size.width + 4) * column}px`;
    }

    function setActive(view) {
        view.element.classList.remove("inactive", "own-entry");
        view.element.classList.add("active", "main-moving", "received-points");
    }

    function setInactive(view) {
        view.element.classList.add("inactive");
        view.element.classList.remove(
            "active", "own-entry", "received-points",
            "received-first", "received-second", "received-third"
        );
    }

    function markReceived(view, rank) {
        const classes = {1: "received-first", 2: "received-second", 3: "received-third"};
        if (classes[rank]) view.element.classList.add(classes[rank]);
    }

    function clearPointColours(view) {
        view.element.classList.remove(
            "received-points", "received-first", "received-second",
            "received-third", "penalised"
        );
    }

    let progressToken = 0;

    function updateJuryProgress(current, total) {
        const token = ++progressToken;
        document.querySelector("#jury-count").textContent = current;
        const bar = document.querySelector("#jury-bar");
        bar.classList.add("animating");
        bar.style.width = `${total ? (current / total) * 100 : 0}%`;
        setTimeout(() => {
            if (token === progressToken) bar.classList.remove("animating");
        }, 2100);
    }

    window.scoreboardTheme = {
        createRow,
        createPointsLegend,
        createVotingCard,
        renderNumber,
        renderText,
        positionRow,
        setActive,
        setInactive,
        markReceived,
        applyPenalty(view) { view.element.classList.add("penalised"); },
        stopMoving(view) { view.element.classList.remove("main-moving"); },
        markCannotWin(view) { view.element.classList.add("no-win"); },
        markWinner(view) {
            clearPointColours(view);
            view.element.classList.add("winner");
            view.element.classList.remove("no-win", "active", "own-entry");
        },
        markOwnEntry(view) {
            clearPointColours(view);
            view.element.classList.remove("active");
            view.element.classList.add("own-entry");
        },
        setFinalPlace(view, place) {
            clearPointColours(view);
            view.element.classList.remove("no-win");
            const parent = view.element.parentElement;
            parent.insertBefore(view.element, parent.childNodes[place]);
        },
        showVotingCard(card) { card.classList.remove("unloaded"); },
        hideVotingCard(card) { card.classList.add("unloaded2"); },
        updateJuryProgress,
        completeJuryProgress() { document.querySelector("#jury-bar").style.width = "100%"; },
        reset() {
            progressToken++;
            document.querySelector("#from").replaceChildren();
            document.querySelector("#jury-count").textContent = "0";
            const bar = document.querySelector("#jury-bar");
            bar.classList.remove("animating");
            bar.style.width = "0";
        },
        toggleHeader() { document.querySelector("header").classList.toggle("hidden"); }
    };
})();
