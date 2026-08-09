(function () {
    "use strict";

    function makeCard(country, context) {
        const label = context.entryLabel(country);
        const card = document.createElement("div");
        card.classList.add("envelope-card", "hidden", "shrunk");

        const back = document.createElement("div");
        back.classList.add("card-back");
        const backLabel = document.createElement("span");
        backLabel.textContent = "QUALIFIER";
        back.appendChild(backLabel);
        card.appendChild(back);

        const front = document.createElement("div");
        front.classList.add("card-front", "flipped");
        card.appendChild(front);

        const flagFrame = document.createElement("div");
        flagFrame.classList.add("qualifier-card-flag-frame");
        front.appendChild(flagFrame);

        const flag = document.createElement("img");
        flag.src = window.flagStaticUrl(country.cc, 96);
        flag.classList.add("card-flag", "flag-image");
        flag.draggable = false;
        flag.alt = country.country;
        flag.title = country.country;
        flagFrame.appendChild(flag);

        const title = document.createElement("h2");
        title.textContent = label;
        title.title = label;
        title.classList.add(context.isSpecial ? "card-title" : "card-country");
        front.appendChild(title);
        return card;
    }

    function makeEnvelopePart(className) {
        const part = document.createElement("div");
        part.classList.add(className, "envelope-part");
        return part;
    }

    function createEnvelope(n, country, isSecondChance, context) {
        const envelope = document.createElement("div");
        envelope.classList.add(
            "envelope",
            isSecondChance ? "second-chance" : "direct-to-final"
        );
        envelope.dataset.id = context.entryKey(country);
        envelope.dataset.cc = country.cc;
        envelope.dataset.song = country.id;
        if (country.is_special) envelope.title = "Special qualifier";

        envelope.appendChild(makeEnvelopePart("envelope-bg"));
        envelope.appendChild(makeEnvelopePart("envelope-bottom"));
        envelope.appendChild(makeEnvelopePart("envelope-top"));

        const number = document.createElement("h2");
        number.textContent = n;
        number.classList.add("envelope-number");
        envelope.appendChild(number);
        envelope.appendChild(makeCard(country, context));
        return envelope;
    }

    function createCountry(country, countryClass, context) {
        const element = document.createElement("div");
        element.classList.add("country", countryClass);
        element.dataset.id = context.entryKey(country);
        element.dataset.cc = country.cc;

        const runningOrder = document.createElement("div");
        runningOrder.classList.add("reveal-running-order");
        runningOrder.textContent = country.running_order;
        runningOrder.setAttribute("aria-label", `Running order ${country.running_order}`);
        element.appendChild(runningOrder);

        const flagFrame = document.createElement("div");
        flagFrame.classList.add("reveal-flag-frame");
        element.appendChild(flagFrame);

        const flag = document.createElement("img");
        flag.classList.add("reveal-flag", "flag-image");
        flag.draggable = false;
        flag.src = window.flagStaticUrl(country.cc, 40);
        flag.alt = country.country;
        flag.title = country.country;
        flagFrame.appendChild(flag);

        const label = context.entryLabel(country);
        const heading = document.createElement("h2");
        heading.classList.add(context.isSpecial ? "reveal-title" : "reveal-country");
        heading.textContent = label;
        heading.title = label;
        element.appendChild(heading);
        return element;
    }

    window.qualifiersTheme = {createEnvelope, createCountry, resultColumns: 1};
})();
