(function () {
    "use strict";

    function makePointDisplay(padding, className) {
        const element = document.createElement("div");
        element.classList.add(className, "number", "point-display", "zero");
        element.dataset.pad = String(padding);
        element.dataset.ghost = "8".repeat(padding);
        return element;
    }

    function createRow(country) {
        const element = document.createElement("div");
        element.classList.add("element", "inactive");
        element.dataset.country = country.name;
        element.dataset.id = country.id;

        const inner = document.createElement("div");
        inner.classList.add("inner-container");
        element.appendChild(inner);

        const overlay = document.createElement("div");
        overlay.classList.add("element-overlay");
        inner.appendChild(overlay);

        const place = document.createElement("div");
        place.classList.add("element-place", "number");
        inner.appendChild(place);

        const flagContainer = document.createElement("div");
        flagContainer.classList.add("flag-container");
        inner.appendChild(flagContainer);

        const flag = document.createElement("img");
        flag.classList.add("flag", "flag-image");
        flag.draggable = false;
        flag.src = window.flagStaticUrl(country.code, 40, "square");
        flag.alt = country.name;
        flagContainer.appendChild(flag);

        const flagOverlay = document.createElement("div");
        flagOverlay.classList.add("flag-overlay");
        flagContainer.appendChild(flagOverlay);

        const nameContainer = document.createElement("div");
        nameContainer.classList.add("name-container");
        inner.appendChild(nameContainer);

        const name = document.createElement("div");
        name.classList.add("name");
        name.textContent = country.name;
        nameContainer.appendChild(name);

        const currentlyVoting = document.createElement("div");
        currentlyVoting.classList.add("currently-voting");
        inner.appendChild(currentlyVoting);

        const current = makePointDisplay(2, "current-points");
        inner.appendChild(current);

        const total = makePointDisplay(3, "total-points");
        inner.appendChild(total);

        return {element, name, current, total, currentlyVoting};
    }

    function createPointsLegend(points) {
        const row = document.querySelector("#points-row");
        row.replaceChildren();

        for (const [index, point] of points.entries()) {
            const container = document.createElement("div");
            container.classList.add("points-container");
            const distanceFromTop = points.length - index;
            if (distanceFromTop === 1) container.classList.add("gold");
            if (distanceFromTop === 2) container.classList.add("silver");
            if (distanceFromTop === 3) container.classList.add("bronze");

            const value = document.createElement("div");
            value.classList.add("points-value", "number");
            value.textContent = String(point).padStart(2, "0");
            value.dataset.pad = "2";
            value.dataset.value = point;
            container.appendChild(value);

            const overlay = document.createElement("div");
            overlay.classList.add("points-overlay");
            container.appendChild(overlay);
            row.prepend(container);
        }
    }

    function createVotingCard({name, code, country, username, penalty = false}) {
        const element = document.createElement("div");
        element.classList.add("voting-card", "unloaded");
        if (penalty) element.classList.add("penalty-card");

        const flag = document.createElement("img");
        flag.classList.add("voting-card-flag", "flag-image");
        flag.draggable = false;
        flag.src = window.flagStaticUrl(code || "XX", 96);
        flag.alt = name;
        element.appendChild(flag);

        const wrapper = document.createElement("div");
        wrapper.classList.add("voting-card-user-wrapper");
        element.appendChild(wrapper);

        const nameElement = document.createElement("span");
        nameElement.classList.add("voting-card-name");
        nameElement.textContent = name;
        wrapper.appendChild(nameElement);

        if (country) {
            const countryElement = document.createElement("span");
            countryElement.classList.add("voting-card-country");
            countryElement.textContent = username && username !== name
                ? `${username} from ${country}`
                : `from ${country}`;
            wrapper.appendChild(countryElement);
        }

        return element;
    }

    function renderNumber(element, value) {
        const padding = +element.dataset.pad || 0;
        element.dataset.value = String(value);
        element.classList.toggle("negative", value < 0);
        element.classList.toggle("zero", value === 0);
        element.textContent = String(Math.abs(value)).padStart(padding, " ");
    }

    function renderText(element, value) {
        const padding = +element.dataset.pad || 0;
        element.classList.remove("negative", "zero");
        element.textContent = String(value).padStart(padding, " ");
    }

    function positionRow(view, position, perColumn) {
        const column = Math.floor(position / perColumn);
        const row = position - perColumn * column;
        const size = view.element.getBoundingClientRect();
        view.element.style.top = `${(size.height + 5) * row}px`;
        view.element.style.left = `${(size.width + 5) * column}px`;
    }

    function setActive(view) {
        view.element.classList.remove("inactive", "own-entry");
        view.current.classList.add("visible");
        view.element.classList.add("main-moving", "active", "received-points");
    }

    function setInactive(view) {
        view.current.classList.remove("visible");
        view.element.classList.add("inactive");
        view.element.classList.remove(
            "received-gold", "received-silver", "received-bronze",
            "received-points", "active", "own-entry"
        );
    }

    function markReceived(view, rank) {
        const classes = {1: "received-gold", 2: "received-silver", 3: "received-bronze"};
        if (classes[rank]) view.element.classList.add(classes[rank]);
    }

    let progressToken = 0;

    function updateJuryProgress(current, total) {
        const token = ++progressToken;
        document.querySelector("#jury-count").textContent = current;
        const bar = document.querySelector("#jury-bar");
        bar.classList.add("animating");
        setTimeout(() => {
            if (token === progressToken) bar.classList.remove("animating");
        }, 2100);
        bar.style.width = `${total ? (current / total) * 100 : 0}%`;
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
            view.element.classList.add("winner");
            view.element.classList.remove("no-win", "own-entry", "active");
        },
        markOwnEntry(view) { view.element.classList.add("own-entry"); },
        setFinalPlace(view, place) {
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
